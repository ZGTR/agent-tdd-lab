"""The environment setup must fail closed instead of widening credential access."""

import importlib.util
from pathlib import Path

import pytest


def module(path):
    spec = importlib.util.spec_from_file_location("setup", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_environment_setup_idempotent_and_main_only(monkeypatch):
    setup = module(Path("scripts/configure_live_environment.py"))
    state = {"environment": None, "policies": []}
    mutations = []

    def api(path, *, method="GET", body=None, missing_ok=False):
        if method == "PUT":
            mutations.append((method, body))
            state["environment"] = body
            return body
        if method == "POST":
            mutations.append((method, body))
            state["policies"].append(body)
            return body
        return (
            {"branch_policies": state["policies"]}
            if path.endswith("deployment-branch-policies")
            else state["environment"]
        )

    monkeypatch.setattr(setup, "api", api)
    with pytest.raises(RuntimeError):
        setup.configure("ZGTR/agent-tdd-lab")
    assert not mutations
    setup.configure("ZGTR/agent-tdd-lab", apply=True)
    assert len(mutations) == 2
    setup.configure("ZGTR/agent-tdd-lab", apply=True)
    assert len(mutations) == 2
    state["policies"].append({"name": "*", "type": "branch"})
    with pytest.raises(RuntimeError):
        setup.configure("ZGTR/agent-tdd-lab", apply=True)
    state["environment"] = {"deployment_branch_policy": None}
    with pytest.raises(RuntimeError):
        setup.configure("ZGTR/agent-tdd-lab", apply=True)
    with pytest.raises(ValueError):
        setup.configure("../not-a-repo")


def test_live_ci_script_passes_inputs_as_data_and_stops_on_failed_baseline(monkeypatch, tmp_path):
    live = module(Path("scripts/run_live_evaluation.py"))
    # Keep the checked-in config readable while isolating artifact writes in a new cwd.
    (tmp_path / "evals" / "providers").mkdir(parents=True)
    (tmp_path / "evals" / "providers" / "openai.example.json").write_text(
        '{"model":"placeholder","endpoint":"https://example.com/v1/chat/completions"}'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EVAL_ENDPOINT", raising=False)
    monkeypatch.setenv("EVAL_PROVIDER", "openai")
    monkeypatch.setenv("EVAL_MODEL", "model; shell injection is data")
    monkeypatch.setenv("EVAL_BASELINE_MODEL", "baseline")
    monkeypatch.setenv("OPENAI_API_KEY", "private-secret")
    calls = []

    def run(command, *, check):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(live.subprocess, "run", run)
    assert live.main() == 0
    assert len(calls) == 2 and "--baseline" in calls[1]
    assert all("private-secret" not in str(command) for command in calls)
    assert (
        "model; shell injection is data"
        in (tmp_path / "artifacts" / "candidate-config.json").read_text()
    )
    calls.clear()
    monkeypatch.setattr(
        live.subprocess, "run", lambda *a, **k: type("Result", (), {"returncode": 1})()
    )
    assert live.main() == 1
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError):
        live.main()
