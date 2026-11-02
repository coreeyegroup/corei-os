from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from corei.contracts.base import MoneyAmount
from corei.contracts.execution import Fill
from corei.contracts.trading import Side
from corei.domain.control import ExecutionMode
from corei.domain.identity import derive_id
from corei.domain.units import Money
from corei.infra.db import MigrationError, migrate, schema_status
from corei.ledger import store as ledger
from corei.ledger.store import FillConflict, FillOutcome, LedgerError, ReservationStatus

from .conftest import SEC, T0

pytestmark = pytest.mark.integration
Connect = Callable[[], psycopg.Connection]
_exec_counter = iter(range(1, 10**9))


def fill(
    account: str = "acc_paper_a",
    side: Side = Side.BUY,
    qty: int = 25,
    price: int = 2_350_000,
    exec_id: str | None = None,
    fee: int = 2_000,
    instrument: str = "ins_test_fut_a",
) -> Fill:
    exec_id = exec_id or f"PX-{next(_exec_counter)}"
    return Fill(
        fill_id=derive_id(
            "fil", "fill", {"account": account, "broker": "paper", "execution": exec_id}
        ),
        account_id=account,
        broker="paper",
        order_id="ord_x",
        broker_order_id="PB-1",
        broker_execution_id=exec_id,
        instrument_id=instrument,
        side=side,
        quantity_units=qty,
        quantity_scale=0,
        price_units=price,
        price_scale=2,
        fees=(MoneyAmount(units=fee, currency="INR", scale=2),) if fee else (),
        executed_at_ns=T0 + SEC,
        received_at_ns=T0 + 2 * SEC,
        mode=ExecutionMode.PAPER,
    )


# ---------------------------------------------------------------- migrations


def test_migrations_are_idempotent_and_at_head(connect: Connect) -> None:
    with connect() as conn:
        assert migrate(conn) == []
        applied, expected = schema_status(conn)
        assert applied == expected >= 1


def test_modified_migration_is_refused(connect: Connect) -> None:
    with connect() as conn:
        conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        with pytest.raises(MigrationError, match="modified"):
            migrate(conn)
        conn.rollback()


