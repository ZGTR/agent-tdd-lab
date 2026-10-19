# Production integration requirements and limits

This repository is a tested reference library and offline lab, not certification that an arbitrary agent is safe to deploy. The generic core can be reused in an application; the bundled refund tool is explicitly a **local effect simulator**. No real refund is performed.

## What is enforced here

- Explicit model/tool/step ports and constructor injection, with no runtime third-party dependencies.
- Per-agent turn limit, an atomic shared model-call limit, and a monotonic cooperative deadline.
- Cancellation checks around model calls and before effectful demo-tool dispatch.
- Unknown tool and malformed decision rejection; finite, bounded-depth JSON boundaries.
- A deterministic policy that binds approval to tenant, actor, request, order, and expiry.
- Tenant-scoped SQL lookups, database uniqueness for request and order, integer stored amounts.
- Ledger write and outbox insert in one SQLite transaction, with rollback on outbox failure.
- Expiry recheck after obtaining the write lock; lock contention is a bounded retryable error.
- Durable replay across fresh storage instances, with tested concurrency and idempotency conflicts.
- At-least-once event delivery with explicit acknowledgment and retained failures.

## Before enabling real integrations

1. **Authenticate callers in host code.** Construct `RunContext` from verified identity. Never accept it from model output. The CLI's `DemoApprovals` and `--approve` are fixtures, not an approval service. Replace them with an authoritative, revocable approval lookup, and define approval expiry/revocation semantics during an in-flight transaction.
2. **Implement service authorization.** The downstream service must independently verify ownership and policy. The Python `AuthorizedRefund` dataclass is not an unforgeable security token. The SQLite adapter's `add_order` is trusted administrative setup; do not register it as an agent tool.
3. **Bound every network request.** Honor timeout arguments at the SDK/HTTP client and tool boundary. This library cannot kill a Python thread, interrupt arbitrary blocking native code, or undo effects after cancellation. Use a supervised process/container if hard termination is required. A deadline or lost reply can coexist with a committed effect; preserve receipts and reconcile.
4. **Use provider idempotency and reconciliation.** A SQLite transaction cannot atomically commit a remote payment, email, cloud API call, or database in another system. Use a stable tenant/operation key at the provider, durable state transitions, and a reconciliation worker. Never claim end-to-end exactly-once behavior based on this local ledger.
5. **Choose appropriate persistence.** SQLite WAL is intended for a local filesystem with correct locking, not shared NFS, a disposable serverless disk, or a multi-region service. Size concurrency and storage with actual load tests. Back up and restore the database using SQLite-aware tooling, retain ledger keys for the required retry window, monitor disk and pending outbox age, and test recovery. Version 1 is the initial schema; future changes require explicit compatible migrations.
6. **Enforce cost/resource budgets.** The shared budget counts model calls, not dollars, tokens, tool calls, database bytes, or parallel workers. Reserve cost/token budgets at the provider boundary if required. Limit prompt/output size and concurrency. The workflow runner is sequential and in-memory; use an appropriate durable engine for crash-safe long-running workflows.
7. **Make event consumers idempotent.** Dispatch may repeat after a crash, partial subscriber success, or concurrent dispatchers. Atomic consumer deduplication must cover the consumer's effect. Configure subscriptions before running workers; the in-process bus is not designed for concurrent subscription mutation.
8. **Evaluate real model behavior separately.** Run reviewed development and held-out cases through your real provider with isolated write tools. Record prompt/model/tool schema versions, cost, latency, all attempts, and policy violations. Offline test success is not evidence of LLM quality or live IAM access.
9. **Operate and secure the host.** Set least-privilege IAM, secrets management, filesystem permissions, TLS, log redaction/retention, monitoring, dependency updates, and incident procedures appropriate to the application. Result/event JSON contains tenant/order/request IDs; do not publish it to unauthorized logs. The library does not install global logging or telemetry exporters.

## Failure semantics

`COMPLETED` means the model returned a structurally valid final decision. It is not a semantic correctness judgment or proof that a requested business effect occurred. Inspect tool observations and authoritative receipts. Workflow failure preserves completed upstream outputs; it does not roll back them. Retrying a workflow must preserve effect keys and reconcile already completed steps.

The refund simulator supports one full refund per tenant/order; it does not implement partial refunds, tax, settlement, currencies beyond stored codes, account balances, or payment-provider interactions. Extend the domain model and tests deliberately before adding those features.
