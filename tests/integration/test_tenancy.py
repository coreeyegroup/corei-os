"""Tenant isolation (B.3 step 1, invariant P2; AT-12, tenant parts of AT-17/18/19, AT-24).

Superusers bypass row-level security, so isolation is proven with a separate,
non-superuser role, the way the installed service connects.
"""

import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from corei import cli
from corei.infra.db import MigrationError, migrate, schema_status, tenant_options

from .conftest import T0, TENANT, _admin_url

pytestmark = pytest.mark.integration
Connect = Callable[[], psycopg.Connection]


def _fresh_db(admin: str) -> str:
    name = f"corei_tn_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return psycopg.conninfo.make_conninfo(admin, dbname=name)


def _drop_db(admin: str, url: str) -> None:
    name = psycopg.conninfo.conninfo_to_dict(url)["dbname"]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(str(name)))
        )


@pytest.fixture
def scratch_db() -> Iterator[str]:
    admin = _admin_url()
    if not admin:
        pytest.skip("integration database not configured")
    url = _fresh_db(admin)
    yield url
    _drop_db(admin, url)


def _entity_sql(entity_id: str) -> str:
    return (
        "INSERT INTO legal_entities (entity_id, jurisdiction, base_currency, version, spec) "
        f"VALUES ('{entity_id}', 'US', 'USD', 1, '{{}}')"
    )


def test_row_level_security_isolates_tenants_for_the_service_role(scratch_db: str) -> None:
    with psycopg.connect(scratch_db, options=tenant_options(TENANT)) as conn:
        migrate(conn)
        conn.execute(_entity_sql("ent_mine"))
        conn.execute(
            "INSERT INTO tenants (tenant_id, name, created_at_ns) VALUES ('other', 'Other', 1)"
        )
        conn.commit()
    with psycopg.connect(scratch_db, options=tenant_options("other")) as conn:
        conn.execute(_entity_sql("ent_theirs"))
        conn.commit()
    role = f"corei_app_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'it-only'").format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role))
        )
        conn.execute(
            sql.SQL("GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                sql.Identifier(role)
            )
        )
    as_role = psycopg.conninfo.make_conninfo(scratch_db, user=role, password="it-only")
    try:
        with psycopg.connect(as_role, options=tenant_options(TENANT)) as conn:
            seen = [r[0] for r in conn.execute("SELECT entity_id FROM legal_entities").fetchall()]
            assert seen == ["ent_mine"]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO legal_entities (tenant_id, entity_id, jurisdiction, base_currency, version, spec) "
                    "VALUES ('other', 'ent_sneaky', 'US', 'USD', 1, '{}')"
                )
            conn.rollback()
            updated = conn.execute(
                "UPDATE legal_entities SET version = 9 WHERE entity_id = 'ent_theirs'"
            ).rowcount
            assert updated == 0
        with psycopg.connect(
            scratch_db, autocommit=True
        ) as conn:  # as installed: the role owns the table
            conn.execute(
                sql.SQL("ALTER TABLE legal_entities OWNER TO {}").format(sql.Identifier(role))
            )
        with psycopg.connect(as_role, options=tenant_options(TENANT)) as conn:
            seen = [r[0] for r in conn.execute("SELECT entity_id FROM legal_entities").fetchall()]
            assert seen == ["ent_mine"]  # FORCE ROW LEVEL SECURITY binds the owner too
        with psycopg.connect(as_role) as conn:  # no tenant on the connection: fail closed
            assert conn.execute("SELECT count(*) FROM legal_entities").fetchone() == (0,)
            with pytest.raises(psycopg.Error):
                conn.execute(_entity_sql("ent_nobody"))
    finally:
        with psycopg.connect(scratch_db, autocommit=True) as conn:
            conn.execute(
                sql.SQL("REASSIGN OWNED BY {} TO CURRENT_USER").format(sql.Identifier(role))
            )
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_rows_cannot_reference_another_tenants_rows(scratch_db: str) -> None:
    with psycopg.connect(scratch_db, options=tenant_options(TENANT)) as conn:
        migrate(conn)
        conn.execute(_entity_sql("ent_mine"))
        conn.execute(
            "INSERT INTO tenants (tenant_id, name, created_at_ns) VALUES ('other', 'Other', 1)"
        )
        conn.commit()
    with (
        psycopg.connect(scratch_db, options=tenant_options("other")) as conn,
        pytest.raises(psycopg.errors.ForeignKeyViolation),
    ):
        conn.execute(
            "INSERT INTO accounts (account_id, portfolio_id, entity_id, ledger_kind, base_currency, "
            "currency_scale, created_at_ns) VALUES ('acc_x', 'pf_x', 'ent_mine', 'PAPER', 'USD', 2, 1)"
        )


