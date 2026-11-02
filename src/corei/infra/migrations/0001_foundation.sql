-- COREI OS — foundation schema (Delivery B).
-- One ordered migration lineage replaces the two competing legacy schema sets (F35, F43).
-- Money and quantities are integers at explicit scales (NUMERIC(38,0)); no floating types.
-- Timestamps are integer nanoseconds since the Unix epoch, UTC.

-- ---------------------------------------------------------------- reference data
CREATE TABLE accounts (
    account_id      TEXT PRIMARY KEY,
    portfolio_id    TEXT NOT NULL UNIQUE,
    broker          TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('SIMULATION', 'PAPER', 'LIVE_SEMI', 'LIVE_AUTO')),
    base_currency   CHAR(3) NOT NULL CHECK (base_currency ~ '^[A-Z]{3}$'),
    currency_scale  SMALLINT NOT NULL CHECK (currency_scale BETWEEN 0 AND 18),
    state_version   BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at_ns   BIGINT NOT NULL CHECK (created_at_ns > 0)
);

CREATE TABLE instruments (
    instrument_id   TEXT NOT NULL,
    version         INTEGER NOT NULL CHECK (version >= 0),
    tradable        BOOLEAN NOT NULL,
    spec            JSONB NOT NULL,
    valid_from_ns   BIGINT NOT NULL,
    valid_to_ns     BIGINT,
    PRIMARY KEY (instrument_id, version)
);

-- ---------------------------------------------------------------- event processing
-- Inbox: one row per (consumer, event). Insert-or-skip makes redelivery a no-op (F12).
CREATE TABLE inbox (
    consumer        TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('APPLIED', 'REJECTED')),
    processed_at_ns BIGINT NOT NULL,
    PRIMARY KEY (consumer, event_id)
);

-- Outbox: events written in the same transaction as the state they describe.
CREATE TABLE outbox (
    seq             BIGSERIAL PRIMARY KEY,
    event_id        TEXT NOT NULL UNIQUE,
    event_type      TEXT NOT NULL,
    topic           TEXT NOT NULL,
    partition_key   TEXT NOT NULL,
    envelope        JSONB NOT NULL,
    created_at_ns   BIGINT NOT NULL,
    published_at_ns BIGINT
);
CREATE INDEX outbox_unpublished ON outbox (seq) WHERE published_at_ns IS NULL;

-- Events refused by a consumer (wrong mode, unparseable, permanent domain error). Kept, not dropped.
CREATE TABLE rejected_events (
    id              BIGSERIAL PRIMARY KEY,
    consumer        TEXT NOT NULL,
    event_id        TEXT,
    event_type      TEXT,
    reason_code     TEXT NOT NULL,
    detail          TEXT NOT NULL,
    raw             TEXT NOT NULL,
    rejected_at_ns  BIGINT NOT NULL
);

-- ---------------------------------------------------------------- control (modes and halts)
CREATE TABLE control_state (
    scope           TEXT PRIMARY KEY,
    version         BIGINT NOT NULL CHECK (version >= 1),
    mode            TEXT NOT NULL CHECK (mode IN ('OFF', 'SIMULATION', 'PAPER', 'LIVE_SEMI', 'LIVE_AUTO')),
    halted          BOOLEAN NOT NULL,
    halt_reason     TEXT,
    updated_at_ns   BIGINT NOT NULL,
    updated_by      TEXT NOT NULL,
    CHECK (NOT halted OR halt_reason IS NOT NULL)
);

CREATE TABLE control_commands (
    command_id        TEXT PRIMARY KEY,
    scope             TEXT NOT NULL,
    type              TEXT NOT NULL,
    expected_version  BIGINT NOT NULL,
    resulting_version BIGINT NOT NULL,
    actor             TEXT NOT NULL,
    authorization_ref TEXT NOT NULL,
    mode              TEXT,
    reason            TEXT,
    at_ns             BIGINT NOT NULL
);

