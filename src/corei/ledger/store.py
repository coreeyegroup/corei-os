"""Multi-currency durable ledger (findings F04, F21, F22; Anchor Law multi-currency).

All functions run inside the caller's transaction, so a fill, its journal
entries, the projections and any outbox event commit together or not at all.

* Cash exists per (account, currency). A fill settles in its instrument's
  settlement currency (the quote currency for FX: EURUSD settles in USD,
  USDJPY in JPY). Fees settle in whatever currency the broker charged.
* Fills are applied once per (account, broker, broker execution id), and only
  from a broker the account is bound to (also enforced by a foreign key).
* An account's ledger kind (BACKTEST / PAPER / LIVE) must match the mode of
  every fill and reservation: paper money never mixes with live money.
* Reservations lock the account row, so parallel requests never over-reserve;
  a stale portfolio ``state_version`` is refused (C12).
* Valuation converts every balance and position into the account's base
  currency using recorded FX rates and caller-supplied marks. A missing or
  stale rate or mark makes the valuation INCOMPLETE — it never guesses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

import psycopg

from corei.contracts.base import MoneyAmount
from corei.contracts.execution import Fill, PortfolioSnapshot, Position, Reservation
from corei.contracts.reference import InstrumentSpec, LedgerKind
from corei.contracts.trading import Side
from corei.domain.control import ExecutionMode
from corei.domain.currencies import UnknownCurrency, currency_scale
from corei.domain.fx import FxError, FxRate, convert
from corei.domain.identity import canonical_hash, derive_id
from corei.domain.positions import PositionState, apply_trade
from corei.domain.units import Money, RoundingMode, from_scaled, rescale, to_scaled
from corei.reference.store import Account, ReferenceError, get_account, instrument_at

LEDGER_FOR_MODE: dict[ExecutionMode, LedgerKind] = {
    ExecutionMode.BACKTEST: LedgerKind.BACKTEST,
    ExecutionMode.PAPER: LedgerKind.PAPER,
    ExecutionMode.LIVE_SEMI: LedgerKind.LIVE,
    ExecutionMode.LIVE_AUTO: LedgerKind.LIVE,
}


class LedgerError(ValueError):
    pass


class FillConflict(LedgerError):
    """Same broker execution id, different content: a reconciliation problem, not a duplicate."""


def _account(conn: psycopg.Connection, account_id: str, lock: bool = False) -> Account:
    try:
        return get_account(conn, account_id, lock=lock)
    except ReferenceError as exc:
        raise LedgerError(str(exc)) from exc


def _mode_matches(account: Account, mode: ExecutionMode) -> bool:
    return LEDGER_FOR_MODE.get(mode) is account.ledger_kind


def _check_money(money: Money) -> None:
    try:
        registered = currency_scale(money.currency)
    except UnknownCurrency as exc:
        raise LedgerError(str(exc)) from exc
    if money.scale != registered:
        raise LedgerError(f"{money.currency} must use scale {registered}, got {money.scale}")


def _bump(conn: psycopg.Connection, account_id: str) -> int:
    row = conn.execute(
        "UPDATE accounts SET state_version = state_version + 1 WHERE account_id = %s RETURNING state_version",
        (account_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _journal(
    conn: psycopg.Connection,
    *,
    entry_id: str,
    account_id: str,
    kind: str,
    money: Money,
    at_ns: int,
    instrument_id: str | None = None,
    quantity_delta: int | None = None,
    fill_id: str | None = None,
) -> None:
    _check_money(money)
    conn.execute(
        """INSERT INTO ledger_entries (entry_id, account_id, kind, currency, currency_scale,
               cash_delta_units, instrument_id, quantity_delta_units, fill_id, at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            entry_id,
            account_id,
            kind,
            money.currency,
            money.scale,
            money.units,
            instrument_id,
            quantity_delta,
            fill_id,
            at_ns,
        ),
    )
    conn.execute(
        """INSERT INTO cash_balances (account_id, currency, currency_scale, balance_units)
           VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, account_id, currency)
           DO UPDATE SET balance_units = cash_balances.balance_units + EXCLUDED.balance_units""",
        (account_id, money.currency, money.scale, money.units),
    )


