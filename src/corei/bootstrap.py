"""Bootstrap a PAPER or BACKTEST environment from a YAML description.

    uv run python -m corei.bootstrap config/paper-bootstrap.yaml

Applies migrations, then registers legal entities, brokers, instruments,
accounts (with ranked broker bindings), account groups, seed cash and control
modes — all idempotently, in one transaction. It refuses LIVE ledgers and
LIVE modes: live activation is a separately authorized step (Delivery H).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import psycopg
import yaml

from corei.contracts.execution import ControlCommandContract
from corei.contracts.reference import (
    AccountGroup,
    AccountSpec,
    AssetClass,
    BrokerSpec,
    InstrumentKind,
    InstrumentSpec,
    LedgerKind,
    LegalEntity,
)
from corei.control import store as control_store
from corei.domain.control import ControlCommandType, ExecutionMode, ScopeKind, scope_key
from corei.domain.currencies import currency_scale
from corei.domain.identity import derive_id
from corei.domain.timeutil import Clock, SystemClock
from corei.domain.units import Money, to_scaled
from corei.infra.db import connection_factory, migrate
from corei.infra.settings import Settings
from corei.ledger import store as ledger
from corei.reference import store as reference

ACTOR = "bootstrap"
FX_LOT_UNITS = 100_000


def fx_instrument(row: dict[str, Any], broker_ids: list[str]) -> InstrumentSpec:
    """Expand a compact pair definition using standard conventions (0.01 lot steps)."""
    quote = row["quote"]
    return InstrumentSpec(
        instrument_id=f"ins_{row['symbol'].lower()}",
        version=int(row.get("version", 1)),
        asset_class=AssetClass(row.get("asset_class", "FX")),
        kind=InstrumentKind(row.get("kind", "SPOT")),
        venue=row.get("venue", "OTC"),
        symbol=row["symbol"],
        tradable=True,
        base_currency=row["base"],
        quote_currency=quote,
        pip_size_units=row.get("pip"),
        price_scale=int(row["price_scale"]),
        tick_size_units=int(row.get("tick", 1)),
        quantity_scale=2,
        quantity_step_units=1,
        min_quantity_units=1,
        contract_multiplier_units=int(row.get("multiplier", FX_LOT_UNITS)),
        multiplier_scale=0,
        settlement_currency=quote,
        currency_scale=currency_scale(quote),
        session_calendar=row.get("session_calendar", "fx-24x5"),
        provider_keys={b: row["symbol"] for b in broker_ids},
        valid_from_ns=1,
    )


def _set_mode(
    conn: psycopg.Connection, settings: Settings, scope: str, mode: ExecutionMode, now: int
) -> None:
    current = control_store.load_states(conn).get(scope)
    if current is not None and current.mode is mode:
        return
    version = current.version if current else 0
    cmd = ControlCommandContract(
        command_id=derive_id(
            "ctl", "bootstrap.mode", {"scope": scope, "mode": mode.value, "from": version}
        ),
        type=ControlCommandType.SET_MODE,
        scope=scope,
        expected_version=version,
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
        envelope_mode=mode,
    )


def bootstrap(config: dict[str, Any], settings: Settings, clock: Clock) -> dict[str, Any]:
    mode = ExecutionMode(config["mode"])
    if mode not in {ExecutionMode.PAPER, ExecutionMode.BACKTEST}:
        raise SystemExit(f"bootstrap only supports PAPER/BACKTEST, got {mode}")
    ledger_kind = LedgerKind(mode.value)
    connect = connection_factory(settings.database_url, settings.tenant_id)
    with connect() as conn:
        applied = migrate(conn)
    now = clock.now_ns()
    summary: dict[str, Any] = {"migrations_applied": applied, "accounts": []}
    with connect() as conn, conn.transaction():
        for e in config.get("entities", []):
            reference.upsert_entity(conn, LegalEntity.model_validate(e, strict=False))
        broker_ids = []
        for b in config.get("brokers", []):
            reference.upsert_broker(conn, BrokerSpec.model_validate(b, strict=False))
            broker_ids.append(b["broker_id"])
        for row in config.get("instruments", []):
            reference.upsert_instrument(conn, fx_instrument(row, broker_ids))
        _set_mode(conn, settings, "GLOBAL", mode, now)
        for a in config.get("accounts", []):
            spec = AccountSpec.model_validate(
                {k: v for k, v in a.items() if k != "seed"} | {"ledger_kind": ledger_kind.value},
                strict=False,
            )
            reference.register_account(conn, spec, now)
            _set_mode(conn, settings, scope_key(ScopeKind.ACCOUNT, spec.account_id), mode, now)
            seeded = []
            for i, s in enumerate(a.get("seed", []), start=1):
                ccy = s["currency"]
                amount = Money(
                    to_scaled(str(s["amount"]), currency_scale(ccy)), ccy, currency_scale(ccy)
                )
                seeded.append(
                    ledger.deposit(
                        conn,
                        account_id=spec.account_id,
                        entry_id=f"seed:{spec.account_id}:{s.get('seed_id', i)}",
                        amount=amount,
                        at_ns=now,
                    )
                )
            summary["accounts"].append({"account_id": spec.account_id, "seeded": any(seeded)})
        for g in config.get("groups", []):
            reference.upsert_group(conn, AccountGroup.model_validate(g, strict=False))
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
