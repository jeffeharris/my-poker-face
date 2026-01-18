-- Schema version tracking (must be first)
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- Games - core game state storage
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    phase TEXT NOT NULL,
    num_players INTEGER NOT NULL,
    pot_size REAL NOT NULL,
    game_state_json TEXT NOT NULL,
    owner_id TEXT,
    owner_name TEXT,
    debug_capture_enabled BOOLEAN DEFAULT 0,
    llm_configs_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_owner ON games(owner_id);

-- Game messages (chat log)
CREATE TABLE IF NOT EXISTS game_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_type TEXT NOT NULL,
    message_text TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_game_id ON game_messages(game_id, timestamp);
