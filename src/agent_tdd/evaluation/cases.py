"""Strict, size-bounded versioned case files; never dynamically import case-supplied code."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_tdd.evaluation.contracts import Case, Suite
from agent_tdd.types import Decision, Status, parse_json


def read_object(path: Path, *, limit: int = 1_048_576) -> dict[str, Any]:
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON file exceeds size limit")

    value = parse_json(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", value):
        raise ValueError("invalid name")
    return value


def load_suite(path: Path) -> Suite:
    data = read_object(path)
    if (
        set(data) != {"schema_version", "name", "version", "cases"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("unsupported case schema")
    name, version = _name(data["name"]), _name(data["version"])
    if not isinstance(data["cases"], list) or not 1 <= len(data["cases"]) <= 1000:
        raise ValueError("suite must contain 1–1000 cases")
    cases: list[Case] = []
    ids: set[str] = set()
    for raw in data["cases"]:
        if not isinstance(raw, dict) or not {"id", "split", "task", "expected"} <= set(raw):
            raise ValueError("case is missing required fields")
        if set(raw) - {"id", "split", "task", "expected", "tags", "script"}:
            raise ValueError("unknown case field")
        case_id = _name(raw["id"])
        if case_id in ids or raw["split"] not in ("dev", "release"):
            raise ValueError("duplicate case ID or invalid split")
        ids.add(case_id)
        expected = raw["expected"]
        if (
            not isinstance(expected, dict)
            or "status" not in expected
            or set(expected)
            - {"status", "output", "error_code", "max_model_calls", "forbidden_tools"}
        ):
            raise ValueError("invalid expectation")
        Status(expected["status"])
        if (
            "error_code" in expected
            and expected["error_code"] is not None
            and not isinstance(expected["error_code"], str)
        ):
            raise ValueError("invalid error code")
        if "max_model_calls" in expected and (
            type(expected["max_model_calls"]) is not int or expected["max_model_calls"] < 0
        ):
            raise ValueError("invalid call limit")
        tags = raw.get("tags", [])
        forbidden = expected.get("forbidden_tools", [])
        if not isinstance(tags, list) or not isinstance(forbidden, list):
            raise ValueError("tags and forbidden tools must be lists")
        for item in [*tags, *forbidden]:
            _name(item)
        script = raw.get("script", [])
        if not isinstance(script, list) or len(script) > 1000:
            raise ValueError("invalid script")
        decisions: list[Decision] = []
        for step in script:
            if not isinstance(step, dict) or set(step) - {"action", "tool", "arguments", "output"}:
                raise ValueError("invalid scripted decision")
            if step.get("action") not in ("finish", "tool"):
                raise ValueError("invalid scripted action")
            if not isinstance(step.get("arguments", {}), dict) or not isinstance(
                step.get("tool", ""), str
            ):
                raise ValueError("invalid scripted tool")
            decisions.append(Decision(**step))
        cases.append(
            Case(case_id, raw["split"], raw["task"], expected, tuple(tags), tuple(decisions))
        )
    return Suite(name, version, tuple(cases), fingerprint(data))
