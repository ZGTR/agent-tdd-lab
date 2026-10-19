"""Credential-free provider adapter and the production system clock."""

import time
from collections.abc import Iterable
from copy import deepcopy

from agent_tdd.types import Decision, JsonValue, ModelError, Observation


class SystemClock:
    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


class ScriptedModel:
    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._decisions = iter(deepcopy(tuple(decisions)))

    def decide(
        self, task: JsonValue, history: tuple[Observation, ...], *, timeout_seconds: float
    ) -> Decision:
        if timeout_seconds <= 0:
            raise TimeoutError("model deadline exhausted")
        try:
            return next(self._decisions)
        except StopIteration as error:
            raise ModelError("script exhausted") from error
