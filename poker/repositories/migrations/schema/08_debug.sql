-- Prompt captures for AI debugging (IMPORTANT: preserve this data during migration)
CREATE TABLE IF NOT EXISTS prompt_captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Identity
    game_id TEXT,  -- Allow NULL for non-game captures
    hand_number INTEGER,
    player_name TEXT,
    -- Game state context
    phase TEXT,  -- PRE_FLOP, FLOP, TURN, RIVER
    pot_total INTEGER,
    cost_to_call INTEGER,
    pot_odds REAL,
    player_stack INTEGER,
    community_cards TEXT,  -- JSON array of cards
    player_hand TEXT,  -- JSON array of cards
    valid_actions TEXT,  -- JSON array of valid actions
    -- Prompt data
    system_prompt TEXT NOT NULL,
    user_message TEXT,  -- The user/game prompt sent to the model
    ai_response TEXT,  -- The AI's response text
    conversation_history TEXT,  -- JSON array of previous messages
    raw_api_response TEXT,  -- Full JSON API response
    -- Decision
    action_taken TEXT,
    raise_amount INTEGER,
    -- Model info
    provider TEXT,  -- openai, anthropic, etc.
    model TEXT,
    reasoning_effort TEXT,  -- For reasoning models
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER DEFAULT 0,
    -- Tracking
    call_type TEXT,  -- PLAYER_DECISION, COMMENTARY, etc.
    original_request_id TEXT,
    experiment_id INTEGER,
    source TEXT DEFAULT 'game',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Legacy columns (kept for backward compatibility)
    user_prompt TEXT,  -- Alias for user_message
    raw_response TEXT,  -- Alias for raw_api_response
    parsed_response TEXT,
    model_used TEXT,  -- Alias for model
    temperature REAL DEFAULT 0.7,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    request_id TEXT,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_timestamp ON prompt_captures(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_source ON prompt_captures(source);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_experiment ON prompt_captures(experiment_id);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type);
CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider);

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
