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
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Extended tracking fields
    estimated_cost REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reasoning_effort TEXT,
    request_id TEXT,
    pricing_ids TEXT,
    prompt_template TEXT,
    prompt_version TEXT,
    max_tokens INTEGER,
    image_count INTEGER DEFAULT 0,
    image_size TEXT,
    status TEXT,
    finish_reason TEXT,
    error_code TEXT,
    message_count INTEGER,
    system_prompt_tokens INTEGER
);

CREATE INDEX IF NOT EXISTS idx_api_usage_game ON api_usage(game_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_owner ON api_usage(owner_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type);
CREATE INDEX IF NOT EXISTS idx_api_usage_model ON api_usage(model);

-- Model pricing configuration (IMPORTANT: preserve this data during migration)
-- Uses row-per-unit approach: each SKU (provider/model/unit combination) has its own row
CREATE TABLE IF NOT EXISTS model_pricing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    unit TEXT NOT NULL,
    cost REAL,
    -- Legacy columns (kept for backwards compat, but cost/unit is the new approach)
    input_price_per_1m REAL DEFAULT 0.0,
    output_price_per_1m REAL DEFAULT 0.0,
    cached_input_price_per_1m REAL DEFAULT 0.0,
    reasoning_price_per_1m REAL DEFAULT 0.0,
    effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    version TEXT,
    valid_from TEXT,
    valid_until TEXT,
    pricing_tier TEXT,
    notes TEXT,
    UNIQUE(model, provider, unit, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_model_pricing_model ON model_pricing(model, provider);

-- Enabled models configuration (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS enabled_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,  -- Match old schema column name
    provider TEXT NOT NULL,
    display_name TEXT,  -- Allow NULL for legacy data
    enabled INTEGER DEFAULT 1,  -- Match old schema
    notes TEXT,
    supports_reasoning INTEGER DEFAULT 0,
    supports_json_mode INTEGER DEFAULT 1,
    supports_image_gen INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(model, provider)
);

CREATE INDEX IF NOT EXISTS idx_enabled_models_provider ON enabled_models(provider);
CREATE INDEX IF NOT EXISTS idx_enabled_models_model ON enabled_models(model);
