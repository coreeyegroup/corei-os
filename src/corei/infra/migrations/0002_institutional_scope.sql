-- COREI OS — institutional scope (trading blueprint Layers 3-4, Anchor Law institutional standard).
-- Multi-entity / multi-jurisdiction, multi-broker with ranked bindings, account groups,
-- multi-currency cash, recorded FX rates, and blueprint operation modes.

-- ---------------------------------------------------------------- operation modes
ALTER TABLE control_state DROP CONSTRAINT control_state_mode_check;
UPDATE control_state SET mode = 'DISABLED' WHERE mode = 'OFF';
UPDATE control_state SET mode = 'BACKTEST' WHERE mode = 'SIMULATION';
ALTER TABLE control_state ADD CONSTRAINT control_state_mode_check
    CHECK (mode IN ('DISABLED', 'BACKTEST', 'PAPER', 'LIVE_SEMI', 'LIVE_AUTO'));
-- A scope that only carries a halt (e.g. ENTITY, BROKER) has no mode of its own.
ALTER TABLE control_state ALTER COLUMN mode DROP NOT NULL;

-- ---------------------------------------------------------------- entities and brokers
CREATE TABLE legal_entities (
    entity_id       TEXT PRIMARY KEY,
    jurisdiction    TEXT NOT NULL CHECK (jurisdiction ~ '^[A-Z]{2}(-[A-Z0-9]{1,8})?$'),
    base_currency   TEXT NOT NULL,
    version         INTEGER NOT NULL,
    spec            JSONB NOT NULL
);

CREATE TABLE brokers (
    broker_id       TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,
    version         INTEGER NOT NULL,
    spec            JSONB NOT NULL
);

-- ---------------------------------------------------------------- accounts
-- An account's money is BACKTEST, PAPER or LIVE for its whole life; its *operating*
-- mode (DISABLED..LIVE_AUTO) lives in control_state and can change.
ALTER TABLE accounts DROP CONSTRAINT accounts_mode_check;
ALTER TABLE accounts RENAME COLUMN mode TO ledger_kind;
UPDATE accounts SET ledger_kind = CASE ledger_kind
    WHEN 'SIMULATION' THEN 'BACKTEST' WHEN 'PAPER' THEN 'PAPER' ELSE 'LIVE' END;
ALTER TABLE accounts ADD CONSTRAINT accounts_ledger_kind_check
    CHECK (ledger_kind IN ('BACKTEST', 'PAPER', 'LIVE'));
ALTER TABLE accounts DROP CONSTRAINT accounts_base_currency_check;
ALTER TABLE accounts ALTER COLUMN base_currency TYPE TEXT;
ALTER TABLE accounts ADD CONSTRAINT accounts_base_currency_check
    CHECK (base_currency ~ '^[A-Z][A-Z0-9]{2,5}$');
ALTER TABLE accounts DROP COLUMN broker;  -- replaced by ranked broker_bindings
ALTER TABLE accounts ADD COLUMN entity_id TEXT REFERENCES legal_entities (entity_id);
ALTER TABLE accounts ADD COLUMN spec_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE accounts ADD COLUMN spec JSONB;

-- Primary / secondary / tertiary broker per account; routing is limited to these.
CREATE TABLE broker_bindings (
    account_id          TEXT NOT NULL REFERENCES accounts (account_id),
    rank                SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 3),
    broker_id           TEXT NOT NULL REFERENCES brokers (broker_id),
    broker_account_ref  TEXT NOT NULL,
    credential_ref      TEXT,
    PRIMARY KEY (account_id, broker_id),
    -- deferred so a re-versioned account can swap primary/secondary within one transaction
    CONSTRAINT broker_bindings_rank_unique UNIQUE (account_id, rank) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE account_groups (
    group_id                TEXT PRIMARY KEY,
    copy_master_account_id  TEXT REFERENCES accounts (account_id),
    version                 INTEGER NOT NULL,
    spec                    JSONB NOT NULL
);
CREATE TABLE account_group_members (
    group_id    TEXT NOT NULL REFERENCES account_groups (group_id) ON DELETE CASCADE,
    account_id  TEXT NOT NULL REFERENCES accounts (account_id),
    PRIMARY KEY (group_id, account_id)
);

-- ---------------------------------------------------------------- multi-currency
ALTER TABLE cash_balances ALTER COLUMN currency TYPE TEXT;
ALTER TABLE ledger_entries ALTER COLUMN currency TYPE TEXT;
ALTER TABLE positions ALTER COLUMN currency TYPE TEXT;
ALTER TABLE reservations ALTER COLUMN currency TYPE TEXT;

-- A fill can only come from a broker the account is bound to (blueprint: an account
-- cannot route to an unassigned broker). Enforced by the database, not just code.
ALTER TABLE fills ADD CONSTRAINT fills_broker_is_bound
    FOREIGN KEY (account_id, broker) REFERENCES broker_bindings (account_id, broker_id);

-- Recorded FX rates used for valuation; never edited, only appended.
CREATE TABLE fx_rates (
    base_currency   TEXT NOT NULL,
    quote_currency  TEXT NOT NULL,
    source          TEXT NOT NULL,
    as_of_ns        BIGINT NOT NULL,
    rate_units      NUMERIC(38, 0) NOT NULL CHECK (rate_units > 0),
    rate_scale      SMALLINT NOT NULL CHECK (rate_scale BETWEEN 0 AND 18),
    PRIMARY KEY (base_currency, quote_currency, source, as_of_ns),
    CHECK (base_currency <> quote_currency)
);
CREATE INDEX fx_rates_latest ON fx_rates (base_currency, quote_currency, as_of_ns DESC);
CREATE TRIGGER fx_rates_append_only BEFORE UPDATE OR DELETE ON fx_rates
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
