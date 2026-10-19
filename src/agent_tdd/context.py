"""Trusted execution context and a thread-safe budget shared by child agents."""

import math
from dataclasses import dataclass
from threading import Event, Lock
from typing import Protocol


class Clock(Protocol):
    def time(self) -> float: ...
    def monotonic(self) -> float: ...


class Budget:
    """Cooperative deadline plus atomic model-call reservation; no claims about token spend."""

    def __init__(self, clock: Clock, *, max_model_calls: int = 20, seconds: float = 30) -> None:
        if type(max_model_calls) is not int or max_model_calls < 1:
            raise ValueError("max_model_calls must be a positive integer")
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("seconds must be finite and positive")
        self._clock = clock
        self._deadline = clock.monotonic() + seconds
        self._max = max_model_calls
        self._used = 0
        self._lock = Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - self._clock.monotonic())

    def reserve_call(self) -> bool:
        with self._lock:
            if self._used >= self._max:
                return False
            self._used += 1
            return True


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id.strip() or not self.actor_id.strip():
            raise ValueError("tenant and actor must be nonempty")


@dataclass(frozen=True)
class RunContext:
    """Constructed by authenticated host code, never from an LLM response."""

    request_id: str
    identity: Identity
    budget: Budget
    cancelled: Event

    def __post_init__(self) -> None:
        if not self.request_id.strip() or len(self.request_id) > 200:
            raise ValueError("request_id must be nonempty and at most 200 characters")
