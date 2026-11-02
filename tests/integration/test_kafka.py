"""Real Kafka: declared topology and outbox -> Kafka -> consumer -> ledger (F12, F44, F45).

Runs when ``COREI_TEST_KAFKA_BOOTSTRAP`` is set (CI). With
``COREI_REQUIRE_INTEGRATION=1`` a missing broker fails instead of skipping.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from confluent_kafka.admin import AdminClient

from corei.domain.control import ExecutionMode
from corei.domain.timeutil import FixedClock
from corei.infra.kafka import KafkaPublisher, run_consumer
from corei.infra.processing import Outcome, Processor, enqueue, relay_outbox
from corei.infra.topics import load_topology, topic_for
from corei.ledger.handlers import make_fill_handler

from .conftest import T0
from .test_processing import fill_envelope

pytestmark = [pytest.mark.integration, pytest.mark.kafka]
ROOT = Path(__file__).resolve().parents[2]
Connect = Callable[[], psycopg.Connection]


@pytest.fixture(scope="module")
def bootstrap_servers() -> str:
    servers = os.environ.get("COREI_TEST_KAFKA_BOOTSTRAP")
    if not servers:
        if os.environ.get("COREI_REQUIRE_INTEGRATION") == "1":
            pytest.fail("COREI_TEST_KAFKA_BOOTSTRAP is required when COREI_REQUIRE_INTEGRATION=1")
        pytest.skip("Kafka not configured (COREI_TEST_KAFKA_BOOTSTRAP)")
    return servers


@pytest.fixture(scope="module")
def env_name(bootstrap_servers: str) -> str:
    name = f"it{uuid.uuid4().hex[:8]}"
    script = [
        sys.executable,
        str(ROOT / "scripts/kafka_topology.py"),
        "--bootstrap",
        bootstrap_servers,
        "--env",
        name,
    ]
    verify_before = subprocess.run(
        [*script, "--verify"], capture_output=True, text=True, check=False
    )
    assert verify_before.returncode == 1 and "missing" in verify_before.stdout  # F45: no false PASS
    applied = subprocess.run([*script, "--apply"], capture_output=True, text=True, check=False)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    again = subprocess.run([*script, "--apply"], capture_output=True, text=True, check=False)
    assert again.returncode == 0 and "created" not in again.stdout  # idempotent
    return name


def test_topology_matches_declaration(bootstrap_servers: str, env_name: str) -> None:
    topics = AdminClient({"bootstrap.servers": bootstrap_servers}).list_topics(timeout=15).topics
    for spec in load_topology():
        assert len(topics[spec.name(env_name)].partitions) == spec.partitions


def test_outbox_to_kafka_to_ledger_applies_once(
    connect: Connect,
    clock: FixedClock,
    paper_accounts: dict[str, int],
    bootstrap_servers: str,
    env_name: str,
) -> None:
    env = fill_envelope("PX-kafka-1")
    with connect() as conn, conn.transaction():
        enqueue(conn, env, env_name, T0)
    publisher = KafkaPublisher(bootstrap_servers)
    assert relay_outbox(connect, publisher, clock) == 1
    # Duplicate publish, as after a relay crash between broker ack and marking the row.
    publisher.publish(
        topic_for("order.fill", env_name), env.partition_key, env.model_dump_json().encode()
    )
    publisher.flush()

    processor = Processor(
        consumer="ledger",
        environment=env_name,
        allowed_modes=frozenset({ExecutionMode.PAPER}),
        handlers={"order.fill": make_fill_handler(clock.now_ns)},
        connect=connect,
        clock=clock,
    )
    seen: list[Outcome] = []

    def handle(raw: bytes) -> Outcome:
        outcome = processor.process_raw(raw)
        seen.append(outcome)
        return outcome

    counts = run_consumer(
        bootstrap_servers,
        group_id=f"ledger-{env_name}",
        topics=[topic_for("order.fill", env_name)],
        handle=handle,
        should_stop=lambda: len(seen) >= 2,
        poll_timeout_s=0.5,
    )
    assert counts == {Outcome.APPLIED: 1, Outcome.DUPLICATE: 1}
    with connect() as conn:
        row = conn.execute(
            "SELECT quantity_units FROM positions WHERE account_id = 'acc_paper_a'"
        ).fetchone()
    assert row is not None and int(row[0]) == 25
