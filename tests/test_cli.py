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
