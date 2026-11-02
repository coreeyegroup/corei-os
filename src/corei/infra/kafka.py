"""Kafka transport (confluent-kafka) and an in-memory bus with the same semantics for tests.

Producer: idempotent, acks=all, keyed by partition_key. ``flush`` raises if any
message failed, so the outbox relay never marks an unacknowledged row.

Consumer: auto-commit disabled. An offset is committed only after
``Processor.process_raw`` returns, i.e. after the database transaction commits.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer

from corei.infra.processing import Outcome


class PublishError(RuntimeError):
    pass


class KafkaPublisher:
    def __init__(self, bootstrap: str, client_id: str = "corei-outbox-relay") -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "client.id": client_id,
                "enable.idempotence": True,
                "acks": "all",
                "compression.type": "zstd",
                "linger.ms": 5,
            }
        )
        self._errors: list[str] = []

    def _on_delivery(self, err: KafkaError | None, msg: Message) -> None:
        if err is not None:
            self._errors.append(f"{msg.topic()}: {err}")

    def publish(self, topic: str, key: str, value: bytes) -> None:
        self._producer.produce(topic, key=key.encode(), value=value, on_delivery=self._on_delivery)
        self._producer.poll(0)

    def flush(self, timeout_s: float = 30.0) -> None:
        remaining = self._producer.flush(timeout_s)
        errors, self._errors = self._errors, []
        if remaining or errors:
            raise PublishError(f"{remaining} unacknowledged; errors: {errors[:5]}")


def run_consumer(
    bootstrap: str,
    group_id: str,
    topics: list[str],
    handle: Callable[[bytes], Outcome],
    should_stop: Callable[[], bool],
    poll_timeout_s: float = 1.0,
) -> dict[Outcome, int]:
    """Consume until ``should_stop()``; commit each offset only after ``handle`` returns."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "isolation.level": "read_committed",
        }
    )
    counts: dict[Outcome, int] = defaultdict(int)
    try:
        consumer.subscribe(topics)
        while not should_stop():
            msg = consumer.poll(poll_timeout_s)
            if msg is None:
                continue
            err = msg.error()
            if err is not None:
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(err)
            value = msg.value()
            outcome = handle(value if isinstance(value, bytes) else b"")
            consumer.commit(message=msg, asynchronous=False)
            counts[outcome] += 1
    finally:
        consumer.close()
    return dict(counts)


@dataclass
class InMemoryBus:
    """Single-process stand-in for Kafka: per-topic logs, per-group offsets, injectable faults."""

    topics: dict[str, list[tuple[str, bytes]]] = field(default_factory=lambda: defaultdict(list))
    offsets: dict[tuple[str, str], int] = field(default_factory=dict)
    fail_next_flush: bool = False
    _pending: list[tuple[str, str, bytes]] = field(default_factory=list)

    def publish(self, topic: str, key: str, value: bytes) -> None:
        self._pending.append((topic, key, value))

    def flush(self) -> None:
        if self.fail_next_flush:
            self.fail_next_flush = False
            self._pending.clear()
            raise PublishError("injected broker failure")
        for topic, key, value in self._pending:
            self.topics[topic].append((key, value))
        self._pending.clear()

    def poll(self, group: str, topic: str) -> tuple[int, bytes] | None:
        pos = self.offsets.get((group, topic), 0)
        log = self.topics.get(topic, [])
        return (pos, log[pos][1]) if pos < len(log) else None

    def commit(self, group: str, topic: str, offset: int) -> None:
        self.offsets[(group, topic)] = offset + 1

    def drain(self, group: str, topic: str, handle: Callable[[bytes], Any]) -> int:
        n = 0
        while (item := self.poll(group, topic)) is not None:
            handle(item[1])
            self.commit(group, topic, item[0])
            n += 1
        return n
