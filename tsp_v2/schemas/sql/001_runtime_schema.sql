PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS persistence_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_fingerprint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fingerprint TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS governor_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    state_reason TEXT NOT NULL,
    pace_classification TEXT NOT NULL,
    aggression_multiplier REAL NOT NULL,
    profile_constraints_json TEXT NOT NULL,
    escalation_flags_json TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    equity REAL NOT NULL,
    balance REAL NOT NULL,
    drawdown_pct REAL NOT NULL,
    daily_loss_pct REAL NOT NULL,
    unrealized_r REAL NOT NULL,
    updated_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_registry (
    setup_id TEXT PRIMARY KEY,
    submission_uuid TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    direction TEXT,
    decision_price REAL,
    cycle_time_utc TEXT,
    expires_at_utc TEXT,
    broker_ticket INTEGER
);

CREATE TABLE IF NOT EXISTS execution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_uuid TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    setup_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    correlation_group TEXT NOT NULL,
    risk_pct REAL NOT NULL,
    signal_score REAL NOT NULL,
    open_time_utc TEXT,
    pyramid_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lifecycle_state (
    position_ticket INTEGER PRIMARY KEY,
    setup_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    opened_at_utc TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    initial_stop REAL NOT NULL,
    initial_r_distance REAL NOT NULL,
    current_stop REAL NOT NULL,
    partial_taken INTEGER NOT NULL,
    trail_active INTEGER NOT NULL,
    pyramid_count INTEGER NOT NULL,
    thesis_expiry_utc TEXT NOT NULL,
    orphan_recovered INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS health_state (
    component TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recovery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time_utc TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time_utc TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
