"""Ports owned by the application, implemented by framework/provider adapters."""

from typing import Protocol

from agent_tdd.context import RunContext
from agent_tdd.types import Decision, JsonObject, JsonValue, Observation, RunResult


class Model(Protocol):
    def decide(
        self, task: JsonValue, history: tuple[Observation, ...], *, timeout_seconds: float
    ) -> Decision: ...


class Tool(Protocol):
    def execute(
        self, arguments: JsonObject, context: RunContext, *, timeout_seconds: float
    ) -> JsonValue: ...


class Step(Protocol):
    def run(self, task: JsonValue, context: RunContext) -> RunResult: ...
