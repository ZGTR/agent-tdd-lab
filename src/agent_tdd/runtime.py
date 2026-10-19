"""Application service: fresh step factories, explicit policies, isolated execution context."""

import math
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from threading import Event
from uuid import uuid4

from agent_tdd.context import Budget, Clock, Identity, RunContext
from agent_tdd.ports import Step
from agent_tdd.telemetry import NullObserver, Observer, ResilientObserver, RunEvent
from agent_tdd.types import JsonValue, RunResult, Status, validate_json

Validator = Callable[[JsonValue], None]
StepFactory = Callable[[], Step]


@dataclass(frozen=True)
class RunSettings:
    max_model_calls: int = 20
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if type(self.max_model_calls) is not int or self.max_model_calls < 1:
            raise ValueError("max_model_calls must be a positive integer")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")


class StepRegistry:
    """Factory registry configured once by trusted application code, with no dynamic imports."""

    def __init__(self, factories: Mapping[str, StepFactory]) -> None:
        if not factories or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", n) for n in factories):
            raise ValueError("application names must match [a-z][a-z0-9_-]{0,63}")
        self._factories = dict(factories)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def create(self, name: str) -> Step:
        try:
            factory = self._factories[name]
        except KeyError as error:
            raise ValueError("unknown application") from error
        return factory()


class ValidatedStep:
    """Decorator for domain schema checks without coupling the generic core to a schema SDK."""

    def __init__(
        self,
        step: Step,
        *,
        input_validator: Validator = validate_json,
        output_validator: Validator = validate_json,
    ) -> None:
        self._step = step
        self._input = input_validator
        self._output = output_validator

    def run(self, task: JsonValue, context: RunContext) -> RunResult:
        try:
            self._input(deepcopy(task))
        except ValueError:
            return RunResult(Status.REJECTED, error_code="invalid_input")
        result = self._step.run(deepcopy(task), context)
        if result.status != Status.COMPLETED:
            return result
        try:
            self._output(deepcopy(result.output))
        except ValueError:
            # Preserve authoritative tool receipts even when the final answer is invalid.
            return RunResult(
                Status.REJECTED,
                observations=result.observations,
                model_calls=result.model_calls,
                error_code="invalid_output",
            )
        return result


class ExecutionService:
    def __init__(
        self, registry: StepRegistry, clock: Clock, observer: Observer | None = None
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._observer = ResilientObserver(observer or NullObserver())

    @property
    def observer_failures(self) -> int:
        return self._observer.failures

    def run(
        self,
        application: str,
        task: JsonValue,
        *,
        identity: Identity,
        request_id: str,
        settings: RunSettings | None = None,
        cancelled: Event | None = None,
    ) -> RunResult:
        if application not in self._registry.names:
            raise ValueError("unknown application")
        settings = settings or RunSettings()
        started = self._clock.monotonic()
        run_id = str(uuid4())
        context = RunContext(
            request_id,
            identity,
            Budget(
                self._clock,
                max_model_calls=settings.max_model_calls,
                seconds=settings.timeout_seconds,
            ),
            cancelled if cancelled is not None else Event(),
        )
        self._observer.observe(RunEvent(run_id, application, "started", 0))
        try:
            stop = context.stop_reason()
            if stop is not None:
                result = RunResult(stop)
            else:
                step = self._registry.create(application)
                stop = context.stop_reason()
                result = (
                    RunResult(stop) if stop is not None else ValidatedStep(step).run(task, context)
                )
            # Factories and plain Steps must not conceal a late stop.
            stop = context.stop_reason()
            if stop is not None:
                result = RunResult(
                    stop, result.output, result.observations, result.model_calls, result.error_code
                )
        except Exception:
            self._observer.observe(
                RunEvent(
                    run_id,
                    application,
                    "failed",
                    max(0, self._clock.monotonic() - started),
                    context.budget.used,
                    "execution_error",
                )
            )
            raise
        self._observer.observe(
            RunEvent(
                run_id,
                application,
                "finished",
                max(0, self._clock.monotonic() - started),
                context.budget.used,
                result.status.value,
            )
        )
        return result
