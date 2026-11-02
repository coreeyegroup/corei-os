"""Event handlers owned by the ledger, for use with ``corei.infra.processing.Processor``."""

from __future__ import annotations

from collections.abc import Callable

import psycopg

from corei.contracts.envelope import EventEnvelope, make_envelope
from corei.contracts.execution import Fill
from corei.infra.processing import Handler, PermanentError
from corei.ledger import store
from corei.ledger.store import FillOutcome, LedgerError

PRODUCER = "corei.ledger"
PRODUCER_VERSION = "0.2.0"


def make_fill_handler(now_ns: Callable[[], int]) -> Handler:
    """Apply an ``order.fill`` once and emit the account's new ``portfolio.snapshot``."""

    def handle(conn: psycopg.Connection, envelope: EventEnvelope) -> list[EventEnvelope]:
        fill = envelope.typed_payload()
        assert isinstance(fill, Fill)
        if fill.mode is not envelope.mode:
            raise PermanentError("MODE_MISMATCH", "fill mode differs from envelope mode")
        try:
            outcome = store.apply_fill(conn, fill, now_ns())
        except LedgerError as exc:  # includes FillConflict: needs reconciliation, not retry
            raise PermanentError(type(exc).__name__.upper(), str(exc)) from exc
        if outcome is FillOutcome.DUPLICATE:
            return []
        snap = store.snapshot(conn, fill.account_id, envelope.occurred_at_ns)
        return [
            make_envelope(
                event_type="portfolio.snapshot",
                payload=snap,
                tenant_id=envelope.tenant_id,
                mode=envelope.mode,
                occurred_at_ns=envelope.occurred_at_ns,
                recorded_at_ns=now_ns(),
                producer=PRODUCER,
                producer_version=PRODUCER_VERSION,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.event_id,
            )
        ]

    return handle
