import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tdd.cli import main
from agent_tdd.evaluation import cli
from agent_tdd.evaluation.contracts import Usage


def invoke(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-tdd",
            "evaluate",
            "--cases",
            "evals/refund-routing.json",
            "--report",
            str(tmp_path / "candidate.json"),
            *args,
        ],
    )
    return main()


def test_cli_scripted_end_to_end_baseline_and_artifacts(tmp_path):
    args = [
        sys.executable,
        "-m",
        "agent_tdd",
        "evaluate",
        "--cases",
        "evals/refund-routing.json",
        "--report",
        str(tmp_path / "baseline.json"),
        "--repeats",
        "2",
    ]
    first = subprocess.run(args, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(
        args[:-3]
        + [
            str(tmp_path / "candidate.json"),
            "--repeats",
            "2",
            "--baseline",
            str(tmp_path / "baseline.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    report = json.loads((tmp_path / "candidate.json").read_text())
    assert report["summary"]["baseline_pass_rate"] == 1
    assert report["provenance"]["suite_kind"] == "scripted"
    assert report["provenance"]["revision"]
    assert len(report["provenance"]["dependency_lock_sha256"]) == 64
    assert all((tmp_path / f"candidate.{ext}").is_file() for ext in ("html", "xml"))


@pytest.mark.parametrize(
    "args",
    [
        ["--repeats", "0"],
        ["--max-requests", "0"],
        ["--report", "invalid.txt"],
        ["--cases", "not-present.json"],
        ["--min-pass-rate", "NaN"],
    ],
)
def test_invalid_cli_inputs_fail_safe(monkeypatch, tmp_path, capsys, args):
    assert invoke(monkeypatch, tmp_path, *args) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_evaluation_configuration"


def test_cli_gate_failure_and_invalid_baseline(monkeypatch, tmp_path, capsys):
    assert invoke(monkeypatch, tmp_path, "--max-requests", "1") == 1
    assert not json.loads(capsys.readouterr().out)["passed"]
    assert invoke(monkeypatch, tmp_path, "--baseline", str(tmp_path / "candidate.json")) == 2


def test_cli_scriptless_and_wrong_config_reject_before_execution(monkeypatch, tmp_path):
    data = json.loads(Path("evals/refund-routing.json").read_text())
    del data["cases"][-1]["script"]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data))
    assert invoke(monkeypatch, tmp_path, "--cases", str(path)) == 2
    config = tmp_path / "config.json"
    config.write_text('{"secret":"do-not-print"}')
    assert invoke(monkeypatch, tmp_path, "--config", str(config)) == 2
    assert invoke(monkeypatch, tmp_path, "--provider", "openai", "--config", str(config)) == 2
    config.write_text('{"model":42,"prompt":"hello","endpoint":"https://example.com"}')
    assert invoke(monkeypatch, tmp_path, "--provider", "openai", "--config", str(config)) == 2


def test_cli_openai_composition_does_not_receive_expected_and_config_secrets_not_logged(
    monkeypatch, tmp_path, capsys
):
    seen = []

    class Model:
        def __init__(self, settings, *, endpoint, api_key):
            assert api_key == "private-token"
            assert endpoint == "https://example.com/v1/chat/completions"

        def decide(self, task, history, *, timeout_seconds):
            seen.append(task)
            raise RuntimeError("private-provider-payload")

        def usage(self):
            return Usage(1)

    monkeypatch.setattr(cli, "OpenAIModel", Model)
    monkeypatch.setenv("OPENAI_API_KEY", "private-token")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "model": "model",
                "prompt": "private-prompt",
                "endpoint": "https://example.com/v1/chat/completions",
            }
        )
    )
    assert (
        invoke(
            monkeypatch,
            tmp_path,
            "--provider",
            "openai",
            "--config",
            str(config),
            "--revision",
            "release-1",
        )
        == 1
    )
    assert len(seen) == 6 and all("expected" not in task for task in seen)
    report = (tmp_path / "candidate.json").read_text()
    captured = capsys.readouterr()
    assert all(
        secret not in report + captured.out + captured.err
        for secret in ("private-token", "private-provider-payload", "private-prompt")
    )
    assert json.loads(report)["provenance"]["revision_state"] == "asserted"


def test_bedrock_optional_dependency_error_and_valid_composition(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    args = ["--provider", "bedrock", "--config", "evals/providers/bedrock.example.json"]
    assert invoke(monkeypatch, tmp_path, *args) == 2
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: SimpleNamespace())

    class Model:
        def __init__(self, settings, *, region):
            assert region == "us-east-1"

        def decide(self, *a, **k):
            raise RuntimeError("no live provider")

        def usage(self):
            return Usage(1)

    monkeypatch.setattr(cli, "BedrockModel", Model)
    assert invoke(monkeypatch, tmp_path, *args) == 1


def test_revision_without_git_and_invalid_override(monkeypatch):
    def fail(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(cli.subprocess, "check_output", fail)
    assert cli._revision(None) == ("unknown", "unknown")
    with pytest.raises(ValueError):
        cli._revision(" " * 201)


def test_report_must_not_overwrite_case_or_provider_config(monkeypatch, tmp_path):
    assert invoke(monkeypatch, tmp_path, "--report", "evals/refund-routing.json") == 2
    assert (
        invoke(
            monkeypatch,
            tmp_path,
            "--provider",
            "openai",
            "--config",
            "evals/providers/openai.example.json",
            "--report",
            "evals/providers/openai.example.json",
        )
        == 2
    )


def test_unwritable_report_destination_rejected_before_execution(monkeypatch, tmp_path):
    calls = []

    def unavailable(path):
        raise PermissionError("private destination")

    def execute(*args, **kwargs):
        calls.append(True)
        raise AssertionError("must not dispatch")

    monkeypatch.setattr(cli, "prepare_reports", unavailable)
    monkeypatch.setattr(cli, "evaluate", execute)
    assert invoke(monkeypatch, tmp_path) == 2
    assert not calls


def test_report_siblings_cannot_overwrite_input_files(monkeypatch, tmp_path):
    original = Path("evals/refund-routing.json").read_text()
    for extension in ("html", "xml"):
        path = tmp_path / f"candidate.{extension}"
        path.write_text(original)
        assert invoke(monkeypatch, tmp_path, "--cases", str(path)) == 2
        assert path.read_text() == original
