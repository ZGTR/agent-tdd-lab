from threading import Event

import pytest

from agent_tdd.adapters.local import ScriptedModel, SystemClock
from agent_tdd.context import Budget, Identity, RunContext
from agent_tdd.harness import Agent
from agent_tdd.types import Decision, Status, ToolError


class Echo:
    def execute(self, arguments, context, *, timeout_seconds):
        return arguments


def test_generic_task_and_custom_tool(ctx):
    model = ScriptedModel(
        [Decision("tool", "echo", {"number": 42}), Decision("finish", output=[True, 42, None])]
    )
    result = Agent(model, {"echo": Echo()}).run({"arbitrary": "task"}, ctx)
    assert result.status == Status.COMPLETED
    assert result.output == [True, 42, None]
    assert result.observations[0].value == {"number": 42}
    assert result.model_calls == ctx.budget.used == 2


@pytest.mark.parametrize(
    "decision,status",
    [
        (Decision("delete_all"), Status.INVALID_DECISION),
        (Decision("tool", "missing"), Status.UNKNOWN_TOOL),
        (object(), Status.INVALID_DECISION),
        (Decision("finish", output=float("nan")), Status.INVALID_DECISION),
        (Decision("tool", arguments=[]), Status.INVALID_DECISION),
        (Decision("tool", tool=42), Status.INVALID_DECISION),
    ],
)
def test_untrusted_decisions_fail_closed(ctx, decision, status):
    class Model:
        def decide(self, *args, **kwargs):
            return decision

    assert Agent(Model(), {}).run("task", ctx).status == status


def test_script_exhaustion_is_explicit(ctx):
    assert Agent(ScriptedModel([]), {}).run(None, ctx).status == Status.MODEL_ERROR


def test_error_and_timeout(ctx):
    class Broken:
        def decide(self, *args, **kwargs):
            raise TimeoutError()

    assert Agent(Broken(), {}).run(None, ctx).status == Status.MODEL_ERROR


def test_cancellation_after_model_prevents_tool(ctx):
    class Model:
        def decide(self, *args, **kwargs):
            ctx.cancelled.set()
            return Decision("tool", "echo")

    result = Agent(Model(), {"echo": Echo()}).run(None, ctx)
    assert result.status == Status.CANCELLED
    assert result.observations == ()


def test_deadline_after_model_prevents_tool(ctx, clock):
    class Slow:
        def decide(self, *args, **kwargs):
            clock.now += 100
            return Decision("tool", "echo")

    assert Agent(Slow(), {"echo": Echo()}).run(None, ctx).status == Status.DEADLINE


def test_deadline_after_effect_preserves_receipt(ctx, clock):
    class SlowTool:
        def execute(self, *args, **kwargs):
            clock.now += 100
            return {"effect": "committed"}

    result = Agent(ScriptedModel([Decision("tool", "slow")]), {"slow": SlowTool()}).run(None, ctx)
    assert result.status == Status.DEADLINE
    assert result.observations[0].value == {"effect": "committed"}


def test_step_limit_and_shared_budget(ctx):
    model = ScriptedModel([Decision("tool", "echo")] * 10)
    assert Agent(model, {"echo": Echo()}, max_steps=2).run(None, ctx).status == Status.STEP_LIMIT
    tiny = RunContext("x", ctx.identity, Budget(SystemClock(), max_model_calls=1), Event())
    result = Agent(ScriptedModel([Decision("tool", "echo")] * 3), {"echo": Echo()}).run(None, tiny)
    assert result.error_code == "shared_budget_exhausted"
    assert result.model_calls == 1


def test_transient_tool_failure_can_retry(ctx):
    class Flaky:
        calls = 0

        def execute(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ToolError("timeout", retryable=True)
            return "ok"

    model = ScriptedModel(
        [Decision("tool", "flaky"), Decision("tool", "flaky"), Decision("finish", output="done")]
    )
    result = Agent(model, {"flaky": Flaky()}).run(None, ctx)
    assert result.status == Status.COMPLETED
    assert [o.ok for o in result.observations] == [False, True]


def test_permanent_tool_error_rejects(ctx):
    class Denied:
        def execute(self, *args, **kwargs):
            raise ToolError("forbidden")

    result = Agent(ScriptedModel([Decision("tool", "x")]), {"x": Denied()}).run(None, ctx)
    assert result.status == Status.REJECTED
    assert result.error_code == "forbidden"


def test_programming_error_is_not_hidden(ctx):
    class Bug:
        def execute(self, *args, **kwargs):
            raise RuntimeError("implementation defect")

    with pytest.raises(RuntimeError):
        Agent(ScriptedModel([Decision("tool", "x")]), {"x": Bug()}).run(None, ctx)


@pytest.mark.parametrize("kwargs", [{"max_steps": 0}, {"max_steps": True}, {"max_steps": 1001}])
def test_invalid_harness_config(kwargs):
    with pytest.raises(ValueError):
        Agent(ScriptedModel([]), {}, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"max_model_calls": 0}, {"max_model_calls": True}, {"seconds": 0}, {"seconds": float("inf")}],
)
def test_invalid_budget_config(clock, kwargs):
    with pytest.raises(ValueError):
        Budget(clock, **kwargs)


def test_cancelled_and_expired_before_model(ctx, clock):
    ctx.cancelled.set()
    assert Agent(ScriptedModel([]), {}).run(None, ctx).status == Status.CANCELLED
    ctx.cancelled.clear()
    clock.now += 100
    assert Agent(ScriptedModel([]), {}).run(None, ctx).status == Status.DEADLINE
    assert ctx.budget.used == 0


def test_context_validation(clock):
    with pytest.raises(ValueError):
        Identity("", "a")
    with pytest.raises(ValueError):
        RunContext("", Identity("a", "b"), Budget(clock), Event())
    with pytest.raises(ValueError):
        Agent(ScriptedModel([]), {"": Echo()})
    with pytest.raises(TimeoutError):
        ScriptedModel([]).decide(None, (), timeout_seconds=0)
    assert SystemClock().time() > 0
    assert SystemClock().monotonic() > 0


def test_provider_cannot_mutate_caller_task(ctx):
    task = {"items": [1]}

    class Mutator:
        def decide(self, task, history, **kwargs):
            task["items"].append(2)
            return Decision("finish")

    Agent(Mutator(), {}).run(task, ctx)
    assert task == {"items": [1]}


def test_budget_reservation_is_atomic(clock):
    from concurrent.futures import ThreadPoolExecutor

    budget = Budget(clock, max_model_calls=5)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: budget.reserve_call(), range(50)))
    assert sum(results) == budget.used == 5


@pytest.mark.parametrize("bad", [{1: "x"}, (1, 2), object()])
def test_invalid_input_json(ctx, bad):
    with pytest.raises(ValueError):
        Agent(ScriptedModel([]), {}).run(bad, ctx)


def test_cyclic_model_output_is_rejected(ctx):
    cycle = []
    cycle.append(cycle)

    class Malformed:
        def decide(self, *args, **kwargs):
            return Decision("finish", output=cycle)

    assert Agent(Malformed(), {}).run(None, ctx).status == Status.INVALID_DECISION
