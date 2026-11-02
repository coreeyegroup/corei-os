"""Durable control, authenticated commands and readiness (F05, F06, F36; C03, C08).

Uses the shipped multi-entity PAPER bootstrap (US / UK / Dubai accounts on the paper broker).
"""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from corei.api.app import AppContext, create_app
from corei.bootstrap import bootstrap
from corei.control import store as control_store
from corei.control.auth import TokenAuthenticator
from corei.domain.control import ExecutionMode, OrderScope, check_new_order, fence_still_valid
from corei.domain.timeutil import FixedClock
from corei.infra.settings import Settings

pytestmark = pytest.mark.integration
Connect = Callable[[], psycopg.Connection]
TOKENS = {"alice": "op-token-alice", "rita": "risk-token-rita", "vic": "view-token-vic"}
CONFIG: dict[str, Any] = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "config/paper-bootstrap.yaml").read_text()
)
US, UK, AE = "acc_us_paper_01", "acc_uk_paper_01", "acc_ae_paper_01"
ENTITY = {US: "ent_us", UK: "ent_uk", AE: "ent_ae"}


def _h(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def client(connect: Connect, clock: FixedClock) -> TestClient:
    auth = TokenAuthenticator.from_config(
        f"alice:trading_ops:{_h(TOKENS['alice'])};rita:risk_admin:{_h(TOKENS['rita'])};"
        f"vic:viewer:{_h(TOKENS['vic'])}"
    )
    ctx = AppContext(
        connect=connect,
        authenticator=auth,
        clock=clock,
        environment="it",
        tenant_id="corei",
        envelope_mode=ExecutionMode.PAPER,
    )
    return TestClient(create_app(ctx))


def hdr(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def settings(database_url: str) -> Settings:
    return Settings.from_env({"COREI_ENV": "it", "DATABASE_URL": database_url})


def order_scope(account: str = US, broker: str = "brk_paper") -> OrderScope:
    return OrderScope(
        entity_id=ENTITY[account],
        broker_id=broker,
        account_id=account,
        strategy_id="forex-momentum-strategy",
        instrument_id="ins_eurusd",
    )


def eligible(
    connect: Connect, account: str = US, mode: ExecutionMode = ExecutionMode.PAPER
) -> tuple[bool, str]:
    with connect() as conn:
        e = check_new_order(
            control_store.load_states(conn),
            order_scope(account),
            order_mode=mode,
            risk_increasing=True,
        )
    return e.allowed, e.reason


def versions(api: TestClient) -> dict[str, int]:
    return {s["scope"]: s["version"] for s in api.get("/control/state", headers=hdr("vic")).json()}


def command(api: TestClient, who: str, **body: Any) -> Any:
    return api.post("/control/commands", headers=hdr(who), json=body)


def test_fresh_system_is_fail_closed_and_not_ready(connect: Connect, clock: FixedClock) -> None:
    assert eligible(connect) == (False, "CONTROL_STATE_MISSING")
    r = client(connect, clock).get("/health/ready")
    assert r.status_code == 503
    checks = {c["name"]: c["ok"] for c in r.json()["detail"]["checks"]}
    assert checks == {"database": True, "schema": True, "control": False}


def test_bootstrap_is_idempotent_and_enables_paper_in_every_region(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    first = bootstrap(CONFIG, settings(database_url), clock)
    second = bootstrap(CONFIG, settings(database_url), clock)
    assert [a["seeded"] for a in first["accounts"]] == [True, True, True]
    assert [a["seeded"] for a in second["accounts"]] == [False, False, False]
    for account in (US, UK, AE):
        assert eligible(connect, account) == (True, "OK")
    assert eligible(connect, US, ExecutionMode.LIVE_AUTO) == (False, "MODE_MISMATCH")
    assert client(connect, clock).get("/health/ready").status_code == 200
    with connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM outbox WHERE event_type = 'control.state'"
        ).fetchone()
    assert row is not None and int(row[0]) == 4  # GLOBAL + three accounts, once each


def test_halt_survives_restart_and_blocks_only_new_risk(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    with connect() as conn:
        fence = check_new_order(
            control_store.load_states(conn),
            order_scope(US),
            order_mode=ExecutionMode.PAPER,
            risk_increasing=True,
        ).fence
    r = command(
        api,
        "alice",
        command_id="cmd-halt-0001",
        type="HALT_NEW_ORDERS",
        scope=f"ACCOUNT:{US}",
        expected_version=versions(api)[f"ACCOUNT:{US}"],
        reason="operator kill switch",
    )
    assert r.status_code == 200 and r.json()["state"]["halted"] is True

    api2 = client(connect, clock)  # "restart": a new app reads the same durable state
    halted = {s["scope"]: s for s in api2.get("/control/state", headers=hdr("vic")).json()}
    assert halted[f"ACCOUNT:{US}"]["halted"] is True
    assert eligible(connect, US) == (False, f"HALTED:ACCOUNT:{US}")
    assert eligible(connect, UK) == (True, "OK")  # scoped to one account
    with connect() as conn:
        states = control_store.load_states(conn)
        assert not fence_still_valid(states, fence)
        reducing = check_new_order(
            states, order_scope(US), order_mode=ExecutionMode.PAPER, risk_increasing=False
        )
    assert reducing.allowed  # risk-reducing orders still allowed under a halt


def test_entity_and_broker_halts_scope_correctly(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    with connect() as conn:
        fence = check_new_order(
            control_store.load_states(conn),
            order_scope(UK),
            order_mode=ExecutionMode.PAPER,
            risk_increasing=True,
        ).fence
    # First halt on an entity scope creates it from v0, and still breaks open fences.
    assert (
        command(
            api,
            "alice",
            command_id="cmd-halt-ent-uk",
            type="HALT_NEW_ORDERS",
            scope="ENTITY:ent_uk",
            expected_version=0,
            reason="FCA review",
        ).status_code
        == 200
    )
    assert eligible(connect, UK) == (False, "HALTED:ENTITY:ent_uk")
    assert eligible(connect, US) == (True, "OK")
    assert eligible(connect, AE) == (True, "OK")
    with connect() as conn:
        assert not fence_still_valid(control_store.load_states(conn), fence)

    assert (
        command(
            api,
            "alice",
            command_id="cmd-halt-brk",
            type="HALT_NEW_ORDERS",
            scope="BROKER:brk_paper",
            expected_version=0,
            reason="broker outage",
        ).status_code
        == 200
    )
    assert eligible(connect, US) == (False, "HALTED:BROKER:brk_paper")
    assert eligible(connect, AE) == (False, "HALTED:BROKER:brk_paper")

    # Modes cannot be set on entity/broker scopes.
    r = command(
        api,
        "rita",
        command_id="cmd-mode-ent",
        type="SET_MODE",
        scope="ENTITY:ent_us",
        expected_version=0,
        mode="PAPER",
    )
    assert r.status_code == 422


def test_strategy_mode_override_per_account(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    pair = f"ACCOUNT_STRATEGY:{AE}:forex-momentum-strategy"
    assert (
        command(
            api,
            "rita",
            command_id="cmd-pair-off",
            type="SET_MODE",
            scope=pair,
            expected_version=0,
            mode="DISABLED",
        ).status_code
        == 200
    )
    assert eligible(connect, AE) == (False, "MODE_DISABLED")
    assert eligible(connect, US) == (True, "OK")


def test_command_authorization_versioning_and_idempotency(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    scope = f"ACCOUNT:{US}"
    v = versions(api)[scope]
    halt = {
        "command_id": "cmd-halt-0002",
        "type": "HALT_NEW_ORDERS",
        "scope": scope,
        "expected_version": v,
        "reason": "test",
    }

    assert api.post("/control/commands", json=halt).status_code == 401
    assert (
        api.post(
            "/control/commands", headers={"Authorization": "Bearer nope"}, json=halt
        ).status_code
        == 401
    )
    assert command(api, "vic", **halt).status_code == 403
    assert command(api, "alice", **halt).status_code == 200
    replay = command(api, "alice", **halt)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert command(api, "alice", **(halt | {"reason": "other"})).status_code == 422

    release = {
        "command_id": "cmd-rel-0001",
        "type": "RELEASE_HALT",
        "scope": scope,
        "expected_version": v + 1,
    }
    assert command(api, "alice", **release).status_code == 403  # only risk_admin releases
    assert command(api, "rita", **(release | {"expected_version": v})).status_code == 409
    assert command(api, "rita", **release).status_code == 200
    assert eligible(connect) == (True, "OK")


def test_live_mode_is_refused_everywhere(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    for scope in ("GLOBAL", f"ACCOUNT:{US}"):
        for mode in ("LIVE_SEMI", "LIVE_AUTO"):
            r = command(
                api,
                "rita",
                command_id=f"cmd-live-{mode}-{scope.replace(':', '-')}",
                type="SET_MODE",
                scope=scope,
                expected_version=versions(api)[scope],
                mode=mode,
            )
            assert r.status_code == 403
    with pytest.raises(Exception, match="LIVE"):
        Settings.from_env(
            {
                "COREI_ENV": "it",
                "DATABASE_URL": database_url,
                "COREI_ALLOWED_MODES": "PAPER,LIVE_AUTO",
            }
        )
    live_cfg = CONFIG | {"mode": "LIVE_AUTO"}
    with pytest.raises(SystemExit):
        bootstrap(live_cfg, settings(database_url), clock)


def test_portfolio_endpoint_reads_the_ledger_in_account_currency(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    r = api.get(f"/portfolio/{UK}", headers=hdr("vic"))
    assert r.status_code == 200
    assert r.json()["snapshot"]["cash"] == [{"units": 8_000_000, "currency": "GBP", "scale": 2}]
    us = api.get(f"/portfolio/{US}", headers=hdr("vic")).json()
    assert us["snapshot"]["cash"] == [{"units": 10_000_000, "currency": "USD", "scale": 2}]
    assert api.get("/portfolio/nope", headers=hdr("vic")).status_code == 404
    assert api.get(f"/portfolio/{UK}").status_code == 401


def test_no_endpoint_accepts_raw_orders_f06(connect: Connect, clock: FixedClock) -> None:
    paths = {route.path for route in client(connect, clock).app.routes}  # type: ignore[attr-defined]
    assert not any("order" in p or "broker" in p for p in paths)
