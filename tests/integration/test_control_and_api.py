"""Durable control, authenticated commands and readiness (F05, F06, F36; C03, C08)."""

import hashlib
from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient

from corei.api.app import AppContext, create_app
from corei.bootstrap import bootstrap
from corei.control import store as control_store
from corei.control.auth import TokenAuthenticator
from corei.domain.control import ExecutionMode, check_new_order, fence_still_valid
from corei.domain.timeutil import FixedClock
from corei.infra.settings import Settings

from .conftest import T0

pytestmark = pytest.mark.integration
Connect = Callable[[], psycopg.Connection]
TOKENS = {"alice": "op-token-alice", "rita": "risk-token-rita", "vic": "view-token-vic"}
CONFIG = {
    "mode": "PAPER",
    "accounts": [
        {
            "account_id": "acc_paper_a",
            "portfolio_id": "pf_paper_a",
            "broker": "paper",
            "currency": "INR",
            "currency_scale": 2,
            "seed_cash": "1000000.00",
        },
        {
            "account_id": "acc_paper_b",
            "portfolio_id": "pf_paper_b",
            "broker": "paper",
            "currency": "INR",
            "currency_scale": 2,
            "seed_cash": "500000.00",
        },
    ],
}


def _h(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def client(connect: Connect, clock: FixedClock) -> TestClient:
    auth = TokenAuthenticator.from_config(
        f"alice:operator:{_h(TOKENS['alice'])};rita:risk_officer:{_h(TOKENS['rita'])};"
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


def eligible(connect: Connect, account: str = "acc_paper_a") -> tuple[bool, str]:
    with connect() as conn:
        e = check_new_order(
            control_store.load_states(conn),
            account_id=account,
            strategy_id="s",
            instrument_id="i",
            order_mode=ExecutionMode.PAPER,
            risk_increasing=True,
        )
    return e.allowed, e.reason


def test_fresh_system_is_fail_closed_and_not_ready(connect: Connect, clock: FixedClock) -> None:
    assert eligible(connect) == (False, "CONTROL_STATE_MISSING")
    r = client(connect, clock).get("/health/ready")
    assert r.status_code == 503
    checks = {c["name"]: c["ok"] for c in r.json()["detail"]["checks"]}
    assert checks == {"database": True, "schema": True, "control": False}


def test_bootstrap_is_idempotent_and_enables_paper(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    first = bootstrap(CONFIG, settings(database_url), clock)
    second = bootstrap(CONFIG, settings(database_url), clock)
    assert [a["seeded"] for a in first["accounts"]] == [True, True]
    assert [a["seeded"] for a in second["accounts"]] == [False, False]
    assert eligible(connect) == (True, "OK")
    assert client(connect, clock).get("/health/ready").status_code == 200
    with connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM outbox WHERE event_type = 'control.state'"
        ).fetchone()
    assert row is not None and int(row[0]) == 3  # GLOBAL + two accounts, once each


def test_halt_survives_restart_and_blocks_only_new_risk(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    state = {s["scope"]: s for s in api.get("/control/state", headers=hdr("vic")).json()}
    v = state["ACCOUNT:acc_paper_a"]["version"]
    with connect() as conn:
        e = check_new_order(
            control_store.load_states(conn),
            account_id="acc_paper_a",
            strategy_id="s",
            instrument_id="i",
            order_mode=ExecutionMode.PAPER,
            risk_increasing=True,
        )
    r = api.post(
        "/control/commands",
        headers=hdr("alice"),
        json={
            "command_id": "cmd-halt-0001",
            "type": "HALT_NEW_ORDERS",
            "scope": "ACCOUNT:acc_paper_a",
            "expected_version": v,
            "reason": "operator kill switch",
        },
    )
    assert r.status_code == 200 and r.json()["state"]["halted"] is True

    # "Restart": brand-new app and connections read the same durable state.
    api2 = client(connect, clock)
    halted = {s["scope"]: s for s in api2.get("/control/state", headers=hdr("vic")).json()}
    assert halted["ACCOUNT:acc_paper_a"]["halted"] is True
    assert eligible(connect) == (False, "HALTED:ACCOUNT:acc_paper_a")
    assert eligible(connect, "acc_paper_b") == (True, "OK")  # scoped to one account
    with connect() as conn:
        assert not fence_still_valid(control_store.load_states(conn), e.fence)


def test_command_authorization_versioning_and_idempotency(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    scope = "ACCOUNT:acc_paper_a"
    v = {s["scope"]: s for s in api.get("/control/state", headers=hdr("vic")).json()}[scope][
        "version"
    ]
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
    assert api.post("/control/commands", headers=hdr("vic"), json=halt).status_code == 403
    assert api.post("/control/commands", headers=hdr("alice"), json=halt).status_code == 200
    replay = api.post("/control/commands", headers=hdr("alice"), json=halt)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    reused = api.post("/control/commands", headers=hdr("alice"), json=halt | {"reason": "other"})
    assert reused.status_code == 422

    release = {
        "command_id": "cmd-rel-0001",
        "type": "RELEASE_HALT",
        "scope": scope,
        "expected_version": v + 1,
    }
    assert api.post("/control/commands", headers=hdr("alice"), json=release).status_code == 403
    stale = api.post(
        "/control/commands", headers=hdr("rita"), json=release | {"expected_version": v}
    )
    assert stale.status_code == 409
    assert api.post("/control/commands", headers=hdr("rita"), json=release).status_code == 200
    assert eligible(connect) == (True, "OK")


def test_live_mode_is_refused_everywhere(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    r = api.post(
        "/control/commands",
        headers=hdr("rita"),
        json={
            "command_id": "cmd-live-0001",
            "type": "SET_MODE",
            "scope": "GLOBAL",
            "expected_version": 1,
            "mode": "LIVE_AUTO",
        },
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


def test_portfolio_endpoint_reads_the_ledger(
    connect: Connect, clock: FixedClock, database_url: str
) -> None:
    bootstrap(CONFIG, settings(database_url), clock)
    api = client(connect, clock)
    r = api.get("/portfolio/acc_paper_b", headers=hdr("vic"))
    assert r.status_code == 200
    assert r.json()["snapshot"]["cash"] == [{"units": 50_000_000, "currency": "INR", "scale": 2}]
    assert api.get("/portfolio/nope", headers=hdr("vic")).status_code == 404
    assert api.get("/portfolio/acc_paper_b").status_code == 401


def test_no_endpoint_accepts_raw_orders_f06(connect: Connect, clock: FixedClock) -> None:
    paths = {route.path for route in client(connect, clock).app.routes}  # type: ignore[attr-defined]
    assert not any("order" in p or "broker" in p for p in paths)
    assert T0  # anchor import
