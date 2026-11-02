"""Exact FX conversion from recorded rates (multi-currency valuation).

A rate ``BASE/QUOTE = r`` means one unit of BASE costs ``r`` units of QUOTE.
Conversion uses Decimal arithmetic on integer units and rounds **toward
negative infinity** at the target scale, so valuations never overstate value.
Every conversion names the rate it used, so a valuation can always be
re-derived (State Law: decisions are explained by state).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from corei.domain.currencies import currency_scale
from corei.domain.units import RoundingMode, from_scaled, to_scaled


class FxError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FxRate:
    base: str
    quote: str
    rate_units: int
    rate_scale: int
    as_of_ns: int
    source: str

    def __post_init__(self) -> None:
        if self.base == self.quote:
            raise FxError("a rate needs two different currencies")
        if self.rate_units <= 0:
            raise FxError("rate must be positive")

    @property
    def value(self) -> Decimal:
        return from_scaled(self.rate_units, self.rate_scale)

    @property
    def pair(self) -> str:
        return f"{self.base}/{self.quote}"


@dataclass(frozen=True, slots=True)
class Conversion:
    units: int
    currency: str
    rates_used: tuple[FxRate, ...]


def _apply(amount: Decimal, frm: str, to: str, rate: FxRate) -> Decimal:
    if (rate.base, rate.quote) == (frm, to):
        return amount * rate.value
    if (rate.base, rate.quote) == (to, frm):
        return amount / rate.value
    raise FxError(f"rate {rate.pair} does not convert {frm}->{to}")


def _find(rates: Iterable[FxRate], a: str, b: str) -> FxRate | None:
    for r in rates:
        if {r.base, r.quote} == {a, b}:
            return r
    return None


def convert(
    units: int, frm: str, to: str, rates: Iterable[FxRate], pivot: str = "USD"
) -> Conversion:
    """Convert integer ``units`` of ``frm`` into ``to`` using direct, inverse or pivot rates."""
    rates = tuple(rates)
    frm_scale, to_scale = currency_scale(frm), currency_scale(to)
    if frm == to:
        return Conversion(units, to, ())
    amount = from_scaled(units, frm_scale)
    direct = _find(rates, frm, to)
    if direct is not None:
        used: tuple[FxRate, ...] = (direct,)
        value = _apply(amount, frm, to, direct)
    else:
        leg1 = _find(rates, frm, pivot) if pivot not in {frm, to} else None
        leg2 = _find(rates, pivot, to) if leg1 is not None else None
        if leg1 is None or leg2 is None:
            raise FxError(f"no rate path {frm}->{to} (direct or via {pivot})")
        value = _apply(_apply(amount, frm, pivot, leg1), pivot, to, leg2)
        used = (leg1, leg2)
    return Conversion(to_scaled(value, to_scale, RoundingMode.FLOOR), to, used)
