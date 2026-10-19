"""CLI controller: parse intent, invoke composition, render a result, set exit status."""

import argparse
import json
import logging
import sys
from pathlib import Path
from threading import Event
from uuid import uuid4

from agent_tdd.adapters.local import SystemClock
from agent_tdd.composition import default_registry, refund_demo, research_demo
from agent_tdd.context import Identity
from agent_tdd.evaluation import cli as evaluation_cli
from agent_tdd.events import EventBus, OutboxDispatcher
from agent_tdd.runtime import ExecutionService, RunSettings
from agent_tdd.signals import cancellation_signals
from agent_tdd.telemetry import LoggingObserver
from agent_tdd.types import RunResult, Status
from agent_tdd.views import render_event, render_result, render_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline agent TDD reference examples")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluation_cli.add_parser(sub)
    refund = sub.add_parser("refund", help="local SQLite refund simulator; never sends money")
    refund.add_argument("--db", type=Path, default=Path("agent-tdd.db"))
    refund.add_argument("--approve", action="store_true", help="seed a demo-only approval")
    sub.add_parser("workflow", help="two generic scripted agents with a typed handoff")
    run = sub.add_parser("run", help="run a registered application with a fresh bounded context")
    run.add_argument("application", choices=default_registry().names)
    run.add_argument(
        "--input-file", type=Path, help="UTF-8 JSON task file; default is an empty object"
    )
    run.add_argument("--result-mode", choices=["full", "metadata"], default="full")
    run.add_argument("--request-id", default=None)
    run.add_argument("--tenant", default="local")
    run.add_argument("--actor", default="cli")
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument("--max-model-calls", type=int, default=20)
    run.add_argument(
        "--telemetry", action="store_true", help="emit metadata-only lifecycle JSON to stderr"
    )
    args = parser.parse_args()
    if args.command == "evaluate":
        return evaluation_cli.run(args)
    if args.command == "run":
        try:
            task = {}
            if args.input_file is not None:
                with args.input_file.open("rb") as source:
                    raw = source.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise ValueError("input file exceeds 1 MiB")
                task = json.loads(raw.decode("utf-8"))
            settings = RunSettings(args.max_model_calls, args.timeout)
            identity = Identity(args.tenant, args.actor)
        except (ValueError, OSError) as error:
            parser.error(f"invalid run configuration: {type(error).__name__}")
        logger = logging.Logger("agent_tdd.lifecycle", level=logging.INFO)
        logger.addHandler(logging.StreamHandler(sys.stderr))
        observer = LoggingObserver(logger) if args.telemetry else None
        service = ExecutionService(default_registry(), SystemClock(), observer)
        cancelled = Event()
        try:
            with cancellation_signals(cancelled):
                result = service.run(
                    args.application,
                    task,
                    identity=identity,
                    request_id=args.request_id or str(uuid4()),
                    settings=settings,
                    cancelled=cancelled,
                )
        except Exception:
            # Host boundary: keep vendor responses/tracebacks out of operational logs.
            print(render_summary(RunResult(Status.EXECUTION_ERROR)))
            return 1
        print(render_summary(result) if args.result_mode == "metadata" else render_result(result))
        return 0 if result.status == Status.COMPLETED else 2
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
