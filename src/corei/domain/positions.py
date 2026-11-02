"""Exact position accounting (finding F22).

A position is (signed quantity, signed open cost). Open cost is in account
currency units and has the same sign as the quantity: buying 10 for 1000 gives
(+10, +1000); selling short 10 for 1200 gives (-10, -1200).

A trade is (signed quantity delta ``dq``, signed cost delta ``dn``): a buy of
notional N is ``(+q, +N)``, a sell is ``(-q, -N)``.

Invariant, exact in integers for every sequence of trades:

    open_cost - realized_pnl == sum(dn)        (and cash moves by -sum(dn) - fees)

so no unit of money is created or lost by rounding: proportional splits of cost
use integer division and the remainder stays in the open position.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PositionState:
    quantity_units: int = 0
    open_cost_units: int = 0
    realized_pnl_units: int = 0

    def __post_init__(self) -> None:
        if (self.quantity_units == 0) != (self.open_cost_units == 0):
            raise ValueError("a flat position has zero cost and vice versa")
        if self.quantity_units * self.open_cost_units < 0:
            raise ValueError("open cost must have the same sign as quantity")


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def _part(total: int, part: int, whole: int) -> int:
    """total * part / whole, truncated toward zero (part <= whole, whole > 0)."""
    q = abs(total) * part // whole
    return q if total >= 0 else -q


def apply_trade(pos: PositionState, dq: int, dn: int) -> PositionState:
    if dq == 0:
        raise ValueError("trade quantity must be non-zero")
    if _sign(dn) != _sign(dq):
        raise ValueError("a buy adds positive cost, a sell negative cost")
    qty, cost, realized = pos.quantity_units, pos.open_cost_units, pos.realized_pnl_units

    if qty == 0 or _sign(qty) == _sign(dq):  # opening or adding
        return PositionState(qty + dq, cost + dn, realized)

    closing = min(abs(dq), abs(qty))
    cost_closed = _part(cost, closing, abs(qty))
    dn_closing = _part(dn, closing, abs(dq))
    realized -= cost_closed + dn_closing
    # Closing the whole position takes the whole cost (closing == |qty| makes _part exact),
    # so truncation remainders only ever stay inside a still-open position.
    qty += _sign(dq) * closing
    cost -= cost_closed
    rest = abs(dq) - closing
    if rest:  # crossed through flat into the opposite side
        qty += _sign(dq) * rest
        cost += dn - dn_closing
    return PositionState(qty, cost, realized)
