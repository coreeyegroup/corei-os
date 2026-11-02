"""Exact instrument-scaled arithmetic (correction C04, findings F18, F50).

Every financial value is an integer at an explicit decimal scale:
``value = units * 10**-scale``. Scales come from instrument reference data
(tick size, quantity step, contract multiplier, currency scale) — never from a
platform-wide "paise and lots" assumption.

Conversions are exact by default. Rounding happens only where a rule names a
direction, and quantity rounding is always *down* to a valid step: rounding a
quantity up can exceed an approved risk budget (F18).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import StrEnum

MAX_SCALE = 18


class UnitsError(ValueError):
    """A value cannot be represented exactly at the requested scale."""


class RoundingMode(StrEnum):
    FLOOR = "FLOOR"
    CEILING = "CEILING"


def _check_scale(scale: int) -> None:
    if not 0 <= scale <= MAX_SCALE:
        raise UnitsError(f"scale must be in [0, {MAX_SCALE}], got {scale}")


def to_scaled(value: str | int | Decimal, scale: int, rounding: RoundingMode | None = None) -> int:
    """Convert a decimal value to an integer at ``scale``.

    Raises ``UnitsError`` if the value is not exactly representable and no
    rounding mode is given. Binary floats are rejected outright.
    """
    _check_scale(scale)
    if isinstance(value, float):  # runtime guard: callers may ignore the type hints
        raise UnitsError("binary floats are not accepted for financial values")
    try:
        dec = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise UnitsError(f"not a decimal value: {value!r}") from exc
    if not dec.is_finite():
        raise UnitsError(f"non-finite value: {value!r}")
    shifted = dec.scaleb(scale)
    if shifted == shifted.to_integral_value():
        return int(shifted)
    if rounding is None:
        raise UnitsError(f"{value} is not representable at scale {scale}")
    mode = ROUND_FLOOR if rounding is RoundingMode.FLOOR else ROUND_CEILING
    return int(shifted.to_integral_value(rounding=mode))


def from_scaled(units: int, scale: int) -> Decimal:
    """Exact Decimal for an integer at ``scale``."""
    _check_scale(scale)
    return Decimal(units).scaleb(-scale)


def rescale(
    units: int, from_scale: int, to_scale: int, rounding: RoundingMode | None = None
) -> int:
    """Move an integer between scales exactly (or with an explicit rounding)."""
    return to_scaled(from_scaled(units, from_scale), to_scale, rounding)


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in one currency at an explicit scale."""

    units: int
    currency: str
    scale: int

    def __post_init__(self) -> None:
        _check_scale(self.scale)
        if len(self.currency) != 3 or not self.currency.isupper():
            raise UnitsError(f"currency must be ISO-4217 uppercase, got {self.currency!r}")

    def __add__(self, other: Money) -> Money:
        self._same_unit(other)
        return Money(self.units + other.units, self.currency, self.scale)

    def __sub__(self, other: Money) -> Money:
        self._same_unit(other)
        return Money(self.units - other.units, self.currency, self.scale)

    def _same_unit(self, other: Money) -> None:
        if (self.currency, self.scale) != (other.currency, other.scale):
            raise UnitsError(
                f"cannot combine {self.currency}@{self.scale} with {other.currency}@{other.scale};"
                " convert explicitly using a recorded FX snapshot"
            )

    @property
    def decimal(self) -> Decimal:
        return from_scaled(self.units, self.scale)


def align_price_to_tick(price_units: int, tick_units: int, rounding: RoundingMode) -> int:
    """Align a price (already at the instrument's price scale) to a tick multiple."""
    if tick_units <= 0:
        raise UnitsError("tick size must be positive")
    q, r = divmod(price_units, tick_units)
    if r == 0:
        return price_units
    return (q if rounding is RoundingMode.FLOOR else q + 1) * tick_units


@dataclass(frozen=True, slots=True)
class QuantityDecision:
    """Result of fitting a desired quantity to an instrument's quantity rules."""

    quantity_units: int
    rejected: bool
    reason: str | None = None


def fit_quantity(
    desired_units: int, step_units: int, min_units: int, max_units: int | None = None
) -> QuantityDecision:
    """Floor a desired quantity to a valid step; reject if below the minimum.

    Never rounds *up* to the minimum (legacy ``ApplyBrokerConstraints`` did, F18):
    a quantity below the minimum tradable size is a rejection, because the
    minimum lot may carry more risk than was approved.
    """
    if step_units <= 0 or min_units <= 0:
        raise UnitsError("quantity step and minimum must be positive")
    if min_units % step_units != 0:
        raise UnitsError("minimum quantity must be a multiple of the quantity step")
    if desired_units <= 0:
        return QuantityDecision(0, True, "NON_POSITIVE_QUANTITY")
    floored = (desired_units // step_units) * step_units
    if max_units is not None and floored > max_units:
        floored = (max_units // step_units) * step_units
    if floored < min_units:
        return QuantityDecision(0, True, "BELOW_MINIMUM_QUANTITY")
    return QuantityDecision(floored, False, None)
