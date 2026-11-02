"""Generate contract test vectors (valid + invalid) into contracts/vectors/.

Scenario: international forex, first vertical slice — EURUSD on the COREI paper
broker, accounts under US, UK and UAE legal entities. Values are fixtures, not
market data; real instrument specifications come from each broker's reference data.

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
RAW = canonical_hash("raw.evidence", {"fixture": "fx-feed-frame-001"})
PAPER = ExecutionMode.PAPER
FEED = "brk_paper"
ACC = "acc_us_paper_01"


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
        producer_version="0.2.0",
        correlation_id="cor_slice_001",
        causation_id=cause,
    )


def eurusd() -> rf.InstrumentSpec:
    return rf.InstrumentSpec(
        instrument_id="ins_eurusd",
        version=1,
        asset_class=rf.AssetClass.FX,
        kind=rf.InstrumentKind.SPOT,
        venue="OTC",
        symbol="EURUSD",
        tradable=True,
        base_currency="EUR",
        quote_currency="USD",
        pip_size_units=10,
        price_scale=5,
        tick_size_units=1,
        quantity_scale=2,
        quantity_step_units=1,
        min_quantity_units=1,
        contract_multiplier_units=100_000,
        multiplier_scale=0,
        settlement_currency="USD",
        currency_scale=2,
        session_calendar="fx-24x5",
        provider_keys={FEED: "EURUSD"},
        valid_from_ns=T0,
    )


def valid() -> dict[str, EventEnvelope]:
    ins = eurusd()
    entity = rf.LegalEntity(
        entity_id="ent_us",
        name="COREI US (paper)",
        jurisdiction="US",
        regulator="NFA/CFTC",
        base_currency="USD",
        reporting_timezone="America/New_York",
    )
    broker = rf.BrokerSpec(
        broker_id=FEED,
        name="COREI paper broker",
        platform=rf.BrokerPlatform.PAPER,
        jurisdictions=("US", "GB", "AE"),
        asset_classes=(rf.AssetClass.FX, rf.AssetClass.METAL),
        order_types=(rf.OrderTypeCapability.MARKET, rf.OrderTypeCapability.LIMIT),
        position_model=rf.PositionModel.NETTING,
    )
    account = rf.AccountSpec(
        account_id=ACC,
        portfolio_id="pf_us_paper_01",
        entity_id="ent_us",
        ledger_kind=rf.LedgerKind.PAPER,
        base_currency="USD",
        bindings=(rf.BrokerBinding(broker_id=FEED, rank=1, broker_account_ref="PAPER-US-01"),),
    )
    group = rf.AccountGroup(
        group_id="grp_forex_swing",
        name="Forex swing (paper)",
        account_ids=(ACC, "acc_uk_paper_01", "acc_ae_paper_01"),
        strategy_ids=("forex-momentum-strategy",),
    )
    rate = rf.FxRateSnapshot(
        base_currency="GBP",
        quote_currency="USD",
        rate_units=127_500,
        rate_scale=5,
        as_of_ns=T0,
        source=FEED,
    )
    obs = dict(
        observation_id="obs_q_001",
        provider=FEED,
        feed_session_id="fs_001",
        instrument_id=ins.instrument_id,
        origin=mk.DataOrigin.OBSERVED,
        provider_ts_ns=T0,
        receive_ts_ns=T0 + 2_000_000,
        price_scale=5,
        raw_evidence_hash=RAW,
    )
    quote = mk.QuoteObservation(
        **obs | {"provider_sequence": 101},
        bid_units=108_250,  # type: ignore[arg-type]
        bid_size_units=500,
        ask_units=108_252,
        ask_size_units=300,
        size_scale=2,
    )
    trade = mk.TradeObservation(
        **obs | {"observation_id": "obs_t_001"},
        price_units=108_251,  # type: ignore[arg-type]
        size_units=100,
        size_scale=2,
    )
    index = mk.IndexObservation(
        **obs
        | {
            "observation_id": "obs_i_001",
            "instrument_id": "ins_dxy",  # type: ignore[arg-type]
            "price_scale": 3,
        },
        value_units=104_125,
    )
    status = mk.StatusObservation(
        **obs | {"observation_id": "obs_s_001"},  # type: ignore[arg-type]
        session_status=mk.SessionStatus.OPEN,
    )
    quarantine = mk.QuarantineRecord(
        observation_id="obs_q_bad",
        instrument_id=ins.instrument_id,
        provider=FEED,
        receive_ts_ns=T0 + 3_000_000,
        reason_code="CROSSED_BOOK",
        raw_evidence_hash=RAW,
    )
    candle = mk.Candle(
        instrument_id=ins.instrument_id,
        timeframe="1m",
        start_ns=T0,
        end_ns=T0 + 60 * SEC,
        price_source="MID",
        price_scale=5,
        open_units=108_250,
        high_units=108_290,
        low_units=108_231,
        close_units=108_270,
        traded_volume_units=None,
        observation_count=118,
        complete=True,
        dataset_version="ds_obs_v1",
    )
    feature = mk.FeatureValue(
        instrument_id=ins.instrument_id,
        timeframe="1m",
        feature_name="ema",
        feature_version="ema_mid_n20_v1",
        as_of_ns=T0 + 60 * SEC,
        inputs_cutoff_ns=T0 + 60 * SEC,
        value_units=108_244,
        value_scale=5,
        warm=True,
        dataset_version="ds_obs_v1",
    )
    mstate = mk.MarketState(
        instrument_id=ins.instrument_id,
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
        strategy_id="forex-momentum-strategy",
        strategy_version="1.0.0",
        instrument_id=ins.instrument_id,
        direction=tr.Direction.LONG,
        strength_bps=6_500,
        as_of_ns=T0 + 60 * SEC,
        expires_at_ns=T0 + 120 * SEC,
        inputs=(
            tr.FeatureRef(
                feature_name="ema", feature_version="ema_mid_n20_v1", as_of_ns=T0 + 60 * SEC
            ),
        ),
    )
    intent = tr.TradeIntent(
        intent_id="int_001",
        origin=tr.Origin.CORE,
        source_ref="forex-momentum-strategy",
        capital_partition="core",
        instrument_id=ins.instrument_id,
        side=tr.Side.BUY,
        purpose=tr.Purpose.OPEN,
        strategy_id="forex-momentum-strategy",
        strategy_version="1.0.0",
        allowed_accounts=(ACC, "acc_uk_paper_01", "acc_ae_paper_01"),
        signal_ids=("sig_001",),
        as_of_ns=T0 + 60 * SEC,
        expires_at_ns=T0 + 120 * SEC,
    )
    risk = tr.RiskDecision(
        risk_decision_id="rsk_001",
        intent_id="int_001",
        account_id=ACC,
        decision=tr.Decision.APPROVED,
        portfolio_state_version=7,
        risk_config_version="risk_v1",
        max_quantity_units=100,
        as_of_ns=T0 + 60 * SEC,
    )
    policy = tr.PolicyDecision(
        policy_decision_id="pol_001",
        intent_id="int_001",
        account_id=ACC,
        decision=tr.Decision.APPROVED,
        policy_version="policy_v1",
        as_of_ns=T0 + 60 * SEC,
    )
    command_id = derive_id("cmd", "order.command", {"intent": "int_001", "account": ACC})
    command = tr.ApprovedOrderCommand(
        command_id=command_id,
        client_order_id=derive_id("cli", "client.order", {"command": command_id}),
        account_id=ACC,
        portfolio_id="pf_us_paper_01",
        mode=PAPER,
        origin=tr.Origin.CORE,
        capital_partition="core",
        intent_id="int_001",
        strategy_id="forex-momentum-strategy",
        strategy_version="1.0.0",
        instrument_id=ins.instrument_id,
        instrument_version=1,
        side=tr.Side.BUY,
        purpose=tr.Purpose.OPEN,
        order_type=tr.OrderType.LIMIT,
        time_in_force=tr.TimeInForce.DAY,
        quantity_units=50,
        quantity_scale=2,
        limit_price_units=108_255,
        price_scale=5,
        reservation_id="rsv_001",
        risk_decision_id="rsk_001",
        policy_decision_id="pol_001",
        portfolio_state_version=7,
        control_fence=(("GLOBAL", 3), (f"ACCOUNT:{ACC}", 5)),
        config_version="cfg_v1",
        as_of_ns=T0 + 60 * SEC,
        expires_at_ns=T0 + 120 * SEC,
    )
    lifecycle = ex.OrderLifecycleEvent(
        order_id="ord_001",
        account_id=ACC,
        command_id=command_id,
        sequence=5,
        event_type=OrderEventType.BROKER_ACK,
        from_status=OrderStatus.PENDING_SUBMISSION,
        to_status=OrderStatus.ACKNOWLEDGED,
        broker_order_id="PB-0001",
        at_ns=T0 + 61 * SEC,
    )
    fill = ex.Fill(
        fill_id=derive_id("fil", "fill", {"account": ACC, "broker": FEED, "execution": "PX-0001"}),
        account_id=ACC,
        broker=FEED,
        order_id="ord_001",
        broker_order_id="PB-0001",
        broker_execution_id="PX-0001",
        instrument_id=ins.instrument_id,
        side=tr.Side.BUY,
        quantity_units=50,
        quantity_scale=2,
        price_units=108_255,
        price_scale=5,
        fees=(MoneyAmount(units=350, currency="USD", scale=2),),
        executed_at_ns=T0 + 62 * SEC,
        received_at_ns=T0 + 62 * SEC + 5_000_000,
        mode=PAPER,
    )
    snapshot = ex.PortfolioSnapshot(
        account_id=ACC,
        portfolio_id="pf_us_paper_01",
        state_version=8,
        ledger_cutoff_sequence=42,
        as_of_ns=T0 + 62 * SEC,
        cash=(MoneyAmount(units=9_458_375, currency="USD", scale=2),),
        positions=(
            ex.Position(
                instrument_id=ins.instrument_id,
                quantity_units=50,
                quantity_scale=2,
                average_price_units=108_255,
                price_scale=5,
                realized_pnl=MoneyAmount(units=0, currency="USD", scale=2),
            ),
        ),
        reservations=(
            ex.Reservation(
                reservation_id="rsv_001",
                command_id=command_id,
                amount=MoneyAmount(units=1_000_000, currency="USD", scale=2),
                expires_at_ns=T0 + 120 * SEC,
            ),
        ),
        mark_source="ins_eurusd:MID",
    )
    control_state = ex.ControlStateContract(
        scope=f"ACCOUNT:{ACC}",
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
        scope=f"ACCOUNT:{ACC}",
        expected_version=5,
        actor="reconciler",
        authorization_ref="policy.auto_halt_on_break",
        reason="reconciliation break brk_001",
        at_ns=T0 + 70 * SEC,
    )
    brk = ex.ReconciliationBreak(
        break_id="brk_001",
        account_id=ACC,
        kind=ex.BreakKind.POSITION,
        instrument_id=ins.instrument_id,
        cutoff_ns=T0 + 69 * SEC,
        internal_value="0.50",
        broker_value="1.00",
        state=BreakState.SCOPED_HALT,
        halt_scope=f"ACCOUNT:{ACC}",
        detected_at_ns=T0 + 70 * SEC,
        updated_at_ns=T0 + 70 * SEC,
    )
    pairs: list[tuple[str, Contract, int]] = [
        ("reference.instrument", ins, T0),
        ("reference.entity", entity, T0),
        ("reference.broker", broker, T0),
        ("reference.account", account, T0),
        ("reference.account_group", group, T0),
        ("market.fx_rate", rate, T0),
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


# (file stem, base event type, field edits (None = remove), expected error substring, finding)
INVALID: list[tuple[str, str, dict[str, Any], str, str]] = [
    ("intent_long_short_side", "trade.intent", {"side": "LONG"}, "side", "F17"),
    ("intent_unknown_field", "trade.intent", {"decision_action": "LONG"}, "Extra inputs", "F17"),
    ("intent_core_without_signals", "trade.intent", {"signal_ids": []}, "cite the signals", "F17"),
    ("intent_side_lowercase", "trade.intent", {"side": "buy"}, "side", "F17"),
    (
        "index_marked_tradable",
        "reference.instrument",
        {
            "asset_class": "INDEX",
            "kind": "INDEX",
            "base_currency": None,
            "quote_currency": None,
            "pip_size_units": None,
        },
        "INDEX",
        "C05",
    ),
    (
        "fx_pair_wrong_settlement",
        "reference.instrument",
        {"settlement_currency": "EUR"},
        "quote currency",
        "C04",
    ),
    ("fx_pair_missing_quote", "reference.instrument", {"quote_currency": None}, "both", "C04"),
    (
        "account_broker_bound_twice",
        "reference.account",
        {
            "bindings": [
                {"broker_id": FEED, "rank": 1, "broker_account_ref": "A"},
                {"broker_id": FEED, "rank": 2, "broker_account_ref": "B"},
            ]
        },
        "bound only once",
        "F07",
    ),
    (
        "account_secondary_without_primary",
        "reference.account",
        {"bindings": [{"broker_id": FEED, "rank": 2, "broker_account_ref": "A"}]},
        "ranked 1..n",
        "F07",
    ),
    (
        "entity_bad_jurisdiction",
        "reference.entity",
        {"jurisdiction": "Dubai"},
        "jurisdiction",
        "C04",
    ),
    (
        "quote_without_sides",
        "market.quote",
        {"bid_units": None, "ask_units": None, "bid_size_units": None, "ask_size_units": None},
        "at least one side",
        "F10",
    ),
    ("quote_float_price", "market.quote", {"bid_units": 1.0825}, "int", "F41"),
    ("quote_integral_float_price", "market.quote", {"bid_units": 108250.0}, "int", "F41"),
    ("candle_inverted_interval", "market.candle", {"end_ns": T0 - 1}, "end_ns", "F15"),
    (
        "feature_future_leakage",
        "market.feature",
        {"inputs_cutoff_ns": T0 + 61 * SEC},
        "future leakage",
        "F14",
    ),
    ("feature_value_while_warming", "market.feature", {"warm": False}, "warming up", "F14"),
    ("command_mode_disabled", "order.command", {"mode": "DISABLED"}, "mode DISABLED", "F06"),
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
    tampered = valid_envs["order.fill"].model_dump(mode="json")
    tampered["payload"]["quantity_units"] = 100
    out["envelope_tampered_payload"] = {
        "envelope": tampered,
        "finding": "F41",
        "expect_error": "payload_hash",
    }
    wrong_key = valid_envs["order.fill"].model_dump(mode="json")
    wrong_key["partition_key"] = "acc_uk_paper_01"
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
