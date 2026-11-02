# COREI OS

Clean rebuild of the COREI trading platform. The legacy repository
(`coreeyegroup/corei` at `a08758dc3`) is preserved read-only as evidence; no
legacy service directory is copied here.

The plan of record is **COREI_PLAN_RECONCILIATION.md** (deliveries A–I,
corrections C01–C13) together with the assessment's findings **F01–F50**.

## Status

| Delivery | Scope | State |
| --- | --- | --- |
| **A — Baseline and contracts** | Source disposition, secret inventory, canonical contracts, units/time/identity rules, CI | **This commit** |
| B — Minimal runtime and controls | Migrations, Kafka topology, authenticated commands, PAPER mode, durable halt, ledger seed, reservations | Next |
| C — Data and research foundations | Simulator/replay, observations, candles, per-key features | — |
| D — Complete paper loop | Strategy → intent → risk/policy/sizing/reservation → OMS → paper broker → ledger → reconciliation | — |
| E–I | Safety qualification, live-data paper, deployment, limited live, expansion | — |

No real orders exist anywhere in this codebase. Real-money execution is a
separately authorized step (Delivery H).

## Layout

```
src/corei/domain/      pure rules: units, time, identity, order/control/reconciliation state machines
src/corei/contracts/   canonical event and command contracts (single source of truth)
contracts/schemas/     language-neutral JSON Schemas exported from the contracts (generated)
contracts/vectors/     valid/invalid example events every producer and consumer must agree on
docs/findings/         F01–F50 register: status, delivery, gate and the tests that cover each
docs/adr/              architecture decisions
scripts/               schema/vector export, repo hygiene
tests/                 unit, contract and acceptance tests
```

## Working

```bash
make sync            # uv sync --frozen (Python >= 3.12)
make check           # lint, mypy, tests, schema drift, repo hygiene — same as CI
make schemas         # regenerate JSON Schemas after changing a contract
uv run python scripts/make_vectors.py   # regenerate contract vectors
```

## Rules that every change must keep

1. **One contract source.** Events are defined once in `corei.contracts`; JSON
   Schemas and vectors are generated and CI fails on drift.
2. **No binary floats for money or quantity.** Integers at instrument-defined
   scales (`corei.domain.units`).
3. **Domain code never reads the wall clock.** It uses the event's `as_of`
   time; a static test enforces this.
4. **Fail closed.** Missing control state, unknown mode or stale data means no
   new order.
5. **A timeout is not a rejection.** Uncertain submissions go to
   `SUBMISSION_UNKNOWN` and are reconciled before any resend.
6. **Evidence is executable.** A gate passes only when CI runs a test against
   this commit. Legacy stage reports are history, not evidence.
7. **No secrets, dependencies or runtime output in Git.** `scripts/repo_hygiene.py`
   and gitleaks run in CI and pre-commit.
