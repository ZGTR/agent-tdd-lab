"""Use-case controller: validate input, obtain trusted approval, apply policy, call store."""

from agent_tdd.context import Clock, RunContext
from agent_tdd.examples.refunds.domain import ApprovalLookup, RefundStore
from agent_tdd.examples.refunds.policy import RefundPolicy
from agent_tdd.types import JsonObject, JsonValue, ToolError


class RefundTool:
    def __init__(
        self, store: RefundStore, approvals: ApprovalLookup, policy: RefundPolicy, clock: Clock
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._policy = policy
        self._clock = clock

    def execute(
        self, arguments: JsonObject, context: RunContext, *, timeout_seconds: float
    ) -> JsonValue:
        if context.cancelled.is_set() or timeout_seconds <= 0:
            raise ToolError("execution_stopped")
        order_id = arguments.get("order_id")
        if set(arguments) != {"order_id"} or not isinstance(order_id, str):
            raise ToolError("invalid_arguments")
        if not order_id.strip() or len(order_id) > 200:
            raise ToolError("invalid_arguments")
        approval = self._approvals.find(context, order_id)
        instruction = self._policy.authorize(context, order_id, approval, self._clock.time())

        def check_active() -> None:
            if context.cancelled.is_set() or context.budget.remaining_seconds() <= 0:
                raise ToolError("execution_stopped")

        check_active()
        return self._store.commit(
            instruction,
            timeout_seconds=min(timeout_seconds, context.budget.remaining_seconds()),
            check_active=check_active,
        )
