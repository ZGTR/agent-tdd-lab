"""Composition adapter for the generic agent. Other harnesses implement Executor directly."""

from collections.abc import Callable, Mapping
from typing import Protocol

from agent_tdd.adapters.local import ScriptedModel
from agent_tdd.context import Clock, Identity
from agent_tdd.evaluation.contracts import Case, Sample, Usage
from agent_tdd.harness import Agent
from agent_tdd.ports import Model, Tool
from agent_tdd.runtime import ExecutionService, RunSettings, StepRegistry
from agent_tdd.types import Decision, JsonValue, Observation, RunResult, Status


class MeteredModel(Model, Protocol):
    def usage(self) -> Usage: ...


class ScriptedEvaluationModel:
    def __init__(self, case: Case) -> None:
        if not case.script:
            raise ValueError("scripted evaluations require an explicit script for every case")
        self._model = ScriptedModel(case.script)
        self._calls = 0

    def decide(
        self, task: JsonValue, history: tuple[Observation, ...], *, timeout_seconds: float
    ) -> Decision:
        self._calls += 1
        return self._model.decide(task, history, timeout_seconds=timeout_seconds)

    def usage(self) -> Usage:
        return Usage(self._calls, 0, 0, 0.0)


class AgentExecutor:
    def __init__(
        self,
        model_factory: Callable[[Case], MeteredModel],
        clock: Clock,
        *,
        tools_factory: Callable[[], Mapping[str, Tool]] = dict,
        settings: RunSettings | None = None,
        max_steps: int = 8,
    ) -> None:
        self._models, self._tools, self._clock = model_factory, tools_factory, clock
        self._settings, self._max_steps = settings or RunSettings(), max_steps

    def run(self, case: Case, *, request_id: str, max_requests: int) -> Sample:
        model = self._models(case)
        service = ExecutionService(
            StepRegistry(
                {"evaluation": lambda: Agent(model, self._tools(), max_steps=self._max_steps)}
            ),
            self._clock,
        )
        try:
            result = service.run(
                "evaluation",
                case.task,
                identity=Identity("evaluation", "runner"),
                request_id=request_id,
                settings=RunSettings(
                    min(max_requests, self._settings.max_model_calls),
                    self._settings.timeout_seconds,
                ),
            )
        except Exception:
            result = RunResult(Status.EXECUTION_ERROR, error_code="execution_error")
        return Sample(result, model.usage())
