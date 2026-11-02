"""Event envelope and contract registry (findings F12, F17, F41, F42).

Every event on the bus is an ``EventEnvelope``. The envelope:

* names its payload type and schema version, which must be registered;
* carries a ``partition_key`` taken from the payload's ownership field
  (instrument for market data, account for execution), so per-key ordering is
  preserved (F12);
* carries ``mode`` so simulated or paper events can be fenced from live
  processing and observed datasets (F42);
* carries ``payload_hash``, the domain-separated canonical hash of the payload,
  which consumers verify;
* derives ``event_id`` deterministically from type, payload hash and causation,
  so a redelivered event keeps its id and inbox deduplication works.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import model_validator

from corei.contracts import execution, market, reference, trading
from corei.contracts.base import (
    Contract,
    Ident,
    Sha256Hex,
    TimestampNs,
)
from corei.domain.control import ExecutionMode
from corei.domain.identity import canonical_hash, derive_id


@dataclass(frozen=True, slots=True)
class EventType:
    name: str
    model: type[Contract]
    version: int
    partition_key: Callable[[Any], str]


def _attr(name: str) -> Callable[[Any], str]:
    def get(payload: Any) -> str:
        return str(getattr(payload, name))

    return get


REGISTRY: dict[str, EventType] = {
    et.name: et
    for et in (
        EventType("reference.instrument", reference.InstrumentSpec, 1, _attr("instrument_id")),
        EventType("market.quote", market.QuoteObservation, 1, _attr("instrument_id")),
        EventType("market.trade", market.TradeObservation, 1, _attr("instrument_id")),
        EventType("market.index", market.IndexObservation, 1, _attr("instrument_id")),
        EventType("market.status", market.StatusObservation, 1, _attr("instrument_id")),
        EventType("market.quarantine", market.QuarantineRecord, 1, _attr("provider")),
        EventType("market.candle", market.Candle, 1, _attr("instrument_id")),
        EventType("market.feature", market.FeatureValue, 1, _attr("instrument_id")),
        EventType("market.state", market.MarketState, 1, _attr("instrument_id")),
        EventType("strategy.signal", trading.Signal, 1, _attr("instrument_id")),
        EventType("trade.intent", trading.TradeIntent, 1, _attr("intent_id")),
        EventType("risk.decision", trading.RiskDecision, 1, _attr("account_id")),
        EventType("policy.decision", trading.PolicyDecision, 1, _attr("account_id")),
        EventType("order.command", trading.ApprovedOrderCommand, 1, _attr("account_id")),
        EventType("order.lifecycle", execution.OrderLifecycleEvent, 1, _attr("account_id")),
        EventType("order.fill", execution.Fill, 1, _attr("account_id")),
        EventType("portfolio.snapshot", execution.PortfolioSnapshot, 1, _attr("account_id")),
        EventType("control.state", execution.ControlStateContract, 1, _attr("scope")),
        EventType("control.command", execution.ControlCommandContract, 1, _attr("scope")),
        EventType("recon.break", execution.ReconciliationBreak, 1, _attr("account_id")),
    )
}


def payload_hash(event_type: str, payload: Contract) -> str:
    return canonical_hash(f"payload.{event_type}", payload.model_dump(mode="json"))


class EventEnvelope(Contract):
    event_id: Ident
    event_type: Ident
    schema_version: int
    tenant_id: Ident
    partition_key: Ident
    mode: ExecutionMode
    occurred_at_ns: TimestampNs  # domain time of the fact (as-of)
    recorded_at_ns: TimestampNs  # when the producer recorded it (operational)
    producer: Ident
    producer_version: Ident
    causation_id: Ident | None = None
    correlation_id: Ident
    payload_hash: Sha256Hex
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _verify(self) -> EventEnvelope:
        et = REGISTRY.get(self.event_type)
        if et is None:
            raise ValueError(f"unregistered event type {self.event_type!r}")
        if self.schema_version != et.version:
            raise ValueError(
                f"{self.event_type} schema v{self.schema_version} not supported (v{et.version})"
            )
        model = et.model.model_validate_json(json.dumps(self.payload))
        if payload_hash(self.event_type, model) != self.payload_hash:
            raise ValueError("payload_hash does not match the canonical payload")
        if et.partition_key(model) != self.partition_key:
            raise ValueError("partition_key does not match the payload's ownership key")
        return self

    def typed_payload(self) -> Contract:
        return REGISTRY[self.event_type].model.model_validate_json(json.dumps(self.payload))


def make_envelope(
    *,
    event_type: str,
    payload: Contract,
    tenant_id: str,
    mode: ExecutionMode,
    occurred_at_ns: int,
    recorded_at_ns: int,
    producer: str,
    producer_version: str,
    correlation_id: str,
    causation_id: str | None = None,
) -> EventEnvelope:
    et = REGISTRY[event_type]
    if not isinstance(payload, et.model):
        raise TypeError(f"{event_type} expects {et.model.__name__}, got {type(payload).__name__}")
    phash = payload_hash(event_type, payload)
    event_id = derive_id(
        "evt",
        "event",
        {"type": event_type, "v": et.version, "payload": phash, "cause": causation_id},
    )
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        schema_version=et.version,
        tenant_id=tenant_id,
        partition_key=et.partition_key(payload),
        mode=mode,
        occurred_at_ns=occurred_at_ns,
        recorded_at_ns=recorded_at_ns,
        producer=producer,
        producer_version=producer_version,
        causation_id=causation_id,
        correlation_id=correlation_id,
        payload_hash=phash,
        payload=payload.model_dump(mode="json"),
    )
