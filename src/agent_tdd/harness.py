"""Generic agent controller: routing, bounds, and outcomes; no domain-specific policy."""

from collections.abc import Mapping
from copy import deepcopy

from agent_tdd.context import RunContext
from agent_tdd.ports import Model, Tool
from agent_tdd.types import (
    Decision,
    JsonValue,
    ModelError,
    Observation,
    RunResult,
    Status,
    ToolError,
    validate_json,
)


class Agent:
    def __init__(self, model: Model, tools: Mapping[str, Tool], *, max_steps: int = 8) -> None:
        if type(max_steps) is not int or not 1 <= max_steps <= 1000:
            raise ValueError("max_steps must be an integer in [1, 1000]")
        if any(not name.strip() for name in tools):
            raise ValueError("tool names must be nonempty")
        self._model = model
        self._tools = dict(tools)
        self._max_steps = max_steps

    def run(self, task: JsonValue, context: RunContext) -> RunResult:
        validate_json(task)
        history: list[Observation] = []
        calls = 0

        def result(status: Status, output: JsonValue = None, code: str | None = None) -> RunResult:
            return RunResult(status, output, tuple(history), calls, code)

        stopped = context.stop_reason

        for _ in range(self._max_steps):
            status = stopped()
            if status is not None:
                return result(status)
            if not context.budget.reserve_call():
                return result(Status.STEP_LIMIT, code="shared_budget_exhausted")
            calls += 1
            try:
                # Adapters cannot mutate the caller's task or prior observations.
                decision = self._model.decide(
                    deepcopy(task),
                    deepcopy(tuple(history)),
                    timeout_seconds=context.budget.remaining_seconds(),
                )
            except (ModelError, TimeoutError):
                return result(stopped() or Status.MODEL_ERROR)
            status = stopped()
            if status is not None:
                return result(status)
            if not isinstance(decision, Decision):
                return result(Status.INVALID_DECISION)
            try:
                if not isinstance(decision.arguments, dict):
                    raise ValueError("arguments must be an object")
                validate_json(decision.arguments)
                validate_json(decision.output)
            except (ValueError, TypeError, OverflowError):
                return result(Status.INVALID_DECISION)
            if decision.action == "finish":
                return result(Status.COMPLETED, deepcopy(decision.output))
            if decision.action != "tool" or not isinstance(decision.tool, str):
                return result(Status.INVALID_DECISION)
            tool = self._tools.get(decision.tool)
            if tool is None:
                return result(Status.UNKNOWN_TOOL)
            try:
                value = tool.execute(
                    deepcopy(decision.arguments),
                    context,
                    timeout_seconds=context.budget.remaining_seconds(),
                )
                validate_json(value)
                history.append(Observation(decision.tool, True, deepcopy(value)))
            except ToolError as error:
                history.append(Observation(decision.tool, False, error_code=error.code))
                if not error.retryable:
                    return result(Status.REJECTED, code=error.code)
            # Unexpected tool errors intentionally propagate: never turn defects into success.
        return result(stopped() or Status.STEP_LIMIT)
