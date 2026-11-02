"""Readiness that proves capability, not just process life (finding F36).

Ready means: the database answers, its schema is exactly at this build's
migration head, and the GLOBAL control scope exists (without it every order is
refused, so the service could not do useful work). Kafka is checked when a
probe is configured. Each check reports its own status and detail.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg

from corei.infra.db import schema_status


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Readiness:
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return all(c.ok for c in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def check_readiness(
    connect: Callable[[], psycopg.Connection],
    kafka_probe: Callable[[], str] | None = None,
) -> Readiness:
    checks: list[Check] = []
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
            checks.append(Check("database", True, "reachable"))
            applied, expected = schema_status(conn)
            checks.append(
                Check("schema", applied == expected, f"applied {applied}, expected {expected}")
            )
            row = (
                conn.execute(
                    "SELECT mode, halted, version FROM control_state WHERE scope = 'GLOBAL'"
                ).fetchone()
                if applied
                else None
            )
            if row is None:
                checks.append(Check("control", False, "GLOBAL control state missing (fail closed)"))
            else:
                checks.append(
                    Check("control", True, f"GLOBAL mode={row[0]} halted={row[1]} v{row[2]}")
                )
    except psycopg.Error as exc:
        checks.append(Check("database", False, type(exc).__name__))
    if kafka_probe is not None:
        try:
            checks.append(Check("kafka", True, kafka_probe()))
        except Exception as exc:
            checks.append(Check("kafka", False, type(exc).__name__))
    return Readiness(tuple(checks))
