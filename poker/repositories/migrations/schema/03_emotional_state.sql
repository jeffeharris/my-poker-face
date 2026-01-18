-- Emotional state (tilt tracking)
CREATE TABLE IF NOT EXISTS emotional_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    tilt_level REAL DEFAULT 0.0,
    current_mood TEXT DEFAULT 'neutral',
    trigger_events TEXT DEFAULT '[]',
    modifier_stack TEXT DEFAULT '[]',
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    UNIQUE(game_id, player_name)
);

CREATE INDEX IF NOT EXISTS idx_emotional_state_game ON emotional_state(game_id);
CREATE INDEX IF NOT EXISTS idx_emotional_state_player ON emotional_state(player_name);

-- Controller state (TiltState, ElasticPersonality persistence)
CREATE TABLE IF NOT EXISTS controller_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    state_type TEXT NOT NULL,
    state_data TEXT NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    UNIQUE(game_id, player_name, state_type)
);

CREATE INDEX IF NOT EXISTS idx_controller_state_game ON controller_state(game_id);
CREATE INDEX IF NOT EXISTS idx_controller_state_player ON controller_state(player_name);

-- Pressure events tracking
CREATE TABLE IF NOT EXISTS pressure_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_pressure_events_game ON pressure_events(game_id);
CREATE INDEX IF NOT EXISTS idx_pressure_events_player ON pressure_events(player_name);
