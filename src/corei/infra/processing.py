"""Transactional event processing: inbox -> handler -> state + outbox (F12, C11, F42).

    consume message
      -> parse and verify envelope           (unparseable: record rejection, ack)
      -> BEGIN
           insert inbox row                   (already there: duplicate, skip)
           mode fence                         (wrong mode: record rejection)
           handler(conn, envelope)            (state changes + events to emit)
           insert outbox rows
         COMMIT
      -> acknowledge the message offset       (only after COMMIT)

    outbox relay: unpublished rows -> publish -> mark published

A crash anywhere before COMMIT leaves no trace, so redelivery reprocesses.
A crash after COMMIT but before the offset ack redelivers a message whose inbox
row exists, so it is skipped. A relay crash after publish but before marking
re-publishes the same deterministic event id, which consumers' inboxes absorb.
Financial effects therefore apply exactly once even though delivery is
at-least-once. Broker-facing submission is *not* covered by this argument; it
uses SUBMISSION_UNKNOWN reconciliation instead.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from corei.contracts.envelope import EventEnvelope
from corei.domain.control import ExecutionMode
from corei.domain.timeutil import Clock
from corei.infra.topics import topic_for


class Outcome(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class PermanentError(ValueError):
    """A handler error that redelivery cannot fix; the event is recorded as rejected."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


Handler = Callable[[psycopg.Connection, EventEnvelope], Iterable[EventEnvelope]]


def enqueue(
    conn: psycopg.Connection, envelope: EventEnvelope, environment: str, now_ns: int
) -> None:
    """Write an event to the outbox inside the caller's transaction (idempotent by event_id)."""
    conn.execute(
        """INSERT INTO outbox (event_id, event_type, topic, partition_key, envelope, created_at_ns)
           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, event_id) DO NOTHING""",
        (
            envelope.event_id,
            envelope.event_type,
            topic_for(envelope.event_type, environment),
            envelope.partition_key,
            Jsonb(envelope.model_dump(mode="json")),
            now_ns,
        ),
    )


def record_rejection(
    conn: psycopg.Connection,
    consumer: str,
    reason_code: str,
    detail: str,
    raw: str,
    now_ns: int,
    event_id: str | None = None,
    event_type: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO rejected_events
           (consumer, event_id, event_type, reason_code, detail, raw, rejected_at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (consumer, event_id, event_type, reason_code, detail[:4000], raw[:100_000], now_ns),
    )


@dataclass
class Processor:
    consumer: str
    environment: str
    allowed_modes: frozenset[ExecutionMode]
    handlers: Mapping[str, Handler]
    connect: Callable[[], psycopg.Connection]
    clock: Clock

    def process_raw(self, raw: bytes | str) -> Outcome:
        """Entry point for transports: parse, then process. Never raises for bad input."""
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            envelope = EventEnvelope.model_validate_json(text)
        except ValidationError as exc:
            with self.connect() as conn, conn.transaction():
                record_rejection(
                    conn,
                    self.consumer,
                    "INVALID_ENVELOPE",
                    str(exc),
                    text,
                    self.clock.now_ns(),
                    event_id=_peek(text, "event_id"),
                    event_type=_peek(text, "event_type"),
                )
            return Outcome.REJECTED
        return self.process(envelope)

    def process(self, envelope: EventEnvelope) -> Outcome:
        now = self.clock.now_ns()
        with self.connect() as conn, conn.transaction():
            claimed = conn.execute(
                """INSERT INTO inbox (consumer, event_id, event_type, outcome, processed_at_ns)
                       VALUES (%s, %s, %s, 'APPLIED', %s)
                       ON CONFLICT (tenant_id, consumer, event_id) DO NOTHING RETURNING event_id""",
                (self.consumer, envelope.event_id, envelope.event_type, now),
            ).fetchone()
            if claimed is None:
                return Outcome.DUPLICATE
            reject = self._precheck(envelope)
            if reject is None:
                try:
                    with conn.transaction():  # savepoint: a permanent error undoes only the handler
                        for out in self.handlers[envelope.event_type](conn, envelope):
                            enqueue(conn, out, self.environment, now)
                except PermanentError as exc:
                    reject = (exc.reason_code, exc.detail)
            if reject is not None:
                conn.execute(
                    "UPDATE inbox SET outcome = 'REJECTED' WHERE consumer = %s AND event_id = %s",
                    (self.consumer, envelope.event_id),
                )
                record_rejection(
                    conn,
                    self.consumer,
                    reject[0],
                    reject[1],
                    envelope.model_dump_json(),
                    now,
                    envelope.event_id,
                    envelope.event_type,
                )
                return Outcome.REJECTED
        return Outcome.APPLIED

    def _precheck(self, envelope: EventEnvelope) -> tuple[str, str] | None:
        if envelope.mode not in self.allowed_modes:
            allowed = sorted(m.value for m in self.allowed_modes)
            return ("MODE_NOT_ALLOWED", f"{envelope.mode.value} not in {allowed}")
        if envelope.event_type not in self.handlers:
            return ("UNHANDLED_EVENT_TYPE", envelope.event_type)
        return None


def _peek(text: str, key: str) -> str | None:
    try:
        value = json.loads(text).get(key)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


class Publisher(Protocol):
    def publish(self, topic: str, key: str, value: bytes) -> None: ...

    def flush(self) -> None:
        """Block until every published message is acknowledged; raise on any failure."""


def relay_outbox(
    connect: Callable[[], psycopg.Connection], publisher: Publisher, clock: Clock, batch: int = 500
) -> int:
    """Publish unpublished outbox rows in order; mark them only after the broker acks."""
    with connect() as conn, conn.transaction():
        rows = conn.execute(
            """SELECT seq, topic, partition_key, envelope FROM outbox
               WHERE published_at_ns IS NULL ORDER BY seq LIMIT %s FOR UPDATE SKIP LOCKED""",
            (batch,),
        ).fetchall()
        if not rows:
            return 0
        for _seq, topic, key, env in rows:
            publisher.publish(topic, key, json.dumps(env, sort_keys=True).encode())
        publisher.flush()  # raises -> transaction rolls back -> rows stay unpublished
        conn.execute(
            "UPDATE outbox SET published_at_ns = %s WHERE seq = ANY(%s)",
            (clock.now_ns(), [r[0] for r in rows]),
        )
    return len(rows)
