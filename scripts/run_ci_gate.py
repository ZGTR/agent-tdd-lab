"""Composite-action controller. Inputs are argv data, never interpolated shell code."""

import os
import subprocess
import sys
from collections.abc import Mapping

OPTIONS = (
    "cases",
    "executor-config",
    "report",
    "baseline",
    "split",
    "repeats",
    "revision",
    "max-requests",
    "max-model-calls",
    "timeout",
    "max-latency",
    "min-pass-rate",
    "max-regression",
    "max-total-tokens",
    "max-estimated-cost-usd",
)


def arguments(environment: Mapping[str, str]) -> list[str]:
    command = [sys.executable, "-m", "agent_tdd", "evaluate"]
    for name in OPTIONS:
        value = environment.get("GATE_" + name.upper().replace("-", "_"), "")
        if value:
            # Equals form also preserves a path beginning with a dash as data.
            command.append(f"--{name}={value}")
    return command


if __name__ == "__main__":
    raise SystemExit(subprocess.run(arguments(os.environ), check=False).returncode)
