# ADR 0002 — Runtime foundation: processing, ledger and control (Delivery B)

Date: 2026-11-02 · Status: accepted

## Event processing

* **Postgres is the source of truth; Kafka is transport.** Every state change
  and the events describing it commit in one transaction (transactional
  outbox). A relay publishes outbox rows and marks them only after the broker
  acknowledges (`acks=all`, idempotent producer).
* **Consumers use an inbox.** `(consumer, event_id)` is inserted first; a
  conflict means "already processed". Offsets are committed only after the
  database commit. Event ids are deterministic, so a re-published event is
  absorbed. Result: at-least-once delivery, exactly-once *effects* in the
  database. This argument does not extend to broker submission, which uses
  `SUBMISSION_UNKNOWN` reconciliation (C02).
* **Failure handling (C11).** Unparseable or tampered envelopes, wrong modes,
  unhandled types and permanent domain errors are recorded in
  `rejected_events` and acknowledged — never silently dropped, never retried
  forever. Any other exception rolls back and the message is redelivered.
* **One topology** (`src/corei/infra/kafka_topics.yaml`): topic
  `corei.<env>.<family>`, key = envelope `partition_key`. Topics are created
  and verified by `scripts/kafka_topology.py`, which fails when the broker
  does not match. Auto-creation is disabled.

## Ledger accounting conventions

* Append-only journal (`ledger_entries`, `fills`; enforced by triggers) with
  projections (`cash_balances`, `positions`) updated in the same transaction.
* Fills dedupe on `(account, broker, broker_execution_id)`. The same id with
  different content raises `FillConflict` (a reconciliation case).
* Position = signed quantity + signed open cost in account currency units.
  Realized P&L excludes fees; fees are separate journal entries. The identity
  `cash change = realized − open cost − fees` holds exactly (property-tested).
* Notional = quantity × price × multiplier, rescaled to the currency scale.
  Exact when representable; otherwise buys round up and sells round down so
  the account is never credited money it may not have received. Broker-specific
  rounding is matched when real broker fills arrive (Delivery F).
* FX conversion is not supported yet: an instrument must settle in the
  account's currency.

## Reservations

* Taken under a row lock on the account (`SELECT … FOR UPDATE`), so parallel
  requests serialize; the test runs 40 concurrent requests against capacity
  for 10 and requires exactly 10 to succeed.
* Idempotent per `command_id`. Expiry is evaluated against the caller's
  `as_of` time, not the wall clock.
* A caller may pass the portfolio `state_version` its risk check used; any
  change since then refuses the reservation (`STALE_PORTFOLIO_STATE`), so risk
  is never approved on stale state (C12).

## Control

* Durable `control_state` per scope with versioned, idempotent commands and an
  append-only command log. Missing state is fail-closed.
* API authentication: bearer tokens, stored only as SHA-256 hashes. Roles:
  `operator` may halt and cancel; `risk_officer` may also release, flatten and
  set modes; `system` may halt (automated controls). LIVE modes are refused by
  settings, authorization, the ledger and bootstrap until Delivery H.
* Readiness reports database, schema head and GLOBAL control state separately;
  a fresh, unbootstrapped system is not ready.

## Not in B (tracked in the register)

Risk evaluator and OMS dispatch fence checks (D), market-data tables (C),
broker sessions and credentials (F), Orbit binding (G).
