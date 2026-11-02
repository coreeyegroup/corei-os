"""Durable reference data with institutional integrity rules.

Every write is idempotent: the same (id, version) with the same content is a
no-op; the same version with different content is refused (publish a new
version instead). Rules enforced here, not left to callers:

* an account's entity must exist and be active;
* every bound broker must exist and serve the entity's jurisdiction;
* every currency must be registered (no phantom balances);
* copy-trading relationships must be acyclic and never self-referencing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from corei.contracts.reference import (
    AccountGroup,
    AccountSpec,
    BrokerSpec,
    FxRateSnapshot,
    InstrumentSpec,
    LedgerKind,
    LegalEntity,
)
from corei.domain.currencies import UnknownCurrency, currency_scale
from corei.domain.fx import FxRate


class ReferenceError(ValueError):
    pass


def _check_currency(code: str, scale: int | None = None) -> int:
    try:
        registered = currency_scale(code)
    except UnknownCurrency as exc:
        raise ReferenceError(str(exc)) from exc
    if scale is not None and scale != registered:
        raise ReferenceError(f"{code} scale {scale} differs from registry scale {registered}")
    return registered


def _same_or_refuse(existing: object, data: object, what: str) -> bool:
    """True if already stored identically; raise if stored differently."""
    if existing is None:
        return False
    if existing != data:
        raise ReferenceError(f"{what} exists with different content; publish a new version")
    return True


def _jurisdiction_served(entity_jurisdiction: str, broker_jurisdictions: Iterable[str]) -> bool:
    """A broker serving 'AE' serves 'AE-DIFC'; one serving 'AE-DIFC' does not serve all of 'AE'."""
    country = entity_jurisdiction.split("-", maxsplit=1)[0]
    return any(j in {entity_jurisdiction, country} for j in broker_jurisdictions)


# ---------------------------------------------------------------- instruments


def upsert_instrument(conn: psycopg.Connection, spec: InstrumentSpec) -> None:
    _check_currency(spec.settlement_currency, spec.currency_scale)
    for code in (spec.base_currency, spec.quote_currency):
        if code is not None:
            _check_currency(code)
    data = spec.model_dump(mode="json")
    row = conn.execute(
        "SELECT spec FROM instruments WHERE instrument_id = %s AND version = %s",
        (spec.instrument_id, spec.version),
    ).fetchone()
    if _same_or_refuse(
        row[0] if row else None, data, f"instrument {spec.instrument_id} v{spec.version}"
    ):
        return
    conn.execute(
        "INSERT INTO instruments (instrument_id, version, tradable, spec, valid_from_ns, valid_to_ns) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            spec.instrument_id,
            spec.version,
            spec.tradable,
            Jsonb(data),
            spec.valid_from_ns,
            spec.valid_to_ns,
        ),
    )


def instrument_at(conn: psycopg.Connection, instrument_id: str, at_ns: int) -> InstrumentSpec:
    row = conn.execute(
        "SELECT spec FROM instruments WHERE instrument_id = %s AND valid_from_ns <= %s "
        "AND (valid_to_ns IS NULL OR valid_to_ns > %s) ORDER BY version DESC LIMIT 1",
        (instrument_id, at_ns, at_ns),
    ).fetchone()
    if row is None:
        raise ReferenceError(f"no instrument {instrument_id} valid at {at_ns}")
    return InstrumentSpec.model_validate(row[0], strict=False)


# ---------------------------------------------------------------- entities and brokers


def upsert_entity(conn: psycopg.Connection, spec: LegalEntity) -> None:
    _check_currency(spec.base_currency)
    data = spec.model_dump(mode="json")
    row = conn.execute(
        "SELECT spec, version FROM legal_entities WHERE entity_id = %s FOR UPDATE",
        (spec.entity_id,),
    ).fetchone()
    if row is not None:
        if int(row[1]) == spec.version:
            _same_or_refuse(row[0], data, f"entity {spec.entity_id} v{spec.version}")
            return
        if spec.version < int(row[1]):
            raise ReferenceError(f"entity {spec.entity_id}: v{spec.version} is older than stored")
    conn.execute(
        """INSERT INTO legal_entities (entity_id, jurisdiction, base_currency, version, spec)
           VALUES (%s, %s, %s, %s, %s) ON CONFLICT (entity_id) DO UPDATE SET
           jurisdiction = EXCLUDED.jurisdiction, base_currency = EXCLUDED.base_currency,
           version = EXCLUDED.version, spec = EXCLUDED.spec""",
        (spec.entity_id, spec.jurisdiction, spec.base_currency, spec.version, Jsonb(data)),
    )


def get_entity(conn: psycopg.Connection, entity_id: str) -> LegalEntity:
    row = conn.execute(
        "SELECT spec FROM legal_entities WHERE entity_id = %s", (entity_id,)
    ).fetchone()
    if row is None:
        raise ReferenceError(f"unknown entity {entity_id}")
    return LegalEntity.model_validate(row[0], strict=False)


def upsert_broker(conn: psycopg.Connection, spec: BrokerSpec) -> None:
    data = spec.model_dump(mode="json")
    row = conn.execute(
        "SELECT spec, version FROM brokers WHERE broker_id = %s FOR UPDATE", (spec.broker_id,)
    ).fetchone()
    if row is not None:
        if int(row[1]) == spec.version:
            _same_or_refuse(row[0], data, f"broker {spec.broker_id} v{spec.version}")
            return
        if spec.version < int(row[1]):
            raise ReferenceError(f"broker {spec.broker_id}: v{spec.version} is older than stored")
    conn.execute(
        """INSERT INTO brokers (broker_id, platform, version, spec) VALUES (%s, %s, %s, %s)
           ON CONFLICT (broker_id) DO UPDATE SET platform = EXCLUDED.platform,
           version = EXCLUDED.version, spec = EXCLUDED.spec""",
        (spec.broker_id, spec.platform.value, spec.version, Jsonb(data)),
    )


def get_broker(conn: psycopg.Connection, broker_id: str) -> BrokerSpec:
    row = conn.execute("SELECT spec FROM brokers WHERE broker_id = %s", (broker_id,)).fetchone()
    if row is None:
        raise ReferenceError(f"unknown broker {broker_id}")
    return BrokerSpec.model_validate(row[0], strict=False)


# ---------------------------------------------------------------- accounts


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    portfolio_id: str
    entity_id: str
    ledger_kind: LedgerKind
    base_currency: str
    base_scale: int
    state_version: int


def register_account(conn: psycopg.Connection, spec: AccountSpec, at_ns: int) -> Account:
    """Create or re-version an account, its broker bindings and base-currency balance."""
    entity = get_entity(conn, spec.entity_id)
    if not entity.active:
        raise ReferenceError(f"entity {entity.entity_id} is inactive")
    base_scale = _check_currency(spec.base_currency)
    for binding in spec.bindings:
        broker = get_broker(conn, binding.broker_id)
        if not _jurisdiction_served(entity.jurisdiction, broker.jurisdictions):
            raise ReferenceError(
                f"broker {broker.broker_id} does not serve {entity.jurisdiction} "
                f"(serves {', '.join(broker.jurisdictions)})"
            )
    data = spec.model_dump(mode="json")
    row = conn.execute(
        "SELECT spec, spec_version, ledger_kind, entity_id FROM accounts WHERE account_id = %s FOR UPDATE",
        (spec.account_id,),
    ).fetchone()
    if row is not None:
        if (row[2], row[3]) != (spec.ledger_kind.value, spec.entity_id):
            raise ReferenceError("an account's ledger kind and legal entity never change")
        if int(row[1]) == spec.version:
            _same_or_refuse(row[0], data, f"account {spec.account_id} v{spec.version}")
            return get_account(conn, spec.account_id)
        if spec.version < int(row[1]):
            raise ReferenceError(f"account {spec.account_id}: v{spec.version} is older than stored")
        conn.execute(
            "UPDATE accounts SET spec = %s, spec_version = %s WHERE account_id = %s",
            (Jsonb(data), spec.version, spec.account_id),
        )
        # Unbinding a broker that already has fills is refused by the fills foreign key.
        conn.execute(
            "DELETE FROM broker_bindings WHERE account_id = %s AND NOT (broker_id = ANY(%s))",
            (spec.account_id, [b.broker_id for b in spec.bindings]),
        )
    else:
        conn.execute(
            """INSERT INTO accounts (account_id, portfolio_id, entity_id, ledger_kind, base_currency,
                   currency_scale, created_at_ns, spec_version, spec)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                spec.account_id,
                spec.portfolio_id,
                spec.entity_id,
                spec.ledger_kind.value,
                spec.base_currency,
                base_scale,
                at_ns,
                spec.version,
                Jsonb(data),
            ),
        )
        conn.execute(
            "INSERT INTO cash_balances (account_id, currency, currency_scale, balance_units) "
            "VALUES (%s, %s, %s, 0)",
            (spec.account_id, spec.base_currency, base_scale),
        )
    for b in spec.bindings:
        conn.execute(
            """INSERT INTO broker_bindings (account_id, rank, broker_id, broker_account_ref, credential_ref)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (account_id, broker_id) DO UPDATE SET
               rank = EXCLUDED.rank, broker_account_ref = EXCLUDED.broker_account_ref,
               credential_ref = EXCLUDED.credential_ref""",
            (spec.account_id, b.rank, b.broker_id, b.broker_account_ref, b.credential_ref),
        )
    return get_account(conn, spec.account_id)


