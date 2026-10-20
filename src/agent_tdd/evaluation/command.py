"""Trusted subprocess bridge for any harness/language; never execute commands from cases."""

import json
import subprocess
import tempfile
from dataclasses import dataclass, replace
from typing import Any, cast

from agent_tdd.evaluation.contracts import Case, Sample, Usage
from agent_tdd.runtime import RunSettings
from agent_tdd.types import Observation, RunResult, Status, parse_json

MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True)
class CommandSettings:
    command: tuple[str, ...]
    suite_kind: str
    adapter_version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.command, tuple)
            or not 1 <= len(self.command) <= 128
            or any(
                not isinstance(arg, str) or not arg or len(arg) > 4096 or "\0" in arg
                for arg in self.command
            )
        ):
            raise ValueError("command must contain 1–128 nonempty arguments")
        if not isinstance(self.suite_kind, str) or self.suite_kind not in {"scripted", "live"}:
            raise ValueError("declare scripted or live evidence explicitly")
        if (
            not isinstance(self.adapter_version, str)
            or not self.adapter_version.strip()
            or len(self.adapter_version) > 200
        ):
            raise ValueError("adapter_version must identify the application adapter")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CommandSettings":
        if set(config) != {"command", "suite_kind", "adapter_version"} or not isinstance(
            config["command"], list
        ):
            raise ValueError("invalid executor configuration")
        return cls(tuple(config["command"]), config["suite_kind"], config["adapter_version"])


def _sample(raw: bytes) -> Sample:
    data = parse_json(raw)
    if not isinstance(data, dict) or set(data) != {"schema_version", "result", "usage"}:
        raise ValueError("invalid executor response")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported executor response version")
    result, usage = data["result"], data["usage"]
    if (
        not isinstance(result, dict)
        or not {"status", "model_calls"} <= set(result)
        or set(result) - {"status", "output", "observations", "model_calls", "error_code"}
        or not isinstance(usage, dict)
        or "requests" not in usage
        or set(usage) - {"requests", "input_tokens", "output_tokens", "estimated_cost_usd"}
    ):
        raise ValueError("invalid result or usage")
    if type(result["model_calls"]) is not int or result["model_calls"] < 0:
        raise ValueError("model_calls must be a nonnegative integer")
    error = result.get("error_code")
    if error is not None and (not isinstance(error, str) or not error or len(error) > 200):
        raise ValueError("invalid error_code")
    observations = result.get("observations", [])
    if not isinstance(observations, list) or len(observations) > 1000:
        raise ValueError("invalid observations")
    normalized = []
    for observation in observations:
        if (
            not isinstance(observation, dict)
            or not {"tool", "ok"} <= set(observation)
            or set(observation) - {"tool", "ok", "value", "error_code"}
            or not isinstance(observation["tool"], str)
            or not observation["tool"]
            or type(observation["ok"]) is not bool
            or (
                observation.get("error_code") is not None
                and not isinstance(observation["error_code"], str)
            )
        ):
            raise ValueError("invalid tool observation")
        normalized.append(
            Observation(
                observation["tool"],
                observation["ok"],
                observation.get("value"),
                cast(str | None, observation.get("error_code")),
            )
        )
    return Sample(
        RunResult(
            Status(cast(str, result["status"])),
            result.get("output"),
            tuple(normalized),
            result["model_calls"],
            error,
        ),
        Usage(**cast(dict[str, Any], usage)),
    )


class CommandExecutor:
    """Fresh trusted adapter process per attempt; model metering stays in the adapter."""

    def __init__(self, command: CommandSettings, settings: RunSettings) -> None:
        self._command, self._settings = command, settings

    def run(self, case: Case, *, request_id: str, max_requests: int) -> Sample:
        if type(max_requests) is not int or max_requests < 1:
            raise ValueError("positive request allowance required")
        allowance = min(max_requests, self._settings.max_model_calls)
        request = {
            "schema_version": 1,
            "task": case.task,
            "request_id": request_id,
            "max_requests": allowance,
            "timeout_seconds": self._settings.timeout_seconds,
        }
        # Spool privately to disk, avoiding unbounded in-memory capture. The byte
        # cap is checked after exit; the CI job must also limit disk/process use.
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                self._command.command,
                input=json.dumps(request, ensure_ascii=False, allow_nan=False).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=self._settings.timeout_seconds,
                check=False,
                shell=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("external_executor_failed")
            output.seek(0)
            raw = output.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("executor response exceeds byte limit")
        sample = _sample(raw)
        if sample.usage.requests > allowance or sample.result.model_calls > sample.usage.requests:
            return Sample(
                replace(
                    sample.result,
                    status=Status.EXECUTION_ERROR,
                    error_code="executor_request_accounting",
                ),
                sample.usage,
            )
        return sample
