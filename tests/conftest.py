from dataclasses import dataclass
from threading import Event

import pytest

from agent_tdd.context import Budget, Identity, RunContext


@dataclass
class FakeClock:
    now: float = 1000.0

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def ctx(clock):
    return RunContext("req-1", Identity("acme", "alice"), Budget(clock), Event())
