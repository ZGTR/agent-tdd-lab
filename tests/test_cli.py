import json
import subprocess
import sys

from agent_tdd.composition import refund_demo, research_demo
from agent_tdd.events import EventBus, OutboxDispatcher
from agent_tdd.types import Status
from agent_tdd.views import render_event, render_result


def test_refund_example_and_durable_ack(tmp_path):
    agent, ctx, store = refund_demo(tmp_path / "demo.db", approved=True)
    result = agent.run("refund A1", ctx)
    assert result.status == Status.COMPLETED
    assert json.loads(render_result(result))["observations"][0]["ok"]
    bus = EventBus()
    events = []
    bus.subscribe("refund.committed", lambda event: events.append(render_event(event)))
    assert OutboxDispatcher(store, bus).dispatch() == 1
    assert json.loads(events[0])["payload"]["amount_cents"] == 1200
    assert store.pending(100) == []


def test_workflow_example():
    workflow, ctx = research_demo()
    assert workflow.run("question", ctx).status == Status.COMPLETED


def test_installed_cli(tmp_path):
    for args, status in [(["workflow"], 0), (["refund"], 2), (["refund", "--approve"], 0)]:
        command = [sys.executable, "-m", "agent_tdd", *args]
        if args[0] == "refund":
            command += ["--db", str(tmp_path / "cli.db")]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == status, result.stderr
        assert "status" in json.loads(result.stdout)


def test_production_runner_cli_and_private_telemetry(tmp_path):
    task = tmp_path / "task.json"
    task.write_text('{"secret":"do-not-log"}')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_tdd",
            "run",
            "workflow",
            "--input-file",
            str(task),
            "--request-id",
            "private-id",
            "--telemetry",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "completed"
    events = [json.loads(line) for line in result.stderr.splitlines()]
    assert [event["phase"] for event in events] == ["started", "finished"]
    assert "do-not-log" not in result.stderr and "private-id" not in result.stderr


def test_cli_rejects_invalid_input_and_settings(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 1_048_577)
    for args in [
        ["--input-file", str(invalid)],
        ["--input-file", str(oversized)],
        ["--input-file", str(tmp_path / "missing")],
        ["--timeout", "0"],
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "agent_tdd", "run", "workflow", *args],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "invalid run configuration" in result.stderr


def test_metadata_mode_excludes_full_result():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_tdd",
            "run",
            "workflow",
            "--result-mode",
            "metadata",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "completed", "model_calls": 2}


def test_host_cli_redacts_unexpected_exception(monkeypatch, capsys):
    from agent_tdd.cli import main
    from agent_tdd.runtime import ExecutionService

    def fail(*args, **kwargs):
        raise RuntimeError("private provider response")

    monkeypatch.setattr(ExecutionService, "run", fail)
    monkeypatch.setattr(sys, "argv", ["agent-tdd", "run", "workflow"])
    assert main() == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "execution_error"
    assert "private" not in captured.out + captured.err
