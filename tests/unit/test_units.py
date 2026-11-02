from decimal import Decimal

import pytest

from corei.domain.units import (
    Money,
    RoundingMode,
    UnitsError,
    align_price_to_tick,
    fit_quantity,
    from_scaled,
    rescale,
    to_scaled,
)


def test_exact_conversion_roundtrips() -> None:
    assert to_scaled("2350.10", 2) == 235010
    assert from_scaled(235010, 2) == Decimal("2350.10")
    assert to_scaled(5, 0) == 5


def test_inexact_value_is_rejected_without_explicit_rounding() -> None:
    with pytest.raises(UnitsError):
        to_scaled("2350.105", 2)
    assert to_scaled("2350.105", 2, RoundingMode.FLOOR) == 235010
    assert to_scaled("2350.105", 2, RoundingMode.CEILING) == 235011


def test_binary_float_is_rejected() -> None:
    with pytest.raises(UnitsError, match="float"):
        to_scaled(0.1, 2)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "abc"])
def test_non_finite_or_garbage_is_rejected(bad: str) -> None:
    with pytest.raises(UnitsError):
        to_scaled(bad, 2)


def test_rescale() -> None:
    assert rescale(235010, 2, 4) == 23501000
    with pytest.raises(UnitsError):
        rescale(235011, 2, 1)


def test_money_refuses_mixed_currency_or_scale() -> None:
    inr = Money(100, "INR", 2)
    assert (inr + Money(50, "INR", 2)).units == 150
    with pytest.raises(UnitsError):
        _ = inr + Money(1, "USD", 2)
    with pytest.raises(UnitsError):
        _ = inr + Money(1, "INR", 4)


def test_tick_alignment() -> None:
    assert align_price_to_tick(1007, 5, RoundingMode.FLOOR) == 1005
    assert align_price_to_tick(1007, 5, RoundingMode.CEILING) == 1010
    assert align_price_to_tick(1010, 5, RoundingMode.FLOOR) == 1010


def test_quantity_is_floored_never_raised_to_minimum_f18() -> None:
    # Legacy ApplyBrokerConstraints returned minLot for anything below it.
    below = fit_quantity(desired_units=40, step_units=75, min_units=75)
    assert below.rejected and below.reason == "BELOW_MINIMUM_QUANTITY"
    assert below.quantity_units == 0

    ok = fit_quantity(desired_units=200, step_units=75, min_units=75)
    assert not ok.rejected and ok.quantity_units == 150

    capped = fit_quantity(desired_units=10_000, step_units=75, min_units=75, max_units=1_000)
    assert capped.quantity_units == 975


def test_quantity_rules_must_be_consistent() -> None:
    with pytest.raises(UnitsError):
        fit_quantity(100, step_units=75, min_units=100)
