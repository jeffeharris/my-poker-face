-- LLM API usage tracking (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT,
    owner_id TEXT,
    player_name TEXT,
    hand_number INTEGER,
    call_type TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    input_cost REAL,
    output_cost REAL,
    total_cost REAL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_usage_game ON api_usage(game_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_owner ON api_usage(owner_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type);
CREATE INDEX IF NOT EXISTS idx_api_usage_model ON api_usage(model);

-- Model pricing configuration (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS model_pricing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    input_price_per_1m REAL NOT NULL DEFAULT 0.0,
    output_price_per_1m REAL NOT NULL DEFAULT 0.0,
    cached_input_price_per_1m REAL NOT NULL DEFAULT 0.0,
    reasoning_price_per_1m REAL NOT NULL DEFAULT 0.0,
    effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model, provider)
);

CREATE INDEX IF NOT EXISTS idx_model_pricing_model ON model_pricing(model, provider);

-- Enabled models configuration (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS enabled_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_default BOOLEAN DEFAULT 0,
    enabled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_enabled_models_provider ON enabled_models(provider);
CREATE INDEX IF NOT EXISTS idx_enabled_models_default ON enabled_models(is_default);
