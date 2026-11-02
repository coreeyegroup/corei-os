"""Postgres access and the migration runner (findings F35, F43).

Migrations are SQL files shipped inside the package, applied in order under an
advisory lock, each in its own transaction. Every applied migration's SHA-256
is recorded; if a file changes after it was applied, startup fails instead of
silently diverging from the deployed schema.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

import psycopg

ConnectionFactory = Callable[[], psycopg.Connection]
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_KEY = 0x0C0E10D8  # advisory lock id for migrations


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def connection_factory(database_url: str) -> ConnectionFactory:
    def connect() -> psycopg.Connection:
        return psycopg.connect(database_url, autocommit=False)

    return connect


def available_migrations() -> list[Migration]:
    root = resources.files("corei.infra.migrations")
    found = []
    for entry in root.iterdir():
        m = _MIGRATION_RE.match(entry.name)
        if m:
            found.append(Migration(int(m.group(1)), entry.name, entry.read_text("utf-8")))
    found.sort(key=lambda mig: mig.version)
    versions = [mig.version for mig in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"migration versions must be contiguous from 1, got {versions}")
    return found


def expected_head() -> int:
    return len(available_migrations())


def applied_versions(conn: psycopg.Connection) -> dict[int, str]:
    exists = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()
    if not exists or exists[0] is None:
        return {}
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(v): str(c) for v, c in rows}


def migrate(conn: psycopg.Connection) -> list[int]:
    """Apply pending migrations; return the versions applied now."""
    applied_now: list[int] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                   checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        done = applied_versions(conn)
        for mig in available_migrations():
            if mig.version in done:
                if done[mig.version] != mig.checksum:
                    raise MigrationError(
                        f"migration {mig.name} was modified after it was applied; "
                        "add a new migration instead"
                    )
                continue
            conn.execute(mig.sql.encode())  # trusted SQL shipped with the package
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (mig.version, mig.name, mig.checksum),
            )
            applied_now.append(mig.version)
        extra = set(done) - {m.version for m in available_migrations()}
        if extra:
            raise MigrationError(
                f"database has migrations this build doesn't know: {sorted(extra)}"
            )
    return applied_now


def schema_status(conn: psycopg.Connection) -> tuple[int, int]:
    """(applied head, expected head)."""
    done = applied_versions(conn)
    return (max(done) if done else 0, expected_head())
