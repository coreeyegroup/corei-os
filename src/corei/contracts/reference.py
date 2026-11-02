"""Institutional reference data: instruments, legal entities, brokers, accounts, groups.

Follows the trading blueprint (Layers 3-5) and the Anchor Law's institutional
standard: multi account, multi broker, multi asset, multi currency, multi
region and multi jurisdiction from inception.

* An **instrument** carries its own units (tick, lot step, multiplier,
  settlement currency). FX pairs carry base/quote currency and pip size; they
  settle in the quote currency. An index is observable, never orderable (C05).
* A **legal entity** owns accounts and fixes jurisdiction (US, GB, AE-DIFC ...),
  regulator and base currency. Jurisdiction-specific rules (e.g. US FIFO/no
  hedging, leverage caps) are enforced by policy using this data.
* A **broker** declares its platform and capabilities; an **account** binds to
  up to three brokers ranked primary/secondary/tertiary and may only route to
  those (blueprint Layer 4). Credentials are referenced, never embedded.
* An **account group** assigns strategies and optionally a copy-trade master.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, model_validator

from corei.contracts.base import (
    Contract,
    Currency,
    Ident,
    PosScaled,
    Scale,
    TimestampNs,
    Version,
)

Jurisdiction = Annotated[str, Field(pattern=r"^[A-Z]{2}(-[A-Z0-9]{1,8})?$")]  # ISO 3166 + zone
TimeZoneName = Annotated[str, Field(pattern=r"^[A-Za-z]+(/[A-Za-z_\-]+)+$|^UTC$")]


class AssetClass(StrEnum):
    FX = "FX"
    METAL = "METAL"
    ENERGY = "ENERGY"
    AGRICULTURE = "AGRICULTURE"
    INDEX = "INDEX"
    EQUITY = "EQUITY"
    CRYPTO = "CRYPTO"
    RATES = "RATES"


class InstrumentKind(StrEnum):
    SPOT = "SPOT"
    CFD = "CFD"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    SHARE = "SHARE"
    INDEX = "INDEX"  # reference/observation only


class OptionRight(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class InstrumentSpec(Contract):
    instrument_id: Ident
    version: Version
    asset_class: AssetClass
    kind: InstrumentKind
    venue: Ident
    symbol: str  # canonical COREI symbol, e.g. EURUSD, XAUUSD
    tradable: bool
    base_currency: Currency | None = None  # FX/crypto/metal pairs: EUR in EURUSD
    quote_currency: Currency | None = None  # USD in EURUSD; P&L currency of the pair
    pip_size_units: PosScaled | None = None  # at price scale, e.g. 10 = 0.00010 at scale 5
    underlying_id: Ident | None = None
    expiry_ns: TimestampNs | None = None
    strike_units: PosScaled | None = None
    option_right: OptionRight | None = None

    price_scale: Scale
    tick_size_units: PosScaled
    quantity_scale: Scale  # e.g. 2 => quantities in 0.01 lot
    quantity_step_units: PosScaled
    min_quantity_units: PosScaled
    max_quantity_units: PosScaled | None = None
    contract_multiplier_units: PosScaled  # e.g. 100000 units of base per FX lot
    multiplier_scale: Scale
    settlement_currency: Currency
    currency_scale: Scale
    session_calendar: Ident  # e.g. fx-24x5, cme-globex, nyse

    provider_keys: dict[str, str] = {}  # broker_id -> broker-native symbol
    valid_from_ns: TimestampNs
    valid_to_ns: TimestampNs | None = None

    @model_validator(mode="after")
    def _consistent(self) -> InstrumentSpec:
        if self.kind is InstrumentKind.INDEX and self.tradable:
            raise ValueError("an INDEX is an observation, not a tradable instrument (C05)")
        if self.kind in {InstrumentKind.FUTURE, InstrumentKind.OPTION} and (
            self.expiry_ns is None or self.underlying_id is None
        ):
            raise ValueError("derivatives need expiry_ns and underlying_id")
        if self.kind is InstrumentKind.OPTION and (
            self.strike_units is None or self.option_right is None
        ):
            raise ValueError("options need strike_units and option_right")
        is_pair = self.base_currency is not None or self.quote_currency is not None
        if self.asset_class is AssetClass.FX and not is_pair:
            raise ValueError("FX instruments are currency pairs: set base and quote currency")
        if is_pair:  # FX, metals quoted as XAUUSD, crypto pairs
            if self.base_currency is None or self.quote_currency is None:
                raise ValueError("currency pairs need both base_currency and quote_currency")
            if self.base_currency == self.quote_currency:
                raise ValueError("base and quote currency must differ")
            if self.settlement_currency != self.quote_currency:
                raise ValueError("a currency pair settles in its quote currency")
        if self.asset_class is AssetClass.FX and self.pip_size_units is None:
            raise ValueError("FX instruments need pip_size_units")
        if self.min_quantity_units % self.quantity_step_units:
            raise ValueError("min_quantity_units must be a multiple of quantity_step_units")
        if self.valid_to_ns is not None and self.valid_to_ns <= self.valid_from_ns:
            raise ValueError("valid_to_ns must be after valid_from_ns")
        return self


class LegalEntity(Contract):
    entity_id: Ident
    name: str = Field(min_length=1, max_length=200)
    jurisdiction: Jurisdiction  # US, GB, AE-DIFC, AE ...
    regulator: str | None = Field(default=None, max_length=100)  # e.g. NFA/CFTC, FCA, DFSA, SCA
    base_currency: Currency
    reporting_timezone: TimeZoneName
    active: bool = True
    version: Version = 1


class BrokerPlatform(StrEnum):
    PAPER = "PAPER"  # COREI's own simulated broker
    MT5 = "MT5"
    IB = "IB"
    CTRADER = "CTRADER"
    FIX = "FIX"
    ALPACA = "ALPACA"
    BINANCE = "BINANCE"
    CUSTOM = "CUSTOM"


class PositionModel(StrEnum):
    NETTING = "NETTING"  # one net position per instrument (e.g. US FIFO rules)
    HEDGING = "HEDGING"  # independent tickets per order (common on MT5)


class OrderTypeCapability(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    OCO = "OCO"


class BrokerSpec(Contract):
    broker_id: Ident
    name: str = Field(min_length=1, max_length=200)
    platform: BrokerPlatform
    jurisdictions: tuple[Jurisdiction, ...] = Field(min_length=1)  # where it may serve accounts
    asset_classes: tuple[AssetClass, ...] = Field(min_length=1)
    order_types: tuple[OrderTypeCapability, ...] = Field(min_length=1)
    position_model: PositionModel
    version: Version = 1


class BrokerBinding(Contract):
    broker_id: Ident
    rank: int = Field(ge=1, le=3)  # 1 primary, 2 secondary, 3 tertiary
    broker_account_ref: Ident  # the account number at the broker (not a secret)
    credential_ref: Ident | None = None  # name of a secret in the secret manager, never its value


class LedgerKind(StrEnum):
    """Where an account's money lives. Fixed for the account's life."""

    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class AccountSpec(Contract):
    account_id: Ident
    portfolio_id: Ident
    entity_id: Ident
    ledger_kind: LedgerKind
    base_currency: Currency
    bindings: tuple[BrokerBinding, ...] = Field(min_length=1, max_length=3)
    version: Version = 1

    @model_validator(mode="after")
    def _bindings(self) -> AccountSpec:
        ranks = sorted(b.rank for b in self.bindings)
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("broker bindings must be ranked 1..n without gaps (primary first)")
        brokers = [b.broker_id for b in self.bindings]
        if len(set(brokers)) != len(brokers):
            raise ValueError("a broker may be bound only once per account")
        return self


class AccountGroup(Contract):
    group_id: Ident
    name: str = Field(min_length=1, max_length=200)
    account_ids: tuple[Ident, ...] = Field(min_length=1)
    strategy_ids: tuple[Ident, ...] = ()
    copy_master_account_id: Ident | None = None
    version: Version = 1

    @model_validator(mode="after")
    def _members(self) -> AccountGroup:
        if len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError("duplicate account in group")
        if self.copy_master_account_id in self.account_ids:
            raise ValueError("a copy master cannot also follow itself in the same group")
        return self


class FxRateSnapshot(Contract):
    """A recorded FX rate: one BASE costs rate QUOTE. Used for valuation, never invented."""

    base_currency: Currency
    quote_currency: Currency
    rate_units: PosScaled
    rate_scale: Scale
    as_of_ns: TimestampNs
    source: Ident

    @model_validator(mode="after")
    def _pair(self) -> FxRateSnapshot:
        if self.base_currency == self.quote_currency:
            raise ValueError("a rate needs two different currencies")
        return self
