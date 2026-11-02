"""FastAPI application for operators and Orbit.

There is deliberately no endpoint that accepts an arbitrary order or talks to a
broker (F06). Orders will only come from approved commands produced inside the
platform (Delivery D).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from corei.api.readiness import check_readiness
from corei.contracts.execution import ControlCommandContract
from corei.control import store as control_store
from corei.control.auth import AuthError, Principal, Role, TokenAuthenticator, authorize
from corei.domain.control import ControlCommandType, ControlError, ExecutionMode, StaleVersion
from corei.domain.timeutil import Clock
from corei.ledger import store as ledger
from corei.ledger.store import LedgerError


@dataclass(frozen=True)
class AppContext:
    connect: Callable[[], psycopg.Connection]
    authenticator: TokenAuthenticator
    clock: Clock
    environment: str
    tenant_id: str
    envelope_mode: ExecutionMode
    kafka_probe: Callable[[], str] | None = None


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")
    type: ControlCommandType
    scope: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=0)
    mode: ExecutionMode | None = None
    reason: str | None = Field(default=None, max_length=500)


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="COREI OS operator API", version="0.2.0")

    def principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        try:
            return ctx.authenticator.authenticate(token)
        except AuthError as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    def viewer(p: Annotated[Principal, Depends(principal)]) -> Principal:
        if not p.roles:
            raise HTTPException(403, "no roles")
        return p

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> Any:
        report = check_readiness(ctx.connect, ctx.kafka_probe)
        if not report.ready:
            raise HTTPException(503, report.as_dict())
        return report.as_dict()

    @app.get("/control/state")
    def control_state(_: Annotated[Principal, Depends(viewer)]) -> list[dict[str, Any]]:
        with ctx.connect() as conn:
            states = control_store.load_states(conn)
        return [
            {
                "scope": s.scope,
                "version": s.version,
                "mode": s.mode.value,
                "halted": s.halted,
                "halt_reason": s.halt_reason,
                "updated_at_ns": s.updated_at_ns,
                "updated_by": s.updated_by,
            }
            for s in sorted(states.values(), key=lambda s: s.scope)
        ]

    @app.post("/control/commands")
    def control_command(
        body: CommandRequest, who: Annotated[Principal, Depends(principal)]
    ) -> dict[str, Any]:
        try:
            role = authorize(who, body.type, body.mode)
        except AuthError as exc:
            raise HTTPException(403, str(exc)) from exc
        now = ctx.clock.now_ns()
        try:
            cmd = ControlCommandContract(
                command_id=body.command_id,
                type=body.type,
                scope=body.scope,
                expected_version=body.expected_version,
                actor=who.name,
                authorization_ref=f"role:{role.value}",
                mode=body.mode,
                reason=body.reason,
                at_ns=now,
            )
            with ctx.connect() as conn, conn.transaction():
                result = control_store.execute(
                    conn,
                    cmd,
                    tenant_id=ctx.tenant_id,
                    environment=ctx.environment,
                    recorded_at_ns=now,
                    envelope_mode=ctx.envelope_mode,
                )
        except StaleVersion as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ControlError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"replayed": result.replayed, "state": result.state.model_dump(mode="json")}

    @app.get("/portfolio/{account_id}")
    def portfolio(account_id: str, _: Annotated[Principal, Depends(viewer)]) -> dict[str, Any]:
        try:
            with ctx.connect() as conn:
                snap = ledger.snapshot(conn, account_id, ctx.clock.now_ns())
        except LedgerError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"provider": "ledger", "snapshot": snap.model_dump(mode="json")}

    return app


__all__ = ["AppContext", "Role", "create_app"]
