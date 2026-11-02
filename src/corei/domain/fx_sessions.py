"""Global FX market sessions, DST-aware (forex architecture section 1.9).

Spot FX trades 24x5. The trading week opens Sunday 17:00 New York time and
closes Friday 17:00 New York time; 17:00 New York is also the daily rollover
(swap/carry is applied to positions held through it).

Sessions are defined in each centre's *local* time and converted with IANA
time zones, so the UTC windows move correctly with daylight saving:

    Sydney   07:00-16:00 Australia/Sydney
    Tokyo    09:00-18:00 Asia/Tokyo
    London   08:00-17:00 Europe/London
    New York 08:00-17:00 America/New_York

The blueprint's fixed UTC table (London 08:00-17:00 UTC, ...) is the winter
approximation of these. The thin window around the daily rollover and the
Sunday open is flagged ILLIQUID so policy can restrict trading there.
Exchange holidays are reference data passed in by the caller.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class FxSession(StrEnum):
    SYDNEY = "SYDNEY"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"


_SESSIONS: dict[FxSession, tuple[ZoneInfo, time, time]] = {
    FxSession.SYDNEY: (ZoneInfo("Australia/Sydney"), time(7), time(16)),
    FxSession.TOKYO: (ZoneInfo("Asia/Tokyo"), time(9), time(18)),
    FxSession.LONDON: (ZoneInfo("Europe/London"), time(8), time(17)),
    FxSession.NEW_YORK: (NEW_YORK, time(8), time(17)),
}
ROLLOVER = time(17)
ILLIQUID_AFTER_ROLLOVER = timedelta(hours=1)  # 17:00-18:00 New York, incl. Sunday open


class MarketPhase(StrEnum):
    CLOSED = "CLOSED"  # weekend or holiday
    ILLIQUID = "ILLIQUID"  # open, but inside the post-rollover thin window
    OPEN = "OPEN"


@dataclass(frozen=True, slots=True)
class FxMarketState:
    at_ns: int
    phase: MarketPhase
    sessions: frozenset[FxSession]
    trading_day: date  # FX value-date convention: the day rolls at 17:00 New York

    @property
    def overlap(self) -> bool:
        return len(self.sessions) >= 2


def _session_open(session: FxSession, at_utc: datetime) -> bool:
    tz, start, end = _SESSIONS[session]
    local = at_utc.astimezone(tz)
    return local.weekday() < 5 and start <= local.time() < end


def fx_market_state(at_ns: int, holidays: Iterable[date] = ()) -> FxMarketState:
    """FX market phase and active sessions at an event time (no wall clock)."""
    at_utc = _EPOCH + timedelta(microseconds=at_ns // 1_000)  # exact, no float
    ny = at_utc.astimezone(NEW_YORK)
    trading_day = (ny + timedelta(days=1)).date() if ny.time() >= ROLLOVER else ny.date()
    wd, t = ny.weekday(), ny.time()
    weekend = (wd == 4 and t >= ROLLOVER) or wd == 5 or (wd == 6 and t < ROLLOVER)
    if weekend or trading_day in set(holidays):
        return FxMarketState(at_ns, MarketPhase.CLOSED, frozenset(), trading_day)
    sessions = frozenset(s for s in FxSession if _session_open(s, at_utc))
    rollover_today = datetime.combine(ny.date(), ROLLOVER, tzinfo=NEW_YORK)
    in_thin_window = rollover_today <= ny < rollover_today + ILLIQUID_AFTER_ROLLOVER
    phase = MarketPhase.ILLIQUID if in_thin_window else MarketPhase.OPEN
    return FxMarketState(at_ns, phase, sessions, trading_day)
