-- Prompt captures for AI debugging (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS prompt_captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT,  -- Allow NULL for legacy data
    hand_number INTEGER,  -- Allow NULL for legacy data
    player_name TEXT,  -- Allow NULL for legacy data
    action_taken TEXT,
    system_prompt TEXT NOT NULL,
    user_prompt TEXT,  -- Allow NULL for legacy data (was user_message)
    raw_response TEXT,
    parsed_response TEXT,
    model_used TEXT,  -- Allow NULL for legacy data (was model)
    temperature REAL DEFAULT 0.7,
    latency_ms INTEGER DEFAULT 0,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT DEFAULT 'game',
    experiment_id INTEGER,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_timestamp ON prompt_captures(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_source ON prompt_captures(source);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_experiment ON prompt_captures(experiment_id);

-- Player decision quality analysis (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS player_decision_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_capture_id INTEGER,  -- Allow NULL for legacy data (was capture_id)
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    request_id TEXT,  -- Allow NULL for legacy data
    hand_number INTEGER NOT NULL,
    ev_analysis TEXT,
    gto_deviation TEXT,
    personality_alignment TEXT,
    decision_quality_score REAL,
    analysis_metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (prompt_capture_id) REFERENCES prompt_captures(id),
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_analysis_capture ON player_decision_analysis(prompt_capture_id);
CREATE INDEX IF NOT EXISTS idx_decision_analysis_game ON player_decision_analysis(game_id);
CREATE INDEX IF NOT EXISTS idx_decision_analysis_player ON player_decision_analysis(player_name);
CREATE INDEX IF NOT EXISTS idx_decision_analysis_request ON player_decision_analysis(request_id);
