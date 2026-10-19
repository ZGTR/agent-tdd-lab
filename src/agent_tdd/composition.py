"""Demo composition root: the only place concrete adapters are wired together."""

from pathlib import Path
from threading import Event

from agent_tdd.adapters.local import ScriptedModel, SystemClock
from agent_tdd.context import Budget, Identity, RunContext
from agent_tdd.examples.refunds.domain import Approval
from agent_tdd.examples.refunds.policy import RefundPolicy
from agent_tdd.examples.refunds.sqlite import SqliteRefundStore
from agent_tdd.examples.refunds.tool import RefundTool
from agent_tdd.harness import Agent
from agent_tdd.runtime import StepRegistry
from agent_tdd.types import Decision
from agent_tdd.workflow import Node, Workflow


class DemoApprovals:
    """Offline fixture, not a production approval authority."""

    def __init__(self, approval: Approval | None) -> None:
        self._approval = approval

    def find(self, context: RunContext, order_id: str) -> Approval | None:
        return self._approval


def context() -> RunContext:
    return RunContext(
        "demo-request", Identity("acme", "support-user"), Budget(SystemClock()), Event()
    )


def refund_demo(path: Path, *, approved: bool) -> tuple[Agent, RunContext, SqliteRefundStore]:
    clock = SystemClock()
    ctx = context()
    store = SqliteRefundStore(path, clock)
    store.initialize()
    store.add_order("acme", "A1", 1200)
    approval = Approval("acme", "support-user", "demo-request", "A1", clock.time() + 60)
    tool = RefundTool(store, DemoApprovals(approval if approved else None), RefundPolicy(), clock)
    model = ScriptedModel(
        [
            Decision("tool", "refund", {"order_id": "A1"}),
            Decision("finish", output={"message": "Refund recorded in local simulator"}),
        ]
    )
    return Agent(model, {"refund": tool}), ctx, store


def research_workflow() -> Workflow:
    # Independent generic agents, no refund policy or storage dependency.
    researcher = Agent(
        ScriptedModel([Decision("finish", output={"fact": "water freezes at 0C"})]), {}
    )
    reviewer = Agent(ScriptedModel([Decision("finish", output={"reviewed": True})]), {})
    return Workflow([Node("research", researcher), Node("review", reviewer, ("research",))])


def default_registry() -> StepRegistry:
    return StepRegistry({"workflow": research_workflow})


def research_demo() -> tuple[Workflow, RunContext]:
    return research_workflow(), context()
