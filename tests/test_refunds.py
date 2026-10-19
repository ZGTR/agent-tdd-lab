import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent_tdd.composition import DemoApprovals
from agent_tdd.examples.refunds.domain import Approval, AuthorizedRefund
from agent_tdd.examples.refunds.policy import RefundPolicy
from agent_tdd.examples.refunds.sqlite import SqliteRefundStore
from agent_tdd.examples.refunds.tool import RefundTool
from agent_tdd.types import ToolError


@pytest.fixture
def store(tmp_path, clock):
    db = SqliteRefundStore(tmp_path / "ledger.db", clock)
    db.initialize()
    db.add_order("acme", "A1", 1200)
    db.add_order("acme", "A2", 500)
    return db


@pytest.fixture
def approval(ctx):
    return Approval("acme", "alice", "req-1", "A1", 1100)


def instruction():
    return AuthorizedRefund("acme", "alice", "req-1", "A1", 1100)


def test_approval_required_and_no_effect(store, ctx, clock, tmp_path):
    tool = RefundTool(store, DemoApprovals(None), RefundPolicy(), clock)
    with pytest.raises(ToolError, match="approval_required"):
        tool.execute({"order_id": "A1"}, ctx, timeout_seconds=5)
    assert store.pending(100) == []
    with sqlite3.connect(tmp_path / "ledger.db") as db:
        assert db.execute("SELECT count(*) FROM refunds").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change",
    [{"tenant_id": "other"}, {"actor_id": "bob"}, {"request_id": "other"}, {"order_id": "A2"}],
)
def test_approval_is_bound_to_exact_context(ctx, approval, change):
    with pytest.raises(ToolError, match="forbidden"):
        RefundPolicy().authorize(ctx, "A1", replace(approval, **change), 1000)


@pytest.mark.parametrize("expiry", [1000, 999, float("nan"), float("inf")])
def test_expired_or_invalid_approval(ctx, approval, expiry):
    with pytest.raises(ToolError, match="approval_expired"):
        RefundPolicy().authorize(ctx, "A1", replace(approval, expires_at=expiry), 1000)


def test_tool_uses_store_amount_and_exact_schema(store, ctx, clock, approval):
    tool = RefundTool(store, DemoApprovals(approval), RefundPolicy(), clock)
    result = tool.execute({"order_id": "A1"}, ctx, timeout_seconds=5)
    assert result["amount_cents"] == 1200
    for invalid in [{"order_id": "A1", "amount": 1}, {"order_id": 1}, {}, {"order_id": ""}]:
        with pytest.raises(ToolError, match="invalid_arguments"):
            tool.execute(invalid, ctx, timeout_seconds=5)


def test_tenant_is_enforced_in_storage(store):
    with pytest.raises(ToolError, match="order_not_found"):
        store.commit(replace(instruction(), tenant_id="other"))
    assert store.pending(100) == []


def test_durable_idempotency_and_outbox(store, tmp_path, clock):
    receipt = store.commit(instruction())
    fresh = SqliteRefundStore(tmp_path / "ledger.db", clock)
    fresh.initialize()
    assert fresh.commit(instruction()) == receipt
    assert len(fresh.pending(100)) == 1
    with pytest.raises(ToolError, match="idempotency_conflict"):
        fresh.commit(replace(instruction(), order_id="A2"))
    with pytest.raises(ToolError, match="already_refunded"):
        fresh.commit(replace(instruction(), request_id="req-2"))


