import pytest

from corei.domain.control import (
    ControlCommand,
    ControlCommandType,
    ControlError,
    ControlState,
    ExecutionMode,
    StaleVersion,
    apply_command,
    check_new_order,
    fence_still_valid,
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


def cmd(kind: ControlCommandType, scope: str, v: int, **kw: object) -> ControlCommand:
    return ControlCommand(
        type=kind,
        scope=scope,
        expected_version=v,
        actor="ops.alice",
        authorization_ref="auth-1",
        at_ns=1,
        **kw,  # type: ignore[arg-type]
    )


def paper_states() -> dict[str, ControlState]:
    g = apply_command(
        ControlState("GLOBAL"), cmd(C.SET_MODE, "GLOBAL", 0, mode=ExecutionMode.PAPER)
    )
    a = apply_command(
        ControlState("ACCOUNT:acc_a"), cmd(C.SET_MODE, "ACCOUNT:acc_a", 0, mode=ExecutionMode.PAPER)
    )
    return {"GLOBAL": g, "ACCOUNT:acc_a": a}


def check(
    states: dict[str, ControlState],
    *,
    mode: ExecutionMode = ExecutionMode.PAPER,
    risk_increasing: bool = True,
) -> tuple[bool, str]:
    e = check_new_order(
        states,
        account_id="acc_a",
        strategy_id="s1",
        instrument_id="i1",
        order_mode=mode,
        risk_increasing=risk_increasing,
    )
    return e.allowed, e.reason


def test_fail_closed_when_control_state_missing_c08() -> None:
    assert check({}) == (False, "CONTROL_STATE_MISSING")
    assert check({"GLOBAL": paper_states()["GLOBAL"]}) == (False, "CONTROL_STATE_MISSING")


def test_default_mode_is_off() -> None:
    assert check(
        {"GLOBAL": ControlState("GLOBAL"), "ACCOUNT:acc_a": ControlState("ACCOUNT:acc_a")}
    ) == (False, "MODE_OFF")


def test_mode_mismatch_blocks_paper_order_on_live_account() -> None:
    assert check(paper_states(), mode=ExecutionMode.LIVE_AUTO) == (False, "MODE_MISMATCH")


def test_halt_blocks_new_risk_but_allows_reducing_orders_c03() -> None:
    s = paper_states()
    s["ACCOUNT:acc_a"] = apply_command(
        s["ACCOUNT:acc_a"], cmd(C.HALT_NEW_ORDERS, "ACCOUNT:acc_a", 1, reason="recon break")
    )
    assert check(s) == (False, "HALTED:ACCOUNT:acc_a")
    assert check(s, risk_increasing=False)[0] is True


def test_halt_and_release_require_reason_auth_and_correct_version() -> None:
    s = paper_states()["ACCOUNT:acc_a"]
    with pytest.raises(ControlError):
        apply_command(s, cmd(C.HALT_NEW_ORDERS, "ACCOUNT:acc_a", 1))  # no reason
    with pytest.raises(StaleVersion):
        apply_command(s, cmd(C.HALT_NEW_ORDERS, "ACCOUNT:acc_a", 0, reason="x"))
    with pytest.raises(ControlError):
        apply_command(s, cmd(C.RELEASE_HALT, "ACCOUNT:acc_a", 1))  # not halted
    no_auth = ControlCommand(
        type=C.HALT_NEW_ORDERS,
        scope="ACCOUNT:acc_a",
        expected_version=1,
        actor="ops",
        authorization_ref="",
        at_ns=1,
        reason="x",
    )
    with pytest.raises(ControlError):
        apply_command(s, no_auth)


def test_cancel_and_flatten_do_not_change_eligibility() -> None:
    s = paper_states()["ACCOUNT:acc_a"]
    after = apply_command(s, cmd(C.CANCEL_OPEN_ORDERS, "ACCOUNT:acc_a", 1))
    assert after.version == 2 and not after.halted and after.mode is s.mode


def test_fence_detects_halt_between_reservation_and_dispatch() -> None:
    s = paper_states()
    e = check_new_order(
        s,
        account_id="acc_a",
        strategy_id="s1",
        instrument_id="i1",
        order_mode=ExecutionMode.PAPER,
        risk_increasing=True,
    )
    assert e.allowed and fence_still_valid(s, e.fence)
    s["ACCOUNT:acc_a"] = apply_command(
        s["ACCOUNT:acc_a"], cmd(C.HALT_NEW_ORDERS, "ACCOUNT:acc_a", 1, reason="kill switch")
    )
    assert not fence_still_valid(s, e.fence)


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
