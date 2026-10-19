"""Idempotently configure main-only evaluation environment using an administrator's gh login.

No secrets are accepted or printed. Default checks only; --apply performs the reviewed setup.
"""

import argparse
import json
import re
import subprocess
from typing import Any


def api(
    path: str, *, method: str = "GET", body: dict[str, Any] | None = None, missing_ok: bool = False
) -> Any:
    command = ["gh", "api", path, "--method", method]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        if missing_ok and "HTTP 404" in result.stderr:
            return None
        raise RuntimeError("GitHub environment API unavailable; administrator access is required")
    return json.loads(result.stdout) if result.stdout.strip() else None


def configure(repository: str, *, apply: bool = False) -> None:
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", repository
    ):
        raise ValueError("use owner/repository")
    path = f"repos/{repository}/environments/live-evaluation"
    environment = api(path, missing_ok=True)
    wanted = {"protected_branches": False, "custom_branch_policies": True}
    if environment is None or environment.get("deployment_branch_policy") != wanted:
        if not apply:
            raise RuntimeError("main-only live-evaluation environment has not been configured")
        if environment is not None:
            # Preserve existing approval/wait rules: do not silently reset another policy.
            raise RuntimeError("existing environment policy differs; review it in GitHub settings")
        api(path, method="PUT", body={"deployment_branch_policy": wanted})
    policies = api(path + "/deployment-branch-policies")["branch_policies"]
    if not policies and apply:
        api(
            path + "/deployment-branch-policies",
            method="POST",
            body={"name": "main", "type": "branch"},
        )
        policies = api(path + "/deployment-branch-policies")["branch_policies"]
    if (
        len(policies) != 1
        or policies[0].get("name") != "main"
        or policies[0].get("type") != "branch"
    ):
        raise RuntimeError("environment must permit exactly the main branch; review existing rules")
    print(f"Verified {repository}: live-evaluation permits only main")
    print("Configure provider secrets/role variables separately; no credential values were read.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        configure(args.repo, apply=args.apply)
    except (ValueError, RuntimeError, OSError, KeyError):
        raise SystemExit(
            "Evaluation environment is not ready; verify administrator access and branch rules."
        ) from None
