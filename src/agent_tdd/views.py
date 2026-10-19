"""Presentation: stable JSON rendering, independent of execution and persistence."""

import json
from dataclasses import asdict

from agent_tdd.events import Event
from agent_tdd.types import RunResult


def render_result(result: RunResult) -> str:
    return json.dumps(asdict(result), sort_keys=True, allow_nan=False)


def render_event(event: Event) -> str:
    return json.dumps(asdict(event), sort_keys=True, allow_nan=False)
