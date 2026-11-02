"""Entities, jurisdictions, broker bindings, groups, multi-currency settlement, valuation."""

from collections.abc import Callable

import psycopg
import pytest

from corei.contracts.execution import Fill
from corei.contracts.reference import (
    AccountGroup,
    BrokerPlatform,
    FxRateSnapshot,
    LedgerKind,
    LegalEntity,
)
from corei.contracts.trading import Side
from corei.domain.control import ExecutionMode
from corei.domain.fx import FxRate
from corei.domain.identity import derive_id
from corei.domain.units import Money
from corei.ledger import store as ledger
from corei.ledger.store import ValuationStatus
from corei.reference import store as reference
from corei.reference.store import ReferenceError

from .conftest import PAPER_BROKER, SEC, T0, UK_MT5, account_spec, broker

pytestmark = pytest.mark.integration
Connect = Callable[[], psycopg.Connection]


def usdjpy_fill(
    qty: int = 100, price: int = 152_100, exec_id: str = "JX-1", side: Side = Side.BUY
) -> Fill:
    return Fill(
        fill_id=derive_id(
            "fil", "fill", {"account": "acc_paper_a", "broker": PAPER_BROKER, "execution": exec_id}
        ),
        account_id="acc_paper_a",
        broker=PAPER_BROKER,
        order_id="ord_j",
        broker_order_id="PB-J",
        broker_execution_id=exec_id,
        instrument_id="ins_usdjpy",
        side=side,
        quantity_units=qty,
        quantity_scale=2,
        price_units=price,
        price_scale=3,
        executed_at_ns=T0 + SEC,
        received_at_ns=T0 + 2 * SEC,
        mode=ExecutionMode.PAPER,
    )


def rate(
    base: str, quote: str, units: int, scale: int, at: int = T0, source: str = "paper"
) -> FxRate:
    return FxRate(base, quote, units, scale, at, source)


# ---------------------------------------------------------------- jurisdictions and bindings


