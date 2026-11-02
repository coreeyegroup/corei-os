"""Signals, trade intents, risk/policy decisions and approved order commands.

One canonical intent (F17): an executable ``side`` plus an explicit
``purpose`` (open / reduce / close) instead of LONG/SHORT that downstream code
must guess. Account, origin, mode and capital partition travel with every
command so per-account fan-out can't collapse (F03, F19, F39).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from corei.contracts.base import (
    Bps,
    Contract,
    Ident,
    PosScaled,
    Scale,
    TimestampNs,
    Version,
)
from corei.domain.control import ExecutionMode


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Purpose(StrEnum):
    OPEN = "OPEN"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


class Origin(StrEnum):
    CORE = "CORE"
    EXTERNAL = "EXTERNAL"  # only via CEIL validation


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"
    IOC = "IOC"


class FeatureRef(Contract):
    feature_name: Ident
    feature_version: Ident
    as_of_ns: TimestampNs


class Signal(Contract):
    signal_id: Ident
    strategy_id: Ident
    strategy_version: Ident
    instrument_id: Ident
    direction: Direction
    strength_bps: Bps
    as_of_ns: TimestampNs
    expires_at_ns: TimestampNs
    inputs: tuple[FeatureRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _expiry(self) -> Signal:
        if self.expires_at_ns <= self.as_of_ns:
            raise ValueError("expires_at_ns must be after as_of_ns")
        return self


class TradeIntent(Contract):
    intent_id: Ident
    origin: Origin
    source_ref: Ident  # strategy id for CORE, CEIL source id for EXTERNAL
    capital_partition: Ident
    instrument_id: Ident  # must resolve to a tradable InstrumentSpec
    side: Side
    purpose: Purpose
    strategy_id: Ident
    strategy_version: Ident
    allowed_accounts: tuple[Ident, ...] = Field(min_length=1)
    signal_ids: tuple[Ident, ...] = ()
    as_of_ns: TimestampNs
    expires_at_ns: TimestampNs

    @model_validator(mode="after")
    def _checks(self) -> TradeIntent:
        if self.expires_at_ns <= self.as_of_ns:
            raise ValueError("expires_at_ns must be after as_of_ns")
        if len(set(self.allowed_accounts)) != len(self.allowed_accounts):
            raise ValueError("allowed_accounts contains duplicates")
        if self.origin is Origin.CORE and not self.signal_ids:
            raise ValueError("a CORE intent must cite the signals it came from")
        return self


class Decision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RiskDecision(Contract):
    risk_decision_id: Ident
    intent_id: Ident
    account_id: Ident
    decision: Decision
    reason_codes: tuple[Ident, ...] = ()
    portfolio_state_version: Version
    risk_config_version: Ident
    max_quantity_units: PosScaled | None = None
    as_of_ns: TimestampNs

    @model_validator(mode="after")
    def _reasons(self) -> RiskDecision:
        if self.decision is Decision.REJECTED and not self.reason_codes:
            raise ValueError("a rejection needs at least one reason code")
        return self


class PolicyDecision(Contract):
    policy_decision_id: Ident
    intent_id: Ident
    account_id: Ident
    decision: Decision
    reason_codes: tuple[Ident, ...] = ()
    policy_version: Ident
    as_of_ns: TimestampNs

    @model_validator(mode="after")
    def _reasons(self) -> PolicyDecision:
        if self.decision is Decision.REJECTED and not self.reason_codes:
            raise ValueError("a rejection needs at least one reason code")
        return self


class ApprovedOrderCommand(Contract):
    """The only thing that may reach a broker adapter (F06)."""

    command_id: Ident
    client_order_id: Ident  # broker-facing idempotency key, derived from command_id
    account_id: Ident
    portfolio_id: Ident
    mode: ExecutionMode
    origin: Origin
    capital_partition: Ident
    intent_id: Ident
    strategy_id: Ident
    strategy_version: Ident
    instrument_id: Ident
    instrument_version: Version
    side: Side
    purpose: Purpose
    order_type: OrderType
    time_in_force: TimeInForce
    quantity_units: PosScaled
    quantity_scale: Scale
    limit_price_units: PosScaled | None = None
    price_scale: Scale
    reservation_id: Ident
    risk_decision_id: Ident
    policy_decision_id: Ident
    portfolio_state_version: Version
    control_fence: tuple[tuple[str, int], ...] = Field(min_length=1)
    config_version: Ident
    as_of_ns: TimestampNs
    expires_at_ns: TimestampNs

    @model_validator(mode="after")
    def _checks(self) -> ApprovedOrderCommand:
        if self.mode is ExecutionMode.DISABLED:
            raise ValueError("no order command can exist in mode DISABLED")
        if (self.order_type is OrderType.LIMIT) != (self.limit_price_units is not None):
            raise ValueError("LIMIT orders need a limit price; MARKET orders must not have one")
        if self.expires_at_ns <= self.as_of_ns:
            raise ValueError("expires_at_ns must be after as_of_ns")
        return self