# ---------------------------------------------------------------- cash


def deposit(
    conn: psycopg.Connection, *, account_id: str, entry_id: str, amount: Money, at_ns: int
) -> bool:
    """Add cash in any registered currency. Idempotent by ``entry_id``."""
    if amount.units <= 0:
        raise LedgerError("deposit must be positive")
    _check_money(amount)
    _account(conn, account_id, lock=True)
    prior = conn.execute(
        "SELECT account_id, currency, cash_delta_units FROM ledger_entries WHERE entry_id = %s",
        (entry_id,),
    ).fetchone()
    if prior is not None:
        if (prior[0], prior[1], int(prior[2])) != (account_id, amount.currency, amount.units):
            raise LedgerError(f"entry {entry_id} already used for a different deposit")
        return False
    _journal(
        conn, entry_id=entry_id, account_id=account_id, kind="DEPOSIT", money=amount, at_ns=at_ns
    )
    _bump(conn, account_id)
    return True


def cash_balance(conn: psycopg.Connection, account_id: str, currency: str) -> int:
    row = conn.execute(
        "SELECT balance_units FROM cash_balances WHERE account_id = %s AND currency = %s",
        (account_id, currency),
    ).fetchone()
    return int(row[0]) if row else 0


def cash_balances(conn: psycopg.Connection, account_id: str) -> dict[str, Money]:
    return {
        c: Money(int(b), c, int(s))
        for c, s, b in conn.execute(
            "SELECT currency, currency_scale, balance_units FROM cash_balances WHERE account_id = %s "
            "ORDER BY currency",
            (account_id,),
        ).fetchall()
    }


# ---------------------------------------------------------------- reservations


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class ReservationResult:
    ok: bool
    reservation_id: str | None
    reason: str
    available_units: int
    state_version: int


def available_cash(conn: psycopg.Connection, account_id: str, currency: str) -> int:
    reserved = conn.execute(
        "SELECT COALESCE(SUM(amount_units), 0) FROM reservations "
        "WHERE account_id = %s AND currency = %s AND status = 'ACTIVE'",
        (account_id, currency),
    ).fetchone()
    return cash_balance(conn, account_id, currency) - int(reserved[0] if reserved else 0)


def reserve(
    conn: psycopg.Connection,
    *,
    account_id: str,
    command_id: str,
    amount: Money,
    mode: ExecutionMode,
    as_of_ns: int,
    expires_at_ns: int,
    expected_state_version: int | None = None,
) -> ReservationResult:
    """Atomically reserve capital (in any held currency) for one order command."""
    if amount.units <= 0:
        raise LedgerError("reservation must be positive")
    if expires_at_ns <= as_of_ns:
        raise LedgerError("reservation must expire after as_of")
    _check_money(amount)
    account = _account(conn, account_id, lock=True)  # serializes all changes to this account
    reservation_id = derive_id("rsv", "reservation", {"command": command_id})

    existing = conn.execute(
        "SELECT reservation_id, account_id, currency, amount_units, status FROM reservations "
        "WHERE command_id = %s",
        (command_id,),
    ).fetchone()
    if existing is not None:
        if (existing[1], existing[2], int(existing[3])) != (
            account_id,
            amount.currency,
            amount.units,
        ):
            raise LedgerError(f"command {command_id} already reserved with different terms")
        return ReservationResult(
            existing[4] == "ACTIVE",
            existing[0],
            f"EXISTING_{existing[4]}",
            available_cash(conn, account_id, amount.currency),
            account.state_version,
        )
    if not _mode_matches(account, mode):
        return ReservationResult(False, None, "MODE_MISMATCH", 0, account.state_version)
    if expected_state_version is not None and expected_state_version != account.state_version:
        return ReservationResult(False, None, "STALE_PORTFOLIO_STATE", 0, account.state_version)

    expired = conn.execute(
        """UPDATE reservations SET status = 'EXPIRED', closed_at_ns = %s
           WHERE account_id = %s AND status = 'ACTIVE' AND expires_at_ns <= %s""",
        (as_of_ns, account_id, as_of_ns),
    ).rowcount
    available = available_cash(conn, account_id, amount.currency)
    if amount.units > available:
        version = _bump(conn, account_id) if expired else account.state_version
        return ReservationResult(False, None, "INSUFFICIENT_CAPITAL", available, version)
    conn.execute(
        """INSERT INTO reservations (reservation_id, account_id, command_id, currency, currency_scale,
               amount_units, status, created_at_ns, expires_at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE', %s, %s)""",
        (
            reservation_id,
            account_id,
            command_id,
            amount.currency,
            amount.scale,
            amount.units,
            as_of_ns,
            expires_at_ns,
        ),
    )
    version = _bump(conn, account_id)
    return ReservationResult(True, reservation_id, "RESERVED", available - amount.units, version)


