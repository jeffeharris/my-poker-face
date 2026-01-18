-- AI player state (conversation history and personality)
CREATE TABLE IF NOT EXISTS ai_player_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    conversation_history TEXT,
    personality_state TEXT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    UNIQUE(game_id, player_name)
);

CREATE INDEX IF NOT EXISTS idx_ai_player_game ON ai_player_state(game_id, player_name);

-- Personality evolution snapshots
CREATE TABLE IF NOT EXISTS personality_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    game_id TEXT NOT NULL,
    hand_number INTEGER,
    personality_traits TEXT,
    pressure_levels TEXT,
    snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_personality_snapshots_game ON personality_snapshots(game_id);
CREATE INDEX IF NOT EXISTS idx_personality_snapshots_player ON personality_snapshots(player_name);

-- Opponent modeling data
CREATE TABLE IF NOT EXISTS opponent_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    observer_name TEXT NOT NULL,
    opponent_name TEXT NOT NULL,
    observations_json TEXT NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    UNIQUE(game_id, observer_name, opponent_name)
);

CREATE INDEX IF NOT EXISTS idx_opponent_models_game ON opponent_models(game_id);
CREATE INDEX IF NOT EXISTS idx_opponent_models_observer ON opponent_models(observer_name);

-- Memorable hands storage
CREATE TABLE IF NOT EXISTS memorable_hands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    hand_number INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    memorability_score REAL NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_memorable_hands_game ON memorable_hands(game_id);
CREATE INDEX IF NOT EXISTS idx_memorable_hands_player ON memorable_hands(player_name);

-- Hand commentary (AI reflections)
CREATE TABLE IF NOT EXISTS hand_commentary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    hand_number INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    commentary TEXT NOT NULL,
    reflection_type TEXT NOT NULL DEFAULT 'general',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_hand_commentary_game ON hand_commentary(game_id);
CREATE INDEX IF NOT EXISTS idx_hand_commentary_player ON hand_commentary(game_id, player_name);
