-- COREI OS: tenant isolation (B.3 step 1; platform invariant P2).
--
-- Every tenant-owned row gets tenant_id. It defaults to the connection's
-- app.tenant_id setting (set by corei.infra.db.connection_factory), leads every
-- primary, unique and foreign key, and is enforced by row-level security that
-- applies to the table owner too (FORCE). A connection without app.tenant_id
-- sees no rows and can write none: fail closed.
--
-- Existing rows are assigned to the tenant of the connection that runs this
-- migration (COREI_TENANT, default "corei"). Runs in one transaction.

DO $$
DECLARE t text := current_setting('app.tenant_id', true);
BEGIN
    IF t IS NULL OR t !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$' THEN
        RAISE EXCEPTION 'TENANT_UNSET: connect with app.tenant_id (COREI_TENANT) before migrating';
    END IF;
END $$;

CREATE TABLE tenants (
    tenant_id     TEXT PRIMARY KEY CHECK (tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'),
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED')),
    created_at_ns BIGINT NOT NULL
);
INSERT INTO tenants (tenant_id, name, created_at_ns)
VALUES (current_setting('app.tenant_id'), current_setting('app.tenant_id'),
        (extract(epoch FROM clock_timestamp()) * 1e9)::bigint);

-- ---------------------------------------------------------------- drop single-tenant keys
ALTER TABLE account_group_members DROP CONSTRAINT account_group_members_account_id_fkey;
ALTER TABLE account_group_members DROP CONSTRAINT account_group_members_group_id_fkey;
ALTER TABLE account_groups DROP CONSTRAINT account_groups_copy_master_account_id_fkey;
ALTER TABLE accounts DROP CONSTRAINT accounts_entity_id_fkey;
ALTER TABLE broker_bindings DROP CONSTRAINT broker_bindings_account_id_fkey;
ALTER TABLE broker_bindings DROP CONSTRAINT broker_bindings_broker_id_fkey;
ALTER TABLE cash_balances DROP CONSTRAINT cash_balances_account_id_fkey;
ALTER TABLE fills DROP CONSTRAINT fills_account_id_fkey;
ALTER TABLE fills DROP CONSTRAINT fills_broker_is_bound;
ALTER TABLE ledger_entries DROP CONSTRAINT ledger_entries_account_id_fkey;
ALTER TABLE ledger_entries DROP CONSTRAINT ledger_entries_fill_id_fkey;
ALTER TABLE positions DROP CONSTRAINT positions_account_id_fkey;
ALTER TABLE reservations DROP CONSTRAINT reservations_account_id_fkey;

-- ---------------------------------------------------------------- tenant_id on every tenant-owned table
-- A non-volatile default is evaluated once for existing rows (no table rewrite,
-- no row triggers), so append-only tables keep their history untouched.
ALTER TABLE accounts              ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE instruments           ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE inbox                 ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE outbox                ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE rejected_events       ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE control_state         ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE control_commands      ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE fills                 ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE ledger_entries        ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE cash_balances         ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE positions             ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE reservations          ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE legal_entities        ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE brokers               ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE broker_bindings       ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE account_groups        ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE account_group_members ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);
ALTER TABLE fx_rates              ADD COLUMN tenant_id TEXT NOT NULL DEFAULT current_setting('app.tenant_id') REFERENCES tenants (tenant_id);

-- ---------------------------------------------------------------- tenant-scoped primary and unique keys
ALTER TABLE accounts DROP CONSTRAINT accounts_pkey, ADD PRIMARY KEY (tenant_id, account_id);
ALTER TABLE accounts DROP CONSTRAINT accounts_portfolio_id_key, ADD UNIQUE (tenant_id, portfolio_id);
ALTER TABLE instruments DROP CONSTRAINT instruments_pkey, ADD PRIMARY KEY (tenant_id, instrument_id, version);
ALTER TABLE inbox DROP CONSTRAINT inbox_pkey, ADD PRIMARY KEY (tenant_id, consumer, event_id);
ALTER TABLE outbox DROP CONSTRAINT outbox_event_id_key, ADD UNIQUE (tenant_id, event_id);
ALTER TABLE control_state DROP CONSTRAINT control_state_pkey, ADD PRIMARY KEY (tenant_id, scope);
ALTER TABLE control_commands DROP CONSTRAINT control_commands_pkey, ADD PRIMARY KEY (tenant_id, command_id);
ALTER TABLE fills DROP CONSTRAINT fills_pkey, ADD PRIMARY KEY (tenant_id, fill_id);
ALTER TABLE fills DROP CONSTRAINT fills_account_id_broker_broker_execution_id_key,
    ADD UNIQUE (tenant_id, account_id, broker, broker_execution_id);
