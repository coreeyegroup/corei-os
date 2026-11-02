import pytest

from corei.domain.identity import (
    CanonicalizationError,
    canonical_bytes,
    canonical_hash,
    derive_id,
)
from corei.domain.timeutil import FixedClock, age_ns, is_expired


def test_canonical_bytes_are_order_and_whitespace_independent() -> None:
    a = {"b": 1, "a": [1, 2], "c": None}
    b = {"a": [1, 2], "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b) == b'{"a":[1,2],"b":1}'


def test_unicode_is_nfc_normalized() -> None:
    composed, decomposed = "é", "é"
    assert canonical_bytes({"x": composed}) == canonical_bytes({"x": decomposed})


def test_floats_are_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="float"):
        canonical_bytes({"price": 2350.1})


def test_hash_is_domain_separated() -> None:
    payload = {"id": "x"}
    assert canonical_hash("order", payload) != canonical_hash("fill", payload)


def test_concatenation_collisions_of_legacy_lineage_are_impossible() -> None:
    # Legacy: sha256(a + b) made ("ab","c") == ("a","bc").
    assert derive_id("ord", "order", {"a": "ab", "b": "c"}) != derive_id(
        "ord", "order", {"a": "a", "b": "bc"}
    )


def test_derived_id_shape_and_determinism() -> None:
    first = derive_id("ord", "order", {"q": 75})
    assert first == derive_id("ord", "order", {"q": 75})
    assert first.startswith("ord_") and len(first) == 36
    assert first != derive_id("ord", "order", {"q": 150})  # quantity is part of identity (F19)


def test_fixed_clock_and_expiry() -> None:
    clock = FixedClock(current_ns=1_000, step_ns=10)
    assert [clock.now_ns(), clock.now_ns()] == [1_000, 1_010]
    assert is_expired(expires_at_ns=2_000, as_of_ns=2_000)
    assert not is_expired(expires_at_ns=2_000, as_of_ns=1_999)
    assert age_ns(1_000, 1_500) == 500
    with pytest.raises(ValueError, match="future"):
        age_ns(2_000, 1_000)
