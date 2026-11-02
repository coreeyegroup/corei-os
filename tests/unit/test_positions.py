import pytest
from hypothesis import given
from hypothesis import strategies as st

from corei.domain.positions import PositionState, apply_trade


def test_long_round_trip() -> None:
    p = apply_trade(PositionState(), 10, 1000)  # buy 10 @ 100
    p = apply_trade(p, -4, -480)  # sell 4 @ 120
    assert (p.quantity_units, p.open_cost_units, p.realized_pnl_units) == (6, 600, 80)
    p = apply_trade(p, -6, -540)  # sell 6 @ 90
    assert (p.quantity_units, p.open_cost_units, p.realized_pnl_units) == (0, 0, 20)


def test_short_round_trip() -> None:
    p = apply_trade(PositionState(), -10, -1200)  # short 10 @ 120
    p = apply_trade(p, 4, 400)  # cover 4 @ 100
    assert (p.quantity_units, p.open_cost_units, p.realized_pnl_units) == (-6, -720, 80)


def test_crossing_through_flat_opens_opposite_side() -> None:
    p = apply_trade(PositionState(), 10, 1000)
    p = apply_trade(p, -15, -1650)  # sell 15 @ 110: close 10 (+100), open short 5 @ 110
    assert (p.quantity_units, p.open_cost_units, p.realized_pnl_units) == (-5, -550, 100)


def test_rounding_remainder_stays_in_position_or_realized() -> None:
    p = apply_trade(PositionState(), 3, 100)  # cost 33.33.. per unit
    p = apply_trade(p, -1, -40)
    assert p.open_cost_units - p.realized_pnl_units == 100 - 40
    p = apply_trade(p, -2, -80)
    assert p.quantity_units == 0 and p.open_cost_units == 0
    assert p.realized_pnl_units == 120 - 100


@pytest.mark.parametrize(("dq", "dn"), [(0, 0), (5, -10), (-5, 10)])
def test_invalid_trades(dq: int, dn: int) -> None:
    with pytest.raises(ValueError):
        apply_trade(PositionState(), dq, dn)


trades = st.lists(
    st.tuples(st.integers(1, 500), st.integers(1, 10_000), st.booleans()), min_size=1, max_size=40
)


@given(trades)
def test_property_money_is_conserved_exactly(seq: list[tuple[int, int, bool]]) -> None:
    p = PositionState()
    total_dn = 0
    for qty, price, buy in seq:
        dq, dn = (qty, qty * price) if buy else (-qty, -qty * price)
        p = apply_trade(p, dq, dn)
        total_dn += dn
        assert p.open_cost_units - p.realized_pnl_units == total_dn
        assert (p.quantity_units == 0) == (p.open_cost_units == 0)
    assert p.quantity_units == sum(q if b else -q for q, _, b in seq)
