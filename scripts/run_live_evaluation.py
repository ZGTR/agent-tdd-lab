"""Trusted manual-CI entry point. Inputs are data, never interpolated shell code."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    provider = os.environ.get("EVAL_PROVIDER", "")
    model = os.environ.get("EVAL_MODEL", "")
    baseline_model = os.environ.get("EVAL_BASELINE_MODEL", "")
    if provider not in {"openai", "bedrock"} or not model.strip():
        raise ValueError("select provider and model")
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("configure OPENAI_API_KEY in the live-evaluation environment")
    directory = Path("artifacts")
    directory.mkdir(mode=0o700, exist_ok=True)
    config = json.loads(Path(f"evals/providers/{provider}.example.json").read_text())
    config.update(model=model)
    if provider == "openai":
        config["endpoint"] = os.environ.get("EVAL_ENDPOINT") or config["endpoint"]
    else:
        config["region"] = os.environ.get("EVAL_REGION") or config["region"]
    base_command = [
        sys.executable,
        "-m",
        "agent_tdd",
        "evaluate",
        "--cases",
        "evals/refund-routing.json",
        "--provider",
        provider,
        "--split",
        "release",
        "--repeats",
        "1",
        "--max-model-calls",
        "2",
        "--max-requests",
        "12",
        "--timeout",
        "30",
        "--max-latency",
        "35",
    ]
    if baseline_model:
        baseline_config = directory / "baseline-config.json"
        baseline_config.write_text(json.dumps(config | {"model": baseline_model}))
        status = subprocess.run(
            base_command
            + ["--config", str(baseline_config), "--report", str(directory / "baseline.json")],
            check=False,
        ).returncode
        if status:
            return status
    candidate_config = directory / "candidate-config.json"
    candidate_config.write_text(json.dumps(config))
    command = base_command + [
        "--config",
        str(candidate_config),
        "--report",
        str(directory / "candidate.json"),
    ]
    if baseline_model:
        command += ["--baseline", str(directory / "baseline.json")]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError):
        print("Live evaluation configuration is incomplete; no passing evidence produced.")
        raise SystemExit(2) from None
