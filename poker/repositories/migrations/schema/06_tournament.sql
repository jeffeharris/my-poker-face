-- Tournament results (columns support both old and new schema for backward compatibility)
CREATE TABLE IF NOT EXISTS tournament_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL UNIQUE,
    tournament_type TEXT,  -- New
    starting_players INTEGER,  -- New
    final_standings TEXT,  -- New
    total_hands INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Legacy columns
    winner_name TEXT,
    biggest_pot INTEGER DEFAULT 0,
    starting_player_count INTEGER,
    human_player_name TEXT,
    human_finishing_position INTEGER,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_tournament_results_game ON tournament_results(game_id);
CREATE INDEX IF NOT EXISTS idx_tournament_results_ended ON tournament_results(ended_at DESC);
CREATE INDEX IF NOT EXISTS idx_tournament_results_winner ON tournament_results(winner_name);

-- Tournament standings per player
CREATE TABLE IF NOT EXISTS tournament_standings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    final_position INTEGER NOT NULL,
    final_chips INTEGER NOT NULL,
    hands_played INTEGER NOT NULL,
    eliminations INTEGER DEFAULT 0,
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    UNIQUE(game_id, player_name)
);

CREATE INDEX IF NOT EXISTS idx_tournament_standings_game ON tournament_standings(game_id);
CREATE INDEX IF NOT EXISTS idx_tournament_standings_player ON tournament_standings(player_name);

-- Player career statistics
CREATE TABLE IF NOT EXISTS player_career_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL UNIQUE,
    tournaments_played INTEGER DEFAULT 0,
    total_wins INTEGER DEFAULT 0,
    total_final_tables INTEGER DEFAULT 0,
    best_finish INTEGER,
    avg_finish REAL DEFAULT 0.0,
    total_eliminations INTEGER DEFAULT 0,
    total_hands_played INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_player_career_stats_name ON player_career_stats(player_name);

-- Tournament tracker (elimination history)
CREATE TABLE IF NOT EXISTS tournament_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL UNIQUE,
    tracker_data TEXT NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_tournament_tracker_game ON tournament_tracker(game_id);
