"""Operation modes, scoped halts and fencing (findings F05, F42; corrections C03, C08, C12).

Operation modes follow the trading blueprint (Layer 9), set per account and
optionally per (account, strategy):

    DISABLED   strategy/account combination inactive
    BACKTEST   historical event replay only, never a live connection
    PAPER      full pipeline, simulated fills, no broker call
    LIVE_SEMI  human confirmation gate per trade
    LIVE_AUTO  fully automated

Halts apply at any institutional scope: GLOBAL, ENTITY (a legal entity /
jurisdiction, e.g. all UK accounts), BROKER, ACCOUNT, STRATEGY, INSTRUMENT,
or one ACCOUNT_STRATEGY pair. Any operator may halt; only risk-admin may release.

Control state changes only through versioned commands (optimistic concurrency).
Eligibility is **fail-closed**: missing control state or an unknown mode means no.
Halt, cancel-open-orders and flatten are separate commands (C03).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum


class ExecutionMode(StrEnum):
    DISABLED = "DISABLED"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE_SEMI = "LIVE_SEMI"
    LIVE_AUTO = "LIVE_AUTO"


LIVE_MODES = frozenset({ExecutionMode.LIVE_SEMI, ExecutionMode.LIVE_AUTO})
TRADING_MODES = frozenset({ExecutionMode.PAPER}) | LIVE_MODES


class ControlCommandType(StrEnum):
    SET_MODE = "SET_MODE"
    HALT_NEW_ORDERS = "HALT_NEW_ORDERS"
    RELEASE_HALT = "RELEASE_HALT"
    CANCEL_OPEN_ORDERS = "CANCEL_OPEN_ORDERS"
    FLATTEN_POSITIONS = "FLATTEN_POSITIONS"


class ScopeKind(StrEnum):
    GLOBAL = "GLOBAL"
    ENTITY = "ENTITY"
    BROKER = "BROKER"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    INSTRUMENT = "INSTRUMENT"
    ACCOUNT_STRATEGY = "ACCOUNT_STRATEGY"


# Scopes whose mode participates in eligibility. Others carry halts only.
MODE_SCOPES = frozenset({ScopeKind.GLOBAL, ScopeKind.ACCOUNT, ScopeKind.ACCOUNT_STRATEGY})


class ControlError(ValueError):
    pass


class StaleVersion(ControlError):
    pass


def scope_key(kind: ScopeKind | str, *ids: str) -> str:
    """``GLOBAL``, ``ACCOUNT:<id>``, ``ACCOUNT_STRATEGY:<account>:<strategy>`` ..."""
    kind = ScopeKind(kind)
    want = {ScopeKind.GLOBAL: 0, ScopeKind.ACCOUNT_STRATEGY: 2}.get(kind, 1)
    if len(ids) != want or any(not i or ":" in i for i in ids):
        raise ControlError(f"scope {kind} needs {want} id(s) without ':', got {ids}")
    return ":".join((kind.value, *ids))


def scope_kind(scope: str) -> ScopeKind:
    try:
        kind = ScopeKind(scope.split(":", 1)[0])
    except ValueError as exc:
        raise ControlError(f"unknown scope {scope!r}") from exc
    scope_key(kind, *scope.split(":")[1:])  # validates the shape
    return kind


@dataclass(frozen=True, slots=True)
class ControlState:
    scope: str
    version: int = 0
    mode: ExecutionMode | None = None  # None: not set here (inherit / fail closed)
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
    kind = scope_kind(cmd.scope)
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
            if kind not in MODE_SCOPES:
                raise ControlError(
                    f"modes are set on GLOBAL, ACCOUNT or ACCOUNT_STRATEGY, not {kind}"
                )
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
    effective_mode: ExecutionMode | None = None


@dataclass(frozen=True, slots=True)
class OrderScope:
    """Everything an order touches that a halt could apply to."""

    entity_id: str
    broker_id: str
    account_id: str
    strategy_id: str
    instrument_id: str


def effective_mode(
    states: Mapping[str, ControlState], account_id: str, strategy_id: str
) -> ExecutionMode | None:
    """Account mode, overridden by an (account, strategy) mode if one is explicitly set.

    A pair scope that only carries a halt (mode unset) does not override anything.
    """
    pair = states.get(scope_key(ScopeKind.ACCOUNT_STRATEGY, account_id, strategy_id))
    if pair is not None and pair.mode is not None:
        return pair.mode
    account = states.get(scope_key(ScopeKind.ACCOUNT, account_id))
    return None if account is None else account.mode


def check_new_order(  # noqa: PLR0911 (one early return per fail-closed reason)
    states: Mapping[str, ControlState],
    scope: OrderScope,
    *,
    order_mode: ExecutionMode,
    risk_increasing: bool,
) -> Eligibility:
    """Fail-closed eligibility for a new order.

    Returns the control versions read (``fence``) so the final dispatch can
    re-check that none changed between reservation and submission.
    """
    global_state = states.get("GLOBAL")
    mode = effective_mode(states, scope.account_id, scope.strategy_id)
    if global_state is None or mode is None or global_state.mode is None:
        return Eligibility(False, "CONTROL_STATE_MISSING")
    if global_state.mode is ExecutionMode.DISABLED or mode is ExecutionMode.DISABLED:
        return Eligibility(False, "MODE_DISABLED", effective_mode=mode)
    if order_mode is not mode:
        return Eligibility(False, "MODE_MISMATCH", effective_mode=mode)
    if order_mode in LIVE_MODES and global_state.mode not in LIVE_MODES:
        return Eligibility(False, "LIVE_NOT_ENABLED_GLOBALLY", effective_mode=mode)
    if order_mode is ExecutionMode.BACKTEST:
        return Eligibility(False, "BACKTEST_NEVER_USES_LIVE_CONTROL", effective_mode=mode)

    scopes = (
        "GLOBAL",
        scope_key(ScopeKind.ENTITY, scope.entity_id),
        scope_key(ScopeKind.BROKER, scope.broker_id),
        scope_key(ScopeKind.ACCOUNT, scope.account_id),
        scope_key(ScopeKind.STRATEGY, scope.strategy_id),
        scope_key(ScopeKind.INSTRUMENT, scope.instrument_id),
        scope_key(ScopeKind.ACCOUNT_STRATEGY, scope.account_id, scope.strategy_id),
    )
    fence = []
    for key in scopes:
        st = states.get(key)
        # Absent scopes are fenced at version 0, so a halt created on a new scope
        # (e.g. the first halt on an entity) still invalidates the fence.
        fence.append((key, 0 if st is None else st.version))
        if st is not None and st.halted and risk_increasing:
            return Eligibility(False, f"HALTED:{key}", effective_mode=mode)
    return Eligibility(True, "OK", tuple(fence), mode)


def fence_still_valid(
    states: Mapping[str, ControlState], fence: tuple[tuple[str, int], ...]
) -> bool:
    """True only if every control scope read at reservation is unchanged (absent = v0)."""
    if not fence:
        return False
    return all((states[s].version if s in states else 0) == v for s, v in fence)
