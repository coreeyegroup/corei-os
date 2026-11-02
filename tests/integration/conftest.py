"""Integration fixtures: a fresh, migrated Postgres database per test session.

Set ``COREI_TEST_DATABASE_URL`` to a server where the user may create databases.
Without it these tests skip locally; with ``COREI_REQUIRE_INTEGRATION=1`` (CI)
a missing database is a failure, so CI can never pass by skipping them.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from psycopg import sql

from corei.contracts.reference import InstrumentKind, InstrumentSpec
from corei.domain.control import ExecutionMode
from corei.domain.timeutil import FixedClock
from corei.domain.units import Money
from corei.infra.db import migrate
from corei.ledger import store as ledger

T0 = 1_788_000_000_000_000_000
SEC = 1_000_000_000
TABLES = [
    "ledger_entries",
    "positions",
    "cash_balances",
    "reservations",
    "fills",
    "accounts",
    "instruments",
    "inbox",
    "outbox",
    "rejected_events",
    "control_commands",
    "control_state",
]


def _admin_url() -> str | None:
    url = os.environ.get("COREI_TEST_DATABASE_URL")
    if not url and os.environ.get("COREI_REQUIRE_INTEGRATION") == "1":
        pytest.fail("COREI_TEST_DATABASE_URL is required when COREI_REQUIRE_INTEGRATION=1")
    return url


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin = _admin_url()
    if not admin:
        pytest.skip("integration database not configured (COREI_TEST_DATABASE_URL)")
    name = f"corei_it_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = psycopg.conninfo.make_conninfo(admin, dbname=name)
    with psycopg.connect(url) as conn:
        migrate(conn)
        conn.commit()
    yield url
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@pytest.fixture
def connect(database_url: str) -> Iterator[Callable[[], psycopg.Connection]]:
    def factory() -> psycopg.Connection:
        return psycopg.connect(database_url)

    with factory() as conn:
        conn.execute(
            sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)
            )
        )
        conn.commit()
    yield factory


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(current_ns=T0, step_ns=1_000)


def instrument(instrument_id: str = "ins_test_fut_a", multiplier: int = 1) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=instrument_id,
        version=1,
        venue="TESTX",
        symbol=instrument_id.upper(),
        kind=InstrumentKind.FUTURE,
        tradable=True,
        underlying_id="ins_test_idx_a",
        expiry_ns=T0 + 30 * 86_400 * SEC,
        price_scale=2,
        tick_size_units=5,
        quantity_scale=0,
        quantity_step_units=25,
        min_quantity_units=25,
        contract_multiplier_units=multiplier,
        multiplier_scale=0,
        settlement_currency="INR",
        currency_scale=2,
        valid_from_ns=T0 - SEC,
    )


@pytest.fixture
def paper_accounts(connect: Callable[[], psycopg.Connection]) -> dict[str, int]:
    """Two seeded PAPER accounts and one test instrument. Returns seed cash in paise."""
    seeds = {"acc_paper_a": 100_000_000, "acc_paper_b": 50_000_000}  # 10 lakh, 5 lakh INR
    with connect() as conn, conn.transaction():
        ledger.upsert_instrument(conn, instrument())
        ledger.upsert_instrument(conn, instrument("ins_test_fut_b"))
        for acct, cash in seeds.items():
            ledger.open_account(
                conn,
                account_id=acct,
                portfolio_id=acct.replace("acc", "pf"),
                broker="paper",
                mode=ExecutionMode.PAPER,
                currency="INR",
                currency_scale=2,
                at_ns=T0,
            )
            ledger.deposit(
                conn,
                account_id=acct,
                entry_id=f"seed:{acct}",
                amount=Money(cash, "INR", 2),
                at_ns=T0,
            )
    return seeds
