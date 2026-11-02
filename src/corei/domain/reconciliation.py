"""Reconciliation comparison and break lifecycle (findings F05, F23).

Legacy reconciliation compared ``broker_order_id == order_id``: empty input
matched (``None == None``) and every real pair mismatched. Here:

* Records are matched through a stored account-scoped id mapping.
* Missing mandatory observations produce ``UNKNOWN``, never ``MATCHED``.
* A break moves through a durable lifecycle; restart keeps it and its halt.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class CompareResult(StrEnum):
    MATCHED = "MATCHED"
    BREAK = "BREAK"
    UNKNOWN = "UNKNOWN"


class BreakState(StrEnum):
    DETECTED = "DETECTED"
    SCOPED_HALT = "SCOPED_HALT"
    INVESTIGATING = "INVESTIGATING"
    RESOLUTION_PROPOSED = "RESOLUTION_PROPOSED"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"


_BREAK_NEXT: dict[BreakState, frozenset[BreakState]] = {
    BreakState.DETECTED: frozenset({BreakState.SCOPED_HALT}),
    BreakState.SCOPED_HALT: frozenset({BreakState.INVESTIGATING}),
    BreakState.INVESTIGATING: frozenset({BreakState.RESOLUTION_PROPOSED}),
    BreakState.RESOLUTION_PROPOSED: frozenset({BreakState.APPROVED, BreakState.INVESTIGATING}),
    BreakState.APPROVED: frozenset({BreakState.APPLIED}),
    BreakState.APPLIED: frozenset({BreakState.VERIFIED, BreakState.INVESTIGATING}),
    BreakState.VERIFIED: frozenset({BreakState.CLOSED}),
    BreakState.CLOSED: frozenset(),
}


class InvalidBreakTransition(ValueError):
    pass


def advance_break(current: BreakState, target: BreakState) -> BreakState:
    if target not in _BREAK_NEXT[current]:
        raise InvalidBreakTransition(f"{current} -> {target} not allowed")
    return target


def break_holds_halt(state: BreakState) -> bool:
    """The scoped halt stays in force until the fix is verified."""
    return state not in {BreakState.DETECTED, BreakState.VERIFIED, BreakState.CLOSED}


@dataclass(frozen=True, slots=True)
class PositionComparison:
    instrument_id: str
    result: CompareResult
    internal_units: int | None
    broker_units: int | None


def compare_positions(
    internal: Mapping[str, int] | None,
    broker: Mapping[str, int] | None,
) -> list[PositionComparison]:
    """Compare signed position quantities per instrument at one cutoff.

    ``None`` for either side means that side was not observed: every
    instrument is ``UNKNOWN``, and an empty comparison is itself ``UNKNOWN``.
    """
    if internal is None or broker is None:
        known = set(internal or {}) | set(broker or {})
        if not known:
            return [PositionComparison("*", CompareResult.UNKNOWN, None, None)]
        return [
            PositionComparison(
                i,
                CompareResult.UNKNOWN,
                None if internal is None else internal.get(i, 0),
                None if broker is None else broker.get(i, 0),
            )
            for i in sorted(known)
        ]
    out = []
    for instrument in sorted(set(internal) | set(broker)):
        a, b = internal.get(instrument, 0), broker.get(instrument, 0)
        out.append(
            PositionComparison(
                instrument, CompareResult.MATCHED if a == b else CompareResult.BREAK, a, b
            )
        )
    return out
