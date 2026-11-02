"""Generate contract test vectors (valid + invalid) into contracts/vectors/.

Vectors follow the first vertical slice: two instruments, two paper accounts.
Instrument parameters are TEST FIXTURES, not real exchange specifications —
real lot sizes, ticks and expiries come from reference data (C05).

    uv run python scripts/make_vectors.py           # regenerate
    uv run python scripts/make_vectors.py --check   # CI: fail on drift
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from corei.contracts import execution as ex
from corei.contracts import market as mk
from corei.contracts import reference as rf
from corei.contracts import trading as tr
from corei.contracts.base import Contract, MoneyAmount
from corei.contracts.envelope import EventEnvelope, make_envelope
from corei.domain.control import ControlCommandType, ExecutionMode
from corei.domain.identity import canonical_hash, derive_id
from corei.domain.orders import OrderEventType, OrderStatus
from corei.domain.reconciliation import BreakState

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "vectors"
T0 = 1_788_000_000_000_000_000  # fixed epoch-ns anchor; vectors never use the wall clock
SEC = 1_000_000_000
RAW = canonical_hash("raw.evidence", {"fixture": "upstox-v3-frame-001"})
PAPER = ExecutionMode.PAPER


def envelope(
    event_type: str, payload: Contract, at: int, cause: str | None = None
) -> EventEnvelope:
    return make_envelope(
        event_type=event_type,
        payload=payload,
        tenant_id="corei",
        mode=PAPER,
        occurred_at_ns=at,
        recorded_at_ns=at + 1_000,
        producer="vector-generator",
        producer_version="0.1.0",
        correlation_id="cor_slice_001",
        causation_id=cause,
    )


def valid() -> dict[str, EventEnvelope]:
    fut = rf.InstrumentSpec(
        instrument_id="ins_test_fut_a",
        version=1,
        venue="TESTX",
        symbol="TEST-A-FUT",
        kind=rf.InstrumentKind.FUTURE,
        tradable=True,
        underlying_id="ins_test_idx_a",
        expiry_ns=T0 + 30 * 86_400 * SEC,
        price_scale=2,
        tick_size_units=5,
        quantity_scale=0,
        quantity_step_units=25,
        min_quantity_units=25,
        contract_multiplier_units=1,
        multiplier_scale=0,
        settlement_currency="INR",
        currency_scale=2,
        provider_keys={"upstox": "TEST_FO|00001"},
        valid_from_ns=T0,
    )
    quote = mk.QuoteObservation(
        observation_id="obs_q_001",
        provider="upstox",
        feed_session_id="fs_001",
        instrument_id=fut.instrument_id,
        origin=mk.DataOrigin.OBSERVED,
        provider_ts_ns=T0,
        receive_ts_ns=T0 + 2_000_000,
        provider_sequence=101,
        price_scale=2,
        bid_units=2_350_010,
        bid_size_units=75,
        ask_units=2_350_030,
        ask_size_units=50,
        raw_evidence_hash=RAW,
    )
    trade = mk.TradeObservation(
        observation_id="obs_t_001",
        provider="upstox",
        feed_session_id="fs_001",
        instrument_id=fut.instrument_id,
        origin=mk.DataOrigin.OBSERVED,
        provider_ts_ns=T0,
        receive_ts_ns=T0 + 2_000_000,
        price_scale=2,
        price_units=2_350_020,
        size_units=25,
        raw_evidence_hash=RAW,
    )
    index = mk.IndexObservation(
        observation_id="obs_i_001",
        provider="upstox",
        feed_session_id="fs_001",
        instrument_id="ins_test_idx_a",
        origin=mk.DataOrigin.OBSERVED,
        provider_ts_ns=T0,
        receive_ts_ns=T0 + 2_000_000,
        price_scale=2,
        value_units=2_349_500,
        raw_evidence_hash=RAW,
    )
    status = mk.StatusObservation(
        observation_id="obs_s_001",
        provider="upstox",
        feed_session_id="fs_001",
        instrument_id=fut.instrument_id,
        origin=mk.DataOrigin.OBSERVED,
        provider_ts_ns=T0,
        receive_ts_ns=T0 + 1_000,
        price_scale=2,
        session_status=mk.SessionStatus.OPEN,
        raw_evidence_hash=RAW,
    )
    quarantine = mk.QuarantineRecord(
        observation_id="obs_q_bad",
        instrument_id=fut.instrument_id,
        provider="upstox",
        receive_ts_ns=T0 + 3_000_000,
        reason_code="CROSSED_BOOK",
        raw_evidence_hash=RAW,
    )
    candle = mk.Candle(
        instrument_id=fut.instrument_id,
        timeframe="1m",
        start_ns=T0,
        end_ns=T0 + 60 * SEC,
        price_source="TRADE",
        price_scale=2,
        open_units=2_350_020,
        high_units=2_351_000,
        low_units=2_349_000,
        close_units=2_350_500,
        traded_volume_units=1_200,
        observation_count=48,
        complete=True,
        dataset_version="ds_obs_v1",
    )
    feature = mk.FeatureValue(
        instrument_id=fut.instrument_id,
        timeframe="1m",
        feature_name="ema",
        feature_version="ema_close_n10_v1",
        as_of_ns=T0 + 60 * SEC,
        inputs_cutoff_ns=T0 + 60 * SEC,
        value_units=2_350_210,
        value_scale=2,
        warm=True,
        dataset_version="ds_obs_v1",
    )
    mstate = mk.MarketState(
        instrument_id=fut.instrument_id,
        as_of_ns=T0 + 60 * SEC,
        watermark_ns=T0 + 60 * SEC,
        last_observation_ns=T0 + 59 * SEC,
        freshness=mk.Freshness.FRESH,
        session_status=mk.SessionStatus.OPEN,
        open_gaps=0,
        source=mk.StateSource.MEASURED,
        feed_session_id="fs_001",
        expires_at_ns=T0 + 65 * SEC,
    )
    signal = tr.Signal(
        signal_id="sig_001",
        strategy_id="ema_cross",
        strategy_version="1.0.0",
        instrument_id=fut.instrument_id,
        direction=tr.Direction.LONG,
        strength_bps=6_500,
        as_of_ns=T0 + 60 * SEC,
        expires_at_ns=T0 + 120 * SEC,
        inputs=(
            tr.FeatureRef(
                feature_name="ema", feature_version="ema_close_n10_v1", as_of_ns=T0 + 60 * SEC
            ),
        ),
    )
    intent = tr.TradeIntent(
        intent_id="int_001",
        origin=tr.Origin.CORE,
        source_ref="ema_cross",
        capital_partition="core",
        instrument_id=fut.instrument_id,
        side=tr.Side.BUY,
        purpose=tr.Purpose.OPEN,
        strategy_id="ema_cross",
        strategy_version="1.0.0",
        allowed_accounts=("acc_paper_a", "acc_paper_b"),
        signal_ids=("sig_001",),
        as_of_ns=T0 + 60 * SEC,
        expires_at_ns=T0 + 120 * SEC,
    )
    risk = tr.RiskDecision(
        risk_decision_id="rsk_001",
        intent_id="int_001",
        account_id="acc_paper_a",
        decision=tr.Decision.APPROVED,
        portfolio_state_version=7,
        risk_config_version="risk_v1",
        max_quantity_units=50,
        as_of_ns=T0 + 60 * SEC,
    )
    policy = tr.PolicyDecision(
        policy_decision_id="pol_001",
        intent_id="int_001",
        account_id="acc_paper_a",
        decision=tr.Decision.APPROVED,
        policy_version="policy_v1",
        as_of_ns=T0 + 60 * SEC,
    )
    command_id = derive_id("cmd", "order.command", {"intent": "int_001", "account": "acc_paper_a"})
    command = tr.ApprovedOrderCommand(
        command_id=command_id,
        client_order_id=derive_id("cli", "client.order", {"command": command_id}),
        account_id="acc_paper_a",
        portfolio_id="pf_paper_a",
        mode=PAPER,
        origin=tr.Origin.CORE,
        capital_partition="core",
        intent_id="int_001",
        strategy_id="ema_cross",
        strategy_version="1.0.0",
        instrument_id=fut.instrument_id,
        instrument_version=1,
        side=tr.Side.BUY,
        purpose=tr.Purpose.OPEN,
        order_type=tr.OrderType.LIMIT,
        time_in_force=tr.TimeInForce.DAY,
        quantity_units=50,
        quantity_scale=0,
        limit_price_units=2_350_500,
        price_scale=2,
        reservation_id="rsv_001",
        risk_decision_id="rsk_001",
        policy_decision_id="pol_001",
        portfolio_state_version=7,
        control_fence=(("GLOBAL", 3), ("ACCOUNT:acc_paper_a", 5)),
        config_version="cfg_v1",
        as_of_ns=T0 + 60 * SEC,
        expires_at_ns=T0 + 120 * SEC,
    )
    lifecycle = ex.OrderLifecycleEvent(
        order_id="ord_001",
        account_id="acc_paper_a",
        command_id=command_id,
        sequence=5,
        event_type=OrderEventType.BROKER_ACK,
        from_status=OrderStatus.PENDING_SUBMISSION,
        to_status=OrderStatus.ACKNOWLEDGED,
        broker_order_id="PB-0001",
        at_ns=T0 + 61 * SEC,
    )
    fill = ex.Fill(
        fill_id=derive_id(
            "fil", "fill", {"account": "acc_paper_a", "broker": "paper", "execution": "PX-0001"}
        ),
        account_id="acc_paper_a",
        broker="paper",
        order_id="ord_001",
        broker_order_id="PB-0001",
        broker_execution_id="PX-0001",
        instrument_id=fut.instrument_id,
        side=tr.Side.BUY,
        quantity_units=25,
        quantity_scale=0,
        price_units=2_350_500,
        price_scale=2,
        fees=(MoneyAmount(units=2_000, currency="INR", scale=2),),
        executed_at_ns=T0 + 62 * SEC,
        received_at_ns=T0 + 62 * SEC + 5_000_000,
        mode=PAPER,
    )
    snapshot = ex.PortfolioSnapshot(
        account_id="acc_paper_a",
        portfolio_id="pf_paper_a",
        state_version=8,
        ledger_cutoff_sequence=42,
        as_of_ns=T0 + 62 * SEC,
        cash=(MoneyAmount(units=999_998_000, currency="INR", scale=2),),
        positions=(
            ex.Position(
                instrument_id=fut.instrument_id,
                quantity_units=25,
                quantity_scale=0,
                average_price_units=2_350_500,
                price_scale=2,
                realized_pnl=MoneyAmount(units=0, currency="INR", scale=2),
            ),
        ),
        reservations=(
            ex.Reservation(
                reservation_id="rsv_001",
                command_id=command_id,
                amount=MoneyAmount(units=5_000_000, currency="INR", scale=2),
                expires_at_ns=T0 + 120 * SEC,
            ),
        ),
        mark_source="ins_test_fut_a:TRADE",
    )
    control_state = ex.ControlStateContract(
        scope="ACCOUNT:acc_paper_a",
        version=6,
        mode=PAPER,
        halted=True,
        halt_reason="reconciliation break brk_001",
        updated_at_ns=T0 + 70 * SEC,
        updated_by="reconciler",
    )
    control_cmd = ex.ControlCommandContract(
        command_id="ctl_001",
        type=ControlCommandType.HALT_NEW_ORDERS,
        scope="ACCOUNT:acc_paper_a",
        expected_version=5,
        actor="reconciler",
        authorization_ref="policy.auto_halt_on_break",
        reason="reconciliation break brk_001",
        at_ns=T0 + 70 * SEC,
    )
    brk = ex.ReconciliationBreak(
        break_id="brk_001",
        account_id="acc_paper_a",
        kind=ex.BreakKind.POSITION,
        instrument_id=fut.instrument_id,
        cutoff_ns=T0 + 69 * SEC,
        internal_value="25",
        broker_value="50",
        state=BreakState.SCOPED_HALT,
        halt_scope="ACCOUNT:acc_paper_a",
        detected_at_ns=T0 + 70 * SEC,
        updated_at_ns=T0 + 70 * SEC,
    )
    pairs: list[tuple[str, Contract, int]] = [
        ("reference.instrument", fut, T0),
        ("market.quote", quote, T0),
        ("market.trade", trade, T0),
        ("market.index", index, T0),
        ("market.status", status, T0),
        ("market.quarantine", quarantine, T0),
        ("market.candle", candle, T0 + 60 * SEC),
        ("market.feature", feature, T0 + 60 * SEC),
        ("market.state", mstate, T0 + 60 * SEC),
        ("strategy.signal", signal, T0 + 60 * SEC),
        ("trade.intent", intent, T0 + 60 * SEC),
        ("risk.decision", risk, T0 + 60 * SEC),
        ("policy.decision", policy, T0 + 60 * SEC),
        ("order.command", command, T0 + 60 * SEC),
        ("order.lifecycle", lifecycle, T0 + 61 * SEC),
        ("order.fill", fill, T0 + 62 * SEC),
        ("portfolio.snapshot", snapshot, T0 + 62 * SEC),
        ("control.state", control_state, T0 + 70 * SEC),
        ("control.command", control_cmd, T0 + 70 * SEC),
        ("recon.break", brk, T0 + 70 * SEC),
    ]
    return {name: envelope(name, payload, at) for name, payload, at in pairs}


# (file stem, base event type, json-pointer-ish edits, expected error substring, finding)
INVALID: list[tuple[str, str, dict[str, Any], str, str]] = [
    ("intent_long_short_side", "trade.intent", {"side": "LONG"}, "side", "F17"),
    ("intent_unknown_field", "trade.intent", {"decision_action": "LONG"}, "Extra inputs", "F17"),
    ("intent_core_without_signals", "trade.intent", {"signal_ids": []}, "cite the signals", "F17"),
    (
        "index_marked_tradable",
        "reference.instrument",
        {"kind": "INDEX", "tradable": True, "underlying_id": None, "expiry_ns": None},
        "INDEX",
        "C05",
    ),
    (
        "quote_without_sides",
        "market.quote",
        {"bid_units": None, "ask_units": None, "bid_size_units": None, "ask_size_units": None},
        "at least one side",
        "F10",
    ),
    ("quote_float_price", "market.quote", {"bid_units": 2350.1}, "int", "F41"),
    ("quote_integral_float_price", "market.quote", {"bid_units": 2350010.0}, "int", "F41"),
    ("intent_side_lowercase", "trade.intent", {"side": "buy"}, "side", "F17"),
    ("candle_inverted_interval", "market.candle", {"end_ns": T0 - 1}, "end_ns", "F15"),
    (
        "feature_future_leakage",
        "market.feature",
        {"inputs_cutoff_ns": T0 + 61 * SEC},
        "future leakage",
        "F14",
    ),
    ("feature_value_while_warming", "market.feature", {"warm": False}, "warming up", "F14"),
    ("command_mode_off", "order.command", {"mode": "OFF"}, "mode OFF", "F06"),
    ("command_without_account", "order.command", {"account_id": None}, "account_id", "F19"),
    ("command_without_fence", "order.command", {"control_fence": []}, "control_fence", "F05"),
    (
        "command_market_with_limit_price",
        "order.command",
        {"order_type": "MARKET"},
        "MARKET orders must not",
        "F19",
    ),
    ("risk_reject_without_reason", "risk.decision", {"decision": "REJECTED"}, "reason code", "F04"),
    (
        "fill_without_execution_id",
        "order.fill",
        {"broker_execution_id": None},
        "broker_execution_id",
        "F21",
    ),
    ("halt_without_reason", "control.state", {"halt_reason": None}, "needs a reason", "F05"),
]


def invalid(valid_envs: dict[str, EventEnvelope]) -> dict[str, dict[str, Any]]:
    out = {}
    for stem, etype, edits, expect, finding in INVALID:
        payload = copy.deepcopy(valid_envs[etype].payload)
        for key, value in edits.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        out[stem] = {
            "event_type": etype,
            "finding": finding,
            "expect_error": expect,
            "payload": payload,
        }
    # Envelope-level tampering: payload changed after hashing.
    tampered = valid_envs["order.fill"].model_dump(mode="json")
    tampered["payload"]["quantity_units"] = 50
    out["envelope_tampered_payload"] = {
        "envelope": tampered,
        "finding": "F41",
        "expect_error": "payload_hash",
    }
    wrong_key = valid_envs["order.fill"].model_dump(mode="json")
    wrong_key["partition_key"] = "acc_paper_b"
    out["envelope_wrong_partition_key"] = {
        "envelope": wrong_key,
        "finding": "F12",
        "expect_error": "partition_key",
    }
    return out


def render() -> dict[str, str]:
    venv = valid()
    files = {f"valid/{k}.json": v.model_dump_json(indent=2) + "\n" for k, v in venv.items()}
    for k, v in invalid(venv).items():
        files[f"invalid/{k}.json"] = json.dumps(v, indent=2, sort_keys=True) + "\n"
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    wanted = render()
    existing = {str(p.relative_to(ROOT)): p.read_text() for p in ROOT.rglob("*.json")}
    if args.check:
        drift = sorted(n for n in set(wanted) | set(existing) if wanted.get(n) != existing.get(n))
        if drift:
            print("Vectors out of date; run scripts/make_vectors.py:", *drift, sep="\n  ")
            return 1
        print(f"{len(wanted)} vectors up to date")
        return 0
    for stale in set(existing) - set(wanted):
        (ROOT / stale).unlink()
    for name, text in wanted.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"wrote {len(wanted)} vectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
