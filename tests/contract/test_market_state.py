import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from corei.contracts.market import (
    DataOrigin,
    Freshness,
    MarketState,
    QuoteObservation,
    SessionStatus,
    StateSource,
)
from corei.contracts.trading import Origin, TradeIntent

VECTORS = Path(__file__).resolve().parents[2] / "contracts" / "vectors" / "valid"


def _payload(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((VECTORS / f"{name}.json").read_text())["payload"]  # type: ignore[no-any-return]


def _state(**changes: object) -> MarketState:
    data = _payload("market.state") | changes
    return MarketState.model_validate_json(json.dumps(data))


def test_measured_fresh_open_state_is_tradable() -> None:
    assert _state().tradable_now


@pytest.mark.parametrize(
    "changes",
    [
        {"freshness": Freshness.UNKNOWN.value},
        {"freshness": Freshness.STALE.value},
        {"source": StateSource.SIMULATED.value},
        {"source": StateSource.DERIVED.value},
        {"session_status": SessionStatus.UNKNOWN.value},
        {"open_gaps": 1},
    ],
)
def test_unknown_or_simulated_state_is_never_tradable(changes: dict[str, object]) -> None:
    """F26: trust must come from measurement; labels alone never make data tradable."""
    assert not _state(**changes).tradable_now


def test_expired_state_is_not_tradable() -> None:
    s = _state()
    assert not _state(as_of_ns=s.expires_at_ns).tradable_now


def test_simulated_observation_is_labelled() -> None:
    """F42: origin is a required field, so simulated data can be fenced from observed datasets."""
    data = _payload("market.quote")
    data["origin"] = DataOrigin.SIMULATED.value
    q = QuoteObservation.model_validate_json(json.dumps(data))
    assert q.origin is DataOrigin.SIMULATED
    del data["origin"]
    with pytest.raises(ValidationError, match="origin"):
        QuoteObservation.model_validate_json(json.dumps(data))


def test_external_intent_keeps_origin_and_partition() -> None:
    """F39: an EXTERNAL (CEIL) intent carries its own origin and capital partition."""
    data = _payload("trade.intent") | {
        "origin": "EXTERNAL",
        "source_ref": "ceil_src_7",
        "capital_partition": "external_a",
        "signal_ids": [],
    }
    intent = TradeIntent.model_validate_json(json.dumps(data))
    assert intent.origin is Origin.EXTERNAL and intent.capital_partition == "external_a"
    data.pop("capital_partition")
    with pytest.raises(ValidationError, match="capital_partition"):
        TradeIntent.model_validate_json(json.dumps(data))
