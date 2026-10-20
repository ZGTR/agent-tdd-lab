"""Real subprocess gates must fail closed without replacing the application harness."""

import json
import runpy
import subprocess
import sys
from dataclasses import replace

import pytest

from agent_tdd.cli import main
from agent_tdd.evaluation.command import CommandExecutor, CommandSettings, _sample
from agent_tdd.evaluation.contracts import Case
from agent_tdd.runtime import RunSettings
from agent_tdd.types import Status

arguments = runpy.run_path("scripts/run_ci_gate.py")["arguments"]


@pytest.fixture
def response():
    return {
        "schema_version": 1,
        "result": {"status": "completed", "output": "answer", "model_calls": 1},
        "usage": {"requests": 1, "input_tokens": 4, "output_tokens": 2},
    }


def child(tmp_path, code):
    path = tmp_path / "adapter with spaces.py"
    path.write_text(code)
    return CommandSettings((sys.executable, str(path)), "scripted", "test-v1")


def test_real_process_receives_only_task_and_limits_not_expected_or_script(tmp_path, response):
    saved = tmp_path / "request.json"
    response["result"]["observations"] = [{"tool": "search", "ok": True, "value": [1, 2]}]
    command = child(
        tmp_path,
        "import sys,json\n"
        "from pathlib import Path\n"
        "data=json.load(sys.stdin)\n"
        f"Path({str(saved)!r}).write_text(json.dumps(data))\n"
        f"print({json.dumps(response)!r})\n",
    )
    sample = CommandExecutor(command, RunSettings(2, 5)).run(
        Case("private-case-id", "release", {"question": "hello"}, {"output": "secret"}),
        request_id="attempt-1",
        max_requests=3,
    )
    assert sample.result.status == Status.COMPLETED
    assert sample.result.observations[0].tool == "search"
    assert sample.usage.requests == 1
    request = json.loads(saved.read_text())
    assert request == {
        "schema_version": 1,
        "task": {"question": "hello"},
        "request_id": "attempt-1",
        "max_requests": 2,
        "timeout_seconds": 5,
    }
    assert not any(word in saved.read_text() for word in ("private-case-id", "secret", "expected"))
    with pytest.raises(ValueError):
        CommandExecutor(command, RunSettings()).run(
            Case("one", "release", {}, {}), request_id="r", max_requests=0
        )


@pytest.mark.parametrize("reported,logical", [(3, 1), (1, 2)])
def test_reported_request_overruns_and_incomplete_accounting_fail_with_measured_usage(
    tmp_path, response, reported, logical
):
    response["usage"]["requests"] = reported
    response["result"]["model_calls"] = logical
    command = child(tmp_path, f"print({json.dumps(response)!r})")
    sample = CommandExecutor(command, RunSettings(2, 5)).run(
        Case("one", "release", {}, {}), request_id="r", max_requests=2
    )
    assert sample.result.status == Status.EXECUTION_ERROR
    assert sample.result.error_code == "executor_request_accounting"
    assert sample.usage.requests == reported


@pytest.mark.parametrize(
    "change",
    [
        {"command": []},
        {"command": "python"},
        {"command": [False]},
        {"command": ["bad\x00arg"]},
        {"command": ["x" * 4097]},
        {"command": ["x"] * 129},
        {"suite_kind": "unknown"},
        {"suite_kind": []},
        {"adapter_version": " "},
        {"adapter_version": 42},
        {"adapter_version": "x" * 201},
        {"extra": True},
    ],
    ids=[
        "empty-command",
        "string-command",
        "nonstring-arg",
        "nul-arg",
        "long-arg",
        "many-args",
        "unknown-kind",
        "nonstring-kind",
        "empty-version",
        "nonstring-version",
        "long-version",
        "unknown-key",
    ],
)
def test_invalid_trusted_command_configuration(change):
    config = {"command": ["python"], "suite_kind": "scripted", "adapter_version": "v1"}
    with pytest.raises(ValueError):
        CommandSettings.from_config(config | change)


def test_direct_command_settings_require_argv_tuple():
    with pytest.raises(ValueError):
        CommandSettings("python", "scripted", "v1")


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"extra": 1},
        {"schema_version": True},
        {"result": []},
        {"result": {"status": "completed"}},
        {"result": {"status": "unknown", "model_calls": 0}},
        {"result": {"status": "completed", "model_calls": True}},
        {"result": {"status": "completed", "model_calls": -1}},
        {"result": {"status": "completed", "model_calls": 0, "error_code": 42}},
        {"result": {"status": "completed", "model_calls": 0, "observations": {}}},
        {"result": {"status": "completed", "model_calls": 0, "observations": [{}]}},
        {"usage": {}},
        {"usage": {"requests": -1}},
        {"usage": {"requests": 0, "unrecognized": 1}},
    ],
    ids=[
        "not-object",
        "extra-key",
        "bool-version",
        "bad-result",
        "missing-calls",
        "bad-status",
        "bool-calls",
        "negative-calls",
        "bad-error",
        "bad-observations",
        "bad-observation",
        "missing-usage",
        "negative-usage",
        "unknown-usage",
    ],
)
def test_invalid_command_results_are_not_passing_samples(response, change):
    data = response | change if change is not None else None
    with pytest.raises((ValueError, TypeError)):
        _sample(json.dumps(data).encode())
    with pytest.raises(ValueError):
        _sample(b'{"duplicate":1,"duplicate":2}')


