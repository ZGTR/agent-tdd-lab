"""CLI controller: parse intent, invoke composition, render a result, set exit status."""

import argparse
import sys
from pathlib import Path

from agent_tdd.composition import refund_demo, research_demo
from agent_tdd.events import EventBus, OutboxDispatcher
from agent_tdd.types import Status
from agent_tdd.views import render_event, render_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline agent TDD reference examples")
    sub = parser.add_subparsers(dest="command", required=True)
    refund = sub.add_parser("refund", help="local SQLite refund simulator; never sends money")
    refund.add_argument("--db", type=Path, default=Path("agent-tdd.db"))
    refund.add_argument("--approve", action="store_true", help="seed a demo-only approval")
    sub.add_parser("workflow", help="two generic scripted agents with a typed handoff")
    args = parser.parse_args()
    if args.command == "workflow":
        workflow, ctx = research_demo()
        result = workflow.run({"question": "When does water freeze?"}, ctx)
    else:
        agent, ctx, store = refund_demo(args.db, approved=args.approve)
        result = agent.run({"request": "Refund order A1"}, ctx)
        bus = EventBus()
        bus.subscribe("refund.committed", lambda event: print(render_event(event), file=sys.stderr))
        OutboxDispatcher(store, bus).dispatch()
    print(render_result(result))
    return 0 if result.status == Status.COMPLETED else 2
