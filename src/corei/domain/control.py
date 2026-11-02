"""Execution modes, scoped halts and fencing (findings F05, F42; corrections C03, C08, C12).

Control state is owned by one module and changes only through versioned
commands (optimistic concurrency: ``expected_version`` must match). The
eligibility check is **fail-closed**: missing control state, an unknown mode or
a stale version means *no*.

Halt, cancel-open-orders and flatten are separate commands (C03). Only
``HALT_NEW_ORDERS`` / ``RELEASE_HALT`` change eligibility here; cancel and
flatten are requests the OMS executes under their own authorization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum


class ExecutionMode(StrEnum):
    OFF = "OFF"
    SIMULATION = "SIMULATION"
    PAPER = "PAPER"
    LIVE_SEMI = "LIVE_SEMI"
    LIVE_AUTO = "LIVE_AUTO"


LIVE_MODES = frozenset({ExecutionMode.LIVE_SEMI, ExecutionMode.LIVE_AUTO})


class ControlCommandType(StrEnum):
    SET_MODE = "SET_MODE"
    HALT_NEW_ORDERS = "HALT_NEW_ORDERS"
    RELEASE_HALT = "RELEASE_HALT"
    CANCEL_OPEN_ORDERS = "CANCEL_OPEN_ORDERS"
    FLATTEN_POSITIONS = "FLATTEN_POSITIONS"


class ControlError(ValueError):
    pass


class StaleVersion(ControlError):
    pass


def scope_key(kind: str, ident: str | None = None) -> str:
    """``GLOBAL`` or ``ACCOUNT:<id>`` / ``STRATEGY:<id>`` / ``INSTRUMENT:<id>``."""
    if kind == "GLOBAL":
        if ident is not None:
            raise ControlError("GLOBAL scope takes no id")
        return "GLOBAL"
    if kind not in {"ACCOUNT", "STRATEGY", "INSTRUMENT"} or not ident:
        raise ControlError(f"invalid scope {kind}:{ident}")
    return f"{kind}:{ident}"


@dataclass(frozen=True, slots=True)
class ControlState:
    scope: str
    version: int = 0
    mode: ExecutionMode = ExecutionMode.OFF
    halted: bool = False
    halt_reason: str | None = None
    updated_at_ns: int = 0
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class ControlCommand:
    type: ControlCommandType
    scope: str
    expected_version: int
    actor: str
    authorization_ref: str
    at_ns: int
    mode: ExecutionMode | None = None
    reason: str | None = None


def apply_command(state: ControlState, cmd: ControlCommand) -> ControlState:
    """Apply a control command with fencing. Cancel/flatten don't change state here."""
    if cmd.scope != state.scope:
        raise ControlError(f"command scope {cmd.scope} != state scope {state.scope}")
    if cmd.expected_version != state.version:
        raise StaleVersion(
            f"{state.scope}: expected v{cmd.expected_version}, current v{state.version}"
        )
    if not cmd.actor or not cmd.authorization_ref:
        raise ControlError("control commands require an actor and an authorization reference")
    base = replace(state, version=state.version + 1, updated_at_ns=cmd.at_ns, updated_by=cmd.actor)
    match cmd.type:
        case ControlCommandType.SET_MODE:
            if cmd.mode is None:
                raise ControlError("SET_MODE requires a mode")
            return replace(base, mode=cmd.mode)
        case ControlCommandType.HALT_NEW_ORDERS:
            if not cmd.reason:
                raise ControlError("a halt requires a reason")
            return replace(base, halted=True, halt_reason=cmd.reason)
        case ControlCommandType.RELEASE_HALT:
            if not state.halted:
                raise ControlError(f"{state.scope} is not halted")
            return replace(base, halted=False, halt_reason=None)
        case ControlCommandType.CANCEL_OPEN_ORDERS | ControlCommandType.FLATTEN_POSITIONS:
            # Recorded (version bump, audit) but executed by the OMS as orders/cancels.
            return base


@dataclass(frozen=True, slots=True)
class Eligibility:
    allowed: bool
    reason: str
    fence: tuple[tuple[str, int], ...] = ()


def check_new_order(
    states: Mapping[str, ControlState],
    *,
    account_id: str,
    strategy_id: str,
    instrument_id: str,
    order_mode: ExecutionMode,
    risk_increasing: bool,
) -> Eligibility:
    """Fail-closed eligibility for a new order.

    Returns the versions read (``fence``) so the final dispatch can re-check
    that none changed between reservation and submission.
    """
    scopes = (
        scope_key("GLOBAL"),
        scope_key("ACCOUNT", account_id),
        scope_key("STRATEGY", strategy_id),
        scope_key("INSTRUMENT", instrument_id),
    )
    global_state = states.get("GLOBAL")
    account_state = states.get(scopes[1])
    if global_state is None or account_state is None:
        return Eligibility(False, "CONTROL_STATE_MISSING")
    if account_state.mode is ExecutionMode.OFF or global_state.mode is ExecutionMode.OFF:
        return Eligibility(False, "MODE_OFF")
    if order_mode is not account_state.mode:
        return Eligibility(False, "MODE_MISMATCH")
    if order_mode in LIVE_MODES and global_state.mode not in LIVE_MODES:
        return Eligibility(False, "LIVE_NOT_ENABLED_GLOBALLY")
    fence = []
    for scope in scopes:
        st = states.get(scope)
        if st is None:
            continue  # strategy/instrument scopes are optional
        fence.append((scope, st.version))
        if st.halted and risk_increasing:
            return Eligibility(False, f"HALTED:{scope}")
    return Eligibility(True, "OK", tuple(fence))


def fence_still_valid(
    states: Mapping[str, ControlState], fence: tuple[tuple[str, int], ...]
) -> bool:
    """True only if every control scope read at reservation is unchanged."""
    return all(scope in states and states[scope].version == v for scope, v in fence)
