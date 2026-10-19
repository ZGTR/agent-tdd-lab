"""Framework-independent data contracts."""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeAlias, cast

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


def parse_json(text: str | bytes) -> JsonValue:
    """Decode JSON without duplicate keys, nonfinite values, or unbounded nesting."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = cast(JsonValue, json.loads(text, object_pairs_hook=unique))
    validate_json(value)
    return value


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
    EXECUTION_ERROR = "execution_error"
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
