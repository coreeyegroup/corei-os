import pytest

from corei.domain.control import (
    ControlCommand,
    ControlCommandType,
    ControlError,
    ControlState,
    ExecutionMode,
    OrderScope,
    ScopeKind,
    StaleVersion,
    apply_command,
    check_new_order,
    fence_still_valid,
    scope_key,
)
from corei.domain.reconciliation import (
    BreakState,
    CompareResult,
    InvalidBreakTransition,
    advance_break,
    break_holds_halt,
    compare_positions,
)

C = ControlCommandType
PAPER = ExecutionMode.PAPER
ORDER = OrderScope(
    entity_id="ent_uk",
    broker_id="brk_paper",
    account_id="acc_a",
    strategy_id="fx-momentum",
    instrument_id="ins_eurusd",
)
ACCT = "ACCOUNT:acc_a"


def cmd(kind: ControlCommandType, scope: str, v: int, **kw: object) -> ControlCommand:
    return ControlCommand(
        type=kind,
        scope=scope,
        expected_version=v,
        actor="ops.alice",
        authorization_ref="auth-1",
        at_ns=1,
        **kw,
    )  # type: ignore[arg-type]


def set_mode(states: dict[str, ControlState], scope: str, mode: ExecutionMode) -> None:
    cur = states.get(scope, ControlState(scope))
    states[scope] = apply_command(cur, cmd(C.SET_MODE, scope, cur.version, mode=mode))


def halt(states: dict[str, ControlState], scope: str, reason: str = "test") -> None:
    cur = states.get(scope, ControlState(scope))
    states[scope] = apply_command(cur, cmd(C.HALT_NEW_ORDERS, scope, cur.version, reason=reason))


def paper_states() -> dict[str, ControlState]:
    states: dict[str, ControlState] = {}
    set_mode(states, "GLOBAL", PAPER)
    set_mode(states, ACCT, PAPER)
    return states


def check(
    states: dict[str, ControlState], *, mode: ExecutionMode = PAPER, risk_increasing: bool = True
) -> tuple[bool, str]:
    e = check_new_order(states, ORDER, order_mode=mode, risk_increasing=risk_increasing)
    return e.allowed, e.reason


def test_scope_keys_are_validated() -> None:
    assert scope_key(ScopeKind.ACCOUNT_STRATEGY, "acc_a", "s1") == "ACCOUNT_STRATEGY:acc_a:s1"
    assert scope_key("ENTITY", "ent_ae") == "ENTITY:ent_ae"
    for bad in (("GLOBAL", "x"), ("ACCOUNT",), ("ACCOUNT_STRATEGY", "a"), ("ACCOUNT", "a:b")):
        with pytest.raises(ControlError):
            scope_key(*bad)


def test_fail_closed_when_control_state_missing_c08() -> None:
    assert check({}) == (False, "CONTROL_STATE_MISSING")
    assert check({"GLOBAL": paper_states()["GLOBAL"]}) == (False, "CONTROL_STATE_MISSING")


def test_unset_or_disabled_modes_refuse_orders() -> None:
    unset = {"GLOBAL": ControlState("GLOBAL"), ACCT: ControlState(ACCT)}
    assert check(unset) == (False, "CONTROL_STATE_MISSING")  # never defaults to allowed
    disabled = paper_states()
    set_mode(disabled, ACCT, ExecutionMode.DISABLED)
    assert check(disabled) == (False, "MODE_DISABLED")


def test_mode_mismatch_and_backtest_isolation() -> None:
    assert check(paper_states(), mode=ExecutionMode.LIVE_AUTO) == (False, "MODE_MISMATCH")
    states = paper_states()
    set_mode(states, ACCT, ExecutionMode.BACKTEST)
    assert check(states, mode=ExecutionMode.BACKTEST) == (False, "BACKTEST_NEVER_USES_LIVE_CONTROL")


def test_mode_per_account_and_strategy_overrides_account_mode() -> None:
    """Blueprint Layer 3: operation modes are set per account, per strategy, independently."""
    states = paper_states()
    pair = scope_key(ScopeKind.ACCOUNT_STRATEGY, "acc_a", "fx-momentum")
    set_mode(states, pair, ExecutionMode.DISABLED)
    assert check(states) == (False, "MODE_DISABLED")
    gold = check_new_order(
        states,
        OrderScope("ent_uk", "brk_paper", "acc_a", "gold-timing", "ins_xauusd"),
        order_mode=PAPER,
        risk_increasing=True,
    )
    assert gold.allowed  # same account, other strategy unaffected


