"""Producer -> consumer contract checks against committed vectors.

Each valid vector must parse into an ``EventEnvelope`` (hash and partition key
verified), survive a JSON round trip unchanged, and validate against the
exported, language-neutral JSON Schema. Each invalid vector must be refused
with its expected reason. Every registered event type needs a valid vector.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from corei.contracts.envelope import REGISTRY, EventEnvelope
from corei.domain.identity import derive_id

ROOT = Path(__file__).resolve().parents[2] / "contracts"
VALID = sorted((ROOT / "vectors" / "valid").glob("*.json"))
INVALID = sorted((ROOT / "vectors" / "invalid").glob("*.json"))


def _schema(event_type: str) -> dict[str, Any]:
    v = REGISTRY[event_type].version
    return json.loads((ROOT / "schemas" / f"{event_type}.v{v}.json").read_text())


def test_every_event_type_has_a_valid_vector() -> None:
    assert {p.stem for p in VALID} == set(REGISTRY)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_vector_parses_round_trips_and_matches_json_schema(path: Path) -> None:
    raw = path.read_text()
    env = EventEnvelope.model_validate_json(raw)
    assert env.event_type == path.stem
    again = EventEnvelope.model_validate_json(env.model_dump_json())
    assert again == env
    # language-neutral check: the payload satisfies the exported JSON Schema
    Draft202012Validator(_schema(env.event_type)).validate(env.payload)
    typed = env.typed_payload()
    assert typed.model_dump(mode="json") == env.payload


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_vector_is_refused(path: Path) -> None:
    case = json.loads(path.read_text())
    with pytest.raises(ValidationError) as err:
        if "envelope" in case:
            EventEnvelope.model_validate_json(json.dumps(case["envelope"]))
        else:
            REGISTRY[case["event_type"]].model.model_validate_json(json.dumps(case["payload"]))
    assert case["expect_error"] in str(err.value), str(err.value)


def test_event_id_is_stable_under_redelivery() -> None:
    env = EventEnvelope.model_validate_json(
        (ROOT / "vectors" / "valid" / "order.fill.json").read_text()
    )
    redelivered = EventEnvelope.model_validate_json(env.model_dump_json())
    assert redelivered.event_id == env.event_id


def test_two_accounts_from_one_intent_get_distinct_command_ids_f03() -> None:
    a = derive_id("cmd", "order.command", {"intent": "int_001", "account": "acc_paper_a"})
    b = derive_id("cmd", "order.command", {"intent": "int_001", "account": "acc_paper_b"})
    assert a != b