ALTER TABLE ledger_entries DROP CONSTRAINT ledger_entries_entry_id_key, ADD UNIQUE (tenant_id, entry_id);
ALTER TABLE cash_balances DROP CONSTRAINT cash_balances_pkey, ADD PRIMARY KEY (tenant_id, account_id, currency);
ALTER TABLE positions DROP CONSTRAINT positions_pkey, ADD PRIMARY KEY (tenant_id, account_id, instrument_id);
ALTER TABLE reservations DROP CONSTRAINT reservations_pkey, ADD PRIMARY KEY (tenant_id, reservation_id);
ALTER TABLE reservations DROP CONSTRAINT reservations_command_id_key, ADD UNIQUE (tenant_id, command_id);
ALTER TABLE legal_entities DROP CONSTRAINT legal_entities_pkey, ADD PRIMARY KEY (tenant_id, entity_id);
ALTER TABLE brokers DROP CONSTRAINT brokers_pkey, ADD PRIMARY KEY (tenant_id, broker_id);
ALTER TABLE broker_bindings DROP CONSTRAINT broker_bindings_pkey, ADD PRIMARY KEY (tenant_id, account_id, broker_id);
ALTER TABLE broker_bindings DROP CONSTRAINT broker_bindings_rank_unique,
    ADD CONSTRAINT broker_bindings_rank_unique UNIQUE (tenant_id, account_id, rank) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE account_groups DROP CONSTRAINT account_groups_pkey, ADD PRIMARY KEY (tenant_id, group_id);
ALTER TABLE account_group_members DROP CONSTRAINT account_group_members_pkey, ADD PRIMARY KEY (tenant_id, group_id, account_id);
ALTER TABLE fx_rates DROP CONSTRAINT fx_rates_pkey, ADD PRIMARY KEY (tenant_id, base_currency, quote_currency, source, as_of_ns);
DROP INDEX fx_rates_latest;
CREATE INDEX fx_rates_latest ON fx_rates (tenant_id, base_currency, quote_currency, as_of_ns DESC);
DROP INDEX reservations_active;
CREATE INDEX reservations_active ON reservations (tenant_id, account_id, currency) WHERE status = 'ACTIVE';
DROP INDEX ledger_entries_account;
CREATE INDEX ledger_entries_account ON ledger_entries (tenant_id, account_id, seq);

-- ---------------------------------------------------------------- tenant-scoped foreign keys
-- A row can only reference rows of its own tenant.
ALTER TABLE accounts ADD FOREIGN KEY (tenant_id, entity_id) REFERENCES legal_entities (tenant_id, entity_id);
ALTER TABLE broker_bindings ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE broker_bindings ADD FOREIGN KEY (tenant_id, broker_id) REFERENCES brokers (tenant_id, broker_id);
ALTER TABLE cash_balances ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE fills ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE fills ADD CONSTRAINT fills_broker_is_bound
    FOREIGN KEY (tenant_id, account_id, broker) REFERENCES broker_bindings (tenant_id, account_id, broker_id);
ALTER TABLE ledger_entries ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE ledger_entries ADD FOREIGN KEY (tenant_id, fill_id) REFERENCES fills (tenant_id, fill_id);
ALTER TABLE positions ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE reservations ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE account_groups ADD FOREIGN KEY (tenant_id, copy_master_account_id) REFERENCES accounts (tenant_id, account_id);
ALTER TABLE account_group_members ADD FOREIGN KEY (tenant_id, group_id)
    REFERENCES account_groups (tenant_id, group_id) ON DELETE CASCADE;
ALTER TABLE account_group_members ADD FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, account_id);

-- ---------------------------------------------------------------- row-level security (FORCE: applies to the owner)
DO $$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'tenants', 'accounts', 'instruments', 'inbox', 'outbox', 'rejected_events', 'control_state',
        'control_commands', 'fills', 'ledger_entries', 'cash_balances', 'positions', 'reservations',
        'legal_entities', 'brokers', 'broker_bindings', 'account_groups', 'account_group_members', 'fx_rates'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I '
            'USING (tenant_id = current_setting(''app.tenant_id'', true)) '
            'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', tbl);
    END LOOP;
END $$;
