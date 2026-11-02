from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from corei.domain.orders import (
    InvalidTransition,
    OrderEvent,
    OrderEventType,
    OrderInvariantError,
    OrderState,
    OrderStatus,
    apply,
)

T = OrderEventType


def ev(kind: OrderEventType, **kw: object) -> OrderEvent:
    return OrderEvent(type=kind, at_ns=1, **kw)  # type: ignore[arg-type]


def reserved(qty: int = 150) -> OrderState:
    s = OrderState(order_id="ord_1", quantity_units=qty)
    for k in (T.RISK_APPROVE, T.POLICY_APPROVE, T.RESERVE):
        s = apply(s, ev(k))
    assert s.status is OrderStatus.RESERVED
    return s


def acked(qty: int = 150) -> OrderState:
    s = apply(reserved(qty), ev(T.SUBMIT))
    return apply(s, ev(T.BROKER_ACK, broker_order_id="B1"))


def test_happy_path_to_filled() -> None:
    s = acked()
    s = apply(s, ev(T.FILL, execution_id="E1", fill_quantity_units=75))
    assert s.status is OrderStatus.PARTIALLY_FILLED and s.remaining_units == 75
    s = apply(s, ev(T.FILL, execution_id="E2", fill_quantity_units=75))
    assert s.status is OrderStatus.FILLED


def test_cannot_skip_risk_policy_or_reservation() -> None:
    with pytest.raises(InvalidTransition):
        apply(OrderState(order_id="o", quantity_units=1), ev(T.SUBMIT))
    with pytest.raises(InvalidTransition):
        apply(OrderState(order_id="o", quantity_units=1), ev(T.RESERVE))


def test_timeout_is_unknown_not_rejected_c02() -> None:
    s = apply(apply(reserved(), ev(T.SUBMIT)), ev(T.SUBMIT_TIMEOUT))
    assert s.status is OrderStatus.SUBMISSION_UNKNOWN
    assert not s.may_submit
    with pytest.raises(InvalidTransition):
        apply(s, ev(T.SUBMIT))  # no blind resubmission


def test_unknown_resolves_by_reconciliation() -> None:
    unknown = apply(apply(reserved(), ev(T.SUBMIT)), ev(T.SUBMIT_TIMEOUT))
    found = apply(unknown, ev(T.UNKNOWN_RESOLVED_FOUND, broker_order_id="B9"))
    assert found.status is OrderStatus.ACKNOWLEDGED and found.broker_order_id == "B9"
    absent = apply(unknown, ev(T.UNKNOWN_RESOLVED_ABSENT))
    assert absent.may_submit and absent.submit_attempts == 1


def test_fill_can_arrive_while_unknown() -> None:
    unknown = apply(apply(reserved(), ev(T.SUBMIT)), ev(T.SUBMIT_TIMEOUT))
    s = apply(unknown, ev(T.FILL, execution_id="E1", fill_quantity_units=150, broker_order_id="B1"))
    assert s.status is OrderStatus.FILLED and s.broker_order_id == "B1"


def test_duplicate_fill_applies_once_f21_f22() -> None:
    s = acked()
    once = apply(s, ev(T.FILL, execution_id="E1", fill_quantity_units=75))
    twice = apply(once, ev(T.FILL, execution_id="E1", fill_quantity_units=75))
    assert twice == once and twice.filled_units == 75


def test_late_fill_after_cancel_is_applied_not_discarded() -> None:
    s = apply(acked(), ev(T.CANCEL_REQUEST))
    s = apply(s, ev(T.CANCEL_ACK))
    assert s.status is OrderStatus.CANCELLED
    s = apply(s, ev(T.FILL, execution_id="E7", fill_quantity_units=75))
    assert s.status is OrderStatus.CANCELLED
    assert s.filled_units == 75 and s.late_fill_units == 75


def test_fill_during_cancel_request_keeps_cancel_pending() -> None:
    s = apply(acked(), ev(T.CANCEL_REQUEST))
    s = apply(s, ev(T.FILL, execution_id="E1", fill_quantity_units=75))
    assert s.status is OrderStatus.CANCEL_REQUESTED
    s = apply(s, ev(T.CANCEL_REJECT))
    assert s.status is OrderStatus.PARTIALLY_FILLED


def test_overfill_raises_for_reconciliation() -> None:
    with pytest.raises(OrderInvariantError):
        apply(acked(150), ev(T.FILL, execution_id="E1", fill_quantity_units=151))


def test_fill_requires_execution_id_and_live_order() -> None:
    with pytest.raises(InvalidTransition):
        apply(acked(), ev(T.FILL, fill_quantity_units=1))
    with pytest.raises(InvalidTransition):
        apply(reserved(), ev(T.FILL, execution_id="E1", fill_quantity_units=1))


def test_terminal_states_reject_new_lifecycle_events() -> None:
    rejected = apply(apply(reserved(), ev(T.SUBMIT)), ev(T.BROKER_REJECT, reason="margin"))
    assert rejected.status is OrderStatus.REJECTED
    for kind in (T.BROKER_ACK, T.CANCEL_REQUEST, T.SUBMIT):
        with pytest.raises(InvalidTransition):
            apply(rejected, ev(kind))


@given(
    qty=st.integers(min_value=1, max_value=1_000),
    deliveries=st.lists(
        st.tuples(st.sampled_from(["E1", "E2", "E3", "E4"]), st.integers(1, 400)), max_size=20
    ),
)
def test_property_filled_never_exceeds_quantity_and_redelivery_is_idempotent(
    qty: int, deliveries: list[tuple[str, int]]
) -> None:
    s = acked(qty)
    first_qty: dict[str, int] = {}
    for exec_id, proposed in deliveries:
        q = first_qty.setdefault(exec_id, proposed)  # a redelivery carries the same quantity
        try:
            s = apply(s, ev(T.FILL, execution_id=exec_id, fill_quantity_units=q))
        except OrderInvariantError:
            continue
        except InvalidTransition:
            break  # order became FILLED and terminal
        assert 0 < s.filled_units <= qty
    again = s
    for exec_id in s.applied_execution_ids:
        try:
            again = apply(again, ev(T.FILL, execution_id=exec_id, fill_quantity_units=1))
        except InvalidTransition:
            again = replace(again)  # FILLED: terminal for duplicates of filled orders too
    assert again.filled_units == s.filled_units