D54_DATA = [
    _entity_sql("ent_us"),
    "INSERT INTO brokers (broker_id, platform, version, spec) VALUES ('brk_paper', 'PAPER', 1, '{}')",
    "INSERT INTO accounts (account_id, portfolio_id, entity_id, ledger_kind, base_currency, currency_scale, "
    "created_at_ns) VALUES ('acc_a', 'pf_a', 'ent_us', 'PAPER', 'USD', 2, 1)",
    "INSERT INTO broker_bindings (account_id, rank, broker_id, broker_account_ref) VALUES ('acc_a', 1, 'brk_paper', 'P-1')",
    "INSERT INTO ledger_entries (entry_id, account_id, kind, currency, currency_scale, cash_delta_units, at_ns) "
    f"VALUES ('seed:acc_a', 'acc_a', 'DEPOSIT', 'USD', 2, 10000000, {T0})",
    "INSERT INTO cash_balances (account_id, currency, currency_scale, balance_units) VALUES ('acc_a', 'USD', 2, 10000000)",
    f"INSERT INTO control_state (scope, version, mode, halted, updated_at_ns, updated_by) VALUES ('GLOBAL', 1, 'PAPER', false, {T0}, 'bootstrap')",
]


def test_upgrade_from_d54bd81_refuses_without_a_tenant_then_assigns_one(scratch_db: str) -> None:
    with psycopg.connect(scratch_db) as conn:  # the d54bd81 schema and data, no tenant concept
        migrate(conn, upto=2)
        for stmt in D54_DATA:
            conn.execute(stmt)
        conn.commit()
    with psycopg.connect(scratch_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="TENANT_UNSET"):
            migrate(conn)
        conn.rollback()
        assert schema_status(conn)[0] == 2  # nothing applied: the whole migration rolled back
        assert conn.execute("SELECT count(*) FROM accounts").fetchone() == (1,)
    with psycopg.connect(scratch_db, options=tenant_options("acme")) as conn:
        assert migrate(conn) == [3]
        conn.commit()
        rows = conn.execute(
            "SELECT (SELECT array_agg(DISTINCT tenant_id) FROM accounts), "
            "(SELECT array_agg(DISTINCT tenant_id) FROM ledger_entries), "
            "(SELECT array_agg(DISTINCT tenant_id) FROM control_state), "
            "(SELECT balance_units FROM cash_balances WHERE account_id = 'acc_a')"
        ).fetchone()
        assert rows == (["acme"], ["acme"], ["acme"], 10000000)
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute(
                "UPDATE ledger_entries SET cash_delta_units = 0"
            )  # history still protected


def test_a_build_without_the_newest_migration_refuses_the_upgraded_database(
    scratch_db: str,
) -> None:
    with psycopg.connect(scratch_db, options=tenant_options(TENANT)) as conn:
        migrate(conn)
        conn.commit()
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES (999, 'future', 'x')"
        )
        with pytest.raises(MigrationError, match="doesn't know"):
            migrate(conn)


@pytest.mark.skipif(shutil.which("pg_restore") is None, reason="pg_restore not installed")
def test_backup_restores_every_row_of_the_tenant(
    connect: Connect,
    paper_accounts: dict[str, int],
    database_url: str,
    tmp_path: Path,
    scratch_db: str,
) -> None:
    env = {
        "COREI_ENV": "it",
        "DATABASE_URL": database_url,
        "COREI_TENANT": TENANT,
        "COREI_ENV_FILE": str(tmp_path / "none.env"),
    }
    args = cli.build_parser().parse_args(["backup", "--dir", str(tmp_path), "--keep", "1"])
    assert cli.cmd_backup(args, env) == 0
    dump = next(tmp_path.glob("corei-manual-*.dump"))
    subprocess.run(
        ["pg_restore", "--no-owner", "--exit-on-error", "-d", scratch_db, str(dump)],
        check=True,
        env={"PGOPTIONS": tenant_options(TENANT), "PATH": "/usr/bin:/bin"},
    )
    counts = (
        "SELECT (SELECT count(*) FROM accounts), (SELECT count(*) FROM ledger_entries), "
        "(SELECT sum(balance_units) FROM cash_balances), (SELECT count(*) FROM legal_entities)"
    )
    with connect() as conn:
        original = conn.execute(counts).fetchone()
    with psycopg.connect(scratch_db, options=tenant_options(TENANT)) as conn:
        assert conn.execute(counts).fetchone() == original
        assert schema_status(conn)[0] == schema_status(conn)[1]
