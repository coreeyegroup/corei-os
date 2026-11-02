from datetime import UTC, date, datetime

import pytest

from corei.domain.currencies import UnknownCurrency, currency_scale
from corei.domain.fx import FxError, FxRate, convert
from corei.domain.fx_sessions import FxSession, MarketPhase, fx_market_state


def ns(*args: int) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp()) * 1_000_000_000


def rate(base: str, quote: str, value_units: int, scale: int = 5) -> FxRate:
    return FxRate(base, quote, value_units, scale, as_of_ns=1, source="test")


# ---------------------------------------------------------------- currencies


def test_currency_scales_follow_iso_minor_units() -> None:
    assert (currency_scale("USD"), currency_scale("JPY"), currency_scale("BHD")) == (2, 0, 3)
    assert currency_scale("AED") == 2 and currency_scale("USDT") == 6
    for bad in ("usd", "XYZ", "U$D"):
        with pytest.raises(UnknownCurrency):
            currency_scale(bad)


# ---------------------------------------------------------------- conversion


def test_direct_inverse_and_pivot_conversion() -> None:
    eurusd, usdjpy, gbpusd = (
        rate("EUR", "USD", 108_250),
        rate("USD", "JPY", 15_210_000),
        rate("GBP", "USD", 127_500),
    )
    assert convert(100_000, "EUR", "USD", [eurusd]).units == 108_250  # 1,000.00 EUR -> 1,082.50 USD
    assert convert(108_250, "USD", "EUR", [eurusd]).units == 100_000  # inverse
    yen = convert(100_000, "EUR", "JPY", [eurusd, usdjpy])  # via USD pivot
    assert yen.units == 164_648 and len(yen.rates_used) == 2  # 1,000 EUR -> 164,648 JPY (floored)
    assert convert(10_000, "GBP", "GBP", []).units == 10_000
    assert convert(100, "GBP", "USD", [gbpusd]).rates_used == (gbpusd,)


def test_conversion_rounds_down_and_fails_without_a_path() -> None:
    assert convert(1, "USD", "EUR", [rate("EUR", "USD", 300_000)]).units == 0  # 0.01/3 -> 0.00
    assert convert(-1, "USD", "EUR", [rate("EUR", "USD", 300_000)]).units == -1  # never overstates
    with pytest.raises(FxError, match="no rate path"):
        convert(100, "AED", "JPY", [rate("EUR", "USD", 108_250)])
    with pytest.raises(FxError):
        FxRate("USD", "USD", 1, 0, 1, "x")


# ---------------------------------------------------------------- sessions


def test_week_open_and_close_follow_new_york_17_00() -> None:
    # Winter (EST, UTC-5): Sunday 17:00 NY = 22:00 UTC
    assert fx_market_state(ns(2026, 1, 11, 21, 59)).phase is MarketPhase.CLOSED
    assert fx_market_state(ns(2026, 1, 11, 22, 0)).phase is MarketPhase.ILLIQUID  # Sunday open
    assert fx_market_state(ns(2026, 1, 11, 23, 0)).phase is MarketPhase.OPEN
    assert fx_market_state(ns(2026, 1, 16, 21, 59)).phase is MarketPhase.OPEN  # Friday 16:59 NY
    assert fx_market_state(ns(2026, 1, 16, 22, 0)).phase is MarketPhase.CLOSED
    # Summer (EDT, UTC-4): the same open is 21:00 UTC
    assert fx_market_state(ns(2026, 7, 12, 21, 0)).phase is MarketPhase.ILLIQUID


def test_sessions_and_overlaps_shift_with_daylight_saving() -> None:
    winter = fx_market_state(ns(2026, 1, 14, 14, 0))  # Wed 14:00 UTC
    assert winter.sessions == {FxSession.LONDON, FxSession.NEW_YORK} and winter.overlap
    assert FxSession.NEW_YORK not in fx_market_state(ns(2026, 1, 14, 12, 30)).sessions  # 07:30 NY
    summer = fx_market_state(ns(2026, 7, 15, 12, 30))  # 08:30 NY (EDT), 13:30 London (BST)
    assert summer.sessions == {FxSession.LONDON, FxSession.NEW_YORK}
    tokyo = fx_market_state(ns(2026, 1, 14, 1, 0))  # 10:00 Tokyo, 12:00 Sydney (AEDT)
    assert tokyo.sessions == {FxSession.TOKYO, FxSession.SYDNEY}


def test_trading_day_rolls_at_new_york_17_00_and_holidays_close() -> None:
    before = fx_market_state(ns(2026, 1, 14, 21, 59))
    after = fx_market_state(ns(2026, 1, 14, 22, 0))
    assert before.trading_day == date(2026, 1, 14) and after.trading_day == date(2026, 1, 15)
    assert after.phase is MarketPhase.ILLIQUID  # post-rollover thin hour
    xmas = fx_market_state(ns(2026, 12, 25, 12, 0), holidays=[date(2026, 12, 25)])
    assert xmas.phase is MarketPhase.CLOSED
