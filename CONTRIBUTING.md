# Contributing

Keep the generic core independent of business-specific examples and vendor SDKs. Use constructor injection, small protocols, explicit error contracts, and composition. Add regression tests for real failure behavior; do not mock the controller under test.

Run `uv sync --frozen`, then `uv run --frozen ruff check .`, `uv run --frozen ruff format --check .`, `uv run --frozen mypy`, `uv run --frozen pytest --cov`, and `uv run --frozen python -m build --no-isolation`.

Update `uv.lock` with `uv lock` after a dependency change, then regenerate pip-compatible pins with `uv export --frozen --only-group dev --no-emit-project --format requirements-txt --output-file requirements-dev.txt`. Never edit the generated requirements by hand. Do not commit credentials, local databases, or model traces containing customer data.
