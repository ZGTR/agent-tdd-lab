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
