"""Currency registry: code -> minor-unit scale (multi-currency, Anchor Law).

Fiat scales follow ISO 4217 minor units; crypto uses the platform's chosen
precision. A currency must be registered before cash in it can exist, so a
typo can never create a phantom balance. Extend by adding a reviewed entry.
"""

from __future__ import annotations

import re

_CODE = re.compile(r"^[A-Z][A-Z0-9]{2,5}$")

SCALES: dict[str, int] = {
    # G10 and other majors
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "JPY": 0,
    "CHF": 2,
    "CAD": 2,
    "AUD": 2,
    "NZD": 2,
    "SEK": 2,
    "NOK": 2,
    "DKK": 2,
    # Middle East (incl. 3-decimal Gulf currencies)
    "AED": 2,
    "SAR": 2,
    "QAR": 2,
    "BHD": 3,
    "KWD": 3,
    "OMR": 3,
    "ILS": 2,
    # Asia-Pacific
    "SGD": 2,
    "HKD": 2,
    "CNH": 2,
    "CNY": 2,
    "INR": 2,
    "KRW": 0,
    "THB": 2,
    "IDR": 2,
    "MYR": 2,
    "PHP": 2,
    "TWD": 2,
    # Emerging / exotic FX
    "TRY": 2,
    "ZAR": 2,
    "MXN": 2,
    "BRL": 2,
    "PLN": 2,
    "HUF": 2,
    "CZK": 2,
    "RUB": 2,
    # Metals as currencies (troy ounces), common on FX platforms
    "XAU": 3,
    "XAG": 3,
    "XPT": 3,
    "XPD": 3,
    # Crypto
    "BTC": 8,
    "ETH": 8,
    "USDT": 6,
    "USDC": 6,
}


class UnknownCurrency(ValueError):
    pass


def currency_scale(code: str) -> int:
    if not _CODE.match(code):
        raise UnknownCurrency(f"invalid currency code {code!r}")
    try:
        return SCALES[code]
    except KeyError as exc:
        raise UnknownCurrency(f"currency {code} is not registered") from exc
