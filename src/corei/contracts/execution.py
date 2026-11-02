"""Order lifecycle events, fills, portfolio, control and reconciliation contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from corei.contracts.base import (
    Contract,
    Ident,
    MoneyAmount,
    NonNegScaled,
    PosScaled,
    Scale,
    Scaled,
    TimestampNs,
    Version,
)
from corei.contracts.trading import Side
from corei.domain.control import ControlCommandType, ExecutionMode
from corei.domain.orders import OrderEventType, OrderStatus
from corei.domain.reconciliation import BreakState


class OrderLifecycleEvent(Contract):
    order_id: Ident
    account_id: Ident
    command_id: Ident
    sequence: int = Field(ge=1)
    event_type: OrderEventType
    from_status: OrderStatus
    to_status: OrderStatus
    broker_order_id: Ident | None = None
    reason: str | None = None
    at_ns: TimestampNs


class Fill(Contract):
    """One broker execution, deduplicated by (account, broker, execution id) (F21)."""

    fill_id: Ident
    account_id: Ident
    broker: Ident
    order_id: Ident
    broker_order_id: Ident
    broker_execution_id: Ident
    instrument_id: Ident
    side: Side
    quantity_units: PosScaled
    quantity_scale: Scale
    price_units: PosScaled
    price_scale: Scale
    fees: tuple[MoneyAmount, ...] = ()
    executed_at_ns: TimestampNs
    received_at_ns: TimestampNs
    mode: ExecutionMode


class Position(Contract):
    instrument_id: Ident
    quantity_units: Scaled  # signed: long > 0, short < 0
    quantity_scale: Scale
    average_price_units: NonNegScaled
    price_scale: Scale
    realized_pnl: MoneyAmount


class Reservation(Contract):
    reservation_id: Ident
    command_id: Ident
    amount: MoneyAmount
    expires_at_ns: TimestampNs


class PortfolioSnapshot(Contract):
    """Ledger projection for one account at a version (F22)."""

    account_id: Ident
    portfolio_id: Ident
    state_version: Version
    ledger_cutoff_sequence: int = Field(ge=0)
    as_of_ns: TimestampNs
    cash: tuple[MoneyAmount, ...]
    positions: tuple[Position, ...] = ()
    reservations: tuple[Reservation, ...] = ()
    mark_source: Ident | None = None

    @model_validator(mode="after")
    def _unique(self) -> PortfolioSnapshot:
        ids = [p.instrument_id for p in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate instrument in positions")
        ccy = [c.currency for c in self.cash]
        if len(ccy) != len(set(ccy)):
            raise ValueError("duplicate currency in cash balances")
        return self


class ControlStateContract(Contract):
    scope: Ident
    version: Version
    mode: ExecutionMode
    halted: bool
    halt_reason: str | None = None
    updated_at_ns: TimestampNs
    updated_by: Ident

    @model_validator(mode="after")
    def _halt_reason(self) -> ControlStateContract:
        if self.halted and not self.halt_reason:
            raise ValueError("a halted scope needs a reason")
        return self


class ControlCommandContract(Contract):
    command_id: Ident
    type: ControlCommandType
    scope: Ident
    expected_version: Version
    actor: Ident
    authorization_ref: Ident
    mode: ExecutionMode | None = None
    reason: str | None = None
    at_ns: TimestampNs


class BreakKind(StrEnum):
    POSITION = "POSITION"
    ORDER = "ORDER"
    FILL = "FILL"
    CASH = "CASH"
    MISSING_DATA = "MISSING_DATA"


class ReconciliationBreak(Contract):
    break_id: Ident
    account_id: Ident
    kind: BreakKind
    instrument_id: Ident | None = None
    cutoff_ns: TimestampNs
    internal_value: str | None
    broker_value: str | None
    state: BreakState
    halt_scope: Ident
    detected_at_ns: TimestampNs
    updated_at_ns: TimestampNs
