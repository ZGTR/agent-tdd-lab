"""Smoke-test the production image offline, including durable SQLite replay on a test volume."""

import argparse
import json
import subprocess
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("image")
args = parser.parse_args()
inspect = subprocess.run(
    ["docker", "image", "inspect", args.image, "--format", "{{.Config.User}}"],
    capture_output=True,
    text=True,
    check=True,
)
if inspect.stdout.strip() != "10001:10001":
    raise SystemExit("Image does not declare the expected non-root user")
base = [
    "docker",
    "run",
    "--rm",
    "--read-only",
    "--network",
    "none",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--pids-limit",
    "64",
    "--memory",
    "512m",
]
result = subprocess.run([*base, args.image], capture_output=True, text=True, check=True)
if json.loads(result.stdout)["status"] != "completed":
    raise SystemExit("Default image command did not complete")
volume = f"agent-tdd-test-{uuid4().hex}"
subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
try:
    command = [
        *base,
        "--mount",
        f"type=volume,source={volume},target=/data",
        args.image,
        "refund",
        "--db",
        "/data/refunds.db",
        "--approve",
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=True)
    second = subprocess.run(command, capture_output=True, text=True, check=True)
    if json.loads(first.stdout) != json.loads(second.stdout):
        raise SystemExit("Repeated request did not return the same result")
    if len(first.stderr.splitlines()) != 1 or second.stderr.strip():
        raise SystemExit("Replay produced a second effect event or first event was missing")
    for name in ("baseline", "candidate"):
        evaluation = [
            *base,
            "--mount",
            f"type=volume,source={volume},target=/data",
            args.image,
            "evaluate",
            "--cases",
            "/opt/agent-tdd/evals/refund-routing.json",
            "--provider",
            "scripted",
            "--repeats",
            "3",
            "--report",
            f"/data/{name}.json",
        ]
        if name == "candidate":
            evaluation += ["--baseline", "/data/baseline.json"]
        completed = subprocess.run(evaluation, capture_output=True, text=True, check=True)
        summary = json.loads(completed.stdout)
        if not summary["passed"] or summary["summary"]["requests"] != 18:
            raise SystemExit("Container evaluation or baseline gate did not pass")
finally:
    subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=True)
print("Container verified: non-root, read-only, offline; durable replay and evaluation gates pass")
