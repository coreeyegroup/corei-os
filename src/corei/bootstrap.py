"""Bootstrap a PAPER environment: migrate, set control modes, open and seed paper accounts.

    uv run python -m corei.bootstrap config/paper-bootstrap.yaml

Idempotent: every command and deposit has a deterministic id, so running it
twice changes nothing. It refuses LIVE modes and accounts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import psycopg
import yaml

from corei.contracts.execution import ControlCommandContract
from corei.control import store as control_store
from corei.domain.control import ControlCommandType, ExecutionMode
from corei.domain.identity import derive_id
from corei.domain.timeutil import Clock, SystemClock
from corei.domain.units import Money, to_scaled
from corei.infra.db import connection_factory, migrate
from corei.infra.settings import Settings
from corei.ledger import store as ledger

ACTOR = "bootstrap"


def _set_mode(
    conn: psycopg.Connection, settings: Settings, scope: str, mode: ExecutionMode, now: int
) -> None:
    states = control_store.load_states(conn)
    current = states.get(scope)
    if current is not None and current.mode is mode:
        return
    cmd = ControlCommandContract(
        command_id=derive_id(
            "ctl",
            "bootstrap.mode",
            {"scope": scope, "mode": mode.value, "from": current.version if current else 0},
        ),
        type=ControlCommandType.SET_MODE,
        scope=scope,
        expected_version=current.version if current else 0,
        actor=ACTOR,
        authorization_ref="bootstrap.config",
        mode=mode,
        at_ns=now,
    )
    control_store.execute(
        conn,
        cmd,
        tenant_id=settings.tenant_id,
        environment=settings.environment,
        recorded_at_ns=now,
    )


def bootstrap(config: dict[str, Any], settings: Settings, clock: Clock) -> dict[str, Any]:
    mode = ExecutionMode(config["mode"])
    if mode not in {ExecutionMode.PAPER, ExecutionMode.SIMULATION}:
        raise SystemExit(f"bootstrap only supports PAPER/SIMULATION, got {mode}")
    connect = connection_factory(settings.database_url)
    with connect() as conn:
        applied = migrate(conn)
    now = clock.now_ns()
    summary: dict[str, Any] = {"migrations_applied": applied, "accounts": []}
    with connect() as conn, conn.transaction():
        _set_mode(conn, settings, "GLOBAL", mode, now)
        for acct in config["accounts"]:
            ledger.open_account(
                conn,
                account_id=acct["account_id"],
                portfolio_id=acct["portfolio_id"],
                broker=acct["broker"],
                mode=mode,
                currency=acct["currency"],
                currency_scale=int(acct["currency_scale"]),
                at_ns=now,
            )
            _set_mode(conn, settings, f"ACCOUNT:{acct['account_id']}", mode, now)
            amount = Money(
                to_scaled(str(acct["seed_cash"]), int(acct["currency_scale"])),
                acct["currency"],
                int(acct["currency_scale"]),
            )
            added = ledger.deposit(
                conn,
                account_id=acct["account_id"],
                entry_id=f"seed:{acct['account_id']}:{acct.get('seed_id', 1)}",
                amount=amount,
                at_ns=now,
            )
            summary["accounts"].append({"account_id": acct["account_id"], "seeded": added})
    return summary


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    config = yaml.safe_load(Path(argv[1]).read_text())
    print(bootstrap(config, Settings.from_env(), SystemClock()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
