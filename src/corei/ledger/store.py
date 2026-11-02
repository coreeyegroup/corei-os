"""Durable ledger (findings F04, F21, F22).

All functions take an open connection and run inside the caller's transaction,
so a fill, its journal entries, the projections and any outbox event commit
together or not at all.

* Fills are applied once per (account, broker, broker execution id). A
  redelivered fill is a no-op; the same id with different content is refused.
* The journal (``ledger_entries``) is append-only; balances and positions are
  projections updated in the same transaction.
* Reservations are taken under a row lock on the account's cash balance, so
  parallel requests can never reserve more than is available. A caller may pass
  the portfolio ``state_version`` its risk decision was based on; if the
  portfolio changed since, the reservation is refused (C12).
* Every change bumps the account's ``state_version``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import psycopg
from psycopg.types.json import Jsonb

from corei.contracts.base import MoneyAmount
from corei.contracts.execution import Fill, PortfolioSnapshot, Position, Reservation
from corei.contracts.reference import InstrumentSpec
from corei.contracts.trading import Side
from corei.domain.control import ExecutionMode
from corei.domain.identity import canonical_hash, derive_id
from corei.domain.positions import PositionState, apply_trade
from corei.domain.units import Money, RoundingMode, from_scaled, rescale, to_scaled


class LedgerError(ValueError):
    pass


class FillConflict(LedgerError):
    """Same broker execution id, different content: a reconciliation problem, not a duplicate."""


# ---------------------------------------------------------------- reference data


def upsert_instrument(conn: psycopg.Connection, spec: InstrumentSpec) -> None:
    row = conn.execute(
        "SELECT spec FROM instruments WHERE instrument_id = %s AND version = %s",
        (spec.instrument_id, spec.version),
    ).fetchone()
    data = spec.model_dump(mode="json")
    if row is not None:
        if row[0] != data:
            raise LedgerError(
                f"instrument {spec.instrument_id} v{spec.version} exists with different content; "
                "publish a new version"
            )
        return
    conn.execute(
        """INSERT INTO instruments (instrument_id, version, tradable, spec, valid_from_ns, valid_to_ns)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            spec.instrument_id,
            spec.version,
            spec.tradable,
            Jsonb(data),
            spec.valid_from_ns,
            spec.valid_to_ns,
        ),
    )


def instrument_at(conn: psycopg.Connection, instrument_id: str, at_ns: int) -> InstrumentSpec:
    row = conn.execute(
        """SELECT spec FROM instruments
           WHERE instrument_id = %s AND valid_from_ns <= %s AND (valid_to_ns IS NULL OR valid_to_ns > %s)
           ORDER BY version DESC LIMIT 1""",
        (instrument_id, at_ns, at_ns),
    ).fetchone()
    if row is None:
        raise LedgerError(f"no instrument {instrument_id} valid at {at_ns}")
    return InstrumentSpec.model_validate(row[0], strict=False)


# ---------------------------------------------------------------- accounts and cash


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    portfolio_id: str
    broker: str
    mode: ExecutionMode
    currency: str
    currency_scale: int
    state_version: int


def open_account(
    conn: psycopg.Connection,
    *,
    account_id: str,
    portfolio_id: str,
    broker: str,
    mode: ExecutionMode,
    currency: str,
    currency_scale: int,
    at_ns: int,
) -> Account:
    if mode in {ExecutionMode.LIVE_SEMI, ExecutionMode.LIVE_AUTO, ExecutionMode.OFF}:
        raise LedgerError(f"accounts cannot be opened in mode {mode} in this build")
    conn.execute(
        """INSERT INTO accounts
           (account_id, portfolio_id, broker, mode, base_currency, currency_scale, created_at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (account_id) DO NOTHING""",
        (account_id, portfolio_id, broker, mode.value, currency, currency_scale, at_ns),
    )
    conn.execute(
        """INSERT INTO cash_balances (account_id, currency, currency_scale, balance_units)
           VALUES (%s, %s, %s, 0) ON CONFLICT DO NOTHING""",
        (account_id, currency, currency_scale),
    )
    acct = get_account(conn, account_id)
    if (acct.portfolio_id, acct.broker, acct.mode, acct.currency, acct.currency_scale) != (
        portfolio_id,
        broker,
        mode,
        currency,
        currency_scale,
    ):
        raise LedgerError(f"account {account_id} already exists with different attributes")
    return acct


def get_account(conn: psycopg.Connection, account_id: str, lock: bool = False) -> Account:
    row = conn.execute(
        "SELECT account_id, portfolio_id, broker, mode, base_currency, currency_scale, state_version "
        "FROM accounts WHERE account_id = %s" + (" FOR UPDATE" if lock else ""),
        (account_id,),
    ).fetchone()
    if row is None:
        raise LedgerError(f"unknown account {account_id}")
    return Account(row[0], row[1], row[2], ExecutionMode(row[3]), row[4], int(row[5]), int(row[6]))


