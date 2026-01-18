-- Hand history records
CREATE TABLE IF NOT EXISTS hand_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    hand_number INTEGER NOT NULL,
    phase TEXT NOT NULL,
    community_cards TEXT,
    pot_size REAL NOT NULL,
    player_hands TEXT,
    actions TEXT,
    winners TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE INDEX IF NOT EXISTS idx_hand_history_game ON hand_history(game_id);
CREATE INDEX IF NOT EXISTS idx_hand_history_hand ON hand_history(game_id, hand_number);
