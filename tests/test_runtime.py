import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from agent_tdd.adapters.local import ScriptedModel
from agent_tdd.context import Identity
from agent_tdd.harness import Agent
from agent_tdd.runtime import ExecutionService, RunSettings, StepRegistry, ValidatedStep
from agent_tdd.telemetry import LoggingObserver
from agent_tdd.types import Decision, Observation, RunResult, Status


def make_agent():
    return Agent(ScriptedModel([Decision("finish", output={"answer": 42})]), {})


def test_factory_is_fresh_for_every_run_including_concurrency(clock):
    service = ExecutionService(StepRegistry({"answer": make_agent}), clock)

    def run(number):
        return service.run(
            "answer", "question", identity=Identity("acme", "alice"), request_id=f"request-{number}"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(20)))
    assert all(result.status == Status.COMPLETED and result.model_calls == 1 for result in results)


def test_registry_is_a_configuration_snapshot():
    factories = {"answer": make_agent}
    registry = StepRegistry(factories)
    factories.clear()
    assert registry.names == ("answer",)
    assert isinstance(registry.create("answer"), Agent)
    for invalid in [{}, {"../plugin": make_agent}, {"A": make_agent}]:
        with pytest.raises(ValueError):
            StepRegistry(invalid)
    with pytest.raises(ValueError, match="unknown application"):
        registry.create("missing")


def test_domain_validators_are_injected_and_preserve_effect_evidence(ctx):
    class Effect:
        def run(self, task, context):
            return RunResult(
                Status.COMPLETED, "bad answer", (Observation("write", True, "receipt"),), 1
            )

    def require_object(value):
        if not isinstance(value, dict):
            raise ValueError("schema rejected")

    step = ValidatedStep(Effect(), input_validator=require_object, output_validator=require_object)
    assert step.run("bad", ctx).error_code == "invalid_input"
    result = step.run({}, ctx)
    assert result.status == Status.REJECTED
    assert result.error_code == "invalid_output"
    assert result.observations[0].value == "receipt"
    assert result.model_calls == 1


def test_unsuccessful_step_is_preserved_without_revalidating_output(ctx):
    class Failed:
        def run(self, task, context):
            return RunResult(Status.CANCELLED, "partial")

    def must_not_run(value):
        raise AssertionError("must not validate failed output")

    assert (
        ValidatedStep(Failed(), output_validator=must_not_run).run({}, ctx).status
        == Status.CANCELLED
    )


def test_observer_failure_cannot_hide_successful_effect(clock):
    class BrokenObserver:
        def observe(self, event):
            raise RuntimeError("exporter down")

    service = ExecutionService(StepRegistry({"answer": make_agent}), clock, BrokenObserver())
    result = service.run("answer", {}, identity=Identity("a", "b"), request_id="request")
    assert result.status == Status.COMPLETED
    assert service.observer_failures == 2


def test_events_exclude_task_output_identity_and_exception_text(clock):
    stream = io.StringIO()
    logger = logging.Logger("isolated")
    logger.addHandler(logging.StreamHandler(stream))
    service = ExecutionService(StepRegistry({"answer": make_agent}), clock, LoggingObserver(logger))
    service.run(
        "answer",
        {"secret": "private-task"},
        identity=Identity("private-tenant", "private-actor"),
        request_id="private-request",
    )
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [event["phase"] for event in events] == ["started", "finished"]
    assert events[0]["run_id"] == events[1]["run_id"]
    assert events[1]["model_calls"] == 1
    assert "private-" not in stream.getvalue()
    assert "answer" not in events[1].get("output", {})


def test_execution_errors_propagate_with_safe_failure_event(clock):
    events = []

    class Recorder:
        def observe(self, event):
            events.append(event)

    class Bug:
        def run(self, task, context):
            raise RuntimeError("sensitive vendor response")

    service = ExecutionService(StepRegistry({"bug": lambda: Bug()}), clock, Recorder())
    with pytest.raises(RuntimeError, match="sensitive"):
        service.run("bug", {}, identity=Identity("a", "b"), request_id="req")
    assert [event.phase for event in events] == ["started", "failed"]
    assert events[-1].status == "execution_error"
    assert "sensitive" not in str(events)


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_factory_cannot_start_work_after_stop(clock, stop):
    cancelled = Event()

    class MustNotRun:
        def run(self, *args):
            raise AssertionError("late execution")

    def factory():
        if stop == "cancel":
            cancelled.set()
        else:
            clock.now += 100
        return MustNotRun()

    service = ExecutionService(StepRegistry({"slow": factory}), clock)
    result = service.run(
        "slow", {}, identity=Identity("a", "b"), request_id="req", cancelled=cancelled
    )
    assert result.status == (Status.CANCELLED if stop == "cancel" else Status.DEADLINE)


def test_pre_cancelled_skips_factory(clock):
    def factory():
        raise AssertionError("must not construct resources")

    cancelled = Event()
    cancelled.set()
    service = ExecutionService(StepRegistry({"app": factory}), clock)
    assert (
        service.run(
            "app", {}, identity=Identity("a", "b"), request_id="req", cancelled=cancelled
        ).status
        == Status.CANCELLED
    )


def test_late_plain_step_preserves_output(clock):
    class Slow:
        def run(self, *args):
            clock.now += 100
            return RunResult(Status.COMPLETED, {"receipt": "stored"})

    service = ExecutionService(StepRegistry({"slow": lambda: Slow()}), clock)
    result = service.run("slow", {}, identity=Identity("a", "b"), request_id="req")
    assert result.status == Status.DEADLINE
    assert result.output == {"receipt": "stored"}


@pytest.mark.parametrize(
    "settings",
    [
        {"max_model_calls": 0},
        {"max_model_calls": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
def test_invalid_settings(settings):
    with pytest.raises(ValueError):
        RunSettings(**settings)


def test_unknown_application_cannot_inject_telemetry(clock):
    events = []

    class Recorder:
        def observe(self, event):
            events.append(event)

    service = ExecutionService(StepRegistry({"answer": make_agent}), clock, Recorder())
    with pytest.raises(ValueError, match="unknown application"):
        service.run("secret-user-input", {}, identity=Identity("a", "b"), request_id="req")
    assert events == []
