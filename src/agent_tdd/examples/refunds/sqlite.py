"""Local durable effect simulator: atomic ledger + outbox, parameterized SQL, schema versioning.

This does not send money. A real payment service must implement provider idempotency
and reconciliation; a local transaction cannot atomically commit a remote payment.
"""

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agent_tdd.context import Clock
from agent_tdd.events import Event
from agent_tdd.examples.refunds.domain import AuthorizedRefund
from agent_tdd.types import JsonObject, ToolError

_SCHEMA = (
    """CREATE TABLE orders (
        tenant TEXT NOT NULL, order_id TEXT NOT NULL,
        cents INTEGER NOT NULL CHECK(typeof(cents)='integer' AND cents>0),
        currency TEXT NOT NULL CHECK(length(currency)=3),
        PRIMARY KEY(tenant, order_id))""",
    """CREATE TABLE refunds (
        tenant TEXT NOT NULL, request_id TEXT NOT NULL, actor TEXT NOT NULL,
        order_id TEXT NOT NULL, cents INTEGER NOT NULL, currency TEXT NOT NULL,
        PRIMARY KEY(tenant, request_id), UNIQUE(tenant, order_id),
        FOREIGN KEY(tenant, order_id) REFERENCES orders(tenant, order_id))""",
    """CREATE TABLE outbox (
        id TEXT PRIMARY KEY, topic TEXT NOT NULL, payload TEXT NOT NULL,
        delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0,1)))""",
)


class SqliteRefundStore:
    def __init__(self, path: Path, clock: Clock) -> None:
        if str(path) == ":memory:":
            raise ValueError("use a file path: operations open independent connections")
        self._path = path
        self._clock = clock

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=1, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise ToolError("storage_busy", retryable=True) from error
            raise
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self._transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute("PRAGMA user_version=1")
            elif version != 1:
                raise RuntimeError(f"unsupported database schema: {version}")

    def add_order(self, tenant: str, order_id: str, cents: int, currency: str = "USD") -> None:
        """Trusted administrative operation; never register this method as an agent tool."""
        if type(cents) is not int or cents <= 0:
            raise ValueError("cents must be a positive integer")
        if (
            len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or not currency.isupper()
        ):
            raise ValueError("currency must be a three-letter uppercase code")
        with self._transaction() as db:
            existing = db.execute(
                "SELECT cents,currency FROM orders WHERE tenant=? AND order_id=?",
                (tenant, order_id),
            ).fetchone()
            if existing is not None:
                if (existing["cents"], existing["currency"]) != (cents, currency):
                    raise ToolError("order_conflict")
                return
            db.execute("INSERT INTO orders VALUES (?,?,?,?)", (tenant, order_id, cents, currency))

    def commit(self, instruction: AuthorizedRefund) -> JsonObject:
        i = instruction
        with self._transaction() as db:
            # Check after acquiring the write lock; a queued transaction can outlive approval.
            if not math.isfinite(i.approved_until) or i.approved_until <= self._clock.time():
                raise ToolError("approval_expired")
            existing = db.execute(
                "SELECT * FROM refunds WHERE tenant=? AND request_id=?", (i.tenant_id, i.request_id)
            ).fetchone()
            if existing is not None:
                if (existing["order_id"], existing["actor"]) != (i.order_id, i.actor_id):
                    raise ToolError("idempotency_conflict")
                return self._receipt(existing)
            order = db.execute(
                "SELECT * FROM orders WHERE tenant=? AND order_id=?", (i.tenant_id, i.order_id)
            ).fetchone()
            if order is None:
                raise ToolError("order_not_found")
            try:
                db.execute(
                    "INSERT INTO refunds VALUES (?,?,?,?,?,?)",
                    (
                        i.tenant_id,
                        i.request_id,
                        i.actor_id,
                        i.order_id,
                        order["cents"],
                        order["currency"],
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ToolError("already_refunded") from error
            row = db.execute(
                "SELECT * FROM refunds WHERE tenant=? AND request_id=?", (i.tenant_id, i.request_id)
            ).fetchone()
            receipt = self._receipt(row)
            db.execute(
                "INSERT INTO outbox(id,topic,payload) VALUES (?,?,?)",
                (str(uuid4()), "refund.committed", json.dumps(receipt)),
            )
            return receipt

    @staticmethod
    def _receipt(row: sqlite3.Row) -> JsonObject:
        return {
            "tenant_id": str(row["tenant"]),
            "request_id": str(row["request_id"]),
            "order_id": str(row["order_id"]),
            "amount_cents": int(row["cents"]),
            "currency": str(row["currency"]),
        }

    def pending(self, limit: int) -> list[Event]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer in [1, 1000]")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT ?", (limit,)
            ).fetchall()
            return [Event(row["id"], row["topic"], json.loads(row["payload"])) for row in rows]

    def acknowledge(self, event_id: str) -> None:
        with self._transaction() as db:
            db.execute("UPDATE outbox SET delivered=1 WHERE id=?", (event_id,))
