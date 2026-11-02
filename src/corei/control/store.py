"""Durable control state (findings F05, F36; corrections C03, C08, C12).

Commands are applied under a row lock with optimistic version checks and are
idempotent by ``command_id``. State survives restarts because it lives in
Postgres; each change emits ``control.state`` and ``control.command`` events
through the outbox in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from corei.contracts.envelope import EventEnvelope, make_envelope
from corei.contracts.execution import ControlCommandContract, ControlStateContract
from corei.domain.control import (
    ControlCommand,
    ControlError,
    ControlState,
    ExecutionMode,
    apply_command,
)
from corei.infra.processing import enqueue

PRODUCER = "corei.control"
PRODUCER_VERSION = "0.2.0"


class DuplicateCommandMismatch(ControlError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    state: ControlStateContract
    replayed: bool  # True when command_id was already applied


def _row_to_state(row: tuple[Any, ...]) -> ControlState:
    scope, version, mode, halted, reason, at_ns, by = row
    return ControlState(
        scope=str(scope),
        version=int(version),
        mode=None if mode is None else ExecutionMode(str(mode)),
        halted=bool(halted),
        halt_reason=None if reason is None else str(reason),
        updated_at_ns=int(at_ns),
        updated_by=str(by),
    )


def _contract(state: ControlState) -> ControlStateContract:
    return ControlStateContract(
        scope=state.scope,
        version=state.version,
        mode=state.mode,
        halted=state.halted,
        halt_reason=state.halt_reason,
        updated_at_ns=state.updated_at_ns,
        updated_by=state.updated_by or "unknown",
    )


def load_states(conn: psycopg.Connection) -> dict[str, ControlState]:
    rows = conn.execute(
        "SELECT scope, version, mode, halted, halt_reason, updated_at_ns, updated_by FROM control_state"
    ).fetchall()
    return {str(r[0]): _row_to_state(r) for r in rows}


def execute(
    conn: psycopg.Connection,
    cmd: ControlCommandContract,
    *,
    tenant_id: str,
    environment: str,
    recorded_at_ns: int,
    envelope_mode: ExecutionMode = ExecutionMode.PAPER,
) -> CommandResult:
    """Apply one control command inside the caller's transaction.

    ``envelope_mode`` is the deployment's operating mode stamped on emitted events;
    control events describe the platform, not one order's mode.
    """
    prior = conn.execute(
        "SELECT scope, type, expected_version, actor, mode, reason FROM control_commands "
        "WHERE command_id = %s",
        (cmd.command_id,),
    ).fetchone()
    if prior is not None:
        same = prior == (
            cmd.scope,
            cmd.type.value,
            cmd.expected_version,
            cmd.actor,
            cmd.mode.value if cmd.mode else None,
            cmd.reason,
        )
        if not same:
            raise DuplicateCommandMismatch(f"command_id {cmd.command_id} reused with other content")
        current = load_states(conn)[cmd.scope]
        return CommandResult(_contract(current), replayed=True)

    row = conn.execute(
        "SELECT scope, version, mode, halted, halt_reason, updated_at_ns, updated_by "
        "FROM control_state WHERE scope = %s FOR UPDATE",
        (cmd.scope,),
    ).fetchone()
    current = _row_to_state(row) if row else ControlState(scope=cmd.scope)
    new = apply_command(
        current,
        ControlCommand(
            type=cmd.type,
            scope=cmd.scope,
            expected_version=cmd.expected_version,
            actor=cmd.actor,
            authorization_ref=cmd.authorization_ref,
            at_ns=cmd.at_ns,
            mode=cmd.mode,
            reason=cmd.reason,
        ),
    )
    conn.execute(
        """INSERT INTO control_state (scope, version, mode, halted, halt_reason, updated_at_ns, updated_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (scope) DO UPDATE SET version = EXCLUDED.version, mode = EXCLUDED.mode,
               halted = EXCLUDED.halted, halt_reason = EXCLUDED.halt_reason,
               updated_at_ns = EXCLUDED.updated_at_ns, updated_by = EXCLUDED.updated_by""",
        (
            new.scope,
            new.version,
            new.mode.value if new.mode else None,
            new.halted,
            new.halt_reason,
            new.updated_at_ns,
            new.updated_by,
        ),
    )
    conn.execute(
        """INSERT INTO control_commands (command_id, scope, type, expected_version, resulting_version,
               actor, authorization_ref, mode, reason, at_ns)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            cmd.command_id,
            cmd.scope,
            cmd.type.value,
            cmd.expected_version,
            new.version,
            cmd.actor,
            cmd.authorization_ref,
            cmd.mode.value if cmd.mode else None,
            cmd.reason,
            cmd.at_ns,
        ),
    )
    state = _contract(new)
    for envelope in _events(cmd, state, tenant_id, recorded_at_ns, envelope_mode):
        enqueue(conn, envelope, environment, recorded_at_ns)
    return CommandResult(state, replayed=False)


def _events(
    cmd: ControlCommandContract,
    state: ControlStateContract,
    tenant_id: str,
    recorded_at_ns: int,
    envelope_mode: ExecutionMode,
) -> list[EventEnvelope]:
    common = dict(
        tenant_id=tenant_id,
        mode=envelope_mode,
        occurred_at_ns=cmd.at_ns,
        recorded_at_ns=recorded_at_ns,
        producer=PRODUCER,
        producer_version=PRODUCER_VERSION,
        correlation_id=cmd.command_id,
    )
    command_event = make_envelope(event_type="control.command", payload=cmd, **common)  # type: ignore[arg-type]
    state_event = make_envelope(
        event_type="control.state",
        payload=state,
        causation_id=command_event.event_id,
        **common,  # type: ignore[arg-type]
    )
    return [command_event, state_event]
