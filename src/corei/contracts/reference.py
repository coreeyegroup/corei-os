"""Instrument reference data (corrections C04, C05).

Units, steps and tradability live here — not in strategy code and not in a
hardcoded table. An index (NIFTY 50) is observable but not orderable; an order
must name a concrete tradable instrument (e.g. a specific futures contract).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import model_validator

from corei.contracts.base import (
    Contract,
    Currency,
    Ident,
    PosScaled,
    Scale,
    TimestampNs,
    Version,
)


class InstrumentKind(StrEnum):
    INDEX = "INDEX"
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX_SPOT = "FX_SPOT"
    CFD = "CFD"
    COMMODITY = "COMMODITY"


class OptionRight(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class InstrumentSpec(Contract):
    instrument_id: Ident
    version: Version
    venue: Ident
    symbol: str
    kind: InstrumentKind
    tradable: bool
    underlying_id: Ident | None = None
    expiry_ns: TimestampNs | None = None
    strike_units: PosScaled | None = None
    option_right: OptionRight | None = None

    price_scale: Scale
    tick_size_units: PosScaled
    quantity_scale: Scale
    quantity_step_units: PosScaled
    min_quantity_units: PosScaled
    max_quantity_units: PosScaled | None = None
    contract_multiplier_units: PosScaled
    multiplier_scale: Scale
    settlement_currency: Currency
    currency_scale: Scale

    provider_keys: dict[str, str] = {}
    valid_from_ns: TimestampNs
    valid_to_ns: TimestampNs | None = None

    @model_validator(mode="after")
    def _consistent(self) -> InstrumentSpec:
        if self.kind is InstrumentKind.INDEX and self.tradable:
            raise ValueError("an INDEX is an observation, not a tradable instrument (C05)")
        is_derivative = self.kind in {InstrumentKind.FUTURE, InstrumentKind.OPTION}
        if is_derivative and (self.expiry_ns is None or self.underlying_id is None):
            raise ValueError("derivatives need expiry_ns and underlying_id")
        if self.kind is InstrumentKind.OPTION and (
            self.strike_units is None or self.option_right is None
        ):
            raise ValueError("options need strike_units and option_right")
        if self.min_quantity_units % self.quantity_step_units:
            raise ValueError("min_quantity_units must be a multiple of quantity_step_units")
        if self.valid_to_ns is not None and self.valid_to_ns <= self.valid_from_ns:
            raise ValueError("valid_to_ns must be after valid_from_ns")
        return self
