# ADR 0003 — Institutional scope: entities, brokers, accounts, currencies, regions

Status: accepted (Delivery B.2)

## Context

The platform is built for international forex operated from the **US, UK and
Dubai**, across many brokers, accounts, strategies, instruments and
currencies. That is the Anchor Law in the legacy `BLUEPRINT_TRADING_PLATFORM_3.1`
and `FOREX_PLATFORM_ARCHITECTURE` documents. Delivery B shipped a single-broker,
single-currency paper slice. B.2 makes the foundations match the blueprint
before more services are built on them.

## Decisions

1. **Legal entity is first-class.** Every account belongs to exactly one
   `LegalEntity`, which carries a jurisdiction (ISO 3166 code plus an optional
   zone: `US`, `GB`, `AE`, `AE-DIFC` …), a regulator (left blank for the
   operator to fill), a base currency and a reporting time zone. An account's
   entity and ledger kind never change once registered.
2. **Brokers are reference data, not code paths.** A `BrokerSpec` names the
   platform (MT5, IB, cTrader, FIX, Alpaca, Binance, PAPER, CUSTOM), the
   jurisdictions it may serve, the asset classes and order types it supports
   and its position model (netting or hedging). An account may bind to that
   broker only if it serves the account entity's jurisdiction. A broker serving
   `AE` also serves `AE-DIFC`; the reverse does not hold.
3. **Primary/secondary/tertiary bindings.** An account has one to three ranked
   broker bindings. Ranks can be swapped in one transaction (deferrable unique
   constraint). Removing a broker that already has fills on the account is
   refused by a foreign key from `fills (account_id, broker)` to
   `broker_bindings`. A binding holds a `credential_ref` (a secret's name),
   never the secret itself.
4. **Multi-currency cash; settlement in the instrument's currency.** Cash is
   held per (account, currency) at the ISO 4217 minor-unit scale (JPY 0, USD 2,
   KWD 3; metals XAU/XAG at 2). An FX fill settles in the pair's quote currency
   (EURUSD → USD, USDJPY → JPY). Fees settle in whatever currency the broker
   charged. Nothing is converted at fill time.
5. **Valuation is fail-closed.** Base-currency equity uses recorded FX rates
   (`fx_rates`, append-only, with source and as-of time; direct, inverse or a
   USD pivot) and caller-supplied marks. A missing or stale rate or mark makes
   the valuation `INCOMPLETE` and withholds equity. It never guesses.
6. **Modes and halts follow the blueprint's Layer 9.** Modes are
   DISABLED / BACKTEST / PAPER / LIVE_SEMI / LIVE_AUTO. They are set on GLOBAL,
   per ACCOUNT, or per (ACCOUNT, STRATEGY); an unset pair inherits the account
   mode. Halts can be applied at GLOBAL, ENTITY, BROKER, ACCOUNT, STRATEGY,
   INSTRUMENT and ACCOUNT_STRATEGY scope. Every scope an order touches is
   fenced, and absent scopes are fenced at v0, so a first halt anywhere
   invalidates in-flight orders. Any `trading_ops` principal can halt; only
   `risk_admin` can release. LIVE modes are refused until Delivery H.
7. **Account groups and copy trading.** Groups share strategies. A group may
   name a copy master. Copy edges across all groups must stay acyclic, and this
   is checked on every write.
8. **FX sessions are DST-aware.** The default `fx-24x5` calendar opens Sunday
   17:00 and closes Friday 17:00 New York time, with the hours computed from
   `zoneinfo`, not fixed UTC offsets.

## Deviation from the blueprint: Kafka as "single source of truth"

The blueprint says Kafka is the single source of truth. We keep **Postgres as
the transactional write model**. Every state change is written once, together
with its outbox event, in the same transaction. The relay then publishes every
outbox event to Kafka, so Kafka still carries a complete, ordered history.
Downstream services, analytics and replay read from Kafka, and state can be
rebuilt from it.

Why: exactly-once financial effects (fill applied once, no over-reservation,
fenced control) need row locks and constraints in the same transaction as the
decision. Kafka transactions cannot enforce those invariants across keys. This
is recorded as an explicit decision rather than left as silent drift.

## Jurisdiction rules: where they will be enforced

Rules such as US retail FX (NFA FIFO and no-hedging, CFTC leverage caps), UK
FCA retail leverage limits and negative-balance protection, and DFSA/SCA
requirements are **not** hard-coded here. They will be enforced in the policy
service (Delivery D), keyed on the account entity's jurisdiction and the
broker's position model. The data needed to do that now exists on every order
path.

## Consequences

* Adding a broker, region or currency is reference data plus an adapter
  (Delivery F), not a schema change.
* `config/paper-bootstrap.yaml` creates US, UK and Dubai paper entities, the
  paper broker, eleven FX/metal instruments, three accounts and one group. Real
  entities and brokers are added the same way.
