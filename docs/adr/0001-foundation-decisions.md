# ADR 0001 — Foundation decisions for the rebuild

Date: 2026-11-02 · Status: accepted

## Context

The legacy platform could not place a trade end to end (F02), lost account and
origin identity between hops (F03), used incompatible contracts across Python
and Go (F17), and certified stages with scripts that never touched a running
service (F25). The finalized plan (COREI_PLAN_RECONCILIATION.md) sets
deliveries A–I and corrections C01–C13.

## Decisions

1. **New repository, fresh history.** The legacy repo is evidence only; its
   history contains exposed keys (F01).
2. **Python first (3.12+).** All Delivery A–D modules are Python. Contracts are
   exported as JSON Schema so another language can be introduced later where a
   benchmark shows the need (C07). Language choice does not by itself prevent
   drift; generated contracts and vectors do.
3. **Logical modules, not ten deployments.** Market data, features, strategy,
   decision, pre-trade (routing + risk + policy + sizing + reservation), OMS,
   broker gateway, ledger, reconciliation and control are module boundaries
   with one state owner each. They may share processes until measurement
   justifies a split (C06).
4. **Contracts are Pydantic models, strict and frozen**, with unknown fields
   rejected. One registry maps event type → model → version → partition key.
5. **Units per instrument** (C04): integers at scales defined by instrument
   reference data; binary floats rejected at every boundary.
6. **Time:** integer nanoseconds UTC; domain logic uses the event's as-of time;
   the wall clock is read only at boundaries (F16).
7. **Identity:** canonical JSON + domain-separated SHA-256; ids derive from the
   full content they identify (F19).
8. **Order lifecycle** includes `SUBMISSION_UNKNOWN`; a timeout never becomes a
   rejection (C02). Late fills after cancel are applied.
9. **Control is fail-closed and fenced.** Missing state blocks orders; the
   control versions read at reservation are re-checked at dispatch (C08, C12).
   Halt, cancel and flatten are separate commands (C03).
10. **Mode on every event** (`SIMULATION`/`PAPER`/`LIVE_*`) and `origin` on
    every observation, so simulated data can be fenced from observed datasets
    (F42) and paper never reaches a live adapter.

## Consequences

* Delivery B must provide durable storage (Postgres) for the inbox/outbox,
  order state, ledger, reservations and control state before any paper order.
* Kafka (or an equivalent log) is a transport, not the source of truth for
  money; compacted topics are read models only (C12).
* Open decisions remain in the plan's register: first tradable instrument and
  product (C05), verified broker sandbox/token/update capabilities (C01),
  target Kubernetes timing (Delivery G).