def close_reservation(
    conn: psycopg.Connection, *, reservation_id: str, status: ReservationStatus, at_ns: int
) -> bool:
    if status not in {ReservationStatus.CONSUMED, ReservationStatus.RELEASED}:
        raise LedgerError("a reservation can only be consumed or released explicitly")
    row = conn.execute(
        """UPDATE reservations SET status = %s, closed_at_ns = %s
           WHERE reservation_id = %s AND status = 'ACTIVE' RETURNING account_id""",
        (status.value, at_ns, reservation_id),
    ).fetchone()
    if row is None:
        return False
    _bump(conn, row[0])
    return True


# ---------------------------------------------------------------- fills


class FillOutcome(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"


def notional_units(fill: Fill, spec: InstrumentSpec) -> int:
    """Quantity x price x multiplier in the settlement currency's units.

    Exact when representable; otherwise buys round up and sells round down so
    the account is never credited with money it may not have received.
    """
    raw = fill.quantity_units * fill.price_units * spec.contract_multiplier_units
    raw_scale = fill.quantity_scale + fill.price_scale + spec.multiplier_scale
    rounding = RoundingMode.CEILING if fill.side is Side.BUY else RoundingMode.FLOOR
    return rescale(raw, raw_scale, spec.currency_scale, rounding)


def apply_fill(conn: psycopg.Connection, fill: Fill, at_ns: int) -> FillOutcome:
    account = _account(conn, fill.account_id, lock=True)
    if not _mode_matches(account, fill.mode):
        raise LedgerError(f"{fill.mode} fill cannot post to a {account.ledger_kind} account")
    bound = conn.execute(
        "SELECT 1 FROM broker_bindings WHERE account_id = %s AND broker_id = %s",
        (fill.account_id, fill.broker),
    ).fetchone()
    if bound is None:
        raise LedgerError(f"broker {fill.broker} is not bound to account {fill.account_id}")
    content = canonical_hash(
        "ledger.fill", fill.model_dump(mode="json", exclude={"received_at_ns"})
    )
    inserted = conn.execute(
        """INSERT INTO fills (fill_id, account_id, broker, broker_execution_id, order_id,
               instrument_id, side, quantity_units, quantity_scale, price_units, price_scale,
               executed_at_ns, content_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT DO NOTHING RETURNING fill_id""",
        (
            fill.fill_id,
            fill.account_id,
            fill.broker,
            fill.broker_execution_id,
            fill.order_id,
            fill.instrument_id,
            fill.side.value,
            fill.quantity_units,
            fill.quantity_scale,
            fill.price_units,
            fill.price_scale,
            fill.executed_at_ns,
            content,
        ),
    ).fetchone()
    if inserted is None:
        prior = conn.execute(
            "SELECT content_hash FROM fills WHERE account_id = %s AND broker = %s AND broker_execution_id = %s",
            (fill.account_id, fill.broker, fill.broker_execution_id),
        ).fetchone()
        if prior is None or prior[0] != content:
            raise FillConflict(
                f"execution {fill.broker}/{fill.broker_execution_id} already recorded with different content"
            )
        return FillOutcome.DUPLICATE

    try:
        spec = instrument_at(conn, fill.instrument_id, fill.executed_at_ns)
    except ReferenceError as exc:
        raise LedgerError(str(exc)) from exc
    if not spec.tradable:
        raise LedgerError(f"{spec.instrument_id} is not tradable")
    if (fill.quantity_scale, fill.price_scale) != (spec.quantity_scale, spec.price_scale):
        raise LedgerError("fill scales do not match the instrument reference data")

    ccy, cscale = spec.settlement_currency, spec.currency_scale
    notional = notional_units(fill, spec)
    dq, dn = (
        (fill.quantity_units, notional)
        if fill.side is Side.BUY
        else (-fill.quantity_units, -notional)
    )
    row = conn.execute(
        "SELECT quantity_units, open_cost_units, realized_pnl_units FROM positions "
        "WHERE account_id = %s AND instrument_id = %s FOR UPDATE",
        (fill.account_id, fill.instrument_id),
    ).fetchone()
    before = PositionState(*(int(v) for v in row)) if row else PositionState()
    after = apply_trade(before, dq, dn)
    conn.execute(
        """INSERT INTO positions (account_id, instrument_id, quantity_units, quantity_scale,
               open_cost_units, realized_pnl_units, currency, currency_scale, updated_at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, account_id, instrument_id) DO UPDATE SET
               quantity_units = EXCLUDED.quantity_units, open_cost_units = EXCLUDED.open_cost_units,
               realized_pnl_units = EXCLUDED.realized_pnl_units, updated_at_ns = EXCLUDED.updated_at_ns""",
        (
            fill.account_id,
            fill.instrument_id,
            after.quantity_units,
            spec.quantity_scale,
            after.open_cost_units,
            after.realized_pnl_units,
            ccy,
            cscale,
            at_ns,
        ),
    )
    _journal(
        conn,
        entry_id=f"{fill.fill_id}:trade",
        account_id=fill.account_id,
        kind="FILL",
        money=Money(-dn, ccy, cscale),
        at_ns=at_ns,
        instrument_id=fill.instrument_id,
        quantity_delta=dq,
        fill_id=fill.fill_id,
    )
    for i, fee in enumerate(fill.fees):
        _journal(
            conn,
            entry_id=f"{fill.fill_id}:fee:{i}",
            account_id=fill.account_id,
            kind="FEE",
            money=Money(-fee.units, fee.currency, fee.scale),
            at_ns=at_ns,
            fill_id=fill.fill_id,
        )
    _bump(conn, fill.account_id)
    return FillOutcome.APPLIED


# ---------------------------------------------------------------- snapshot and valuation


def _average_price(qty: int, cost: int, spec: InstrumentSpec) -> int:
    if not qty:
        return 0
    per_unit: Decimal = from_scaled(abs(cost), spec.currency_scale) / (
        from_scaled(abs(qty), spec.quantity_scale)
        * from_scaled(spec.contract_multiplier_units, spec.multiplier_scale)
    )
    return to_scaled(per_unit, spec.price_scale, RoundingMode.FLOOR)


def snapshot(conn: psycopg.Connection, account_id: str, as_of_ns: int) -> PortfolioSnapshot:
    account = _account(conn, account_id)
    cutoff = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM ledger_entries WHERE account_id = %s", (account_id,)
    ).fetchone()
    positions = []
    for inst, raw_qty, raw_cost, realized, ccy, cscale in conn.execute(
        "SELECT instrument_id, quantity_units, open_cost_units, realized_pnl_units, currency, currency_scale "
        "FROM positions WHERE account_id = %s ORDER BY instrument_id",
        (account_id,),
    ).fetchall():
        spec = instrument_at(conn, inst, as_of_ns)
        positions.append(
            Position(
                instrument_id=inst,
                quantity_units=int(raw_qty),
                quantity_scale=spec.quantity_scale,
                average_price_units=_average_price(int(raw_qty), int(raw_cost), spec),
                price_scale=spec.price_scale,
                realized_pnl=MoneyAmount(units=int(realized), currency=ccy, scale=int(cscale)),
            )
        )
    reservations = [
        Reservation(
            reservation_id=r[0],
            command_id=r[1],
            amount=MoneyAmount(units=int(r[2]), currency=r[3], scale=int(r[4])),
            expires_at_ns=int(r[5]),
        )
        for r in conn.execute(
            "SELECT reservation_id, command_id, amount_units, currency, currency_scale, expires_at_ns "
            "FROM reservations WHERE account_id = %s AND status = 'ACTIVE' AND expires_at_ns > %s "
            "ORDER BY reservation_id",
            (account_id, as_of_ns),
        ).fetchall()
    ]
    return PortfolioSnapshot(
        account_id=account_id,
        portfolio_id=account.portfolio_id,
        state_version=account.state_version,
        ledger_cutoff_sequence=int(cutoff[0] if cutoff else 0),
        as_of_ns=as_of_ns,
        cash=tuple(
            MoneyAmount(units=m.units, currency=m.currency, scale=m.scale)
            for m in cash_balances(conn, account_id).values()
        ),
        positions=tuple(positions),
        reservations=tuple(reservations),
    )


class ValuationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"  # some balance or position could not be valued


@dataclass(frozen=True, slots=True)
class Valuation:
    account_id: str
    base_currency: str
    as_of_ns: int
    status: ValuationStatus
    cash_in_base_units: int
    positions_in_base_units: int
    equity_in_base_units: int | None  # None unless COMPLETE
    missing: tuple[str, ...]
    rates_used: tuple[FxRate, ...] = field(default=())


def valuation(
    conn: psycopg.Connection,
    account_id: str,
    *,
    as_of_ns: int,
    rates: list[FxRate],
    marks: Mapping[str, int],
) -> Valuation:
    """Equity in the account's base currency from cash + marked positions.

    ``marks`` maps instrument_id -> mark price units (at the instrument's price
    scale), taken from measured market state. Anything that can't be valued is
    listed in ``missing`` and the equity is withheld.
    """
    account = _account(conn, account_id)
    base = account.base_currency
    missing: list[str] = []
    used: list[FxRate] = []
    cash_total = 0
    for money in cash_balances(conn, account_id).values():
        try:
            conv = convert(money.units, money.currency, base, rates)
        except FxError:
            missing.append(f"FX:{money.currency}/{base}")
            continue
        cash_total += conv.units
        used.extend(conv.rates_used)
    pos_total = 0
    for inst, raw_qty in conn.execute(
        "SELECT instrument_id, quantity_units FROM positions "
        "WHERE account_id = %s AND quantity_units <> 0",
        (account_id,),
    ).fetchall():
        spec = instrument_at(conn, inst, as_of_ns)
        if inst not in marks:
            missing.append(f"MARK:{inst}")
            continue
        raw = int(raw_qty) * marks[inst] * spec.contract_multiplier_units
        value = rescale(
            raw,
            spec.quantity_scale + spec.price_scale + spec.multiplier_scale,
            spec.currency_scale,
            RoundingMode.FLOOR,
        )
        try:
            conv = convert(value, spec.settlement_currency, base, rates)
        except FxError:
            missing.append(f"FX:{spec.settlement_currency}/{base}")
            continue
        pos_total += conv.units
        used.extend(conv.rates_used)
    complete = not missing
    return Valuation(
        account_id=account_id,
        base_currency=base,
        as_of_ns=as_of_ns,
        status=ValuationStatus.COMPLETE if complete else ValuationStatus.INCOMPLETE,
        cash_in_base_units=cash_total,
        positions_in_base_units=pos_total,
        equity_in_base_units=cash_total + pos_total if complete else None,
        missing=tuple(sorted(set(missing))),
        rates_used=tuple(dict.fromkeys(used)),
    )
