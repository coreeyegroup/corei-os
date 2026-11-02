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

from corei.bootstrap import fx_instrument
from corei.contracts.reference import (
    AccountSpec,
    AssetClass,
    BrokerBinding,
    BrokerPlatform,
    BrokerSpec,
    LedgerKind,
    LegalEntity,
    OrderTypeCapability,
    PositionModel,
)
from corei.domain.timeutil import FixedClock
from corei.domain.units import Money
from corei.infra.db import migrate
from corei.ledger import store as ledger
from corei.reference import store as reference

T0 = 1_788_000_000_000_000_000
SEC = 1_000_000_000
TABLES = [
    "ledger_entries",
    "positions",
    "cash_balances",
    "reservations",
    "fills",
    "broker_bindings",
    "account_group_members",
    "account_groups",
    "accounts",
    "legal_entities",
    "brokers",
    "instruments",
    "fx_rates",
    "inbox",
    "outbox",
    "rejected_events",
    "control_commands",
    "control_state",
]
PAPER_BROKER = "brk_paper"
UK_MT5 = "brk_mt5_uk"


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


def broker(broker_id: str, platform: BrokerPlatform, jurisdictions: tuple[str, ...]) -> BrokerSpec:
    return BrokerSpec(
        broker_id=broker_id,
        name=broker_id,
        platform=platform,
        jurisdictions=jurisdictions,
        asset_classes=(AssetClass.FX, AssetClass.METAL),
        order_types=(OrderTypeCapability.MARKET, OrderTypeCapability.LIMIT),
        position_model=PositionModel.NETTING,
    )


def account_spec(
    account_id: str,
    entity_id: str,
    base: str = "USD",
    version: int = 1,
    bindings: tuple[tuple[str, int], ...] = ((PAPER_BROKER, 1),),
    kind: LedgerKind = LedgerKind.PAPER,
) -> AccountSpec:
    return AccountSpec(
        account_id=account_id,
        portfolio_id=account_id.replace("acc", "pf"),
        entity_id=entity_id,
        ledger_kind=kind,
        base_currency=base,
        version=version,
        bindings=tuple(
            BrokerBinding(broker_id=b, rank=r, broker_account_ref=f"{b}-{account_id}")
            for b, r in bindings
        ),
    )


PAIRS = [
    {"symbol": "EURUSD", "base": "EUR", "quote": "USD", "price_scale": 5, "pip": 10},
    {"symbol": "USDJPY", "base": "USD", "quote": "JPY", "price_scale": 3, "pip": 10},
    {"symbol": "GBPUSD", "base": "GBP", "quote": "USD", "price_scale": 5, "pip": 10},
]


@pytest.fixture
def paper_accounts(connect: Callable[[], psycopg.Connection]) -> dict[str, int]:
    """US and UK entities, paper + UK-only MT5 brokers, FX instruments, two USD paper accounts."""
    seeds = {"acc_paper_a": 10_000_000, "acc_paper_b": 5_000_000}  # $100,000.00 and $50,000.00
    with connect() as conn, conn.transaction():
        reference.upsert_entity(
            conn,
            LegalEntity(
                entity_id="ent_us",
                name="US",
                jurisdiction="US",
                base_currency="USD",
                reporting_timezone="America/New_York",
            ),
        )
        reference.upsert_entity(
            conn,
            LegalEntity(
                entity_id="ent_uk",
                name="UK",
                jurisdiction="GB",
                base_currency="GBP",
                reporting_timezone="Europe/London",
            ),
        )
        reference.upsert_broker(
            conn, broker(PAPER_BROKER, BrokerPlatform.PAPER, ("US", "GB", "AE"))
        )
        reference.upsert_broker(conn, broker(UK_MT5, BrokerPlatform.MT5, ("GB",)))
        for pair in PAIRS:
            reference.upsert_instrument(conn, fx_instrument(pair, [PAPER_BROKER, UK_MT5]))
        for acct, entity in (("acc_paper_a", "ent_us"), ("acc_paper_b", "ent_uk")):
            reference.register_account(conn, account_spec(acct, entity), T0)
            ledger.deposit(
                conn,
                account_id=acct,
                entry_id=f"seed:{acct}",
                amount=Money(seeds[acct], "USD", 2),
                at_ns=T0,
            )
    return seeds
