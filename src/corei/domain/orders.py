"""Order lifecycle reducer (findings F19, F20, F21; corrections C02, C03).

The OMS owns this state. It is a pure reducer: ``apply(state, event) -> state``.
Persistence, idempotent commands and broker I/O wrap it in later deliveries.

Key rules:

* A submission timeout is **not** a rejection (C02). It moves the order to
  ``SUBMISSION_UNKNOWN``, which blocks resubmission until reconciliation either
  finds the order at the broker or confirms the broker never received it.
* Fills are deduplicated by broker execution id; a duplicate is a no-op.
* Fills after a cancel/expiry acknowledgement are applied (late fills), never
  discarded, because an accepted order can still execute at the broker.
* Over-fills are never silently applied; they raise ``OrderInvariantError``,
  which the caller turns into a reconciliation break.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum


class OrderStatus(StrEnum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    POLICY_APPROVED = "POLICY_APPROVED"
    RESERVED = "RESERVED"
    PENDING_SUBMISSION = "PENDING_SUBMISSION"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED}
)
# States from which the broker may still produce executions for this order.
LIVE_AT_BROKER = frozenset(
    {
        OrderStatus.SUBMISSION_UNKNOWN,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCEL_REQUESTED,
    }
)
# Terminal states that may still receive a late execution report.
LATE_FILL_ALLOWED = frozenset({OrderStatus.CANCELLED, OrderStatus.EXPIRED})


class OrderEventType(StrEnum):
    RISK_APPROVE = "RISK_APPROVE"
    POLICY_APPROVE = "POLICY_APPROVE"
    RESERVE = "RESERVE"
    SUBMIT = "SUBMIT"
    SUBMIT_TIMEOUT = "SUBMIT_TIMEOUT"
    BROKER_ACK = "BROKER_ACK"
    BROKER_REJECT = "BROKER_REJECT"
    UNKNOWN_RESOLVED_FOUND = "UNKNOWN_RESOLVED_FOUND"
    UNKNOWN_RESOLVED_ABSENT = "UNKNOWN_RESOLVED_ABSENT"
    FILL = "FILL"
    CANCEL_REQUEST = "CANCEL_REQUEST"
    CANCEL_ACK = "CANCEL_ACK"
    CANCEL_REJECT = "CANCEL_REJECT"
    EXPIRE = "EXPIRE"
    REJECT = "REJECT"


class InvalidTransition(ValueError):
    pass


class OrderInvariantError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OrderEvent:
    type: OrderEventType
    at_ns: int
    broker_order_id: str | None = None
    execution_id: str | None = None
    fill_quantity_units: int = 0
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class OrderState:
    order_id: str
    quantity_units: int
    status: OrderStatus = OrderStatus.PROPOSED
    filled_units: int = 0
    broker_order_id: str | None = None
    applied_execution_ids: frozenset[str] = field(default_factory=frozenset)
    submit_attempts: int = 0
    late_fill_units: int = 0
    last_event_ns: int = 0

    @property
    def remaining_units(self) -> int:
        return self.quantity_units - self.filled_units

    @property
    def may_submit(self) -> bool:
        """Only a reserved order may be sent; never one in SUBMISSION_UNKNOWN."""
        return self.status is OrderStatus.RESERVED


_SIMPLE: dict[tuple[OrderStatus, OrderEventType], OrderStatus] = {
    (OrderStatus.PROPOSED, OrderEventType.RISK_APPROVE): OrderStatus.RISK_APPROVED,
    (OrderStatus.RISK_APPROVED, OrderEventType.POLICY_APPROVE): OrderStatus.POLICY_APPROVED,
    (OrderStatus.POLICY_APPROVED, OrderEventType.RESERVE): OrderStatus.RESERVED,
    (OrderStatus.PENDING_SUBMISSION, OrderEventType.SUBMIT_TIMEOUT): OrderStatus.SUBMISSION_UNKNOWN,
    (OrderStatus.PENDING_SUBMISSION, OrderEventType.BROKER_ACK): OrderStatus.ACKNOWLEDGED,
    (OrderStatus.PENDING_SUBMISSION, OrderEventType.BROKER_REJECT): OrderStatus.REJECTED,
    (
        OrderStatus.SUBMISSION_UNKNOWN,
        OrderEventType.UNKNOWN_RESOLVED_FOUND,
    ): OrderStatus.ACKNOWLEDGED,
    # Confirmed never received: capacity is still reserved, order may be resent.
    (OrderStatus.SUBMISSION_UNKNOWN, OrderEventType.UNKNOWN_RESOLVED_ABSENT): OrderStatus.RESERVED,
    (OrderStatus.ACKNOWLEDGED, OrderEventType.CANCEL_REQUEST): OrderStatus.CANCEL_REQUESTED,
    (OrderStatus.PARTIALLY_FILLED, OrderEventType.CANCEL_REQUEST): OrderStatus.CANCEL_REQUESTED,
    (OrderStatus.CANCEL_REQUESTED, OrderEventType.CANCEL_ACK): OrderStatus.CANCELLED,
    (OrderStatus.ACKNOWLEDGED, OrderEventType.EXPIRE): OrderStatus.EXPIRED,
    (OrderStatus.PARTIALLY_FILLED, OrderEventType.EXPIRE): OrderStatus.EXPIRED,
    (OrderStatus.ACKNOWLEDGED, OrderEventType.BROKER_REJECT): OrderStatus.REJECTED,
}
_PRE_SUBMIT = (
    OrderStatus.PROPOSED,
    OrderStatus.RISK_APPROVED,
    OrderStatus.POLICY_APPROVED,
    OrderStatus.RESERVED,
)
for _s in _PRE_SUBMIT:
    _SIMPLE[(_s, OrderEventType.REJECT)] = OrderStatus.REJECTED
    _SIMPLE[(_s, OrderEventType.EXPIRE)] = OrderStatus.EXPIRED


def _after_fill_status(state: OrderState, filled: int) -> OrderStatus:
    if state.status in LATE_FILL_ALLOWED:
        return state.status
    if filled == state.quantity_units:
        return OrderStatus.FILLED
    if state.status is OrderStatus.CANCEL_REQUESTED:
        return OrderStatus.CANCEL_REQUESTED
    return OrderStatus.PARTIALLY_FILLED


def _apply_fill(state: OrderState, event: OrderEvent) -> OrderState:
    if not event.execution_id:
        raise InvalidTransition("FILL requires a broker execution_id")
    if event.execution_id in state.applied_execution_ids:
        return state  # duplicate delivery: applied once only
    if event.fill_quantity_units <= 0:
        raise InvalidTransition("fill quantity must be positive")
    if state.status not in LIVE_AT_BROKER | LATE_FILL_ALLOWED:
        raise InvalidTransition(f"FILL not possible in {state.status}")
    filled = state.filled_units + event.fill_quantity_units
    if filled > state.quantity_units:
        raise OrderInvariantError(
            f"over-fill on {state.order_id}: {filled} > {state.quantity_units}"
        )
    late = event.fill_quantity_units if state.status in LATE_FILL_ALLOWED else 0
    return replace(
        state,
        status=_after_fill_status(state, filled),
        filled_units=filled,
        applied_execution_ids=state.applied_execution_ids | {event.execution_id},
        late_fill_units=state.late_fill_units + late,
        broker_order_id=state.broker_order_id or event.broker_order_id,
        last_event_ns=event.at_ns,
    )


def apply(state: OrderState, event: OrderEvent) -> OrderState:
    """Apply one event; raise ``InvalidTransition`` for anything not allowed."""
    if event.type is OrderEventType.FILL:
        return _apply_fill(state, event)

    if event.type is OrderEventType.SUBMIT:
        if not state.may_submit:
            raise InvalidTransition(f"SUBMIT not allowed in {state.status}")
        return replace(
            state,
            status=OrderStatus.PENDING_SUBMISSION,
            submit_attempts=state.submit_attempts + 1,
            last_event_ns=event.at_ns,
        )

    if event.type is OrderEventType.CANCEL_REJECT and state.status is OrderStatus.CANCEL_REQUESTED:
        restored = (
            OrderStatus.PARTIALLY_FILLED if state.filled_units > 0 else OrderStatus.ACKNOWLEDGED
        )
        return replace(state, status=restored, last_event_ns=event.at_ns)

    target = _SIMPLE.get((state.status, event.type))
    if target is None:
        raise InvalidTransition(f"{event.type} not allowed in {state.status}")
    return replace(
        state,
        status=target,
        broker_order_id=state.broker_order_id or event.broker_order_id,
        last_event_ns=event.at_ns,
    )
