import pytest

from agent_tdd.adapters.local import ScriptedModel
from agent_tdd.harness import Agent
from agent_tdd.types import Decision, RunResult, Status
from agent_tdd.workflow import Node, Workflow


class FunctionStep:
    def __init__(self, fn):
        self.fn = fn

    def run(self, task, context):
        return RunResult(Status.COMPLETED, self.fn(task))


def test_dag_reorders_and_passes_only_declared_upstream(ctx):
    review = FunctionStep(lambda task: task["upstream"])
    workflow = Workflow(
        [
            Node("review", review, ("research",)),
            Node("research", FunctionStep(lambda task: {"fact": 42})),
            Node("unrelated", FunctionStep(lambda task: "other")),
        ]
    )
    result = workflow.run("question", ctx)
    assert result.status == Status.COMPLETED
    assert result.output["review"] == {"research": {"fact": 42}}


def test_nested_agents_share_budget(ctx):
    def agent():
        return Agent(ScriptedModel([Decision("finish", output=42)]), {})

    workflow = Workflow([Node("nested", Workflow([Node("a", agent())])), Node("b", agent())])
    result = workflow.run(None, ctx)
    assert result.status == Status.COMPLETED
    assert result.model_calls == ctx.budget.used == 2


def test_upstream_failure_stops_downstream(ctx):
    class MustNotRun:
        def run(self, *args):
            raise AssertionError("unsafe downstream execution")

    workflow = Workflow([Node("a", Agent(ScriptedModel([]), {})), Node("b", MustNotRun(), ("a",))])
    result = workflow.run(None, ctx)
    assert result.status == Status.UPSTREAM_FAILED
    assert result.error_code == "a:model_error"


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [Node("a", None), Node("a", None)],
        [Node("", None)],
        [Node("a", None, ("missing",))],
        [Node("a", None, ("a",))],
    ],
)
def test_invalid_graph(nodes):
    with pytest.raises(ValueError):
        Workflow(nodes)


def test_workflow_cancel_and_deadline(ctx, clock):
    workflow = Workflow([Node("a", FunctionStep(lambda _: 42))])
    ctx.cancelled.set()
    assert workflow.run(None, ctx).status == Status.CANCELLED
    ctx.cancelled.clear()
    clock.now += 100
    assert workflow.run(None, ctx).status == Status.DEADLINE


def test_step_cannot_mutate_another_steps_outputs(ctx):
    task = {"question": [1]}

    def mutate(handoff):
        handoff["upstream"]["a"]["items"].append(2)
        handoff["task"]["question"].append(2)
        return "reviewed"

    workflow = Workflow(
        [
            Node("a", FunctionStep(lambda _: {"items": [1]})),
            Node("b", FunctionStep(mutate), ("a",)),
        ]
    )
    result = workflow.run(task, ctx)
    assert task == {"question": [1]}
    assert result.output["a"] == {"items": [1]}