-- ---------------------------------------------------------------- ledger
-- Fills: deduplicated by the broker's execution identity within account scope (F21).
CREATE TABLE fills (
    fill_id             TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES accounts (account_id),
    broker              TEXT NOT NULL,
    broker_execution_id TEXT NOT NULL,
    order_id            TEXT NOT NULL,
    instrument_id       TEXT NOT NULL,
    side                TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity_units      NUMERIC(38, 0) NOT NULL CHECK (quantity_units > 0),
    quantity_scale      SMALLINT NOT NULL,
    price_units         NUMERIC(38, 0) NOT NULL CHECK (price_units > 0),
    price_scale         SMALLINT NOT NULL,
    executed_at_ns      BIGINT NOT NULL,
    content_hash        TEXT NOT NULL,
    UNIQUE (account_id, broker, broker_execution_id)
);

-- Append-only journal: every cash or position change has exactly one entry.
CREATE TABLE ledger_entries (
    seq                  BIGSERIAL PRIMARY KEY,
    entry_id             TEXT NOT NULL UNIQUE,
    account_id           TEXT NOT NULL REFERENCES accounts (account_id),
    kind                 TEXT NOT NULL CHECK (kind IN ('DEPOSIT', 'WITHDRAWAL', 'FILL', 'FEE')),
    currency             CHAR(3) NOT NULL,
    currency_scale       SMALLINT NOT NULL,
    cash_delta_units     NUMERIC(38, 0) NOT NULL,
    instrument_id        TEXT,
    quantity_delta_units NUMERIC(38, 0),
    fill_id              TEXT REFERENCES fills (fill_id),
    at_ns                BIGINT NOT NULL
);
CREATE INDEX ledger_entries_account ON ledger_entries (account_id, seq);

CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;
CREATE TRIGGER ledger_entries_append_only BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER fills_append_only BEFORE UPDATE OR DELETE ON fills
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER control_commands_append_only BEFORE UPDATE OR DELETE ON control_commands
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

-- Projections maintained in the same transaction as the journal.
CREATE TABLE cash_balances (
    account_id      TEXT NOT NULL REFERENCES accounts (account_id),
    currency        CHAR(3) NOT NULL,
    currency_scale  SMALLINT NOT NULL,
    balance_units   NUMERIC(38, 0) NOT NULL,
    PRIMARY KEY (account_id, currency)
);

CREATE TABLE positions (
    account_id          TEXT NOT NULL REFERENCES accounts (account_id),
    instrument_id       TEXT NOT NULL,
    quantity_units      NUMERIC(38, 0) NOT NULL,        -- signed: long > 0, short < 0
    quantity_scale      SMALLINT NOT NULL,
    open_cost_units     NUMERIC(38, 0) NOT NULL,        -- signed, currency scale; same sign as quantity
    realized_pnl_units  NUMERIC(38, 0) NOT NULL,        -- currency scale, before fees
    currency            CHAR(3) NOT NULL,
    currency_scale      SMALLINT NOT NULL,
    updated_at_ns       BIGINT NOT NULL,
    PRIMARY KEY (account_id, instrument_id),
    CHECK ((quantity_units = 0) = (open_cost_units = 0))
);

-- Capital reservations (F04): taken atomically before an order command exists.
CREATE TABLE reservations (
    reservation_id  TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts (account_id),
    command_id      TEXT NOT NULL UNIQUE,
    currency        CHAR(3) NOT NULL,
    currency_scale  SMALLINT NOT NULL,
    amount_units    NUMERIC(38, 0) NOT NULL CHECK (amount_units > 0),
    status          TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CONSUMED', 'RELEASED', 'EXPIRED')),
    created_at_ns   BIGINT NOT NULL,
    expires_at_ns   BIGINT NOT NULL CHECK (expires_at_ns > created_at_ns),
    closed_at_ns    BIGINT
);
CREATE INDEX reservations_active ON reservations (account_id, currency) WHERE status = 'ACTIVE';
