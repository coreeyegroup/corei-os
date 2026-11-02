"""Inbox/outbox processing under redelivery and failure at every boundary (F12, C11)."""

import json
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from corei.contracts.envelope import EventEnvelope, make_envelope
from corei.contracts.execution import Fill
from corei.domain.control import ExecutionMode
from corei.domain.timeutil import FixedClock
from corei.infra.kafka import InMemoryBus, PublishError
from corei.infra.processing import Outcome, PermanentError, Processor, enqueue, relay_outbox
from corei.infra.topics import topic_for
from corei.ledger import store as ledger
from corei.ledger.handlers import make_fill_handler

from .conftest import T0

pytestmark = pytest.mark.integration
Connect = Callable[[], psycopg.Connection]
VECTOR = Path(__file__).resolve().parents[2] / "contracts/vectors/valid/order.fill.json"
ENV = "it"


def fill_envelope(
    execution_id: str = "PX-0001", mode: ExecutionMode = ExecutionMode.PAPER
) -> EventEnvelope:
    base = EventEnvelope.model_validate_json(VECTOR.read_text())
    payload = base.payload | {
        "account_id": "acc_paper_a",
        "broker_execution_id": execution_id,
        "mode": mode.value,
        "fill_id": f"fil_{execution_id.lower().replace('-', '')}",
    }
    fill = Fill.model_validate_json(json.dumps(payload))
    return make_envelope(
        event_type="order.fill",
        payload=fill,
        tenant_id="corei",
        mode=mode,
        occurred_at_ns=fill.executed_at_ns,
        recorded_at_ns=T0,
        producer="test",
        producer_version="1",
        correlation_id="cor_test",
    )


def processor(connect: Connect, clock: FixedClock, handlers: dict | None = None) -> Processor:  # type: ignore[type-arg]
    return Processor(
        consumer="ledger",
        environment=ENV,
        allowed_modes=frozenset({ExecutionMode.PAPER}),
        handlers=handlers or {"order.fill": make_fill_handler(clock.now_ns)},
        connect=connect,
        clock=clock,
    )


def count(connect: Connect, table: str, where: str = "TRUE") -> int:
    with connect() as conn:
        row = conn.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()
        return int(row[0]) if row else 0


def position(connect: Connect) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT quantity_units FROM positions WHERE account_id='acc_paper_a'"
        ).fetchone()
        return int(row[0]) if row else 0


def test_redelivery_applies_once_and_emits_one_snapshot(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    p, env = processor(connect, clock), fill_envelope()
    assert p.process(env) is Outcome.APPLIED
    assert p.process(env) is Outcome.DUPLICATE
    assert p.process_raw(env.model_dump_json()) is Outcome.DUPLICATE
    assert position(connect) == 50
    assert count(connect, "outbox", "event_type = 'portfolio.snapshot'") == 1


def test_mode_fence_rejects_and_records(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    env = fill_envelope(mode=ExecutionMode.BACKTEST)
    assert processor(connect, clock).process(env) is Outcome.REJECTED
    assert position(connect) == 0
    assert count(connect, "rejected_events", "reason_code = 'MODE_NOT_ALLOWED'") == 1
    assert processor(connect, clock).process(env) is Outcome.DUPLICATE  # not retried forever


def test_crash_inside_handler_leaves_no_trace_and_redelivery_applies(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    real = make_fill_handler(clock.now_ns)

    def crash_after_write(conn: psycopg.Connection, env: EventEnvelope) -> list[EventEnvelope]:
        list(real(conn, env))  # ledger rows written...
        raise RuntimeError("process killed")  # ...then the process dies before commit

    env = fill_envelope()
    with pytest.raises(RuntimeError):
        processor(connect, clock, {"order.fill": crash_after_write}).process(env)
    assert position(connect) == 0 and count(connect, "inbox") == 0 and count(connect, "outbox") == 0
    assert processor(connect, clock).process(env) is Outcome.APPLIED
    assert position(connect) == 50


def test_permanent_error_is_recorded_without_partial_effects(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    def bad(conn: psycopg.Connection, env: EventEnvelope) -> list[EventEnvelope]:
        conn.execute("UPDATE accounts SET state_version = state_version + 100")
        raise PermanentError("BAD_INPUT", "cannot be fixed by retry")

    assert (
        processor(connect, clock, {"order.fill": bad}).process(fill_envelope()) is Outcome.REJECTED
    )
    with connect() as conn:
        row = conn.execute("SELECT max(state_version) FROM accounts").fetchone()
    assert row is not None and int(row[0]) < 100  # savepoint undid the handler's writes
    assert count(connect, "inbox", "outcome = 'REJECTED'") == 1


def test_garbage_and_tampered_messages_are_quarantined(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    p = processor(connect, clock)
    assert p.process_raw(b"not json") is Outcome.REJECTED
    tampered = json.loads(fill_envelope().model_dump_json())
    tampered["payload"]["quantity_units"] = 999
    assert p.process_raw(json.dumps(tampered)) is Outcome.REJECTED
    assert count(connect, "rejected_events", "reason_code = 'INVALID_ENVELOPE'") == 2
    assert position(connect) == 0


def test_fill_conflict_is_rejected_for_reconciliation(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    p = processor(connect, clock)
    first = fill_envelope("PX-77")
    assert p.process(first) is Outcome.APPLIED
    payload = first.payload | {"quantity_units": 75}
    changed = make_envelope(
        event_type="order.fill",
        payload=Fill.model_validate_json(json.dumps(payload)),
        tenant_id="corei",
        mode=ExecutionMode.PAPER,
        occurred_at_ns=T0,
        recorded_at_ns=T0,
        producer="test",
        producer_version="1",
        correlation_id="cor_test",
    )
    assert p.process(changed) is Outcome.REJECTED
    assert count(connect, "rejected_events", "reason_code = 'FILLCONFLICT'") == 1
    assert position(connect) == 50


def test_outbox_relay_end_to_end_with_broker_failure_and_duplicate_publish(
    connect: Connect, clock: FixedClock, paper_accounts: dict[str, int]
) -> None:
    bus = InMemoryBus()
    fills_topic = topic_for("order.fill", ENV)
    # Upstream producer writes a fill into its outbox (same DB here for the test).
    with connect() as conn, conn.transaction():
        enqueue(conn, fill_envelope(), ENV, T0)
    bus.fail_next_flush = True
    with pytest.raises(PublishError):
        relay_outbox(connect, bus, clock)
    assert count(connect, "outbox", "published_at_ns IS NULL") == 1  # nothing marked
    assert relay_outbox(connect, bus, clock) == 1
    # Simulate a relay crash after publish but before marking: the same event is published again.
    bus.topics[fills_topic].append(bus.topics[fills_topic][0])
    p = processor(connect, clock)
    outcomes = []
    bus.drain("ledger", fills_topic, lambda raw: outcomes.append(p.process_raw(raw)))
    assert outcomes == [Outcome.APPLIED, Outcome.DUPLICATE]
    assert position(connect) == 50
    with connect() as conn:
        snap = ledger.snapshot(conn, "acc_paper_a", T0)
    assert snap.positions[0].quantity_units == 50
