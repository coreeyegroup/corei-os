"""Market observations, candles, features and market state.

Separate observation types per capability (F10): a quote, a trade print, an
index value and a session status are different facts. Missing book sides stay
missing — they are never invented from the last price. Validation here is
structural; admissibility (stale, crossed, out-of-session) is a separate policy
(C10, C11) so quarantine decisions are explicit and counted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import model_validator

from corei.contracts.base import (
    Contract,
    Ident,
    NonNegScaled,
    PosScaled,
    Scale,
    Scaled,
    Sha256Hex,
    TimestampNs,
)


class DataOrigin(StrEnum):
    """Where data came from. Simulated data never enters observed datasets (F42)."""

    OBSERVED = "OBSERVED"
    REPLAYED = "REPLAYED"
    SIMULATED = "SIMULATED"


class _Observation(Contract):
    observation_id: Ident
    provider: Ident
    feed_session_id: Ident
    instrument_id: Ident
    origin: DataOrigin
    provider_ts_ns: TimestampNs | None
    receive_ts_ns: TimestampNs
    provider_sequence: int | None = None
    price_scale: Scale
    raw_evidence_hash: Sha256Hex


class QuoteObservation(_Observation):
    kind: Literal["QUOTE"] = "QUOTE"
    bid_units: PosScaled | None = None
    bid_size_units: NonNegScaled | None = None
    ask_units: PosScaled | None = None
    ask_size_units: NonNegScaled | None = None
    size_scale: Scale = 0

    @model_validator(mode="after")
    def _has_a_side(self) -> QuoteObservation:
        if self.bid_units is None and self.ask_units is None:
            raise ValueError("a quote needs at least one side")
        return self


class TradeObservation(_Observation):
    kind: Literal["TRADE"] = "TRADE"
    price_units: PosScaled
    size_units: NonNegScaled | None = None
    size_scale: Scale = 0
    cumulative_volume_units: NonNegScaled | None = None


class IndexObservation(_Observation):
    kind: Literal["INDEX"] = "INDEX"
    value_units: PosScaled


class SessionStatus(StrEnum):
    PRE_OPEN = "PRE_OPEN"
    OPEN = "OPEN"
    HALTED = "HALTED"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class StatusObservation(_Observation):
    kind: Literal["STATUS"] = "STATUS"
    session_status: SessionStatus


MarketObservation = QuoteObservation | TradeObservation | IndexObservation | StatusObservation


class QuarantineRecord(Contract):
    """An observation rejected by admissibility policy — kept, counted, never dropped silently."""

    observation_id: Ident
    instrument_id: Ident | None
    provider: Ident
    receive_ts_ns: TimestampNs
    reason_code: Ident
    raw_evidence_hash: Sha256Hex


class Candle(Contract):
    """A time bar over the half-open interval [start_ns, end_ns) (F15)."""

    instrument_id: Ident
    timeframe: Ident
    start_ns: TimestampNs
    end_ns: TimestampNs
    price_source: Literal["TRADE", "MID", "BID", "ASK", "INDEX"]
    price_scale: Scale
    open_units: PosScaled
    high_units: PosScaled
    low_units: PosScaled
    close_units: PosScaled
    traded_volume_units: NonNegScaled | None = (
        None  # None when no trade-volume source (not tick count)
    )
    observation_count: int
    complete: bool
    dataset_version: Ident

    @model_validator(mode="after")
    def _ohlc(self) -> Candle:
        if self.end_ns <= self.start_ns:
            raise ValueError("end_ns must be after start_ns")
        if self.observation_count < 1:
            raise ValueError("a candle needs at least one observation")
        lo, hi = self.low_units, self.high_units
        if not (
            lo <= min(self.open_units, self.close_units)
            and max(self.open_units, self.close_units) <= hi
        ):
            raise ValueError("OHLC values are inconsistent")
        return self


class FeatureValue(Contract):
    """A versioned feature for one instrument and timeframe (F13, F14)."""

    instrument_id: Ident
    timeframe: Ident
    feature_name: Ident
    feature_version: Ident
    as_of_ns: TimestampNs
    inputs_cutoff_ns: TimestampNs
    value_units: Scaled | None  # None while warming up
    value_scale: Scale
    warm: bool
    dataset_version: Ident

    @model_validator(mode="after")
    def _warmup(self) -> FeatureValue:
        if self.warm and self.value_units is None:
            raise ValueError("a warm feature must have a value")
        if not self.warm and self.value_units is not None:
            raise ValueError("a feature still warming up must not publish a value")
        if self.inputs_cutoff_ns > self.as_of_ns:
            raise ValueError("inputs_cutoff_ns cannot be after as_of_ns (future leakage)")
        return self


class Freshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class StateSource(StrEnum):
    MEASURED = "MEASURED"
    DERIVED = "DERIVED"
    SIMULATED = "SIMULATED"
    UNAVAILABLE = "UNAVAILABLE"


class MarketState(Contract):
    """Measured trust state for an instrument (F26, C10). UNKNOWN is never healthy."""

    instrument_id: Ident
    as_of_ns: TimestampNs
    watermark_ns: TimestampNs | None
    last_observation_ns: TimestampNs | None
    freshness: Freshness
    session_status: SessionStatus
    open_gaps: int
    source: StateSource
    feed_session_id: Ident | None
    expires_at_ns: TimestampNs

    @property
    def tradable_now(self) -> bool:
        return (
            self.freshness is Freshness.FRESH
            and self.session_status is SessionStatus.OPEN
            and self.source is StateSource.MEASURED
            and self.open_gaps == 0
            and self.as_of_ns < self.expires_at_ns
        )
