"""Python-side command protocol helper; applications inject their existing harness."""

import json
import math
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import cast

from agent_tdd.evaluation.contracts import Sample
from agent_tdd.types import JsonValue, parse_json


@dataclass(frozen=True)
class Invocation:
    task: JsonValue
    request_id: str
    max_requests: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not 1 <= len(self.request_id) <= 200:
            raise ValueError("invalid request ID")
        if type(self.max_requests) is not int or self.max_requests < 1:
            raise ValueError("invalid request allowance")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("invalid timeout")


def serve(handler: Callable[[Invocation], Sample]) -> int:
    """Read one task, call the application adapter, write one Sample, return an exit code."""
    try:
        raw = sys.stdin.buffer.read(1_048_576 + 4097)
        if len(raw) > 1_048_576 + 4096:
            raise ValueError("request too large")
        data = parse_json(raw)
        if (
            not isinstance(data, dict)
            or set(data)
            != {"schema_version", "task", "request_id", "max_requests", "timeout_seconds"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
        ):
            raise ValueError("invalid invocation")
        invocation = Invocation(
            data["task"],
            cast(str, data["request_id"]),
            cast(int, data["max_requests"]),
            cast(float, data["timeout_seconds"]),
        )
    except (ValueError, TypeError, OSError, RecursionError, OverflowError):
        print('{"error":"invalid_invocation"}')
        return 2
    try:
        sample = handler(invocation)
        response = {"schema_version": 1, **asdict(sample)}
        sys.stdout.buffer.write(
            json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        )
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Never echo vendor exceptions, task data or credentials into shared logs.
        print('{"error":"adapter_failed"}')
        return 1
