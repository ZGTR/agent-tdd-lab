"""Prove the approval regression catches a real unauthorized effect, without editing checkout."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    temporary = Path(directory)
    shutil.copytree(root / "src", temporary / "src")
    policy = temporary / "src/agent_tdd/examples/refunds/policy.py"
    source = policy.read_text()
    original = '        if approval is None:\n            raise ToolError("approval_required")'
    mutation = """        if approval is None:
            approval = Approval(
                context.identity.tenant_id, context.identity.actor_id,
                context.request_id, order_id, now + 60,
            )"""
    if original not in source:
        raise SystemExit("mutation target changed; update this check")
    policy.write_text(source.replace(original, mutation, 1))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(root / "tests/test_refunds.py"),
            "-q",
            "-k",
            "test_approval_required_and_no_effect",
        ],
        cwd=temporary,
        env={**os.environ, "PYTHONPATH": str(temporary / "src")},
        capture_output=True,
        text=True,
    )
    if result.returncode != 1 or "DID NOT RAISE" not in result.stdout:
        print(result.stdout, result.stderr)
        raise SystemExit("mutation did not produce the expected authorization-test failure")
    print("PASS: inventing a missing approval causes the invariant test to fail")