def test_modes_can_only_be_set_on_mode_scopes() -> None:
    with pytest.raises(ControlError, match="modes are set"):
        apply_command(
            ControlState("ENTITY:ent_uk"), cmd(C.SET_MODE, "ENTITY:ent_uk", 0, mode=PAPER)
        )


@pytest.mark.parametrize(
    "scope",
    [
        "GLOBAL",
        "ENTITY:ent_uk",
        "BROKER:brk_paper",
        ACCT,
        "STRATEGY:fx-momentum",
        "INSTRUMENT:ins_eurusd",
        "ACCOUNT_STRATEGY:acc_a:fx-momentum",
    ],
)
def test_halt_at_any_institutional_scope_blocks_new_risk_c03(scope: str) -> None:
    states = paper_states()
    halt(states, scope)
    assert check(states) == (False, f"HALTED:{scope}")
    assert check(states, risk_increasing=False)[0] is True  # reducing orders stay possible


def test_entity_halt_only_affects_that_entity() -> None:
    states = paper_states()
    halt(states, "ENTITY:ent_us", "US regulatory hold")
    assert check(states) == (True, "OK")  # ORDER belongs to ent_uk


def test_halt_and_release_require_reason_auth_and_correct_version() -> None:
    s = paper_states()[ACCT]
    with pytest.raises(ControlError):
        apply_command(s, cmd(C.HALT_NEW_ORDERS, ACCT, 1))  # no reason
    with pytest.raises(StaleVersion):
        apply_command(s, cmd(C.HALT_NEW_ORDERS, ACCT, 0, reason="x"))
    with pytest.raises(ControlError):
        apply_command(s, cmd(C.RELEASE_HALT, ACCT, 1))  # not halted
    no_auth = ControlCommand(
        type=C.HALT_NEW_ORDERS,
        scope=ACCT,
        expected_version=1,
        actor="ops",
        authorization_ref="",
        at_ns=1,
        reason="x",
    )
    with pytest.raises(ControlError):
        apply_command(s, no_auth)


def test_cancel_and_flatten_do_not_change_eligibility() -> None:
    s = paper_states()[ACCT]
    after = apply_command(s, cmd(C.CANCEL_OPEN_ORDERS, ACCT, 1))
    assert after.version == 2 and not after.halted and after.mode is s.mode


def test_fence_detects_halt_between_reservation_and_dispatch() -> None:
    states = paper_states()
    e = check_new_order(states, ORDER, order_mode=PAPER, risk_increasing=True)
    assert e.allowed and fence_still_valid(states, e.fence)
    halt(states, ACCT, "kill switch")
    assert not fence_still_valid(states, e.fence)


def test_fence_detects_a_halt_on_a_scope_that_did_not_exist_before() -> None:
    states = paper_states()
    e = check_new_order(states, ORDER, order_mode=PAPER, risk_increasing=True)
    halt(states, "ENTITY:ent_uk", "first ever entity halt")  # scope created after the read
    assert not fence_still_valid(states, e.fence)
    assert not fence_still_valid(states, ())


def test_empty_reconciliation_is_unknown_not_matched_f23() -> None:
    assert compare_positions(None, None)[0].result is CompareResult.UNKNOWN
    assert compare_positions({"i1": 10}, None)[0].result is CompareResult.UNKNOWN


def test_reconciliation_compares_quantities() -> None:
    out = {
        c.instrument_id: c.result
        for c in compare_positions({"i1": 10, "i2": 5}, {"i1": 20, "i2": 5})
    }
    assert out == {"i1": CompareResult.BREAK, "i2": CompareResult.MATCHED}
    missing_at_broker = compare_positions({"i1": 10}, {})
    assert missing_at_broker[0].result is CompareResult.BREAK


def test_break_lifecycle_and_halt_retention() -> None:
    state = BreakState.DETECTED
    for nxt in (
        BreakState.SCOPED_HALT,
        BreakState.INVESTIGATING,
        BreakState.RESOLUTION_PROPOSED,
        BreakState.APPROVED,
        BreakState.APPLIED,
    ):
        state = advance_break(state, nxt)
        assert break_holds_halt(state)
    state = advance_break(state, BreakState.VERIFIED)
    assert not break_holds_halt(state)
    with pytest.raises(InvalidBreakTransition):
        advance_break(BreakState.DETECTED, BreakState.CLOSED)