def cli_args(monkeypatch, tmp_path, command, *extra):
    config = tmp_path / "executor.json"
    config.write_text(
        json.dumps(
            {
                "command": list(command.command),
                "suite_kind": command.suite_kind,
                "adapter_version": command.adapter_version,
            }
        )
    )
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "app",
                "version": "1",
                "cases": [
                    {
                        "id": name,
                        "split": "release",
                        "task": {"question": name},
                        "expected": {"status": "completed", "output": "answer"},
                    }
                    for name in ("one", "two")
                ],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-tdd",
            "evaluate",
            "--cases",
            str(cases),
            "--executor-config",
            str(config),
            "--report",
            str(tmp_path / "report.json"),
            *extra,
        ],
    )
    return config


def test_cli_external_baseline_candidate_and_fresh_processes(tmp_path, monkeypatch, response):
    # A module global is reset to 0 by a genuinely fresh interpreter on every attempt.
    command = child(
        tmp_path, "counter=0\ncounter+=1\nassert counter==1\n" + f"print({json.dumps(response)!r})"
    )
    cli_args(monkeypatch, tmp_path, command, "--repeats", "2")
    assert main() == 0
    baseline = tmp_path / "baseline.json"
    (tmp_path / "report.json").replace(baseline)
    cli_args(monkeypatch, tmp_path, command, "--repeats", "2", "--baseline", str(baseline))
    assert main() == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert len(report["runs"]) == 4
    assert report["summary"]["baseline_pass_rate"] == 1
    assert report["provenance"]["provider"] == "command"
    assert report["provenance"]["adapter_version"] == "test-v1"
    for suffix in ("json", "html", "xml"):
        assert (tmp_path / f"report.{suffix}").is_file()


@pytest.mark.parametrize(
    "code,timeout",
    [
        (
            "import sys; print('private response'); "
            "print('private token',file=sys.stderr); sys.exit(1)",
            5,
        ),
        ("print('not JSON: private response')", 5),
        ("print('x'*1048577)", 5),
        ("import time; time.sleep(10)", 0.2),
    ],
    ids=["nonzero", "invalid-json", "oversized-response", "timeout"],
)
def test_adapter_failure_stops_later_cases_and_hides_private_output(
    tmp_path, monkeypatch, capsys, code, timeout
):
    counter = tmp_path / "attempted.txt"
    command = child(tmp_path, f"open({str(counter)!r},'a').write('attempt\\n')\n" + code)
    cli_args(monkeypatch, tmp_path, command, "--timeout", str(timeout))
    assert main() == 1
    report_text = (tmp_path / "report.json").read_text()
    report = json.loads(report_text)
    assert report["summary"]["requests"] is None
    assert len(report["runs"]) == 2
    assert "execution_errors" in report["gate_failures"]
    if counter.exists():
        assert counter.read_text().splitlines() == ["attempt"]
    captured = capsys.readouterr()
    assert "private" not in captured.out + captured.err + report_text


def test_executor_config_collision_and_provider_config_rejected(tmp_path, monkeypatch, response):
    command = child(tmp_path, f"print({json.dumps(response)!r})")
    config = cli_args(monkeypatch, tmp_path, command)
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--report", str(config)])
    original = config.read_text()
    assert main() == 2
    assert config.read_text() == original
    cli_args(monkeypatch, tmp_path, command, "--config", str(config))
    assert main() == 2
    cli_args(monkeypatch, tmp_path, command, "--provider", "scripted")
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_scripted_and_live_command_baselines_cannot_mix(tmp_path, monkeypatch, response):
    command = child(tmp_path, f"print({json.dumps(response)!r})")
    cli_args(monkeypatch, tmp_path, command)
    assert main() == 0
    baseline = tmp_path / "baseline.json"
    (tmp_path / "report.json").replace(baseline)
    sentinel = tmp_path / "must-not-start"
    changed = child(
        tmp_path, f"open({str(sentinel)!r},'w').close()\nprint({json.dumps(response)!r})"
    )
    cli_args(
        monkeypatch, tmp_path, replace(changed, suite_kind="live"), "--baseline", str(baseline)
    )
    assert main() == 2
    assert not sentinel.exists()


def test_action_inputs_are_argument_data_not_shell_code(tmp_path):
    value = str(tmp_path / "$(touch injected); file with spaces.json")
    command = arguments({"GATE_CASES": value, "GATE_REPORT": "-report.json", "GATE_REPEATS": "3"})
    assert command == [
        sys.executable,
        "-m",
        "agent_tdd",
        "evaluate",
        f"--cases={value}",
        "--report=-report.json",
        "--repeats=3",
    ]
    assert not (tmp_path / "injected").exists()


def test_worker_helper_round_trip_and_private_failure(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text("""from agent_tdd.evaluation.worker import serve
from agent_tdd.evaluation.contracts import Sample, Usage
from agent_tdd.types import RunResult, Status
def execute(request):
    if request.task == "explode":
        raise RuntimeError("private provider token")
    return Sample(RunResult(Status.COMPLETED, request.task), Usage(0, 0, 0, 0))
raise SystemExit(serve(execute))
""")
    request = {
        "schema_version": 1,
        "task": ["hello", "world"],
        "request_id": "r",
        "max_requests": 2,
        "timeout_seconds": 5,
    }

    def run(data):
        return subprocess.run(
            [sys.executable, str(script)], input=json.dumps(data), capture_output=True, text=True
        )

    good = run(request)
    assert good.returncode == 0
    assert _sample(good.stdout.encode()).result.output == ["hello", "world"]
    for change in (
        {"schema_version": True},
        {"max_requests": 0},
        {"timeout_seconds": False},
        {"timeout_seconds": 0},
        {"request_id": ""},
        {"extra": "not-accepted"},
    ):
        assert run(request | change).returncode == 2
    bad = run(request | {"task": "explode"})
    assert bad.returncode == 1
    assert "private" not in bad.stdout + bad.stderr
    huge = subprocess.run(
        [sys.executable, str(script)],
        input='"' + "x" * 1052673 + '"',
        capture_output=True,
        text=True,
    )
    assert huge.returncode == 2
