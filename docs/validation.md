# Initial local verification — 19 October 2026

This records checks actually performed, not a general production certification.

- 73 tests passed on Linux with Python 3.11, 3.12, and 3.13.
- Measured statement and branch coverage: 100%, with a required 90% floor.
- Ruff lint/format and strict mypy passed.
- An isolated policy mutation that invents a missing approval produced the intended regression-test failure.
- A fresh Git clone passed the complete suite with frozen dependencies.
- The README's hash-verified plain-pip installation succeeded in a fresh virtual environment.
- Wheel and source distribution built successfully; an isolated wheel installation ran both CLI examples without runtime dependencies or API credentials.
- An adversarial review found final-step cancellation and queued-write cancellation gaps. Both were fixed and regression-tested, including output preservation and rollback.

The public [GitHub CI run](https://github.com/ZGTR/agent-tdd-lab/actions/runs/39288676945) passed all six Linux/Windows jobs on Python 3.11–3.13, including lint, strict types, coverage, and package builds. Real provider integration, IAM, token/spend budgets, and semantic evaluations are application-specific work, as described in production.md.

## Runtime and infrastructure extension — 19 October 2026

- 95 Python tests passed with 100% measured statement and branch coverage; lint, formatting, strict types, mutation verification, and package builds passed.
- The [extension CI run](https://github.com/ZGTR/agent-tdd-lab/actions/runs/39292616907) passed all eight jobs: the six Python/OS combinations, container build/smoke checks, and OpenTofu validation/tests.
- The final container passed a local smoke check with a non-root user, read-only root, no networking, and dropped capabilities. Its named-volume refund replay retained one authoritative receipt without a duplicate effect event.
- The checksum-pinned OpenTofu installer ran successfully. All nine AWS provider-mocked infrastructure tests passed, including bootstrap, image/runtime hardening, deadlines, and exit-code handling.

No AWS resources were provisioned and no live IAM/network/provider integration was exercised. These checks establish the reusable foundation's tested behavior; application-specific adapters, model evaluations, cloud permissions, and recovery drills still require verification in the target environment. See [deployment and operations](deployment.md) for the executable runbook and boundaries.

## Evaluation release — 19 October 2026

- 193 tests passed on Linux with Python 3.11, 3.12 and 3.13. Python 3.12 measured 100% statement/branch coverage. Ruff, strict mypy, policy mutation verification and wheel/source builds passed.
- Local HTTP contract tests exercised real socket requests, malformed responses, redirects, usage gaps and HTTP failures. AWS SDK Stubber verified Converse parameters, timeout configuration and disabled inference retries without contacting AWS.
- Both default and optional Bedrock images passed non-root/read-only/offline smoke checks, durable refund replay, and repeated evaluation/baseline artifact gates. The Bedrock SDK imported successfully in its optional image without network access.
- OpenTofu format/validate and all 13 provider-mocked tests passed, including opt-in selected-model IAM and repository/environment-specific OIDC trust.
- Independent adversarial review found and closed report/input sibling collisions, invalid repository-owner validation, and inaccurate per-request token/cost wording. Report destinations are checked before inference; cost/token gates apply after each executor/case run.

The public fixture partition is illustrative. No live model-quality or AWS-account integration evidence was produced: provider credentials were not configured. GitHub environment administration returned HTTP 403; the main-only environment/credential setup is documented and scripted, not claimed as provisioned. A live application release still needs its actual provider report, target-account contract checks and recovery evidence. See [evaluation setup](evaluation.md).