def test_concurrent_duplicate_requests_create_one_effect(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: store.commit(instruction()), range(20)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(store.pending(100)) == 1


def test_loss_after_commit_can_retry_without_second_effect(store):
    def lost_reply():
        store.commit(instruction())
        raise TimeoutError("network reply lost after commit")

    with pytest.raises(TimeoutError):
        lost_reply()
    assert store.commit(instruction())["amount_cents"] == 1200
    assert len(store.pending(100)) == 1


def test_approval_rechecked_at_commit(store, clock):
    clock.now = 1100
    with pytest.raises(ToolError, match="approval_expired"):
        store.commit(instruction())
    assert store.pending(100) == []


def test_outbox_failure_rolls_back_effect(store, tmp_path):
    db = sqlite3.connect(tmp_path / "ledger.db")
    db.execute(
        "CREATE TRIGGER reject_event BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'fault'); END"
    )
    db.commit()
    db.close()
    with pytest.raises(sqlite3.IntegrityError):
        store.commit(instruction())
    db = sqlite3.connect(tmp_path / "ledger.db")
    assert db.execute("SELECT count(*) FROM refunds").fetchone()[0] == 0
    db.close()


def test_schema_and_input_guards(store, tmp_path, clock):
    store.add_order("acme", "A1", 1200)
    with pytest.raises(ToolError, match="order_conflict"):
        store.add_order("acme", "A1", 1)
    for cents in [0, -1, True]:
        with pytest.raises(ValueError):
            store.add_order("acme", "A3", cents)
    with pytest.raises(ValueError):
        store.add_order("acme", "A3", 1, "usd")
    with pytest.raises(ValueError):
        store.pending(0)
    with pytest.raises(ValueError):
        SqliteRefundStore(type(tmp_path)(":memory:"), clock)
    db = sqlite3.connect(tmp_path / "ledger.db")
    db.execute("PRAGMA user_version=99")
    db.close()
    with pytest.raises(RuntimeError, match="unsupported"):
        store.initialize()


def test_cancel_during_approval_lookup(store, ctx, clock, approval):
    class CancelApprovals:
        def find(self, *args):
            ctx.cancelled.set()
            return approval

    tool = RefundTool(store, CancelApprovals(), RefundPolicy(), clock)
    with pytest.raises(ToolError, match="execution_stopped"):
        tool.execute({"order_id": "A1"}, ctx, timeout_seconds=5)
    assert store.pending(100) == []
    with pytest.raises(ToolError, match="execution_stopped"):
        tool.execute({"order_id": "A1"}, ctx, timeout_seconds=0)


def test_database_contention_is_retryable(store, tmp_path):
    db = sqlite3.connect(tmp_path / "ledger.db", isolation_level=None)
    db.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ToolError, match="storage_busy") as failure:
            store.commit(instruction())
        assert failure.value.retryable
    finally:
        db.rollback()
        db.close()
    assert len(store.pending(100)) == 0


def test_unexpected_storage_error_is_not_hidden(store, tmp_path):
    db = sqlite3.connect(tmp_path / "ledger.db")
    db.execute("DROP TABLE outbox")
    db.close()
    with pytest.raises(sqlite3.OperationalError):
        store.pending(100)


def test_different_request_ids_racing_for_same_order_cannot_double_refund(store):
    def attempt(number):
        try:
            return store.commit(replace(instruction(), request_id=f"req-{number}"))
        except ToolError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("already_refunded") == 19
    assert len(store.pending(100)) == 1


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_cancel_or_deadline_while_waiting_for_lock_does_not_commit(
    store, ctx, clock, approval, tmp_path, monkeypatch, stop
):
    from contextlib import contextmanager
    from threading import Event

    entered = Event()
    original = store._connect

    @contextmanager
    def observed(timeout_seconds=1):
        with original(timeout_seconds) as db:
            entered.set()
            yield db

    monkeypatch.setattr(store, "_connect", observed)
    tool = RefundTool(store, DemoApprovals(approval), RefundPolicy(), clock)
    db = sqlite3.connect(tmp_path / "ledger.db", isolation_level=None)
    db.execute("BEGIN IMMEDIATE")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tool.execute, {"order_id": "A1"}, ctx, timeout_seconds=1)
        assert entered.wait(2)
        if stop == "cancel":
            ctx.cancelled.set()
        else:
            clock.now += 100
        db.rollback()
        db.close()
        with pytest.raises(ToolError, match="execution_stopped"):
            future.result(timeout=2)
    assert store.pending(100) == []
    with sqlite3.connect(tmp_path / "ledger.db") as db:
        assert db.execute("SELECT count(*) FROM refunds").fetchone()[0] == 0


def test_cancellation_before_commit_rolls_back_effect(store):
    checks = 0

    def guard():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ToolError("execution_stopped")

    with pytest.raises(ToolError, match="execution_stopped"):
        store.commit(instruction(), check_active=guard)
    assert store.pending(100) == []
    with pytest.raises(ToolError, match="execution_stopped"):
        store.commit(instruction(), timeout_seconds=0)
