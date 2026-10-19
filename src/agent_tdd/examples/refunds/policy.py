"""Centralized action-scoped approval policy; deterministic and independently tested."""

import math

from agent_tdd.context import RunContext
from agent_tdd.examples.refunds.domain import Approval, AuthorizedRefund
from agent_tdd.types import ToolError


class RefundPolicy:
    def authorize(
        self, context: RunContext, order_id: str, approval: Approval | None, now: float
    ) -> AuthorizedRefund:
        if approval is None:
            raise ToolError("approval_required")
        expected = (
            context.identity.tenant_id,
            context.identity.actor_id,
            context.request_id,
            order_id,
        )
        actual = (approval.tenant_id, approval.actor_id, approval.request_id, approval.order_id)
        if expected != actual:
            raise ToolError("forbidden")
        if not math.isfinite(approval.expires_at) or approval.expires_at <= now:
            raise ToolError("approval_expired")
        return AuthorizedRefund(*expected, approved_until=approval.expires_at)
