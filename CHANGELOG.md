# Changelog

## 1.1.0

- Add an execution service with per-run factories, a configuration-snapshot registry, injected schema validation, and exception-isolated lifecycle observers.
- Centralize cancellation/deadline checks across agents, workflows, and the runtime. Add scoped CLI signal handling and metadata-only result output.
- Keep raw provider exceptions out of operational CLI logs while preserving explicit nonzero exit status.
- Add a digest-pinned non-root container, offline Compose configuration, and persistent-volume replay checks.
- Add an AWS OpenTofu root configuration for on-demand Fargate jobs: immutable image promotion, scoped IAM, explicit networking, remote state configuration, Step Functions deadlines/exit-code gates, and failure/timeout alarms.
- Add provider-mocked infrastructure tests and container verification to CI. No cloud resources are applied by CI.

## 1.0.0

- Generic Model/Tool/Step interfaces, bounded harnesses, and sequential DAG workflows.
- Offline SQLite refund simulator with action-scoped approval, idempotency, transactions, and outbox delivery.
- Python 3.11–3.13 Linux/Windows CI, package builds, mutation check, and adapter documentation.
