-- Experiment metadata and configuration
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    config TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_experiments_name ON experiments(name);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_created ON experiments(created_at DESC);

-- Links games to experiments
CREATE TABLE IF NOT EXISTS experiment_games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id INTEGER NOT NULL,
    game_id TEXT NOT NULL,
    game_number INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id),
    FOREIGN KEY (game_id) REFERENCES games(game_id),
    UNIQUE(experiment_id, game_id)
);

CREATE INDEX IF NOT EXISTS idx_experiment_games_experiment ON experiment_games(experiment_id);
CREATE INDEX IF NOT EXISTS idx_experiment_games_game ON experiment_games(game_id);
CREATE INDEX IF NOT EXISTS idx_experiment_games_status ON experiment_games(status);
