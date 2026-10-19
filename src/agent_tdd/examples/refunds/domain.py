"""Use-case data. None of these types are required by the generic agent core."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from agent_tdd.context import RunContext
from agent_tdd.types import JsonObject


@dataclass(frozen=True)
class Approval:
    tenant_id: str
    actor_id: str
    request_id: str
    order_id: str
    expires_at: float


@dataclass(frozen=True)
class AuthorizedRefund:
    tenant_id: str
    actor_id: str
    request_id: str
    order_id: str
    approved_until: float


class ApprovalLookup(Protocol):
    def find(self, context: RunContext, order_id: str) -> Approval | None: ...


class RefundStore(Protocol):
    def commit(
        self,
        instruction: AuthorizedRefund,
        *,
        timeout_seconds: float,
        check_active: Callable[[], None],
    ) -> JsonObject: ...