def test_journal_and_fills_are_append_only(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE ledger_entries SET cash_delta_units = 0")
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM ledger_entries")


# ---------------------------------------------------------------- accounts and deposits


def test_seed_deposit_is_idempotent(connect: Connect, paper_accounts: dict[str, int]) -> None:
    with connect() as conn, conn.transaction():
        again = ledger.deposit(
            conn,
            account_id="acc_paper_a",
            entry_id="seed:acc_paper_a",
            amount=Money(100_000_000, "INR", 2),
            at_ns=T0,
        )
        assert again is False
        assert ledger.cash_balance(conn, "acc_paper_a", "INR") == paper_accounts["acc_paper_a"]
        with pytest.raises(LedgerError):
            ledger.deposit(
                conn,
                account_id="acc_paper_a",
                entry_id="seed:acc_paper_a",
                amount=Money(1, "INR", 2),
                at_ns=T0,
            )


def test_live_accounts_cannot_be_opened(connect: Connect) -> None:
    with connect() as conn, pytest.raises(LedgerError, match="mode"):
        ledger.open_account(
            conn,
            account_id="acc_live",
            portfolio_id="pf_live",
            broker="upstox",
            mode=ExecutionMode.LIVE_AUTO,
            currency="INR",
            currency_scale=2,
            at_ns=T0,
        )


# ---------------------------------------------------------------- fills


def test_duplicate_fill_changes_state_once_f21_f22(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    f = fill(qty=50, price=2_350_000, fee=2_000)
    with connect() as conn, conn.transaction():
        assert ledger.apply_fill(conn, f, T0 + 3 * SEC) is FillOutcome.APPLIED
        assert ledger.apply_fill(conn, f, T0 + 4 * SEC) is FillOutcome.DUPLICATE
        snap = ledger.snapshot(conn, "acc_paper_a", T0 + 5 * SEC)
    assert snap.positions[0].quantity_units == 50
    assert snap.positions[0].average_price_units == 2_350_000
    # 50 x 23,500.00 = 11,75,000.00 INR = 117,500,000 paise, plus 20.00 fee
    assert snap.cash[0].units == paper_accounts["acc_paper_a"] - 117_500_000 - 2_000


def test_same_execution_with_different_content_is_a_conflict(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        ledger.apply_fill(conn, fill(exec_id="PX-dup", qty=25), T0)
        with pytest.raises(FillConflict):
            ledger.apply_fill(conn, fill(exec_id="PX-dup", qty=50), T0)


def test_accounts_are_isolated_f03(connect: Connect, paper_accounts: dict[str, int]) -> None:
    with connect() as conn, conn.transaction():
        ledger.apply_fill(conn, fill(account="acc_paper_a", exec_id="PX-same"), T0)
        # Same broker execution id in another account is a different execution.
        assert (
            ledger.apply_fill(conn, fill(account="acc_paper_b", exec_id="PX-same"), T0)
            is FillOutcome.APPLIED
        )
        a = ledger.snapshot(conn, "acc_paper_a", T0)
        b = ledger.snapshot(conn, "acc_paper_b", T0)
    assert a.positions[0].quantity_units == b.positions[0].quantity_units == 25
    assert (
        a.cash[0].units - paper_accounts["acc_paper_a"]
        == b.cash[0].units - paper_accounts["acc_paper_b"]
    )


def test_fill_scales_must_match_reference_data(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    bad = fill().model_copy(update={"price_scale": 4})
    with connect() as conn, conn.transaction(), pytest.raises(LedgerError, match="scales"):
        ledger.apply_fill(conn, bad, T0)


@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    st.lists(
        st.tuples(
            st.booleans(),
            st.integers(1, 8),
            st.integers(2_000_000, 2_500_000),
            st.integers(0, 5_000),
        ),
        min_size=1,
        max_size=12,
    )
)
def test_property_ledger_conserves_cash(
    connect: Connect, paper_accounts: dict[str, int], trades: list[tuple[bool, int, int, int]]
) -> None:
    """cash change == realized P&L - open cost - fees, exactly, after any fill sequence."""
    with connect() as conn:
        conn.execute("DELETE FROM positions WHERE account_id = 'acc_paper_b'")
        conn.commit()
        start = ledger.cash_balance(conn, "acc_paper_b", "INR")
        fees = 0
        with conn.transaction():
            for buy, lots, price, fee in trades:
                ledger.apply_fill(
                    conn,
                    fill(
                        account="acc_paper_b",
                        side=Side.BUY if buy else Side.SELL,
                        qty=25 * lots,
                        price=price - price % 5,
                        fee=fee,
                        instrument="ins_test_fut_b",
                    ),
                    T0,
                )
                fees += fee
            row = conn.execute(
                "SELECT open_cost_units, realized_pnl_units FROM positions "
                "WHERE account_id = 'acc_paper_b' AND instrument_id = 'ins_test_fut_b'"
            ).fetchone()
            assert row is not None
            open_cost, realized = int(row[0]), int(row[1])
            delta = ledger.cash_balance(conn, "acc_paper_b", "INR") - start
            assert delta == realized - open_cost - fees
            raise psycopg.Rollback()  # undo, so every example starts from the same seed


# ---------------------------------------------------------------- reservations


def _reserve(
    conn: psycopg.Connection, command: str, amount: int, **kw: object
) -> ledger.ReservationResult:
    return ledger.reserve(
        conn,
        account_id=kw.pop("account", "acc_paper_a"),
        command_id=command,  # type: ignore[arg-type]
        amount=Money(amount, "INR", 2),
        mode=kw.pop("mode", ExecutionMode.PAPER),  # type: ignore[arg-type]
        as_of_ns=kw.pop("as_of", T0),
        expires_at_ns=kw.pop("expires", T0 + 60 * SEC),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def test_reservation_limits_and_idempotency(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    cash = paper_accounts["acc_paper_a"]
    with connect() as conn, conn.transaction():
        first = _reserve(conn, "cmd_1", cash - 100)
        assert first.ok and first.available_units == 100
        assert _reserve(conn, "cmd_1", cash - 100).reservation_id == first.reservation_id  # replay
        denied = _reserve(conn, "cmd_2", 101)
        assert not denied.ok and denied.reason == "INSUFFICIENT_CAPITAL"
        with pytest.raises(LedgerError, match="different terms"):
            _reserve(conn, "cmd_1", 5)
        assert ledger.close_reservation(
            conn,
            reservation_id=first.reservation_id or "",
            status=ReservationStatus.RELEASED,
            at_ns=T0,
        )
        assert _reserve(conn, "cmd_2", 101).ok


def test_reservation_expiry_frees_capital(connect: Connect, paper_accounts: dict[str, int]) -> None:
    cash = paper_accounts["acc_paper_a"]
    with connect() as conn, conn.transaction():
        assert _reserve(conn, "cmd_a", cash, expires=T0 + SEC).ok
        assert not _reserve(conn, "cmd_b", 1, as_of=T0 + SEC // 2).ok
        assert _reserve(conn, "cmd_b", 1, as_of=T0 + SEC).ok  # expiry is exclusive at expires_at


def test_reservation_refuses_stale_portfolio_version_and_wrong_mode(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    with connect() as conn, conn.transaction():
        version = ledger.get_account(conn, "acc_paper_a").state_version
        assert _reserve(conn, "cmd_v1", 100, expected_state_version=version).ok
        stale = _reserve(conn, "cmd_v2", 100, expected_state_version=version)
        assert not stale.ok and stale.reason == "STALE_PORTFOLIO_STATE"
        wrong = _reserve(conn, "cmd_m", 100, mode=ExecutionMode.SIMULATION)
        assert not wrong.ok and wrong.reason == "MODE_MISMATCH"


def test_parallel_reservations_never_exceed_capital_f04(
    connect: Connect, paper_accounts: dict[str, int]
) -> None:
    cash = paper_accounts["acc_paper_b"]
    chunk = cash // 10  # exactly 10 can fit

    def attempt(i: int) -> bool:
        with connect() as conn, conn.transaction():
            return _reserve(conn, f"cmd_par_{i}", chunk, account="acc_paper_b").ok

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(40)))
    assert sum(results) == 10
    with connect() as conn:
        assert ledger.available_cash(conn, "acc_paper_b", "INR") == cash - 10 * chunk
