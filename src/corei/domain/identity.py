"""Canonical serialization and deterministic identity (findings F16, F19, F24).

Legacy lineage hashed a few concatenated strings (``a + b + c``), so different
inputs could collide and the hash ignored fields such as final quantity.

Rules here:

* Canonical JSON: UTF-8, keys sorted, no insignificant whitespace, strings
  NFC-normalized, integers only (floats rejected), ``None`` values omitted so
  "absent" and "null" hash the same.
* Hashes are domain-separated: ``sha256("corei:v1:" + domain + NUL + canonical)``.
  An order hash can never equal a fill hash for the same bytes.
* Identifiers derive from the complete canonical content they stand for.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

HASH_PREFIX = b"corei:v1:"
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")
_PREFIX_RE = re.compile(r"^[a-z]{2,8}$")


class CanonicalizationError(ValueError):
    pass


def _normalize(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise CanonicalizationError(f"float at {path}: use scaled integers")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string key at {path}: {key!r}")
            if item is None:
                continue
            out[unicodedata.normalize("NFC", key)] = _normalize(item, f"{path}.{key}")
        return out
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [_normalize(item, f"{path}[{i}]") for i, item in enumerate(value)]
    raise CanonicalizationError(f"unsupported type at {path}: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON bytes for a JSON-compatible value."""
    normalized = _normalize(value, "$")
    return json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_hash(domain: str, value: Any) -> str:
    """Domain-separated SHA-256 hex digest of ``value``'s canonical form."""
    if not _DOMAIN_RE.match(domain):
        raise CanonicalizationError(f"invalid hash domain {domain!r}")
    digest = hashlib.sha256()
    digest.update(HASH_PREFIX)
    digest.update(domain.encode("ascii"))
    digest.update(b"\x00")
    digest.update(canonical_bytes(value))
    return digest.hexdigest()


def derive_id(prefix: str, domain: str, value: Any) -> str:
    """Deterministic identifier such as ``ord_<32 hex>`` for the given content."""
    if not _PREFIX_RE.match(prefix):
        raise CanonicalizationError(f"invalid id prefix {prefix!r}")
    return f"{prefix}_{canonical_hash(domain, value)[:32]}"
