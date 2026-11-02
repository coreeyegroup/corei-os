# ADR 0005 — Tenant isolation in the database (B.3 step 1)

Status: accepted (B.3 approved 3 Nov 2026, decision A12)

## Decision

- Every tenant-owned table has `tenant_id`, leading every primary, unique and foreign key, so a row can never reference another tenant's row.
- A connection is bound to one tenant by the libpq option `-c app.tenant_id=<tenant>`; `corei.infra.db.connection_factory` always sets it from `COREI_TENANT`.
- New rows take `tenant_id` from that setting by default.
- Row-level security is enabled and **forced** on every tenant table, so it also binds the table owner — the installed service connects as the database owner.
- A connection without a tenant sees no rows and can write none (fail closed). Superusers bypass row-level security by PostgreSQL design; the service never connects as one.
- Migration 0003 assigns existing rows to the tenant of the connection that runs it, and refuses to run without one (`TENANT_UNSET`). It is a single transaction: it either completes or changes nothing.
- Backups (`corei-os backup`) run with `--enable-row-security` as the host's tenant and contain that tenant's rows plus the full schema.

  Restore with:

  `PGOPTIONS="-c app.tenant_id=<tenant>" pg_restore --no-owner -d <database> <dump>`

## Delivery changes from the B.3 design (recorded, not silent)

- **Four migrations instead of one.** B.3 ships as four steps, each one atomic and released separately: tenancy (0003); the account model (0004); accounting (0005); profiles and the API.
- **No "outbox not drained" pre-check.** Outbox rows keep their recorded topic and key and still publish after the upgrade. Refusing the upgrade whenever Kafka was briefly down would leave the services stopped for no safety gain.
- **No tenant prefix on Kafka partition keys yet.** Every installation today has a single tenant. The prefix, with new topics so that ordering is preserved, is added before a second tenant is onboarded (stage C). Tenant isolation is enforced in the database now.

## Evidence

`tests/integration/test_tenancy.py` covers:

- isolation for a non-owner role and for the owner;
- no rows without a tenant;
- cross-tenant foreign keys refused;
- upgrade of a d54bd81 database, refused without a tenant and then assigned correctly, with history still append-only;
- a newer schema refused by an older build;
- backup and restore of every row.

Removing `FORCE` makes the owner test fail.

The `packages` workflow also upgrades the currently published release to the new build on Ubuntu 22.04 and 24.04.
