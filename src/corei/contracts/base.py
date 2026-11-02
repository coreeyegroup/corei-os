"""Shared contract building blocks.

All contracts are strict, immutable and reject unknown fields, so a producer
that drifts from the contract fails loudly at the consumer instead of being
half-understood (F17, F41).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        use_enum_values=False,
        validate_default=True,
        ser_json_inf_nan="strings",
    )


# Identifiers: lowercase-ish tokens, prefixes like ord_, acc_, ins_.
Ident = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$")
]
# Nanoseconds since Unix epoch, UTC.
TimestampNs = Annotated[int, Field(gt=0, lt=10**20)]
# Integer at a declared decimal scale (see corei.domain.units).
Scaled = Annotated[int, Field(ge=-(10**30), le=10**30)]
NonNegScaled = Annotated[int, Field(ge=0, le=10**30)]
PosScaled = Annotated[int, Field(gt=0, le=10**30)]
Scale = Annotated[int, Field(ge=0, le=18)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Bps = Annotated[int, Field(ge=0, le=10_000)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Version = Annotated[int, Field(ge=0)]


class MoneyAmount(Contract):
    units: Scaled
    currency: Currency
    scale: Scale