def _bump(conn: psycopg.Connection, account_id: str) -> int:
    row = conn.execute(
        "UPDATE accounts SET state_version = state_version + 1 WHERE account_id = %s "
        "RETURNING state_version",
        (account_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _journal(
    conn: psycopg.Connection,
    *,
    entry_id: str,
    account: Account,
    kind: str,
    cash_delta: int,
    at_ns: int,
    instrument_id: str | None = None,
    quantity_delta: int | None = None,
    fill_id: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO ledger_entries (entry_id, account_id, kind, currency, currency_scale,
               cash_delta_units, instrument_id, quantity_delta_units, fill_id, at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            entry_id,
            account.account_id,
            kind,
            account.currency,
            account.currency_scale,
            cash_delta,
            instrument_id,
            quantity_delta,
            fill_id,
            at_ns,
        ),
    )
    if cash_delta:
        conn.execute(
            "UPDATE cash_balances SET balance_units = balance_units + %s "
            "WHERE account_id = %s AND currency = %s",
            (cash_delta, account.account_id, account.currency),
        )


def _check_money(account: Account, amount: Money) -> None:
    if (amount.currency, amount.scale) != (account.currency, account.currency_scale):
        raise LedgerError(
            f"{amount.currency}@{amount.scale} does not match account "
            f"{account.currency}@{account.currency_scale}; FX conversion is not supported yet"
        )


def deposit(
    conn: psycopg.Connection, *, account_id: str, entry_id: str, amount: Money, at_ns: int
) -> bool:
    """Seed or add cash. Idempotent by ``entry_id``; returns False if already applied."""
    if amount.units <= 0:
        raise LedgerError("deposit must be positive")
    account = get_account(conn, account_id, lock=True)
    _check_money(account, amount)
    exists = conn.execute(
        "SELECT account_id, cash_delta_units FROM ledger_entries WHERE entry_id = %s", (entry_id,)
    ).fetchone()
    if exists is not None:
        if (exists[0], int(exists[1])) != (account_id, amount.units):
            raise LedgerError(f"entry {entry_id} already used for a different deposit")
        return False
    _journal(
        conn,
        entry_id=entry_id,
        account=account,
        kind="DEPOSIT",
        cash_delta=amount.units,
        at_ns=at_ns,
    )
    _bump(conn, account_id)
    return True


def cash_balance(conn: psycopg.Connection, account_id: str, currency: str) -> int:
    row = conn.execute(
        "SELECT balance_units FROM cash_balances WHERE account_id = %s AND currency = %s",
        (account_id, currency),
    ).fetchone()
    return int(row[0]) if row else 0


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


def _expire(conn: psycopg.Connection, account_id: str, as_of_ns: int) -> int:
    cur = conn.execute(
        """UPDATE reservations SET status = 'EXPIRED', closed_at_ns = %s
           WHERE account_id = %s AND status = 'ACTIVE' AND expires_at_ns <= %s""",
        (as_of_ns, account_id, as_of_ns),
    )
    return cur.rowcount


def available_cash(conn: psycopg.Connection, account_id: str, currency: str) -> int:
    """Cash minus active reservations (call ``_expire`` first inside a locked transaction)."""
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
    """Atomically reserve capital for one order command. Idempotent per ``command_id``."""
    if amount.units <= 0:
        raise LedgerError("reservation must be positive")
    if expires_at_ns <= as_of_ns:
        raise LedgerError("reservation must expire after as_of")
    account = get_account(conn, account_id, lock=True)  # serializes all changes to this account
    _check_money(account, amount)
    reservation_id = derive_id("rsv", "reservation", {"command": command_id})

    existing = conn.execute(
        "SELECT reservation_id, account_id, amount_units, status FROM reservations "
        "WHERE command_id = %s",
        (command_id,),
    ).fetchone()
    if existing is not None:
        same = existing[1] == account_id and int(existing[2]) == amount.units
        if not same:
            raise LedgerError(f"command {command_id} already reserved with different terms")
        return ReservationResult(
            existing[3] == "ACTIVE",
            existing[0],
            f"EXISTING_{existing[3]}",
            available_cash(conn, account_id, account.currency),
            account.state_version,
        )
    if account.mode is not mode:
        return ReservationResult(False, None, "MODE_MISMATCH", 0, account.state_version)
    if expected_state_version is not None and expected_state_version != account.state_version:
        return ReservationResult(False, None, "STALE_PORTFOLIO_STATE", 0, account.state_version)

    expired = _expire(conn, account_id, as_of_ns)
    available = available_cash(conn, account_id, account.currency)
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
    """Mark an ACTIVE reservation CONSUMED or RELEASED. Returns False if it was not active."""
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


def notional_units(fill: Fill, spec: InstrumentSpec, currency_scale: int) -> int:
    """Quantity x price x multiplier at the account currency scale.

    Exact when representable; otherwise buys round up and sells round down (the
    account is never credited with money it might not have received).
    """
    raw = fill.quantity_units * fill.price_units * spec.contract_multiplier_units
    raw_scale = fill.quantity_scale + fill.price_scale + spec.multiplier_scale
    rounding = RoundingMode.CEILING if fill.side is Side.BUY else RoundingMode.FLOOR
    return rescale(raw, raw_scale, currency_scale, rounding)


def apply_fill(conn: psycopg.Connection, fill: Fill, at_ns: int) -> FillOutcome:
    account = get_account(conn, fill.account_id, lock=True)
    if fill.mode is not account.mode:
        raise LedgerError(f"fill mode {fill.mode} does not match account mode {account.mode}")
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
            "SELECT content_hash FROM fills WHERE account_id = %s AND broker = %s "
            "AND broker_execution_id = %s",
            (fill.account_id, fill.broker, fill.broker_execution_id),
        ).fetchone()
        if prior is None or prior[0] != content:
            raise FillConflict(
                f"execution {fill.broker}/{fill.broker_execution_id} already recorded with "
                "different content"
            )
        return FillOutcome.DUPLICATE

    spec = instrument_at(conn, fill.instrument_id, fill.executed_at_ns)
    if spec.settlement_currency != account.currency:
        raise LedgerError(f"{spec.instrument_id} settles in {spec.settlement_currency}")
    if (fill.quantity_scale, fill.price_scale) != (spec.quantity_scale, spec.price_scale):
        raise LedgerError("fill scales do not match the instrument reference data")

    notional = notional_units(fill, spec, account.currency_scale)
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
           ON CONFLICT (account_id, instrument_id) DO UPDATE SET
               quantity_units = EXCLUDED.quantity_units, open_cost_units = EXCLUDED.open_cost_units,
               realized_pnl_units = EXCLUDED.realized_pnl_units, updated_at_ns = EXCLUDED.updated_at_ns""",
        (
            fill.account_id,
            fill.instrument_id,
            after.quantity_units,
            spec.quantity_scale,
            after.open_cost_units,
            after.realized_pnl_units,
            account.currency,
            account.currency_scale,
            at_ns,
        ),
    )
    _journal(
        conn,
        entry_id=f"{fill.fill_id}:trade",
        account=account,
        kind="FILL",
        cash_delta=-dn,
        at_ns=at_ns,
        instrument_id=fill.instrument_id,
        quantity_delta=dq,
        fill_id=fill.fill_id,
    )
    for i, fee in enumerate(fill.fees):
        money = Money(fee.units, fee.currency, fee.scale)
        _check_money(account, money)
        _journal(
            conn,
            entry_id=f"{fill.fill_id}:fee:{i}",
            account=account,
            kind="FEE",
            cash_delta=-fee.units,
            at_ns=at_ns,
            fill_id=fill.fill_id,
        )
    _bump(conn, fill.account_id)
    return FillOutcome.APPLIED


# ---------------------------------------------------------------- snapshot


def snapshot(conn: psycopg.Connection, account_id: str, as_of_ns: int) -> PortfolioSnapshot:
    account = get_account(conn, account_id)
    cutoff = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM ledger_entries WHERE account_id = %s", (account_id,)
    ).fetchone()
    cash = conn.execute(
        "SELECT currency, currency_scale, balance_units FROM cash_balances WHERE account_id = %s "
        "ORDER BY currency",
        (account_id,),
    ).fetchall()
    positions = []
    for inst, raw_qty, raw_cost, realized in conn.execute(
        "SELECT instrument_id, quantity_units, open_cost_units, realized_pnl_units FROM positions "
        "WHERE account_id = %s ORDER BY instrument_id",
        (account_id,),
    ).fetchall():
        spec = instrument_at(conn, inst, as_of_ns)
        qty, cost = int(raw_qty), int(raw_cost)
        avg = 0
        if qty:
            per_unit: Decimal = from_scaled(abs(cost), account.currency_scale) / (
                from_scaled(abs(qty), spec.quantity_scale)
                * from_scaled(spec.contract_multiplier_units, spec.multiplier_scale)
            )
            avg = to_scaled(per_unit, spec.price_scale, RoundingMode.FLOOR)
        positions.append(
            Position(
                instrument_id=inst,
                quantity_units=qty,
                quantity_scale=spec.quantity_scale,
                average_price_units=avg,
                price_scale=spec.price_scale,
                realized_pnl=MoneyAmount(
                    units=int(realized), currency=account.currency, scale=account.currency_scale
                ),
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
        cash=tuple(MoneyAmount(units=int(b), currency=c, scale=int(s)) for c, s, b in cash),
        positions=tuple(positions),
        reservations=tuple(reservations),
    )