def test_broker_must_serve_the_entity_jurisdiction(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        with pytest.raises(ReferenceError, match="does not serve US"):
            reference.register_account(
                conn, account_spec("acc_us_mt5", "ent_us", bindings=((UK_MT5, 1),)), T0
            )
        uk = reference.register_account(
            conn,
            account_spec(
                "acc_uk_mt5", "ent_uk", base="GBP", bindings=((UK_MT5, 1), (PAPER_BROKER, 2))
            ),
            T0,
        )
        assert uk.base_currency == "GBP"
        assert reference.bound_brokers(conn, "acc_uk_mt5") == [UK_MT5, PAPER_BROKER]


def test_country_broker_serves_free_zone_but_not_the_reverse(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        for eid, jur in (("ent_difc", "AE-DIFC"), ("ent_ae", "AE")):
            reference.upsert_entity(
                conn,
                LegalEntity(
                    entity_id=eid,
                    name=eid,
                    jurisdiction=jur,
                    base_currency="USD",
                    reporting_timezone="Asia/Dubai",
                ),
            )
        reference.upsert_broker(conn, broker("brk_ae", BrokerPlatform.MT5, ("AE",)))
        reference.upsert_broker(conn, broker("brk_difc", BrokerPlatform.CTRADER, ("AE-DIFC",)))
        reference.register_account(
            conn, account_spec("acc_difc_1", "ent_difc", bindings=(("brk_ae", 1),)), T0
        )
        reference.register_account(
            conn, account_spec("acc_difc_2", "ent_difc", bindings=(("brk_difc", 1),)), T0
        )
        with pytest.raises(ReferenceError, match="does not serve AE"):
            reference.register_account(
                conn, account_spec("acc_ae_1", "ent_ae", bindings=(("brk_difc", 1),)), T0
            )


def test_ledger_kind_and_entity_never_change(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        with pytest.raises(ReferenceError, match="never change"):
            reference.register_account(
                conn, account_spec("acc_paper_a", "ent_us", version=2, kind=LedgerKind.BACKTEST), T0
            )
        with pytest.raises(ReferenceError, match="never change"):
            reference.register_account(conn, account_spec("acc_paper_a", "ent_uk", version=2), T0)
        with pytest.raises(ReferenceError, match="different content"):
            reference.register_account(conn, account_spec("acc_paper_a", "ent_us", base="EUR"), T0)


def test_binding_rank_swap_works_but_unbinding_a_traded_broker_is_refused(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn:
        with conn.transaction():
            reference.upsert_broker(conn, broker("brk_ib", BrokerPlatform.IB, ("US", "GB")))
            reference.register_account(
                conn,
                account_spec(
                    "acc_paper_a", "ent_us", version=2, bindings=((PAPER_BROKER, 1), ("brk_ib", 2))
                ),
                T0,
            )
            reference.register_account(
                conn,
                account_spec(
                    "acc_paper_a", "ent_us", version=3, bindings=(("brk_ib", 1), (PAPER_BROKER, 2))
                ),
                T0,
            )
            assert reference.bound_brokers(conn, "acc_paper_a") == ["brk_ib", PAPER_BROKER]
            ledger.apply_fill(conn, usdjpy_fill(), T0)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            reference.register_account(
                conn,
                account_spec("acc_paper_a", "ent_us", version=4, bindings=(("brk_ib", 1),)),
                T0,
            )


def test_fills_table_refuses_unbound_broker_even_bypassing_the_api(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            """INSERT INTO fills (fill_id, account_id, broker, broker_execution_id, order_id,
                   instrument_id, side, quantity_units, quantity_scale, price_units,
                   price_scale, executed_at_ns, content_hash)
               VALUES ('fil_x', 'acc_paper_a', %s, 'X1', 'ord', 'ins_eurusd', 'BUY',
                       1, 2, 1, 5, 1, 'h')""",
            (UK_MT5,),
        )


def test_copy_trading_cycles_are_refused(connect: Connect, paper_accounts: dict[str, int]) -> None:
    with connect() as conn, conn.transaction():
        reference.upsert_group(
            conn,
            AccountGroup(
                group_id="grp_copy_a",
                name="a follows",
                account_ids=("acc_paper_b",),
                copy_master_account_id="acc_paper_a",
            ),
        )
        with pytest.raises(ReferenceError, match="cycle"):
            reference.upsert_group(
                conn,
                AccountGroup(
                    group_id="grp_copy_b",
                    name="b follows",
                    account_ids=("acc_paper_a",),
                    copy_master_account_id="acc_paper_b",
                ),
            )


# ---------------------------------------------------------------- multi-currency settlement


def test_usdjpy_fill_settles_in_jpy(connect: Connect, paper_accounts: dict[str, int]) -> None:
    with connect() as conn, conn.transaction():
        ledger.apply_fill(conn, usdjpy_fill(), T0)  # 1.00 lot x 100,000 USD x 152.100
        balances = ledger.cash_balances(conn, "acc_paper_a")
        snap = ledger.snapshot(conn, "acc_paper_a", T0 + 3 * SEC)
    assert balances["JPY"].units == -15_210_000 and balances["JPY"].scale == 0
    assert balances["USD"].units == paper_accounts["acc_paper_a"]  # untouched: no fee
    assert snap.positions[0].instrument_id == "ins_usdjpy"
    assert snap.positions[0].average_price_units == 152_100
    assert snap.positions[0].realized_pnl.currency == "JPY"


def test_valuation_in_base_currency_is_exact_or_withheld(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        ledger.apply_fill(conn, usdjpy_fill(), T0)
        rates = [rate("USD", "JPY", 152_200, 3)]
        marks = {"ins_usdjpy": 152_200}
        full = ledger.valuation(
            conn, "acc_paper_a", as_of_ns=T0 + 5 * SEC, rates=rates, marks=marks
        )
        no_rate = ledger.valuation(
            conn, "acc_paper_a", as_of_ns=T0 + 5 * SEC, rates=[], marks=marks
        )
        no_mark = ledger.valuation(
            conn, "acc_paper_a", as_of_ns=T0 + 5 * SEC, rates=rates, marks={}
        )
    # Long 100,000 USD bought at 152.100, marked at 152.200: +10,000 JPY = +$65.70 at 152.200.
    assert full.status is ValuationStatus.COMPLETE and full.base_currency == "USD"
    assert full.positions_in_base_units == 10_000_000  # 15,220,000 JPY / 152.2
    assert full.equity_in_base_units is not None
    assert abs(full.equity_in_base_units - (paper_accounts["acc_paper_a"] + 6_570)) <= 1
    assert no_rate.status is ValuationStatus.INCOMPLETE and no_rate.equity_in_base_units is None
    assert "FX:JPY/USD" in no_rate.missing
    assert no_mark.status is ValuationStatus.INCOMPLETE and no_mark.missing == ("MARK:ins_usdjpy",)


def test_gbp_account_values_usd_cash_through_recorded_rate(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        reference.register_account(conn, account_spec("acc_uk_gbp", "ent_uk", base="GBP"), T0)
        ledger.deposit(
            conn,
            account_id="acc_uk_gbp",
            entry_id="seed:gbp",
            amount=Money(1_000_000, "GBP", 2),
            at_ns=T0,
        )
        ledger.deposit(
            conn,
            account_id="acc_uk_gbp",
            entry_id="seed:usd",
            amount=Money(1_270_000, "USD", 2),
            at_ns=T0,
        )
        v = ledger.valuation(
            conn, "acc_uk_gbp", as_of_ns=T0, rates=[rate("GBP", "USD", 127_000, 5)], marks={}
        )
    assert v.status is ValuationStatus.COMPLETE
    assert v.equity_in_base_units == 2_000_000  # £10,000 + $12,700 / 1.27 = £20,000.00


# ---------------------------------------------------------------- FX rate store


def test_fx_rates_are_append_only_idempotent_and_age_limited(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    def snap(units: int, at: int) -> FxRateSnapshot:
        return FxRateSnapshot(
            base_currency="EUR",
            quote_currency="USD",
            rate_units=units,
            rate_scale=5,
            as_of_ns=at,
            source="paper",
        )

    with connect() as conn:
        with conn.transaction():
            assert reference.record_fx_rate(conn, snap(108_250, T0))
            assert not reference.record_fx_rate(conn, snap(108_250, T0))
            assert reference.record_fx_rate(conn, snap(108_300, T0 + 10 * SEC))
        newest = reference.latest_rates(conn, T0 + 15 * SEC, 60 * SEC)
        assert [(r.rate_units, r.as_of_ns) for r in newest] == [(108_300, T0 + 10 * SEC)]
        assert [r.rate_units for r in reference.latest_rates(conn, T0 + 5 * SEC, 60 * SEC)] == [
            108_250
        ]
        assert reference.latest_rates(conn, T0 + 15 * SEC, 3 * SEC) == []  # too old: fail closed
        assert reference.latest_rates(conn, T0 + 15 * SEC, 60 * SEC, sources=("other",)) == []
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM fx_rates")
        conn.rollback()
