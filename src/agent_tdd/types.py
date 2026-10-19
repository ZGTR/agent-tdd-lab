"""Framework-independent data contracts."""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
JsonObject: TypeAlias = dict[str, JsonValue]


def validate_json(value: JsonValue, *, _depth: int = 0) -> None:
    """Reject NaN, custom objects, and non-string dictionary keys at boundaries."""
    if _depth > 64:
        raise ValueError("JSON exceeds maximum depth of 64")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            validate_json(item, _depth=_depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_json(item, _depth=_depth + 1)
    elif value is None or type(value) in (bool, int, float, str):
        json.dumps(value, allow_nan=False)
    else:
        raise ValueError("unsupported JSON value")


@dataclass(frozen=True)
class Decision:
    action: str
    tool: str = ""
    arguments: JsonObject = field(default_factory=dict)
    output: JsonValue = None


@dataclass(frozen=True)
class Observation:
    tool: str
    ok: bool
    value: JsonValue = None
    error_code: str | None = None


class Status(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"
    MODEL_ERROR = "model_error"
    INVALID_DECISION = "invalid_decision"
    UNKNOWN_TOOL = "unknown_tool"
    STEP_LIMIT = "step_limit"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"
    UPSTREAM_FAILED = "upstream_failed"


@dataclass(frozen=True)
class RunResult:
    status: Status
    output: JsonValue = None
    observations: tuple[Observation, ...] = ()
    model_calls: int = 0
    error_code: str | None = None


class ModelError(Exception):
    """Expected provider/transport failure; adapters normalize vendor exceptions."""


class ToolError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