def get_account(conn: psycopg.Connection, account_id: str, lock: bool = False) -> Account:
    row = conn.execute(
        "SELECT account_id, portfolio_id, entity_id, ledger_kind, base_currency, currency_scale, "
        "state_version FROM accounts WHERE account_id = %s" + (" FOR UPDATE" if lock else ""),
        (account_id,),
    ).fetchone()
    if row is None:
        raise ReferenceError(f"unknown account {account_id}")
    return Account(row[0], row[1], row[2], LedgerKind(row[3]), row[4], int(row[5]), int(row[6]))


def bound_brokers(conn: psycopg.Connection, account_id: str) -> list[str]:
    """Broker ids in routing order: primary, secondary, tertiary."""
    rows = conn.execute(
        "SELECT broker_id FROM broker_bindings WHERE account_id = %s ORDER BY rank", (account_id,)
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------- groups and copy trading


def _copy_edges(conn: psycopg.Connection) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for master, member in conn.execute(
        "SELECT g.copy_master_account_id, m.account_id FROM account_groups g "
        "JOIN account_group_members m USING (group_id) WHERE g.copy_master_account_id IS NOT NULL"
    ).fetchall():
        edges.setdefault(master, set()).add(member)
    return edges


def _has_cycle(edges: Mapping[str, set[str]]) -> bool:
    state: dict[str, int] = {}  # 1 visiting, 2 done

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(n) for n in edges.get(node, ())):
            return True
        state[node] = 2
        return False

    return any(visit(n) for n in list(edges))


