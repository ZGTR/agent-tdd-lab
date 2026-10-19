"""Immutable evaluation contracts. Application executors own framework/provider details."""

import math
from dataclasses import dataclass, field
from typing import Protocol

from agent_tdd.types import Decision, JsonObject, JsonValue, RunResult


def positive(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class Case:
    id: str
    split: str
    task: JsonValue
    expected: JsonObject
    tags: tuple[str, ...] = ()
    script: tuple[Decision, ...] = ()


@dataclass(frozen=True)
class Suite:
    name: str
    version: str
    cases: tuple[Case, ...]
    digest: str


@dataclass(frozen=True)
class Usage:
    requests: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if type(self.requests) is not int or self.requests < 0:
            raise ValueError("requests must be a nonnegative integer")
        for value in (self.input_tokens, self.output_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("token counts must be nonnegative integers or unknown")
        if self.estimated_cost_usd is not None and (
            isinstance(self.estimated_cost_usd, bool)
            or not math.isfinite(self.estimated_cost_usd)
            or self.estimated_cost_usd < 0
        ):
            raise ValueError("estimated cost must be finite and nonnegative or unknown")


@dataclass(frozen=True)
class Sample:
    result: RunResult
    usage: Usage = field(default_factory=Usage)


class Executor(Protocol):
    def run(self, case: Case, *, request_id: str, max_requests: int) -> Sample: ...


@dataclass(frozen=True)
class Grade:
    quality_passed: bool
    policy_violations: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


class Grader(Protocol):
    @property
    def version(self) -> str: ...
    def grade(self, case: Case, sample: Sample) -> Grade: ...


@dataclass(frozen=True)
class Gates:
    min_pass_rate: float = 1
    max_regression: float = 0
    max_latency_seconds: float = 60
    max_requests: int = 100
    max_total_tokens: int | None = None
    max_estimated_cost_usd: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("min_pass_rate", self.min_pass_rate),
            ("max_regression", self.max_regression),
        ):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        positive(self.max_latency_seconds, "max_latency_seconds")
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        if self.max_total_tokens is not None and (
            type(self.max_total_tokens) is not int or self.max_total_tokens < 1
        ):
            raise ValueError("max_total_tokens must be a positive integer")
        if self.max_estimated_cost_usd is not None:
            positive(self.max_estimated_cost_usd, "max_estimated_cost_usd")
