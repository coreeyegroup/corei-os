"""Time model (finding F16).

* All timestamps are integer nanoseconds since the Unix epoch, UTC (``*_ns``).
* Domain logic never reads the wall clock. It receives ``as_of_ns`` from the
  event it is processing, so replaying the same inputs yields the same output.
* The wall clock is read only at system boundaries (ingestion receive time,
  operator commands) through a ``Clock``, which tests replace with
  ``FixedClock``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

NS_PER_SECOND = 1_000_000_000
NS_PER_MS = 1_000_000


class Clock(Protocol):
    def now_ns(self) -> int: ...


class SystemClock:
    """The only sanctioned wall-clock reader. Use at boundaries only."""

    def now_ns(self) -> int:
        return time.time_ns()


@dataclass
class FixedClock:
    """Deterministic clock for tests and replay."""

    current_ns: int
    step_ns: int = 0
    _calls: int = field(default=0, init=False)

    def now_ns(self) -> int:
        value = self.current_ns + self._calls * self.step_ns
        self._calls += 1
        return value


def is_expired(expires_at_ns: int, as_of_ns: int) -> bool:
    """True when the item is no longer valid at ``as_of_ns`` (expiry is exclusive)."""
    return as_of_ns >= expires_at_ns


def age_ns(observed_at_ns: int, as_of_ns: int) -> int:
    """Age of an observation at ``as_of_ns``; negative ages are a clock fault."""
    age = as_of_ns - observed_at_ns
    if age < 0:
        raise ValueError(
            f"observation is {-age} ns in the future relative to as_of; check clock alignment"
        )
    return age