def upsert_group(conn: psycopg.Connection, spec: AccountGroup) -> None:
    for acct in (
        *spec.account_ids,
        *([spec.copy_master_account_id] if spec.copy_master_account_id else []),
    ):
        get_account(conn, acct)
    data = spec.model_dump(mode="json")
    row = conn.execute(
        "SELECT spec, version FROM account_groups WHERE group_id = %s FOR UPDATE", (spec.group_id,)
    ).fetchone()
    if row is not None:
        if int(row[1]) == spec.version:
            _same_or_refuse(row[0], data, f"group {spec.group_id} v{spec.version}")
            return
        if spec.version < int(row[1]):
            raise ReferenceError(f"group {spec.group_id}: v{spec.version} is older than stored")
    conn.execute(
        """INSERT INTO account_groups (group_id, copy_master_account_id, version, spec)
           VALUES (%s, %s, %s, %s) ON CONFLICT (group_id) DO UPDATE SET
           copy_master_account_id = EXCLUDED.copy_master_account_id,
           version = EXCLUDED.version, spec = EXCLUDED.spec""",
        (spec.group_id, spec.copy_master_account_id, spec.version, Jsonb(data)),
    )
    conn.execute("DELETE FROM account_group_members WHERE group_id = %s", (spec.group_id,))
    for acct in spec.account_ids:
        conn.execute(
            "INSERT INTO account_group_members (group_id, account_id) VALUES (%s, %s)",
            (spec.group_id, acct),
        )
    if _has_cycle(_copy_edges(conn)):
        raise ReferenceError(f"group {spec.group_id} would create a copy-trading cycle")


# ---------------------------------------------------------------- FX rates


def record_fx_rate(conn: psycopg.Connection, snap: FxRateSnapshot) -> bool:
    _check_currency(snap.base_currency)
    _check_currency(snap.quote_currency)
    row = conn.execute(
        """INSERT INTO fx_rates (base_currency, quote_currency, source, as_of_ns, rate_units, rate_scale)
           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING as_of_ns""",
        (
            snap.base_currency,
            snap.quote_currency,
            snap.source,
            snap.as_of_ns,
            snap.rate_units,
            snap.rate_scale,
        ),
    ).fetchone()
    return row is not None


def latest_rates(
    conn: psycopg.Connection, as_of_ns: int, max_age_ns: int, sources: tuple[str, ...] | None = None
) -> list[FxRate]:
    """Newest rate per pair at or before ``as_of_ns`` and no older than ``max_age_ns``."""
    rows = conn.execute(
        """SELECT DISTINCT ON (base_currency, quote_currency)
                  base_currency, quote_currency, rate_units, rate_scale, as_of_ns, source
           FROM fx_rates WHERE as_of_ns <= %s AND as_of_ns > %s
             AND (%s::text[] IS NULL OR source = ANY(%s::text[]))
           ORDER BY base_currency, quote_currency, as_of_ns DESC, source""",
        (
            as_of_ns,
            as_of_ns - max_age_ns,
            list(sources) if sources else None,
            list(sources) if sources else None,
        ),
    ).fetchall()
    return [FxRate(r[0], r[1], int(r[2]), int(r[3]), int(r[4]), r[5]) for r in rows]
