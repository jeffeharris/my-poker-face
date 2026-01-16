"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import numpy as np

from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from core.card import Card
import logging

logger = logging.getLogger(__name__)

# Current schema version - increment when adding migrations
# v42: Schema consolidation - all tables now created in _init_db(), migrations are no-ops
# v43: Add experiments and experiment_games tables for experiment tracking
# v44: Add app_settings table for dynamic configuration
# v45: Add users table for Google OAuth authentication
# v46: Add error_message column to api_usage table
# v47: Add Pollinations image models to enabled_models table
SCHEMA_VERSION = 47


@dataclass
class SavedGame:
    """Represents a saved game with metadata."""
    game_id: str
    created_at: datetime
    updated_at: datetime
    phase: str
    num_players: int
    pot_size: float
    game_state_json: str
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None


class GamePersistence:
    """Handles persistence of poker games to SQLite database."""

    def __init__(self, db_path: str = "poker_games.db"):
        self.db_path = db_path
        self._enable_wal_mode()
        self._init_db()
        self._run_migrations()

    def _enable_wal_mode(self):
        """Enable WAL mode for better concurrent read/write performance.

        WAL (Write-Ahead Logging) mode allows concurrent readers and writers,
        which is important for parallel tournament execution. The 5-second
        busy timeout prevents immediate failures on brief lock contention
        while failing fast on real deadlocks.
        """
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")  # 5 second timeout
                conn.execute("PRAGMA synchronous=NORMAL")  # Good balance of safety/speed
        except Exception as e:
            logger.warning(f"Could not enable WAL mode: {e}")
    
    def _serialize_card(self, card) -> Dict[str, Any]:
        """Ensure card is properly serialized."""
        if hasattr(card, 'to_dict'):
            return card.to_dict()
        elif isinstance(card, dict):
            # Validate dict has required fields
            if 'rank' in card and 'suit' in card:
                return card
            else:
                raise ValueError(f"Invalid card dict: missing rank or suit in {card}")
        else:
            raise ValueError(f"Unknown card format: {type(card)}")
    
    def _deserialize_card(self, card_data) -> Card:
        """Ensure card is properly deserialized to Card object."""
        if isinstance(card_data, dict):
            return Card.from_dict(card_data)
        elif hasattr(card_data, 'rank'):  # Already a Card object
            return card_data
        else:
            raise ValueError(f"Cannot deserialize card: {card_data}")
    
    def _serialize_cards(self, cards) -> List[Dict[str, Any]]:
        """Serialize a collection of cards."""
        if not cards:
            return []
        return [self._serialize_card(card) for card in cards]
    
    def _deserialize_cards(self, cards_data) -> tuple:
        """Deserialize a collection of cards."""
        if not cards_data:
            return tuple()
        return tuple(self._deserialize_card(card_data) for card_data in cards_data)
    
    def _init_db(self):
        """Initialize the database schema.

        This method creates ALL tables for fresh databases. Existing databases
        will have tables created by migrations, which are now no-ops.

        Tables (25 total):
        1. schema_version - Migration tracking
        2. games - Core game state
        3. game_messages - Chat log
        4. ai_player_state - AI conversation history
        5. personality_snapshots - Personality evolution
        6. pressure_events - Event tracking
        7. personalities - AI personality storage
        8. hand_history - Historical hands
        9. opponent_models - AI learning (v27 constraint)
        10. memorable_hands - Memorable hand storage
        11. hand_commentary - AI reflections (v41)
        12. emotional_state - Tilt persistence (v3)
        13. controller_state - TiltState/ElasticPersonality (v3, v40)
        14. tournament_results - Tournament outcomes (v4)
        15. tournament_standings - Player standings (v4)
        16. player_career_stats - Career statistics (v4)
        17. avatar_images - Character images (v5, v28)
        18. api_usage - LLM cost tracking (v6-v17)
        19. model_pricing - SKU-based pricing (v14, v15)
        20. enabled_models - Model management (v38)
        21. prompt_captures - AI debugging (v18, v39)
        22. player_decision_analysis - Quality monitoring (v20-v23)
        23. tournament_tracker - Elimination history (v29)
        24. experiments - Experiment metadata and config (v43)
        25. experiment_games - Links games to experiments (v43)
        """
        with sqlite3.connect(self.db_path) as conn:
            # 1. Schema version tracking - must be first
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """)

            # 2. Games - core game state (v1 added owner columns, v26 debug, v34 llm_configs)
            conn.execute("""
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
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_games_owner ON games(owner_id)")

            # 3. Game messages
            conn.execute("""
                CREATE TABLE IF NOT EXISTS game_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_type TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_game_id ON game_messages(game_id, timestamp)")

            # 4. AI player state
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_player_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    conversation_history TEXT,
                    personality_state TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_player_game ON ai_player_state(game_id, player_name)")

            # 5. Personality snapshots
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personality_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER,
                    personality_traits TEXT,
                    pressure_levels TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_personality_snapshots ON personality_snapshots(game_id, hand_number)")

            # 6. Pressure events
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pressure_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    details_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pressure_events_game ON pressure_events(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pressure_events_player ON pressure_events(player_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pressure_events_type ON pressure_events(event_type)")

            # 7. Personalities (v5 added elasticity_config)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personalities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_generated BOOLEAN DEFAULT 1,
                    source TEXT DEFAULT 'ai_generated',
                    times_used INTEGER DEFAULT 0,
                    elasticity_config TEXT
                )
            """)

            # 8. Hand history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hand_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    players_json TEXT NOT NULL,
                    hole_cards_json TEXT,
                    community_cards_json TEXT,
                    actions_json TEXT NOT NULL,
                    winners_json TEXT,
                    pot_size INTEGER,
                    showdown BOOLEAN,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_history_game ON hand_history(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_history_timestamp ON hand_history(timestamp DESC)")

            # 9. Opponent models (v21 added game_id, v25 added notes, v27 fixed constraint)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS opponent_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    hands_observed INTEGER DEFAULT 0,
                    vpip REAL DEFAULT 0.5,
                    pfr REAL DEFAULT 0.5,
                    aggression_factor REAL DEFAULT 1.0,
                    fold_to_cbet REAL DEFAULT 0.5,
                    bluff_frequency REAL DEFAULT 0.3,
                    showdown_win_rate REAL DEFAULT 0.5,
                    recent_trend TEXT,
                    notes TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(game_id, observer_name, opponent_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_opponent_models_observer ON opponent_models(observer_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_opponent_models_game ON opponent_models(game_id)")

            # 10. Memorable hands (v21 added game_id)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memorable_hands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    hand_id INTEGER NOT NULL,
                    game_id TEXT,
                    memory_type TEXT NOT NULL,
                    impact_score REAL,
                    narrative TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (hand_id) REFERENCES hand_history(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memorable_observer ON memorable_hands(observer_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memorable_opponent ON memorable_hands(opponent_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memorable_hands_game ON memorable_hands(game_id)")

            # 11. Hand commentary (v41)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hand_commentary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER NOT NULL,
                    player_name TEXT NOT NULL,
                    emotional_reaction TEXT,
                    strategic_reflection TEXT,
                    opponent_observations TEXT,
                    key_insight TEXT,
                    decision_plans TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number, player_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_commentary_game ON hand_commentary(game_id, player_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_commentary_player_recent ON hand_commentary(game_id, player_name, hand_number DESC)")

            # 12. Emotional state (v3)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS emotional_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    valence REAL DEFAULT 0.0,
                    arousal REAL DEFAULT 0.5,
                    control REAL DEFAULT 0.5,
                    focus REAL DEFAULT 0.5,
                    narrative TEXT,
                    inner_voice TEXT,
                    generated_at_hand INTEGER DEFAULT 0,
                    source_events TEXT,
                    metadata_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emotional_state_game ON emotional_state(game_id, player_name)")

            # 13. Controller state (v3, v40 added prompt_config_json)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS controller_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    tilt_state_json TEXT,
                    elastic_personality_json TEXT,
                    prompt_config_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_controller_state_game ON controller_state(game_id, player_name)")

            # 14. Tournament results (v4)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournament_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL UNIQUE,
                    winner_name TEXT,
                    total_hands INTEGER DEFAULT 0,
                    biggest_pot INTEGER DEFAULT 0,
                    starting_player_count INTEGER,
                    human_player_name TEXT,
                    human_finishing_position INTEGER,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tournament_results_winner ON tournament_results(winner_name)")

            # 15. Tournament standings (v4)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournament_standings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    is_human BOOLEAN DEFAULT 0,
                    finishing_position INTEGER,
                    eliminated_by TEXT,
                    eliminated_at_hand INTEGER,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tournament_standings_game ON tournament_standings(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tournament_standings_player ON tournament_standings(player_name)")

            # 16. Player career stats (v4)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_career_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL UNIQUE,
                    games_played INTEGER DEFAULT 0,
                    games_won INTEGER DEFAULT 0,
                    total_eliminations INTEGER DEFAULT 0,
                    best_finish INTEGER,
                    worst_finish INTEGER,
                    avg_finish REAL,
                    biggest_pot_ever INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_career_stats_player ON player_career_stats(player_name)")

            # 17. Avatar images (v5, v28 added full_image columns)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS avatar_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    personality_name TEXT NOT NULL,
                    emotion TEXT NOT NULL,
                    image_data BLOB NOT NULL,
                    content_type TEXT DEFAULT 'image/png',
                    width INTEGER DEFAULT 256,
                    height INTEGER DEFAULT 256,
                    file_size INTEGER,
                    full_image_data BLOB,
                    full_width INTEGER,
                    full_height INTEGER,
                    full_file_size INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(personality_name, emotion)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_avatar_personality ON avatar_images(personality_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_avatar_emotion ON avatar_images(emotion)")

            # 18. API usage (v6-v17: comprehensive LLM tracking)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_usage (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
                    owner_id TEXT,
                    player_name TEXT,
                    hand_number INTEGER,
                    call_type TEXT NOT NULL,
                    prompt_template TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    image_count INTEGER DEFAULT 0,
                    image_size TEXT,
                    latency_ms INTEGER,
                    status TEXT NOT NULL,
                    finish_reason TEXT,
                    error_code TEXT,
                    reasoning_effort TEXT,
                    request_id TEXT,
                    max_tokens INTEGER,
                    message_count INTEGER,
                    system_prompt_tokens INTEGER,
                    estimated_cost REAL,
                    pricing_ids TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_owner ON api_usage(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_game ON api_usage(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_owner_created ON api_usage(owner_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_owner_call_type ON api_usage(owner_id, call_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_game_call_type ON api_usage(game_id, call_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_model_created ON api_usage(model, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_model_effort ON api_usage(model, reasoning_effort)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_request_id ON api_usage(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_cost ON api_usage(estimated_cost)")

            # 19. Model pricing (v14 SKU-based, v15 validity dates)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_pricing (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    cost REAL NOT NULL,
                    valid_from TIMESTAMP,
                    valid_until TIMESTAMP,
                    effective_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT,
                    UNIQUE(provider, model, unit, valid_from)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup ON model_pricing(provider, model)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_model_pricing_validity ON model_pricing(provider, model, unit, valid_from, valid_until)")

            # 20. Enabled models (v38)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enabled_models (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    display_name TEXT,
                    notes TEXT,
                    supports_reasoning INTEGER DEFAULT 0,
                    supports_json_mode INTEGER DEFAULT 1,
                    supports_image_gen INTEGER DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider, model)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_enabled_models_provider ON enabled_models(provider, enabled)")

            # 21. Prompt captures (v18, v19, v24, v30, v33, v39)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    game_id TEXT,
                    player_name TEXT,
                    hand_number INTEGER,
                    phase TEXT,
                    action_taken TEXT,
                    system_prompt TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    ai_response TEXT NOT NULL,
                    pot_total INTEGER,
                    cost_to_call INTEGER,
                    pot_odds REAL,
                    player_stack INTEGER,
                    community_cards TEXT,
                    player_hand TEXT,
                    valid_actions TEXT,
                    raise_amount INTEGER,
                    model TEXT,
                    latency_ms INTEGER,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    tags TEXT,
                    notes TEXT,
                    conversation_history TEXT,
                    raw_api_response TEXT,
                    prompt_template TEXT,
                    prompt_version TEXT,
                    prompt_hash TEXT,
                    raw_request TEXT,
                    reasoning_effort TEXT,
                    original_request_id TEXT,
                    provider TEXT DEFAULT 'openai',
                    call_type TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE SET NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)")
            # These indexes are on columns added by migrations v33 and v39
            # Use try-except to handle older databases that haven't been migrated yet
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type)")
            except sqlite3.OperationalError:
                pass  # Columns don't exist yet, will be created by migrations

            # 22. Player decision analysis (v20, v22, v23)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_decision_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    request_id TEXT,
                    capture_id INTEGER,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    hand_number INTEGER,
                    phase TEXT,
                    pot_total INTEGER,
                    cost_to_call INTEGER,
                    player_stack INTEGER,
                    num_opponents INTEGER,
                    player_hand TEXT,
                    community_cards TEXT,
                    action_taken TEXT,
                    raise_amount INTEGER,
                    equity REAL,
                    required_equity REAL,
                    ev_call REAL,
                    equity_vs_ranges REAL,
                    optimal_action TEXT,
                    decision_quality TEXT,
                    ev_lost REAL,
                    hand_rank INTEGER,
                    relative_strength REAL,
                    player_position TEXT,
                    opponent_positions TEXT,
                    analyzer_version TEXT,
                    processing_time_ms INTEGER,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_game ON player_decision_analysis(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_request ON player_decision_analysis(request_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_quality ON player_decision_analysis(decision_quality)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_ev_lost ON player_decision_analysis(ev_lost DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_player ON player_decision_analysis(player_name)")

            # 23. Tournament tracker (v29)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournament_tracker (
                    game_id TEXT PRIMARY KEY,
                    tracker_json TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tournament_tracker_game ON tournament_tracker(game_id)")

            # 24. Experiments (v43) - experiment metadata and configuration
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    hypothesis TEXT,
                    tags TEXT,
                    notes TEXT,
                    config_json TEXT NOT NULL,
                    status TEXT DEFAULT 'running',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    summary_json TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_name ON experiments(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)")

            # 25. Experiment games (v43) - links games to experiments with variant config
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiment_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL,
                    game_id TEXT NOT NULL,
                    variant TEXT,
                    variant_config_json TEXT,
                    tournament_number INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE,
                    UNIQUE(experiment_id, game_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiment_games_experiment ON experiment_games(experiment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiment_games_game ON experiment_games(game_id)")

            # 26. App settings (v44) - Dynamic configuration
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    description TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 27. Users table (v45) - Google OAuth authentication
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    picture TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    linked_guest_id TEXT,
                    is_guest BOOLEAN DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_linked_guest ON users(linked_guest_id)")

    def _get_current_schema_version(self) -> int:
        """Get the current schema version from the database."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                cursor = conn.execute("SELECT MAX(version) FROM schema_version")
                result = cursor.fetchone()[0]
                return result if result is not None else 0
            except sqlite3.OperationalError:
                # Table doesn't exist yet
                return 0

    def _run_migrations(self) -> None:
        """Run any pending schema migrations."""
        current_version = self._get_current_schema_version()

        if current_version >= SCHEMA_VERSION:
            return

        logger.info(f"Running database migrations from version {current_version} to {SCHEMA_VERSION}")

        migrations: Dict[int, tuple] = {
            1: (self._migrate_v1_add_owner_columns, "Add owner_id and owner_name to games table"),
            2: (self._migrate_v2_add_memory_tables, "Add AI memory and learning tables"),
            3: (self._migrate_v3_add_controller_state_tables, "Add emotional state and controller state tables"),
            4: (self._migrate_v4_add_tournament_tables, "Add tournament results and career stats tables"),
            5: (self._migrate_v5_add_avatar_images_table, "Add avatar_images table for storing character images"),
            6: (self._migrate_v6_add_api_usage_table, "Add api_usage table for LLM cost tracking"),
            7: (self._migrate_v7_add_reasoning_effort, "Add reasoning_effort column to api_usage table"),
            8: (self._migrate_v8_add_request_id, "Add request_id column for vendor correlation"),
            9: (self._migrate_v9_add_max_tokens, "Add max_tokens column for token limit tracking"),
            10: (self._migrate_v10_add_conversation_metrics, "Add message_count and system_prompt_length columns"),
            11: (self._migrate_v11_add_system_prompt_tokens, "Add system_prompt_tokens column for accurate token tracking"),
            12: (self._migrate_v12_drop_system_prompt_length, "Drop unused system_prompt_length column"),
            13: (self._migrate_v13_add_pricing_tables, "Add model_pricing table and estimated_cost column"),
            14: (self._migrate_v14_sku_based_pricing, "Redesign model_pricing as SKU-based rows"),
            15: (self._migrate_v15_add_pricing_validity_dates, "Add valid_from and valid_until to model_pricing"),
            16: (self._migrate_v16_add_pricing_id_to_usage, "Add pricing_id foreign key to api_usage"),
            17: (self._migrate_v17_consolidate_pricing_ids, "Replace 4 pricing_id columns with single JSON column"),
            18: (self._migrate_v18_add_prompt_captures, "Add prompt_captures table for debugging AI decisions"),
            19: (self._migrate_v19_add_conversation_history, "Add conversation_history column to prompt_captures"),
            20: (self._migrate_v20_add_decision_analysis, "Add player_decision_analysis table for quality monitoring"),
            21: (self._migrate_v21_add_game_id_to_opponent_models, "Add game_id to opponent_models for game-specific tracking"),
            22: (self._migrate_v22_add_position_equity, "Add position-based equity fields to decision analysis"),
            23: (self._migrate_v23_add_player_position, "Add player_position to decision analysis"),
            24: (self._migrate_v24_add_prompt_versioning, "Add prompt version tracking to prompt_captures"),
            25: (self._migrate_v25_add_opponent_notes, "Add notes column to opponent_models for player observations"),
            26: (self._migrate_v26_add_debug_capture, "Add debug_capture_enabled column to games table"),
            27: (self._migrate_v27_fix_opponent_models_constraint, "Fix opponent_models unique constraint to include game_id"),
            28: (self._migrate_v28_add_full_image_column, "Add full_image_data column for uncropped avatar images"),
            29: (self._migrate_v29_add_tournament_tracker, "Add tournament_tracker table for persisting elimination history"),
            30: (self._migrate_v30_add_prompt_capture_columns, "Add raw_request and reasoning columns to prompt_captures"),
            31: (self._migrate_v31_add_provider_pricing, "Add Groq and Claude 4.5 pricing to model_pricing"),
            32: (self._migrate_v32_add_more_providers, "Add DeepSeek, Mistral, and Google Gemini pricing"),
            33: (self._migrate_v33_add_provider_to_captures, "Add provider column to prompt_captures"),
            34: (self._migrate_v34_add_llm_configs, "Add llm_configs_json column to games table"),
            35: (self._migrate_v35_add_provider_index, "Add index on provider column in prompt_captures"),
            36: (self._migrate_v36_add_xai_pricing, "Add xAI Grok pricing to model_pricing"),
            37: (self._migrate_v37_add_gpt5_pricing, "Add OpenAI GPT-5 pricing"),
            38: (self._migrate_v38_add_enabled_models, "Add enabled_models table for model management"),
            39: (self._migrate_v39_playground_capture_support, "Make game_id nullable and add call_type to prompt_captures for playground"),
            40: (self._migrate_v40_add_prompt_config, "Add prompt_config_json column for toggleable prompt components"),
            41: (self._migrate_v41_add_hand_commentary, "Add hand_commentary table for AI reflection persistence"),
            42: (self._migrate_v42_schema_consolidation, "Schema consolidation - all tables now in _init_db, pricing from YAML"),
            43: (self._migrate_v43_add_experiments, "Add experiments and experiment_games tables for experiment tracking"),
            44: (self._migrate_v44_add_app_settings, "Add app_settings table for dynamic configuration"),
            45: (self._migrate_v45_add_users_table, "Add users table for Google OAuth authentication"),
            46: (self._migrate_v46_add_error_message, "Add error_message column to api_usage table"),
            47: (self._migrate_v47_add_pollinations_models, "Add Pollinations image models to enabled_models"),
        }

        with sqlite3.connect(self.db_path) as conn:
            for version in range(current_version + 1, SCHEMA_VERSION + 1):
                if version in migrations:
                    migrate_func, description = migrations[version]
                    try:
                        migrate_func(conn)
                        conn.execute(
                            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                            (version, description)
                        )
                        conn.commit()
                        logger.info(f"Applied migration v{version}: {description}")
                    except Exception as e:
                        logger.error(f"Migration v{version} failed: {e}")
                        raise

    def _migrate_v1_add_owner_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v1: Add owner_id and owner_name columns to games table."""
        cursor = conn.execute("PRAGMA table_info(games)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'owner_id' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN owner_id TEXT")
            conn.execute("ALTER TABLE games ADD COLUMN owner_name TEXT")
            # Purge old games without owners
            conn.execute("DELETE FROM games")
            logger.info("Added owner_id column and purged old games without owners")

    def _migrate_v2_add_memory_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v2: Add AI memory and learning tables.

        These tables may already exist from _init_db, but this migration
        ensures the schema_version table tracks their addition.
        """
        # Verify tables exist (they should from _init_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
            ('hand_history', 'opponent_models', 'memorable_hands')
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {'hand_history', 'opponent_models', 'memorable_hands'}
        missing_tables = expected_tables - existing_tables

        if missing_tables:
            logger.warning(f"Memory tables missing (will be created by _init_db): {missing_tables}")

        logger.info("AI memory tables verified/registered in schema version")

    def _migrate_v3_add_controller_state_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v3: Add tables for emotional state and controller state persistence.

        This fixes the issue where TiltState and ElasticPersonality were lost on game reload.
        """
        # Emotional state table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emotional_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                valence REAL DEFAULT 0.0,
                arousal REAL DEFAULT 0.5,
                control REAL DEFAULT 0.5,
                focus REAL DEFAULT 0.5,
                narrative TEXT,
                inner_voice TEXT,
                generated_at_hand INTEGER DEFAULT 0,
                source_events TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, player_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_emotional_state_game
            ON emotional_state(game_id, player_name)
        """)

        # Controller state table (for tilt and other controller-specific state)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS controller_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                tilt_state_json TEXT,
                elastic_personality_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, player_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_controller_state_game
            ON controller_state(game_id, player_name)
        """)

        logger.info("Created emotional_state and controller_state tables")

    def _migrate_v4_add_tournament_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v4: Add tournament results and career stats tables."""
        # Tournament results - one row per completed game
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL UNIQUE,
                winner_name TEXT,
                total_hands INTEGER DEFAULT 0,
                biggest_pot INTEGER DEFAULT 0,
                starting_player_count INTEGER,
                human_player_name TEXT,
                human_finishing_position INTEGER,
                started_at TIMESTAMP,
                ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_results_winner
            ON tournament_results(winner_name)
        """)

        # Tournament standings - one row per player per tournament
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_standings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                is_human BOOLEAN DEFAULT 0,
                finishing_position INTEGER,
                eliminated_by TEXT,
                eliminated_at_hand INTEGER,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE(game_id, player_name)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_standings_game
            ON tournament_standings(game_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_standings_player
            ON tournament_standings(player_name)
        """)

        # Player career stats - human player only, aggregated across games
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_career_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL UNIQUE,
                games_played INTEGER DEFAULT 0,
                games_won INTEGER DEFAULT 0,
                total_eliminations INTEGER DEFAULT 0,
                best_finish INTEGER,
                worst_finish INTEGER,
                avg_finish REAL,
                biggest_pot_ever INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_career_stats_player
            ON player_career_stats(player_name)
        """)

        logger.info("Created tournament_results, tournament_standings, and player_career_stats tables")

    def _migrate_v5_add_avatar_images_table(self, conn: sqlite3.Connection) -> None:
        """Migration v5: Add avatar_images table for storing character images in DB."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS avatar_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                personality_name TEXT NOT NULL,
                emotion TEXT NOT NULL,
                image_data BLOB NOT NULL,
                content_type TEXT DEFAULT 'image/png',
                width INTEGER DEFAULT 256,
                height INTEGER DEFAULT 256,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(personality_name, emotion)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_avatar_personality
            ON avatar_images(personality_name)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_avatar_emotion
            ON avatar_images(emotion)
        """)

        # Add elasticity_config column to personalities if missing
        cursor = conn.execute("PRAGMA table_info(personalities)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'elasticity_config' not in columns:
            conn.execute("ALTER TABLE personalities ADD COLUMN elasticity_config TEXT")

        logger.info("Created avatar_images table and verified personalities schema")

    def _migrate_v6_add_api_usage_table(self, conn: sqlite3.Connection) -> None:
        """Migration v6: Add api_usage table for LLM cost tracking."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                -- Context (nullable - not all calls have game context)
                game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
                owner_id TEXT,
                player_name TEXT,
                hand_number INTEGER,

                -- Call classification (validated enum in code)
                call_type TEXT NOT NULL,
                prompt_template TEXT,

                -- Provider/Model
                provider TEXT NOT NULL,
                model TEXT NOT NULL,

                -- Token usage (for text completions)
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,

                -- Image usage (for DALL-E - cost is per-image, not tokens)
                image_count INTEGER DEFAULT 0,
                image_size TEXT,

                -- Performance & Status
                latency_ms INTEGER,
                status TEXT NOT NULL,
                finish_reason TEXT,
                error_code TEXT
            )
        """)

        # Single-column indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_owner
            ON api_usage(owner_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_game
            ON api_usage(game_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_created
            ON api_usage(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_call_type
            ON api_usage(call_type)
        """)

        # Composite indexes for common cost queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_owner_created
            ON api_usage(owner_id, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_owner_call_type
            ON api_usage(owner_id, call_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_game_call_type
            ON api_usage(game_id, call_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_usage_model_created
            ON api_usage(model, created_at)
        """)

        logger.info("Created api_usage table for LLM cost tracking")

    def _migrate_v7_add_reasoning_effort(self, conn: sqlite3.Connection) -> None:
        """Migration v7: Legacy - schema now in _init_db."""
        # No-op: api_usage.reasoning_effort created in _init_db()
        pass

    def _migrate_v8_add_request_id(self, conn: sqlite3.Connection) -> None:
        """Migration v8: Legacy - schema now in _init_db."""
        # No-op: api_usage.request_id created in _init_db()
        pass

    def _migrate_v9_add_max_tokens(self, conn: sqlite3.Connection) -> None:
        """Migration v9: Legacy - schema now in _init_db."""
        # No-op: api_usage.max_tokens created in _init_db()
        pass

    def _migrate_v10_add_conversation_metrics(self, conn: sqlite3.Connection) -> None:
        """Migration v10: Legacy - schema now in _init_db."""
        # No-op: api_usage.message_count created in _init_db()
        pass

    def _migrate_v11_add_system_prompt_tokens(self, conn: sqlite3.Connection) -> None:
        """Migration v11: Legacy - schema now in _init_db."""
        # No-op: api_usage.system_prompt_tokens created in _init_db()
        pass

    def _migrate_v12_drop_system_prompt_length(self, conn: sqlite3.Connection) -> None:
        """Migration v12: Legacy - column already absent in _init_db."""
        # No-op: system_prompt_length was never added in consolidated schema
        pass

    def _migrate_v13_add_pricing_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v13: Legacy - schema now in _init_db, pricing from YAML."""
        # No-op: model_pricing and api_usage.estimated_cost created in _init_db()
        # Pricing data now loaded from config/pricing.yaml via pricing_loader
        pass

    def _migrate_v14_sku_based_pricing(self, conn: sqlite3.Connection) -> None:
        """Migration v14: Legacy - schema now in _init_db, pricing from YAML."""
        # No-op: model_pricing with SKU schema created in _init_db()
        # Pricing data now loaded from config/pricing.yaml via pricing_loader
        pass

    def _migrate_v15_add_pricing_validity_dates(self, conn: sqlite3.Connection) -> None:
        """Migration v15: Legacy - schema now in _init_db."""
        # No-op: model_pricing with validity dates created in _init_db()
        pass

    def _migrate_v16_add_pricing_id_to_usage(self, conn: sqlite3.Connection) -> None:
        """Migration v16: Legacy - schema now in _init_db."""
        # No-op: api_usage.pricing_ids created in _init_db()
        pass

    def _migrate_v17_consolidate_pricing_ids(self, conn: sqlite3.Connection) -> None:
        """Migration v17: Consolidate 4 pricing_id columns into single JSON column.

        This fixes v16 which may have created separate columns instead of JSON.
        SQLite doesn't support DROP COLUMN in older versions, so we recreate the table.
        """
        # Check if we need to migrate (4 columns exist instead of pricing_ids)
        cursor = conn.execute("PRAGMA table_info(api_usage)")
        columns = {row[1] for row in cursor}

        if 'input_pricing_id' in columns:
            # Old schema - need to migrate
            # Create new table without the 4 pricing_id columns, with pricing_ids JSON
            conn.execute("""
                CREATE TABLE api_usage_new (
                    id INTEGER PRIMARY KEY,
                    created_at TIMESTAMP,
                    game_id TEXT,
                    owner_id TEXT,
                    player_name TEXT,
                    hand_number INTEGER,
                    call_type TEXT,
                    prompt_template TEXT,
                    provider TEXT,
                    model TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cached_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    image_count INTEGER,
                    image_size TEXT,
                    latency_ms INTEGER,
                    status TEXT,
                    finish_reason TEXT,
                    error_code TEXT,
                    reasoning_effort TEXT,
                    request_id TEXT,
                    max_tokens INTEGER,
                    message_count INTEGER,
                    system_prompt_tokens INTEGER,
                    estimated_cost REAL,
                    pricing_ids TEXT
                )
            """)

            # Copy data, converting old columns to JSON
            conn.execute("""
                INSERT INTO api_usage_new
                SELECT
                    id, created_at, game_id, owner_id, player_name, hand_number,
                    call_type, prompt_template, provider, model,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    image_count, image_size, latency_ms, status, finish_reason,
                    error_code, reasoning_effort, request_id, max_tokens,
                    message_count, system_prompt_tokens, estimated_cost,
                    CASE
                        WHEN image_pricing_id IS NOT NULL THEN json_object('image', image_pricing_id)
                        WHEN input_pricing_id IS NOT NULL THEN
                            CASE
                                WHEN cached_pricing_id IS NOT NULL THEN
                                    json_object('input', input_pricing_id, 'output', output_pricing_id, 'cached', cached_pricing_id)
                                ELSE
                                    json_object('input', input_pricing_id, 'output', output_pricing_id)
                            END
                        ELSE NULL
                    END
                FROM api_usage
            """)

            conn.execute("DROP TABLE api_usage")
            conn.execute("ALTER TABLE api_usage_new RENAME TO api_usage")

            # Recreate indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_game ON api_usage(game_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_cost ON api_usage(estimated_cost)")

            logger.info("Consolidated 4 pricing_id columns into pricing_ids JSON")
        elif 'pricing_ids' not in columns:
            # Neither schema exists - add the column
            conn.execute("ALTER TABLE api_usage ADD COLUMN pricing_ids TEXT")
            logger.info("Added pricing_ids column to api_usage table")
        else:
            logger.info("pricing_ids column already exists, no migration needed")

    def _migrate_v18_add_prompt_captures(self, conn: sqlite3.Connection) -> None:
        """Migration v18: Add prompt_captures table for debugging AI decisions.

        This table stores full prompts and responses for AI player decisions,
        enabling analysis and replay of AI behavior.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                hand_number INTEGER,
                phase TEXT NOT NULL,
                action_taken TEXT,
                system_prompt TEXT NOT NULL,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                pot_total INTEGER,
                cost_to_call INTEGER,
                pot_odds REAL,
                player_stack INTEGER,
                community_cards TEXT,
                player_hand TEXT,
                valid_actions TEXT,
                raise_amount INTEGER,
                model TEXT,
                latency_ms INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                tags TEXT,
                notes TEXT,
                FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
            )
        """)

        # Create indexes for efficient querying
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)")

        logger.info("Created prompt_captures table for AI decision debugging")

    def _migrate_v19_add_conversation_history(self, conn: sqlite3.Connection) -> None:
        """Migration v19: Add conversation_history column to prompt_captures.

        This stores the full conversation history (prior messages) that were
        sent to the LLM, which affects the AI's decision.
        """
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}

        if 'conversation_history' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN conversation_history TEXT")
            logger.info("Added conversation_history column to prompt_captures")

        # Also add raw_api_response column
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}
        if 'raw_api_response' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN raw_api_response TEXT")
            logger.info("Added raw_api_response column to prompt_captures")

    def _migrate_v20_add_decision_analysis(self, conn: sqlite3.Connection) -> None:
        """Migration v20: Add player_decision_analysis table for quality monitoring.

        This table stores equity and decision quality metrics for EVERY AI decision,
        enabling quality monitoring across all games without storing full prompts.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_decision_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                -- Link to other tables (nullable - not all may exist)
                request_id TEXT,              -- Links to api_usage.request_id
                capture_id INTEGER,           -- Links to prompt_captures.id (if captured)

                -- Identity
                game_id TEXT NOT NULL,
                player_name TEXT NOT NULL,
                hand_number INTEGER,
                phase TEXT,

                -- Game State (compact)
                pot_total INTEGER,
                cost_to_call INTEGER,
                player_stack INTEGER,
                num_opponents INTEGER,

                -- Cards (for recalculation)
                player_hand TEXT,             -- JSON: ["As", "Kd"]
                community_cards TEXT,         -- JSON: ["Jh", "2d", "5s"]

                -- Decision
                action_taken TEXT,
                raise_amount INTEGER,

                -- Equity Analysis
                equity REAL,                  -- Win probability (0.0-1.0)
                required_equity REAL,         -- Minimum equity to call profitably
                ev_call REAL,                 -- Expected value of calling

                -- Decision Quality
                optimal_action TEXT,          -- "fold", "call", "raise"
                decision_quality TEXT,        -- "correct", "mistake", "marginal", "unknown"
                ev_lost REAL,                 -- EV lost if suboptimal

                -- Hand Strength
                hand_rank INTEGER,            -- eval7 rank (lower = stronger)
                relative_strength REAL,       -- Percentile (0-100)

                -- Processing Metadata
                analyzer_version TEXT,
                processing_time_ms INTEGER,

                FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
            )
        """)

        # Create indexes for efficient querying
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_game ON player_decision_analysis(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_request ON player_decision_analysis(request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_quality ON player_decision_analysis(decision_quality)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_ev_lost ON player_decision_analysis(ev_lost DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_analysis_player ON player_decision_analysis(player_name)")

        logger.info("Created player_decision_analysis table for AI quality monitoring")

    def _migrate_v21_add_game_id_to_opponent_models(self, conn: sqlite3.Connection) -> None:
        """Migration v21: Add game_id to opponent_models and memorable_hands.

        This enables game-specific opponent tracking while preserving cross-game learning capability.
        """
        # Check if game_id column exists in opponent_models
        cursor = conn.execute("PRAGMA table_info(opponent_models)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'game_id' not in columns:
            conn.execute("ALTER TABLE opponent_models ADD COLUMN game_id TEXT")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_opponent_models_game
                ON opponent_models(game_id)
            """)
            # Update unique constraint by recreating table (SQLite limitation)
            # For now, just add the column - uniqueness will be (game_id, observer, opponent)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_opponent_models_unique
                ON opponent_models(game_id, observer_name, opponent_name)
            """)
            logger.info("Added game_id column to opponent_models")

        # Check if game_id column exists in memorable_hands
        cursor = conn.execute("PRAGMA table_info(memorable_hands)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'game_id' not in columns:
            conn.execute("ALTER TABLE memorable_hands ADD COLUMN game_id TEXT")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memorable_hands_game
                ON memorable_hands(game_id)
            """)
            logger.info("Added game_id column to memorable_hands")

        logger.info("Migration v21 complete: opponent_models now supports game-specific tracking")

    def _migrate_v22_add_position_equity(self, conn: sqlite3.Connection) -> None:
        """Migration v22: Add position-based equity fields to player_decision_analysis.

        Adds equity_vs_ranges for position-aware equity calculation alongside
        the existing random-based equity.
        """
        # Check if columns exist
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'equity_vs_ranges' not in columns:
            conn.execute("""
                ALTER TABLE player_decision_analysis ADD COLUMN equity_vs_ranges REAL
            """)
            logger.info("Added equity_vs_ranges column to player_decision_analysis")

        if 'opponent_positions' not in columns:
            conn.execute("""
                ALTER TABLE player_decision_analysis ADD COLUMN opponent_positions TEXT
            """)
            logger.info("Added opponent_positions column to player_decision_analysis")

        logger.info("Migration v22 complete: position-based equity fields added")

    def _migrate_v23_add_player_position(self, conn: sqlite3.Connection) -> None:
        """Migration v23: Add player_position to track hero's table position."""
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'player_position' not in columns:
            conn.execute("""
                ALTER TABLE player_decision_analysis ADD COLUMN player_position TEXT
            """)
            logger.info("Added player_position column to player_decision_analysis")

        logger.info("Migration v23 complete: player_position added")

    def _migrate_v24_add_prompt_versioning(self, conn: sqlite3.Connection) -> None:
        """Migration v24: Add prompt version tracking to prompt_captures.

        Tracks which version of a prompt template was used, plus a hash
        for detecting unversioned changes.
        """
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'prompt_template' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN prompt_template TEXT")
            logger.info("Added prompt_template column to prompt_captures")

        if 'prompt_version' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN prompt_version TEXT")
            logger.info("Added prompt_version column to prompt_captures")

        if 'prompt_hash' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN prompt_hash TEXT")
            logger.info("Added prompt_hash column to prompt_captures")

        logger.info("Migration v24 complete: prompt versioning added")

    def _migrate_v25_add_opponent_notes(self, conn: sqlite3.Connection) -> None:
        """Migration v25: Add notes column to opponent_models for player observations.

        Stores observations like "caught bluffing twice", "folds to 3-bets".
        """
        cursor = conn.execute("PRAGMA table_info(opponent_models)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'notes' not in columns:
            conn.execute("ALTER TABLE opponent_models ADD COLUMN notes TEXT")
            logger.info("Added notes column to opponent_models")

        logger.info("Migration v25 complete: opponent notes added")

    def _migrate_v26_add_debug_capture(self, conn: sqlite3.Connection) -> None:
        """Migration v26: Add debug_capture_enabled column to games table.

        Persists the debug capture toggle state so it survives game reloads.
        Defaults to FALSE (off).
        """
        cursor = conn.execute("PRAGMA table_info(games)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'debug_capture_enabled' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN debug_capture_enabled BOOLEAN DEFAULT 0")
            logger.info("Added debug_capture_enabled column to games table")

        logger.info("Migration v26 complete: debug_capture_enabled added")

    def _migrate_v27_fix_opponent_models_constraint(self, conn: sqlite3.Connection) -> None:
        """Migration v27: Fix opponent_models unique constraint to include game_id.

        The original table had UNIQUE(observer_name, opponent_name) which prevented
        the same observer from tracking the same opponent across different games.
        This migration recreates the table with UNIQUE(game_id, observer_name, opponent_name).
        """
        # Create new table with correct constraint
        conn.execute("""
            CREATE TABLE IF NOT EXISTS opponent_models_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT,
                observer_name TEXT NOT NULL,
                opponent_name TEXT NOT NULL,
                hands_observed INTEGER DEFAULT 0,
                vpip REAL DEFAULT 0.5,
                pfr REAL DEFAULT 0.5,
                aggression_factor REAL DEFAULT 1.0,
                fold_to_cbet REAL DEFAULT 0.5,
                bluff_frequency REAL DEFAULT 0.3,
                showdown_win_rate REAL DEFAULT 0.5,
                recent_trend TEXT,
                notes TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(game_id, observer_name, opponent_name)
            )
        """)

        # Copy existing data (preserving all columns)
        conn.execute("""
            INSERT INTO opponent_models_new (
                id, game_id, observer_name, opponent_name, hands_observed,
                vpip, pfr, aggression_factor, fold_to_cbet,
                bluff_frequency, showdown_win_rate, recent_trend, notes, last_updated
            )
            SELECT
                id, game_id, observer_name, opponent_name, hands_observed,
                vpip, pfr, aggression_factor, fold_to_cbet,
                bluff_frequency, showdown_win_rate, recent_trend, notes, last_updated
            FROM opponent_models
        """)

        # Drop old table and rename new one
        conn.execute("DROP TABLE opponent_models")
        conn.execute("ALTER TABLE opponent_models_new RENAME TO opponent_models")

        # Recreate indexes (without the old broken unique constraint)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opponent_models_observer
            ON opponent_models(observer_name)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_opponent_models_game
            ON opponent_models(game_id)
        """)

        logger.info("Migration v27 complete: opponent_models constraint fixed")

    def _migrate_v28_add_full_image_column(self, conn: sqlite3.Connection) -> None:
        """Migration v28: Add full_image_data column for storing uncropped avatar images.

        This allows storing the original full-size image alongside the circular icon.
        The full image is used for context-aware CSS cropping on mobile.
        """
        cursor = conn.execute("PRAGMA table_info(avatar_images)")
        columns = {row[1] for row in cursor.fetchall()}

        if 'full_image_data' not in columns:
            conn.execute("ALTER TABLE avatar_images ADD COLUMN full_image_data BLOB")
            conn.execute("ALTER TABLE avatar_images ADD COLUMN full_width INTEGER")
            conn.execute("ALTER TABLE avatar_images ADD COLUMN full_height INTEGER")
            conn.execute("ALTER TABLE avatar_images ADD COLUMN full_file_size INTEGER")
            logger.info("Added full_image_data columns to avatar_images table")

        logger.info("Migration v28 complete: full_image_data support added")

    def _migrate_v29_add_tournament_tracker(self, conn: sqlite3.Connection) -> None:
        """Migration v29: Add tournament_tracker table for persisting elimination history.

        This fixes the bug where elimination history was lost on game reload,
        causing incorrect tournament standings display.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_tracker (
                game_id TEXT PRIMARY KEY,
                tracker_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tournament_tracker_game
            ON tournament_tracker(game_id)
        """)

        logger.info("Migration v29 complete: tournament_tracker table added")

    def _migrate_v30_add_prompt_capture_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v30: Add raw_request and reasoning columns to prompt_captures.

        These columns were added to the INSERT statement but missing from schema:
        - raw_request: Full messages array sent to LLM (for debugging message history)
        - reasoning_effort: LLM reasoning effort setting used
        - original_request_id: Vendor request ID for correlation
        """
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}

        if 'raw_request' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN raw_request TEXT")
            logger.info("Added raw_request column to prompt_captures")

        if 'reasoning_effort' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN reasoning_effort TEXT")
            logger.info("Added reasoning_effort column to prompt_captures")

        if 'original_request_id' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN original_request_id TEXT")
            logger.info("Added original_request_id column to prompt_captures")

        logger.info("Migration v30 complete: prompt_captures columns added")

    def _migrate_v31_add_provider_pricing(self, conn: sqlite3.Connection) -> None:
        """Migration v31: Legacy - pricing now in config/pricing.yaml."""
        # No-op: Groq and Claude 4.5 pricing now loaded from config/pricing.yaml
        pass

    def _migrate_v32_add_more_providers(self, conn: sqlite3.Connection) -> None:
        """Migration v32: Legacy - pricing now in config/pricing.yaml."""
        # No-op: DeepSeek, Mistral, and Google pricing now loaded from config/pricing.yaml
        pass

    def _migrate_v33_add_provider_to_captures(self, conn: sqlite3.Connection) -> None:
        """Migration v33: Add provider column to prompt_captures.

        Enables tracking which LLM provider was used for each captured decision.
        """
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}

        if 'provider' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN provider TEXT DEFAULT 'openai'")
            logger.info("Added provider column to prompt_captures")

        logger.info("Migration v33 complete: Added provider to prompt_captures")

    def _migrate_v34_add_llm_configs(self, conn: sqlite3.Connection) -> None:
        """Migration v34: Add llm_configs_json column to games table.

        Stores per-player LLM provider configurations so they persist across
        game reloads and page refreshes.
        """
        cursor = conn.execute("PRAGMA table_info(games)")
        columns = {row[1] for row in cursor}

        if 'llm_configs_json' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN llm_configs_json TEXT")
            logger.info("Added llm_configs_json column to games table")

        logger.info("Migration v34 complete: Added llm_configs_json to games")

    def _migrate_v35_add_provider_index(self, conn: sqlite3.Connection) -> None:
        """Migration v35: Add index on provider column in prompt_captures.

        Improves query performance when filtering captures by provider.
        """
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider
            ON prompt_captures(provider)
        """)
        logger.info("Migration v35 complete: Added index on prompt_captures.provider")

    def _migrate_v36_add_xai_pricing(self, conn: sqlite3.Connection) -> None:
        """Migration v36: Legacy - pricing now in config/pricing.yaml."""
        # No-op: xAI Grok pricing now loaded from config/pricing.yaml
        pass

    def _migrate_v37_add_gpt5_pricing(self, conn: sqlite3.Connection) -> None:
        """Migration v37: Legacy - pricing now in config/pricing.yaml."""
        # No-op: GPT-5 pricing now loaded from config/pricing.yaml
        pass

    def _migrate_v38_add_enabled_models(self, conn: sqlite3.Connection) -> None:
        """Migration v38: Add enabled_models table for model management.

        This table allows admins to enable/disable models in the game UI
        without code changes. Models are seeded from PROVIDER_MODELS config.
        """
        # Create enabled_models table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enabled_models (
                id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                display_name TEXT,
                notes TEXT,
                supports_reasoning INTEGER DEFAULT 0,
                supports_json_mode INTEGER DEFAULT 1,
                supports_image_gen INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(provider, model)
            )
        """)

        # Create index for fast lookups
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_enabled_models_provider
            ON enabled_models(provider, enabled)
        """)

        # Seed from PROVIDER_MODELS config
        # Import here to avoid circular imports
        from core.llm.config import PROVIDER_MODELS, PROVIDER_CAPABILITIES, DEFAULT_ENABLED_MODELS

        for provider, models in PROVIDER_MODELS.items():
            capabilities = PROVIDER_CAPABILITIES.get(provider, {})
            supports_reasoning = 1 if capabilities.get('supports_reasoning', False) else 0
            supports_json = 1 if capabilities.get('supports_json_mode', True) else 0
            supports_image = 1 if capabilities.get('supports_image_generation', False) else 0

            # Determine which models should be enabled by default
            # If DEFAULT_ENABLED_MODELS is empty/None, enable all (backwards compatible)
            # Otherwise, only enable models explicitly listed for this provider
            enabled_whitelist = DEFAULT_ENABLED_MODELS.get(provider, []) if DEFAULT_ENABLED_MODELS else []
            enable_all = not DEFAULT_ENABLED_MODELS

            for sort_order, model in enumerate(models):
                enabled = 1 if (enable_all or model in enabled_whitelist) else 0
                conn.execute("""
                    INSERT OR IGNORE INTO enabled_models
                    (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (provider, model, enabled, supports_reasoning, supports_json, supports_image, sort_order))

        logger.info("Migration v38 complete: Added enabled_models table with seeded data")

    def _migrate_v39_playground_capture_support(self, conn: sqlite3.Connection) -> None:
        """Migration v39: Enable prompt_captures for non-game playground captures.

        Changes:
        1. Makes game_id nullable (for non-game LLM calls like commentary, personality gen)
        2. Adds call_type column to identify capture source
        3. Changes ON DELETE CASCADE to ON DELETE SET NULL for game_id FK

        SQLite doesn't support ALTER TABLE to change constraints, so we recreate the table.
        """
        # Check if call_type already exists (idempotency)
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        columns = {row[1] for row in cursor}

        if 'call_type' in columns:
            logger.info("Migration v39: call_type already exists, skipping")
            return

        # Create new table with nullable game_id and call_type
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_captures_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                game_id TEXT,
                player_name TEXT,
                hand_number INTEGER,
                phase TEXT,
                action_taken TEXT,
                system_prompt TEXT NOT NULL,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                pot_total INTEGER,
                cost_to_call INTEGER,
                pot_odds REAL,
                player_stack INTEGER,
                community_cards TEXT,
                player_hand TEXT,
                valid_actions TEXT,
                raise_amount INTEGER,
                model TEXT,
                latency_ms INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                tags TEXT,
                notes TEXT,
                conversation_history TEXT,
                raw_api_response TEXT,
                prompt_template TEXT,
                prompt_version TEXT,
                prompt_hash TEXT,
                raw_request TEXT,
                reasoning_effort TEXT,
                original_request_id TEXT,
                provider TEXT DEFAULT 'openai',
                call_type TEXT,
                FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE SET NULL
            )
        """)

        # Copy existing data
        conn.execute("""
            INSERT INTO prompt_captures_new (
                id, created_at, game_id, player_name, hand_number, phase, action_taken,
                system_prompt, user_message, ai_response,
                pot_total, cost_to_call, pot_odds, player_stack,
                community_cards, player_hand, valid_actions, raise_amount,
                model, latency_ms, input_tokens, output_tokens,
                tags, notes, conversation_history, raw_api_response,
                prompt_template, prompt_version, prompt_hash,
                raw_request, reasoning_effort, original_request_id, provider
            )
            SELECT
                id, created_at, game_id, player_name, hand_number, phase, action_taken,
                system_prompt, user_message, ai_response,
                pot_total, cost_to_call, pot_odds, player_stack,
                community_cards, player_hand, valid_actions, raise_amount,
                model, latency_ms, input_tokens, output_tokens,
                tags, notes, conversation_history, raw_api_response,
                prompt_template, prompt_version, prompt_hash,
                raw_request, reasoning_effort, original_request_id, provider
            FROM prompt_captures
        """)

        # Drop old table and rename new one
        conn.execute("DROP TABLE prompt_captures")
        conn.execute("ALTER TABLE prompt_captures_new RENAME TO prompt_captures")

        # Recreate indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type)")

        logger.info("Migration v39 complete: prompt_captures now supports playground captures")

    def _migrate_v40_add_prompt_config(self, conn: sqlite3.Connection) -> None:
        """Migration v40: Add prompt_config_json column to controller_state.

        This column stores the PromptConfig for toggling prompt components on/off.
        """
        cursor = conn.execute("PRAGMA table_info(controller_state)")
        columns = {row[1] for row in cursor}

        if 'prompt_config_json' not in columns:
            conn.execute("ALTER TABLE controller_state ADD COLUMN prompt_config_json TEXT")
            logger.info("Added prompt_config_json column to controller_state")

        logger.info("Migration v40 complete: prompt_config support added")

    def _migrate_v41_add_hand_commentary(self, conn: sqlite3.Connection) -> None:
        """Migration v41: Add hand_commentary table for AI reflection persistence.

        This table stores AI commentary (strategic_reflection, opponent_observations)
        to enable feeding past insights back into future decisions.
        """
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hand_commentary'"
        )
        if cursor.fetchone() is None:
            conn.execute("""
                CREATE TABLE hand_commentary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER NOT NULL,
                    player_name TEXT NOT NULL,
                    emotional_reaction TEXT,
                    strategic_reflection TEXT,
                    opponent_observations TEXT,
                    key_insight TEXT,
                    decision_plans TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number, player_name)
                )
            """)
            conn.execute("""
                CREATE INDEX idx_hand_commentary_game
                ON hand_commentary(game_id, player_name)
            """)
            conn.execute("""
                CREATE INDEX idx_hand_commentary_player_recent
                ON hand_commentary(game_id, player_name, hand_number DESC)
            """)
            logger.info("Created hand_commentary table with indices")

        logger.info("Migration v41 complete: hand_commentary table added")

    def _migrate_v42_schema_consolidation(self, conn: sqlite3.Connection) -> None:
        """Migration v42: Schema consolidation marker.

        This migration marks the schema consolidation where:
        - All 23 tables are now defined in _init_db()
        - Migrations v1-v41 are now no-ops (they've already run on existing DBs)
        - Pricing data is loaded from config/pricing.yaml via pricing_loader

        For existing databases (at v41), this is a no-op marker.
        For new databases, _init_db() creates all tables, then this runs.
        """
        logger.info("Migration v42 complete: Schema consolidation marker applied")

    def _migrate_v43_add_experiments(self, conn: sqlite3.Connection) -> None:
        """Migration v43: Add experiments and experiment_games tables.

        These tables enable experiment tracking for AI tournaments:
        - experiments: Stores experiment metadata, config, and summary
        - experiment_games: Links games to experiments with variant info
        """
        # Create experiments table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                hypothesis TEXT,
                tags TEXT,
                notes TEXT,
                config_json TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                summary_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_name ON experiments(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)")

        # Create experiment_games table if it doesn't exist
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER NOT NULL,
                game_id TEXT NOT NULL,
                variant TEXT,
                variant_config_json TEXT,
                tournament_number INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE,
                FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE,
                UNIQUE(experiment_id, game_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experiment_games_experiment ON experiment_games(experiment_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experiment_games_game ON experiment_games(game_id)")

        logger.info("Migration v43 complete: Added experiments and experiment_games tables")

    def _migrate_v44_add_app_settings(self, conn: sqlite3.Connection) -> None:
        """Migration v44: Add app_settings table for dynamic configuration.

        This allows settings like LLM_PROMPT_CAPTURE and LLM_PROMPT_RETENTION_DAYS
        to be changed from the admin dashboard without restarting the server.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("Migration v44 complete: app_settings table created")

    def _migrate_v45_add_users_table(self, conn: sqlite3.Connection) -> None:
        """Migration v45: Add users table for Google OAuth authentication.

        Creates the users table for storing authenticated user information
        from Google OAuth. Supports linking guest accounts to Google accounts.
        """
        # Check if table already exists (for fresh databases created with v45 _init_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        )
        if cursor.fetchone():
            logger.info("Users table already exists (created in _init_db), skipping creation")
        else:
            conn.execute("""
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    picture TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    linked_guest_id TEXT,
                    is_guest BOOLEAN DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX idx_users_email ON users(email)")
            conn.execute("CREATE INDEX idx_users_linked_guest ON users(linked_guest_id)")
            logger.info("Created users table with indices")

        logger.info("Migration v45 complete: Users table added")

    def _migrate_v46_add_error_message(self, conn: sqlite3.Connection) -> None:
        """Migration v46: Add error_message column to api_usage table.

        Stores the full error message when API calls fail, enabling better
        debugging and error analysis in experiments.
        """
        # Check if column already exists
        cursor = conn.execute("PRAGMA table_info(api_usage)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'error_message' not in columns:
            conn.execute("ALTER TABLE api_usage ADD COLUMN error_message TEXT")
            logger.info("Added error_message column to api_usage table")
        else:
            logger.info("error_message column already exists in api_usage table")

        logger.info("Migration v46 complete: error_message column added")

    def _migrate_v47_add_pollinations_models(self, conn: sqlite3.Connection) -> None:
        """Migration v47: Add Pollinations image models to enabled_models table.

        Pollinations.ai is an image-only provider with extremely cheap pricing (~$0.0002/image).
        This migration adds Pollinations models to the enabled_models table with:
        - flux and flux-realism enabled by default (most commonly used)
        - All models marked as image-only (supports_image_gen=1, supports_reasoning=0, supports_json_mode=0)

        Note: We use INSERT OR REPLACE to ensure correct enabled status, since v38 may have
        already created these rows with enabled=0 (for new databases where Pollinations
        is in PROVIDER_MODELS but not in DEFAULT_ENABLED_MODELS).
        """
        from core.llm.config import POLLINATIONS_AVAILABLE_MODELS

        # Models that should be enabled by default (cheapest, most reliable)
        default_enabled = {"flux", "flux-realism"}

        for sort_order, model in enumerate(POLLINATIONS_AVAILABLE_MODELS):
            enabled = 1 if model in default_enabled else 0
            # Use INSERT OR REPLACE to update existing rows (from v38) with correct enabled status
            conn.execute("""
                INSERT OR REPLACE INTO enabled_models
                (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, ("pollinations", model, enabled, 0, 0, 1, sort_order))

        logger.info("Migration v44 complete: Added Pollinations image models to enabled_models")

    def save_game(self, game_id: str, state_machine: PokerStateMachine,
                  owner_id: Optional[str] = None, owner_name: Optional[str] = None,
                  llm_configs: Optional[Dict] = None) -> None:
        """Save a game state to the database.

        Args:
            game_id: The game identifier
            state_machine: The game's state machine
            owner_id: The owner/user ID
            owner_name: The owner's display name
            llm_configs: Dict with 'player_llm_configs' and 'default_llm_config'
        """
        game_state = state_machine.game_state

        # Convert game state to dict and then to JSON
        state_dict = self._prepare_state_for_save(game_state)
        state_dict['current_phase'] = state_machine.current_phase.value

        game_json = json.dumps(state_dict)
        llm_configs_json = json.dumps(llm_configs) if llm_configs else None

        with sqlite3.connect(self.db_path) as conn:
            # Use ON CONFLICT DO UPDATE to preserve columns not being updated
            # (like debug_capture_enabled) instead of INSERT OR REPLACE which
            # deletes and re-inserts, resetting unspecified columns to defaults
            conn.execute("""
                INSERT INTO games
                (game_id, updated_at, phase, num_players, pot_size, game_state_json, owner_id, owner_name, llm_configs_json)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP,
                    phase = excluded.phase,
                    num_players = excluded.num_players,
                    pot_size = excluded.pot_size,
                    game_state_json = excluded.game_state_json,
                    owner_id = excluded.owner_id,
                    owner_name = excluded.owner_name,
                    llm_configs_json = COALESCE(excluded.llm_configs_json, games.llm_configs_json)
            """, (
                game_id,
                state_machine.current_phase.value,
                len(game_state.players),
                game_state.pot['total'],
                game_json,
                owner_id,
                owner_name,
                llm_configs_json
            ))
    
    def load_game(self, game_id: str) -> Optional[PokerStateMachine]:
        """Load a game state from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM games WHERE game_id = ?", 
                (game_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # Parse the JSON and recreate the game state
            state_dict = json.loads(row['game_state_json'])
            game_state = self._restore_state_from_dict(state_dict)

            # Restore the phase - handle both int and string values
            try:
                phase_value = state_dict.get('current_phase', 0)
                if isinstance(phase_value, str):
                    phase_value = int(phase_value)
                phase = PokerPhase(phase_value)
            except (ValueError, KeyError):
                print(f"Warning: Could not restore phase {state_dict.get('current_phase')}, using INITIALIZING_HAND")
                phase = PokerPhase.INITIALIZING_HAND

            # Create state machine with the loaded state and phase
            return PokerStateMachine.from_saved_state(game_state, phase)

    def load_llm_configs(self, game_id: str) -> Optional[Dict]:
        """Load LLM configs for a game.

        Args:
            game_id: The game identifier

        Returns:
            Dict with 'player_llm_configs' and 'default_llm_config', or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT llm_configs_json FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()

            if not row or not row['llm_configs_json']:
                return None

            return json.loads(row['llm_configs_json'])

    def save_tournament_tracker(self, game_id: str, tracker) -> None:
        """Save tournament tracker state to the database.

        Args:
            game_id: The game identifier
            tracker: TournamentTracker instance or dict from to_dict()
        """
        # Convert to dict if it's a TournamentTracker object
        if hasattr(tracker, 'to_dict'):
            tracker_dict = tracker.to_dict()
        else:
            tracker_dict = tracker

        tracker_json = json.dumps(tracker_dict)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO tournament_tracker (game_id, tracker_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(game_id) DO UPDATE SET
                    tracker_json = excluded.tracker_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (game_id, tracker_json))

    def load_tournament_tracker(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament tracker state from the database.

        Args:
            game_id: The game identifier

        Returns:
            Dict that can be passed to TournamentTracker.from_dict(), or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT tracker_json FROM tournament_tracker WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            return json.loads(row[0])

    def list_games(self, owner_id: Optional[str] = None, limit: int = 20) -> List[SavedGame]:
        """List saved games, most recently updated first. Filter by owner_id if provided."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if owner_id:
                cursor = conn.execute("""
                    SELECT * FROM games 
                    WHERE owner_id = ?
                    ORDER BY updated_at DESC 
                    LIMIT ?
                """, (owner_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT * FROM games 
                    ORDER BY updated_at DESC 
                    LIMIT ?
                """, (limit,))
            
            games = []
            for row in cursor:
                games.append(SavedGame(
                    game_id=row['game_id'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at']),
                    phase=row['phase'],
                    num_players=row['num_players'],
                    pot_size=row['pot_size'],
                    game_state_json=row['game_state_json'],
                    owner_id=row['owner_id'],
                    owner_name=row['owner_name']
                ))
            
            return games
    
    def count_user_games(self, owner_id: str) -> int:
        """Count how many games a user owns."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM games WHERE owner_id = ?
            """, (owner_id,))
            return cursor.fetchone()[0]

    # ==================== User Management Methods ====================

    def create_google_user(
        self,
        google_sub: str,
        email: str,
        name: str,
        picture: Optional[str] = None,
        linked_guest_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new user from Google OAuth.

        Args:
            google_sub: Google's unique subject identifier
            email: User's email address
            name: User's display name
            picture: URL to user's profile picture
            linked_guest_id: Optional guest ID this account was linked from

        Returns:
            Dict containing user data

        Raises:
            sqlite3.IntegrityError: If email already exists
        """
        user_id = f"google_{google_sub}"
        now = datetime.utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO users (id, email, name, picture, created_at, last_login, linked_guest_id, is_guest)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (user_id, email, name, picture, now, now, linked_guest_id))

        return {
            'id': user_id,
            'email': email,
            'name': name,
            'picture': picture,
            'is_guest': False,
            'created_at': now,
            'linked_guest_id': linked_guest_id
        }

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by their ID.

        Args:
            user_id: The user's unique identifier

        Returns:
            User dict if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by their email address.

        Args:
            email: The user's email address

        Returns:
            User dict if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_user_by_linked_guest(self, guest_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by the guest ID they were linked from.

        Args:
            guest_id: The original guest ID

        Returns:
            User dict if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM users WHERE linked_guest_id = ?",
                (guest_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def update_user_last_login(self, user_id: str) -> None:
        """Update the last login timestamp for a user.

        Args:
            user_id: The user's unique identifier
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), user_id)
            )

    def transfer_game_ownership(self, from_owner_id: str, to_owner_id: str, to_owner_name: str) -> int:
        """Transfer all games from one owner to another.

        Used when a guest links their account to Google OAuth.

        Args:
            from_owner_id: The current owner ID (e.g., guest_jeff)
            to_owner_id: The new owner ID (e.g., google_12345)
            to_owner_name: The new owner's display name

        Returns:
            Number of games transferred
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE games
                SET owner_id = ?, owner_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE owner_id = ?
            """, (to_owner_id, to_owner_name, from_owner_id))
            return cursor.rowcount

    def get_enabled_models(self) -> Dict[str, List[str]]:
        """Get all enabled models grouped by provider.

        Returns:
            Dict mapping provider name to list of enabled model names.
            Example: {'openai': ['gpt-4o', 'gpt-5-nano'], 'groq': ['llama-3.1-8b-instant']}
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT provider, model
                FROM enabled_models
                WHERE enabled = 1
                ORDER BY provider, sort_order
            """)
            result: Dict[str, List[str]] = {}
            for row in cursor.fetchall():
                provider = row['provider']
                if provider not in result:
                    result[provider] = []
                result[provider].append(row['model'])
            return result

    def get_all_enabled_models(self) -> List[Dict[str, Any]]:
        """Get all models with their enabled status.

        Returns:
            List of dicts with provider, model, enabled, display_name, etc.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, provider, model, enabled, display_name, notes,
                       supports_reasoning, supports_json_mode, supports_image_gen,
                       sort_order, created_at, updated_at
                FROM enabled_models
                ORDER BY provider, sort_order
            """)
            return [dict(row) for row in cursor.fetchall()]

    def update_model_enabled(self, model_id: int, enabled: bool) -> bool:
        """Update the enabled status of a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (1 if enabled else 0, model_id))
            return cursor.rowcount > 0

    def update_model_details(self, model_id: int, display_name: str = None, notes: str = None) -> bool:
        """Update display name and notes for a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET display_name = COALESCE(?, display_name),
                    notes = COALESCE(?, notes),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (display_name, notes, model_id))
            return cursor.rowcount > 0

    def delete_game(self, game_id: str) -> None:
        """Delete a game and all associated data."""
        with sqlite3.connect(self.db_path) as conn:
            # Delete all associated data (order matters for foreign keys)
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_messages WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
    
    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO game_messages (game_id, message_type, message_text)
                VALUES (?, ?, ?)
            """, (game_id, message_type, message_text))
    
    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM game_messages
                WHERE game_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (game_id, limit))

            messages = []
            for row in cursor:
                # Parse "sender: content" format back into separate fields
                text = row['message_text']
                if ': ' in text:
                    sender, content = text.split(': ', 1)
                else:
                    sender = 'System'
                    content = text
                messages.append({
                    'id': str(row['id']),
                    'timestamp': row['timestamp'],
                    'type': row['message_type'],
                    'sender': sender,
                    'content': content
                })

            return list(reversed(messages))  # Return in chronological order
    
    def _prepare_state_for_save(self, game_state: PokerGameState) -> Dict[str, Any]:
        """Prepare game state for JSON serialization."""
        state_dict = game_state.to_dict()
        
        # The to_dict() method already handles most serialization,
        # but we need to ensure all custom objects are properly converted
        return state_dict
    
    def _restore_state_from_dict(self, state_dict: Dict[str, Any]) -> PokerGameState:
        """Restore game state from dictionary."""
        # Reconstruct players
        players = []
        for player_data in state_dict['players']:
            # Reconstruct hand if present
            hand = None
            if player_data.get('hand'):
                try:
                    hand = self._deserialize_cards(player_data['hand'])
                except Exception as e:
                    logger.warning(f"Error deserializing hand for {player_data['name']}: {e}")
                    hand = None
            
            player = Player(
                name=player_data['name'],
                stack=player_data['stack'],
                is_human=player_data['is_human'],
                bet=player_data['bet'],
                hand=hand,
                is_all_in=player_data['is_all_in'],
                is_folded=player_data['is_folded'],
                has_acted=player_data['has_acted']
            )
            players.append(player)
        
        # Reconstruct deck
        try:
            deck = self._deserialize_cards(state_dict.get('deck', []))
        except Exception as e:
            logger.warning(f"Error deserializing deck: {e}")
            deck = tuple()
        
        # Reconstruct discard pile
        try:
            discard_pile = self._deserialize_cards(state_dict.get('discard_pile', []))
        except Exception as e:
            logger.warning(f"Error deserializing discard pile: {e}")
            discard_pile = tuple()
        
        # Reconstruct community cards
        try:
            community_cards = self._deserialize_cards(state_dict.get('community_cards', []))
        except Exception as e:
            logger.warning(f"Error deserializing community cards: {e}")
            community_cards = tuple()
        
        # Create the game state
        return PokerGameState(
            players=tuple(players),
            deck=deck,
            discard_pile=discard_pile,
            pot=state_dict['pot'],
            current_player_idx=state_dict['current_player_idx'],
            current_dealer_idx=state_dict['current_dealer_idx'],
            community_cards=community_cards,
            current_ante=state_dict['current_ante'],
            pre_flop_action_taken=state_dict['pre_flop_action_taken'],
            awaiting_action=state_dict['awaiting_action'],
            run_it_out=state_dict.get('run_it_out', False)
        )
    
    # AI State Persistence Methods
    def save_ai_player_state(self, game_id: str, player_name: str, 
                            messages: List[Dict[str, str]], 
                            personality_state: Dict[str, Any]) -> None:
        """Save AI player conversation history and personality state."""
        with sqlite3.connect(self.db_path) as conn:
            conversation_history = json.dumps(messages)
            personality_json = json.dumps(personality_state)
            
            conn.execute("""
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """, (game_id, player_name, conversation_history, personality_json))
    
    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT player_name, conversation_history, personality_state
                FROM ai_player_state
                WHERE game_id = ?
            """, (game_id,))
            
            ai_states = {}
            for row in cursor.fetchall():
                ai_states[row['player_name']] = {
                    'messages': json.loads(row['conversation_history']),
                    'personality_state': json.loads(row['personality_state'])
                }
            
            return ai_states
    
    def save_personality_snapshot(self, game_id: str, player_name: str, 
                                 hand_number: int, traits: Dict[str, Any], 
                                 pressure_levels: Optional[Dict[str, float]] = None) -> None:
        """Save a snapshot of personality state for elasticity tracking."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO personality_snapshots
                (player_name, game_id, hand_number, personality_traits, pressure_levels)
                VALUES (?, ?, ?, ?, ?)
            """, (
                player_name,
                game_id,
                hand_number,
                json.dumps(traits),
                json.dumps(pressure_levels or {})
            ))
    
    def save_personality(self, name: str, config: Dict[str, Any], source: str = 'ai_generated') -> None:
        """Save a personality configuration to the database."""
        # Extract elasticity_config if present in the main config
        elasticity_config = config.get('elasticity_config', {})
        
        # Remove elasticity_config from main config if it exists (to store separately)
        config_without_elasticity = {k: v for k, v in config.items() if k != 'elasticity_config'}
        
        with sqlite3.connect(self.db_path) as conn:
            # Check if elasticity_config column exists
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'elasticity_config' in columns:
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config_without_elasticity), json.dumps(elasticity_config), source))
            else:
                # Fallback for old schema
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, source, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config), source))
    
    def load_personality(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a personality configuration from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Check if elasticity_config column exists
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'elasticity_config' in columns:
                cursor = conn.execute("""
                    SELECT config_json, elasticity_config FROM personalities
                    WHERE name = ?
                """, (name,))
            else:
                cursor = conn.execute("""
                    SELECT config_json FROM personalities
                    WHERE name = ?
                """, (name,))
            
            row = cursor.fetchone()
            if row:
                # Increment usage counter
                conn.execute("""
                    UPDATE personalities 
                    SET times_used = times_used + 1 
                    WHERE name = ?
                """, (name,))
                
                config = json.loads(row['config_json'])
                
                # Add elasticity_config if available
                if 'elasticity_config' in columns and row['elasticity_config']:
                    config['elasticity_config'] = json.loads(row['elasticity_config'])
                
                return config
            
            return None
    
    def increment_personality_usage(self, name: str) -> None:
        """Increment the usage counter for a personality."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE personalities 
                SET times_used = times_used + 1 
                WHERE name = ?
            """, (name,))
    
    def list_personalities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all personalities with metadata."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT name, source, created_at, updated_at, times_used, is_generated
                FROM personalities
                ORDER BY times_used DESC, updated_at DESC
                LIMIT ?
            """, (limit,))
            
            personalities = []
            for row in cursor:
                personalities.append({
                    'name': row['name'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'times_used': row['times_used'],
                    'is_generated': bool(row['is_generated'])
                })
            
            return personalities
    
    def delete_personality(self, name: str) -> bool:
        """Delete a personality from the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM personalities WHERE name = ?",
                    (name,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting personality {name}: {e}")
            return False

    # Emotional State Persistence Methods
    def save_emotional_state(self, game_id: str, player_name: str,
                             emotional_state) -> None:
        """Save emotional state for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            emotional_state: EmotionalState object or dict from EmotionalState.to_dict()
        """
        # Convert to dict if it's an EmotionalState object
        if hasattr(emotional_state, 'to_dict'):
            state_dict = emotional_state.to_dict()
        else:
            state_dict = emotional_state

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO emotional_state
                (game_id, player_name, valence, arousal, control, focus,
                 narrative, inner_voice, generated_at_hand, source_events,
                 metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                player_name,
                state_dict.get('valence', 0.0),
                state_dict.get('arousal', 0.5),
                state_dict.get('control', 0.5),
                state_dict.get('focus', 0.5),
                state_dict.get('narrative', ''),
                state_dict.get('inner_voice', ''),
                state_dict.get('generated_at_hand', 0),
                json.dumps(state_dict.get('source_events', [])),
                json.dumps({
                    'created_at': state_dict.get('created_at'),
                    'used_fallback': state_dict.get('used_fallback', False)
                })
            ))

    def load_emotional_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a player.

        Returns:
            Dict suitable for EmotionalState.from_dict(), or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM emotional_state
                WHERE game_id = ? AND player_name = ?
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}

            return {
                'valence': row['valence'],
                'arousal': row['arousal'],
                'control': row['control'],
                'focus': row['focus'],
                'narrative': row['narrative'] or '',
                'inner_voice': row['inner_voice'] or '',
                'generated_at_hand': row['generated_at_hand'],
                'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                'created_at': metadata.get('created_at'),
                'used_fallback': metadata.get('used_fallback', False)
            }

    def load_all_emotional_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game.

        Returns:
            Dict mapping player_name -> emotional_state dict
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM emotional_state
                WHERE game_id = ?
            """, (game_id,))

            states = {}
            for row in cursor.fetchall():
                metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}
                states[row['player_name']] = {
                    'valence': row['valence'],
                    'arousal': row['arousal'],
                    'control': row['control'],
                    'focus': row['focus'],
                    'narrative': row['narrative'] or '',
                    'inner_voice': row['inner_voice'] or '',
                    'generated_at_hand': row['generated_at_hand'],
                    'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                    'created_at': metadata.get('created_at'),
                    'used_fallback': metadata.get('used_fallback', False)
                }

            return states

    # Controller State Persistence Methods (Tilt + ElasticPersonality + PromptConfig)
    def save_controller_state(self, game_id: str, player_name: str,
                              psychology: Dict[str, Any],
                              prompt_config: Optional[Dict[str, Any]] = None) -> None:
        """Save unified psychology state and prompt config for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            psychology: Dict from PlayerPsychology.to_dict()
            prompt_config: Dict from PromptConfig.to_dict() (optional)
        """
        # Extract components from unified psychology
        tilt_state = psychology.get('tilt')
        elastic_personality = psychology.get('elastic')

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO controller_state
                (game_id, player_name, tilt_state_json, elastic_personality_json, prompt_config_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                player_name,
                json.dumps(tilt_state) if tilt_state else None,
                json.dumps(elastic_personality) if elastic_personality else None,
                json.dumps(prompt_config) if prompt_config else None
            ))

    def load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player.

        Returns:
            Dict with 'tilt_state', 'elastic_personality', and 'prompt_config' keys, or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT tilt_state_json, elastic_personality_json, prompt_config_json
                FROM controller_state
                WHERE game_id = ? AND player_name = ?
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            # Handle prompt_config_json which may not exist in older databases
            prompt_config = None
            try:
                if row['prompt_config_json']:
                    prompt_config = json.loads(row['prompt_config_json'])
            except (KeyError, IndexError):
                # Column doesn't exist in older schema
                logger.warning(f"prompt_config_json column not found for {player_name}, using defaults")

            return {
                'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None,
                'prompt_config': prompt_config
            }

    def load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game.

        Returns:
            Dict mapping player_name -> controller state dict
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT player_name, tilt_state_json, elastic_personality_json, prompt_config_json
                FROM controller_state
                WHERE game_id = ?
            """, (game_id,))

            states = {}
            for row in cursor.fetchall():
                # Handle prompt_config_json which may not exist in older databases
                prompt_config = None
                try:
                    if row['prompt_config_json']:
                        prompt_config = json.loads(row['prompt_config_json'])
                except (KeyError, IndexError):
                    pass  # Column doesn't exist in older schema

                states[row['player_name']] = {
                    'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                    'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None,
                    'prompt_config': prompt_config
                }

            return states

    def delete_emotional_state_for_game(self, game_id: str) -> None:
        """Delete all emotional states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM emotional_state WHERE game_id = ?", (game_id,))

    def delete_controller_state_for_game(self, game_id: str) -> None:
        """Delete all controller states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM controller_state WHERE game_id = ?", (game_id,))

    # Opponent Model Persistence Methods
    def save_opponent_models(self, game_id: str, opponent_model_manager) -> None:
        """Save opponent models for a game.

        Args:
            game_id: The game identifier
            opponent_model_manager: OpponentModelManager instance or dict from to_dict()
        """
        # Convert to dict if it's an OpponentModelManager object
        if hasattr(opponent_model_manager, 'to_dict'):
            models_dict = opponent_model_manager.to_dict()
        else:
            models_dict = opponent_model_manager

        if not models_dict:
            return

        with sqlite3.connect(self.db_path) as conn:
            # Clear existing models for this game
            conn.execute("DELETE FROM opponent_models WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM memorable_hands WHERE game_id = ?", (game_id,))

            # Save each observer -> opponent -> model
            for observer_name, opponents in models_dict.items():
                for opponent_name, model_data in opponents.items():
                    tendencies = model_data.get('tendencies', {})

                    conn.execute("""
                        INSERT OR REPLACE INTO opponent_models
                        (game_id, observer_name, opponent_name, hands_observed,
                         vpip, pfr, aggression_factor, fold_to_cbet,
                         bluff_frequency, showdown_win_rate, recent_trend, notes, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        game_id,
                        observer_name,
                        opponent_name,
                        tendencies.get('hands_observed', 0),
                        tendencies.get('vpip', 0.5),
                        tendencies.get('pfr', 0.5),
                        tendencies.get('aggression_factor', 1.0),
                        tendencies.get('fold_to_cbet', 0.5),
                        tendencies.get('bluff_frequency', 0.3),
                        tendencies.get('showdown_win_rate', 0.5),
                        tendencies.get('recent_trend', 'stable'),
                        model_data.get('notes')
                    ))

                    # Save memorable hands
                    memorable_hands = model_data.get('memorable_hands', [])
                    for hand in memorable_hands:
                        conn.execute("""
                            INSERT INTO memorable_hands
                            (game_id, observer_name, opponent_name, hand_id,
                             memory_type, impact_score, narrative, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            game_id,
                            observer_name,
                            opponent_name,
                            hand.get('hand_id', 0),
                            hand.get('memory_type', ''),
                            hand.get('impact_score', 0.0),
                            hand.get('narrative', ''),
                            hand.get('timestamp', datetime.now().isoformat())
                        ))

            logger.debug(f"Saved opponent models for game {game_id}")

    def load_opponent_models(self, game_id: str) -> Dict[str, Any]:
        """Load opponent models for a game.

        Returns:
            Dict suitable for OpponentModelManager.from_dict(), or empty dict if not found
        """
        models_dict = {}

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Load all opponent models for this game
            cursor = conn.execute("""
                SELECT * FROM opponent_models WHERE game_id = ?
            """, (game_id,))

            for row in cursor.fetchall():
                observer_name = row['observer_name']
                opponent_name = row['opponent_name']

                if observer_name not in models_dict:
                    models_dict[observer_name] = {}

                # Build tendencies dict matching OpponentTendencies.to_dict() format
                tendencies = {
                    'hands_observed': row['hands_observed'],
                    'vpip': row['vpip'],
                    'pfr': row['pfr'],
                    'aggression_factor': row['aggression_factor'],
                    'fold_to_cbet': row['fold_to_cbet'],
                    'bluff_frequency': row['bluff_frequency'],
                    'showdown_win_rate': row['showdown_win_rate'],
                    'recent_trend': row['recent_trend'] or 'stable',
                    # Counters - we can't restore these perfectly, but we can estimate
                    '_vpip_count': int(row['vpip'] * row['hands_observed']),
                    '_pfr_count': int(row['pfr'] * row['hands_observed']),
                    '_bet_raise_count': 0,  # Can't restore
                    '_call_count': 0,  # Can't restore
                    '_fold_to_cbet_count': 0,  # Can't restore
                    '_cbet_faced_count': 0,  # Can't restore
                    '_showdowns': 0,  # Can't restore
                    '_showdowns_won': 0,  # Can't restore
                }

                models_dict[observer_name][opponent_name] = {
                    'observer': observer_name,
                    'opponent': opponent_name,
                    'tendencies': tendencies,
                    'memorable_hands': [],
                    'notes': row['notes'] if 'notes' in row.keys() else None
                }

            # Load memorable hands
            cursor = conn.execute("""
                SELECT * FROM memorable_hands WHERE game_id = ?
            """, (game_id,))

            for row in cursor.fetchall():
                observer_name = row['observer_name']
                opponent_name = row['opponent_name']

                if observer_name in models_dict and opponent_name in models_dict[observer_name]:
                    models_dict[observer_name][opponent_name]['memorable_hands'].append({
                        'hand_id': row['hand_id'],
                        'memory_type': row['memory_type'],
                        'opponent_name': opponent_name,
                        'impact_score': row['impact_score'],
                        'narrative': row['narrative'] or '',
                        'hand_summary': '',  # Not stored in DB
                        'timestamp': row['created_at'] or datetime.now().isoformat()
                    })

        if models_dict:
            logger.debug(f"Loaded opponent models for game {game_id}: {len(models_dict)} observers")

        return models_dict

    def delete_opponent_models_for_game(self, game_id: str) -> None:
        """Delete all opponent models for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM opponent_models WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM memorable_hands WHERE game_id = ?", (game_id,))

    # Hand History Persistence Methods
    def save_hand_history(self, recorded_hand) -> int:
        """Save a completed hand to the database.

        Args:
            recorded_hand: RecordedHand instance from hand_history.py

        Returns:
            The database ID of the saved hand
        """
        # Convert to dict if it's a RecordedHand object
        if hasattr(recorded_hand, 'to_dict'):
            hand_dict = recorded_hand.to_dict()
        else:
            hand_dict = recorded_hand

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT OR REPLACE INTO hand_history
                (game_id, hand_number, timestamp, players_json, hole_cards_json,
                 community_cards_json, actions_json, winners_json, pot_size, showdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hand_dict['game_id'],
                hand_dict['hand_number'],
                hand_dict.get('timestamp', datetime.now().isoformat()),
                json.dumps(hand_dict.get('players', [])),
                json.dumps(hand_dict.get('hole_cards', {})),
                json.dumps(hand_dict.get('community_cards', [])),
                json.dumps(hand_dict.get('actions', [])),
                json.dumps(hand_dict.get('winners', [])),
                hand_dict.get('pot_size', 0),
                hand_dict.get('was_showdown', False)
            ))

            hand_id = cursor.lastrowid
            logger.debug(f"Saved hand #{hand_dict['hand_number']} for game {hand_dict['game_id']}")
            return hand_id

    # Hand Commentary Persistence Methods
    def save_hand_commentary(self, game_id: str, hand_number: int, player_name: str,
                             commentary) -> None:
        """Save AI commentary for a completed hand.

        Args:
            game_id: The game identifier
            hand_number: The hand number
            player_name: The AI player's name
            commentary: HandCommentary instance or dict
        """
        if hasattr(commentary, 'to_dict'):
            c = commentary.to_dict()
        else:
            c = commentary

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO hand_commentary
                (game_id, hand_number, player_name, emotional_reaction,
                 strategic_reflection, opponent_observations, key_insight, decision_plans)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                hand_number,
                player_name,
                c.get('emotional_reaction'),
                c.get('strategic_reflection'),
                json.dumps(c.get('opponent_observations', [])),
                c.get('key_insight'),
                json.dumps(c.get('decision_plans', []))
            ))
            logger.debug(f"Saved commentary for {player_name} hand #{hand_number}")

    def get_recent_reflections(self, game_id: str, player_name: str,
                               limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent strategic reflections for a player.

        Args:
            game_id: The game identifier
            player_name: The AI player's name
            limit: Maximum number of reflections to return

        Returns:
            List of dicts with hand_number, strategic_reflection, key_insight
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT hand_number, strategic_reflection, key_insight,
                       opponent_observations
                FROM hand_commentary
                WHERE game_id = ? AND player_name = ?
                ORDER BY hand_number DESC
                LIMIT ?
            """, (game_id, player_name, limit))

            return [dict(row) for row in cursor.fetchall()]

    def get_hand_count(self, game_id: str) -> int:
        """Get the current hand count for a game.

        Args:
            game_id: The game identifier

        Returns:
            The maximum hand_number for this game, or 0 if no hands recorded
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT MAX(hand_number) FROM hand_history WHERE game_id = ?",
                (game_id,)
            )
            result = cursor.fetchone()[0]
            return result or 0

    def load_hand_history(self, game_id: str, limit: int = None) -> List[Dict[str, Any]]:
        """Load hand history for a game.

        Args:
            game_id: The game identifier
            limit: Optional limit on number of hands to load (most recent first)

        Returns:
            List of hand dicts suitable for RecordedHand.from_dict()
        """
        hands = []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            query = """
                SELECT * FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number DESC
            """
            if limit:
                query += f" LIMIT {limit}"

            cursor = conn.execute(query, (game_id,))

            for row in cursor.fetchall():
                hand = {
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'hand_number': row['hand_number'],
                    'timestamp': row['timestamp'],
                    'players': json.loads(row['players_json'] or '[]'),
                    'hole_cards': json.loads(row['hole_cards_json'] or '{}'),
                    'community_cards': json.loads(row['community_cards_json'] or '[]'),
                    'actions': json.loads(row['actions_json'] or '[]'),
                    'winners': json.loads(row['winners_json'] or '[]'),
                    'pot_size': row['pot_size'] or 0,
                    'was_showdown': bool(row['showdown'])
                }
                hands.append(hand)

        # Return in chronological order (oldest first)
        hands.reverse()

        if hands:
            logger.debug(f"Loaded {len(hands)} hands for game {game_id}")

        return hands

    def delete_hand_history_for_game(self, game_id: str) -> None:
        """Delete all hand history for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM hand_history WHERE game_id = ?", (game_id,))

    def get_session_stats(self, game_id: str, player_name: str) -> Dict[str, Any]:
        """Compute session statistics for a player from hand history.

        Args:
            game_id: The game identifier
            player_name: The player to get stats for

        Returns:
            Dict with session statistics:
                - hands_played: Total hands where player participated
                - hands_won: Hands where player won
                - total_winnings: Net chip change (positive = up, negative = down)
                - biggest_pot_won: Largest pot won
                - biggest_pot_lost: Largest pot lost at showdown
                - current_streak: 'winning', 'losing', or 'neutral'
                - streak_count: Length of current streak
                - recent_hands: List of last N hand summaries
        """
        stats = {
            'hands_played': 0,
            'hands_won': 0,
            'total_winnings': 0,
            'biggest_pot_won': 0,
            'biggest_pot_lost': 0,
            'current_streak': 'neutral',
            'streak_count': 0,
            'recent_hands': []
        }

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get all hands for this game, ordered by hand_number
            cursor = conn.execute("""
                SELECT hand_number, players_json, winners_json, actions_json, pot_size, showdown
                FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number ASC
            """, (game_id,))

            outcomes = []  # Track outcomes for streak calculation

            for row in cursor.fetchall():
                players = json.loads(row['players_json'] or '[]')
                winners = json.loads(row['winners_json'] or '[]')
                actions = json.loads(row['actions_json'] or '[]')
                pot_size = row['pot_size'] or 0

                # Check if player participated in this hand
                player_in_hand = any(p.get('name') == player_name for p in players)
                if not player_in_hand:
                    continue

                stats['hands_played'] += 1

                # Check if player won
                player_won = False
                amount_won = 0
                for winner in winners:
                    if winner.get('name') == player_name:
                        player_won = True
                        amount_won = winner.get('amount_won', 0)
                        break

                # Calculate amount lost (sum of player's bets)
                amount_bet = sum(
                    a.get('amount', 0)
                    for a in actions
                    if a.get('player_name') == player_name
                )

                # Determine outcome
                if player_won:
                    stats['hands_won'] += 1
                    stats['total_winnings'] += amount_won
                    outcomes.append('won')
                    if pot_size > stats['biggest_pot_won']:
                        stats['biggest_pot_won'] = pot_size
                else:
                    # Check if folded or lost at showdown
                    folded = any(
                        a.get('player_name') == player_name and a.get('action') == 'fold'
                        for a in actions
                    )
                    if folded:
                        outcomes.append('folded')
                        stats['total_winnings'] -= amount_bet
                    else:
                        # Lost at showdown
                        outcomes.append('lost')
                        stats['total_winnings'] -= amount_bet
                        if pot_size > stats['biggest_pot_lost']:
                            stats['biggest_pot_lost'] = pot_size

                # Build recent hand summary (keep last 5)
                if len(stats['recent_hands']) >= 5:
                    stats['recent_hands'].pop(0)

                outcome_str = outcomes[-1]
                if outcome_str == 'won':
                    summary = f"Hand {row['hand_number']}: Won ${amount_won}"
                elif outcome_str == 'folded':
                    summary = f"Hand {row['hand_number']}: Folded"
                else:
                    summary = f"Hand {row['hand_number']}: Lost ${amount_bet}"
                stats['recent_hands'].append(summary)

            # Calculate current streak from outcomes
            if outcomes:
                current = outcomes[-1]
                if current in ('won', 'lost'):
                    streak_type = 'winning' if current == 'won' else 'losing'
                    streak_count = 1
                    for outcome in reversed(outcomes[:-1]):
                        if (streak_type == 'winning' and outcome == 'won') or \
                           (streak_type == 'losing' and outcome == 'lost'):
                            streak_count += 1
                        else:
                            break
                    stats['current_streak'] = streak_type
                    stats['streak_count'] = streak_count

        return stats

    def get_session_context_for_prompt(self, game_id: str, player_name: str,
                                        max_recent: int = 3) -> str:
        """Get formatted session context string for AI prompts.

        Args:
            game_id: The game identifier
            player_name: The player to get context for
            max_recent: Maximum number of recent hands to include

        Returns:
            Formatted string suitable for injection into AI prompts
        """
        stats = self.get_session_stats(game_id, player_name)

        parts = []

        # Session overview
        if stats['hands_played'] > 0:
            win_rate = (stats['hands_won'] / stats['hands_played']) * 100
            parts.append(f"Session: {stats['hands_won']}/{stats['hands_played']} hands won ({win_rate:.0f}%)")

            # Net result
            if stats['total_winnings'] > 0:
                parts.append(f"Up ${stats['total_winnings']}")
            elif stats['total_winnings'] < 0:
                parts.append(f"Down ${abs(stats['total_winnings'])}")

            # Current streak (only show if 2+)
            if stats['streak_count'] >= 2:
                parts.append(f"On a {stats['streak_count']}-hand {stats['current_streak']} streak")

        # Recent hands
        recent = stats['recent_hands'][-max_recent:]
        if recent:
            parts.append("Recent: " + " | ".join(recent))

        return ". ".join(parts) if parts else ""

    # Tournament Results Persistence Methods
    def save_tournament_result(self, game_id: str, result: Dict[str, Any]) -> None:
        """Save tournament result when game completes.

        Args:
            game_id: The game identifier
            result: Dict with keys: winner_name, total_hands, biggest_pot,
                   starting_player_count, human_player_name, human_finishing_position,
                   started_at, standings (list of player standings)
        """
        with sqlite3.connect(self.db_path) as conn:
            # Save main tournament result
            conn.execute("""
                INSERT OR REPLACE INTO tournament_results
                (game_id, winner_name, total_hands, biggest_pot, starting_player_count,
                 human_player_name, human_finishing_position, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                result.get('winner_name'),
                result.get('total_hands', 0),
                result.get('biggest_pot', 0),
                result.get('starting_player_count'),
                result.get('human_player_name'),
                result.get('human_finishing_position'),
                result.get('started_at')
            ))

            # Save individual standings
            standings = result.get('standings', [])
            for standing in standings:
                conn.execute("""
                    INSERT OR REPLACE INTO tournament_standings
                    (game_id, player_name, is_human, finishing_position,
                     eliminated_by, eliminated_at_hand)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    game_id,
                    standing.get('player_name'),
                    standing.get('is_human', False),
                    standing.get('finishing_position'),
                    standing.get('eliminated_by'),
                    standing.get('eliminated_at_hand')
                ))

    def get_tournament_result(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament result for a completed game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get main result
            cursor = conn.execute("""
                SELECT * FROM tournament_results WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()

            if not row:
                return None

            # Get standings
            standings_cursor = conn.execute("""
                SELECT * FROM tournament_standings
                WHERE game_id = ?
                ORDER BY finishing_position ASC
            """, (game_id,))

            standings = []
            for s_row in standings_cursor.fetchall():
                standings.append({
                    'player_name': s_row['player_name'],
                    'is_human': bool(s_row['is_human']),
                    'finishing_position': s_row['finishing_position'],
                    'eliminated_by': s_row['eliminated_by'],
                    'eliminated_at_hand': s_row['eliminated_at_hand']
                })

            return {
                'game_id': row['game_id'],
                'winner_name': row['winner_name'],
                'total_hands': row['total_hands'],
                'biggest_pot': row['biggest_pot'],
                'starting_player_count': row['starting_player_count'],
                'human_player_name': row['human_player_name'],
                'human_finishing_position': row['human_finishing_position'],
                'started_at': row['started_at'],
                'ended_at': row['ended_at'],
                'standings': standings
            }

    def update_career_stats(self, player_name: str, tournament_result: Dict[str, Any]) -> None:
        """Update career stats for a player after a tournament.

        Args:
            player_name: The human player's name
            tournament_result: Dict with tournament result data
        """
        # Find the player's standing in this tournament
        standings = tournament_result.get('standings', [])
        player_standing = next(
            (s for s in standings if s.get('player_name') == player_name),
            None
        )

        if not player_standing:
            logger.warning(f"Player {player_name} not found in tournament standings")
            return

        finishing_position = player_standing.get('finishing_position', 0)
        is_winner = finishing_position == 1

        # Count eliminations by this player
        eliminations_this_game = sum(
            1 for s in standings
            if s.get('eliminated_by') == player_name
        )

        biggest_pot = tournament_result.get('biggest_pot', 0)

        with sqlite3.connect(self.db_path) as conn:
            # Check if player exists
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE player_name = ?
            """, (player_name,))
            existing = cursor.fetchone()

            if existing:
                # Update existing stats
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM player_career_stats WHERE player_name = ?
                """, (player_name,))
                row = cursor.fetchone()

                games_played = row['games_played'] + 1
                games_won = row['games_won'] + (1 if is_winner else 0)
                total_eliminations = row['total_eliminations'] + eliminations_this_game

                # Update best/worst finish
                best_finish = row['best_finish']
                if best_finish is None or finishing_position < best_finish:
                    best_finish = finishing_position

                worst_finish = row['worst_finish']
                if worst_finish is None or finishing_position > worst_finish:
                    worst_finish = finishing_position

                # Calculate new average
                old_avg = row['avg_finish'] or finishing_position
                avg_finish = ((old_avg * (games_played - 1)) + finishing_position) / games_played

                # Update biggest pot
                biggest_pot_ever = max(row['biggest_pot_ever'] or 0, biggest_pot)

                conn.execute("""
                    UPDATE player_career_stats
                    SET games_played = ?,
                        games_won = ?,
                        total_eliminations = ?,
                        best_finish = ?,
                        worst_finish = ?,
                        avg_finish = ?,
                        biggest_pot_ever = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE player_name = ?
                """, (
                    games_played, games_won, total_eliminations,
                    best_finish, worst_finish, avg_finish, biggest_pot_ever,
                    player_name
                ))
            else:
                # Insert new player
                conn.execute("""
                    INSERT INTO player_career_stats
                    (player_name, games_played, games_won, total_eliminations,
                     best_finish, worst_finish, avg_finish, biggest_pot_ever)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                """, (
                    player_name,
                    1 if is_winner else 0,
                    eliminations_this_game,
                    finishing_position,
                    finishing_position,
                    float(finishing_position),
                    biggest_pot
                ))

    def get_career_stats(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get career stats for a player."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE player_name = ?
            """, (player_name,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'player_name': row['player_name'],
                'games_played': row['games_played'],
                'games_won': row['games_won'],
                'total_eliminations': row['total_eliminations'],
                'best_finish': row['best_finish'],
                'worst_finish': row['worst_finish'],
                'avg_finish': row['avg_finish'],
                'biggest_pot_ever': row['biggest_pot_ever'],
                'win_rate': row['games_won'] / row['games_played'] if row['games_played'] > 0 else 0
            }

    def get_tournament_history(self, player_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get tournament history for a player."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT tr.*, ts.finishing_position, ts.eliminated_by
                FROM tournament_results tr
                JOIN tournament_standings ts ON tr.game_id = ts.game_id
                WHERE ts.player_name = ?
                ORDER BY tr.ended_at DESC
                LIMIT ?
            """, (player_name, limit))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'game_id': row['game_id'],
                    'winner_name': row['winner_name'],
                    'total_hands': row['total_hands'],
                    'biggest_pot': row['biggest_pot'],
                    'player_count': row['starting_player_count'],
                    'your_position': row['finishing_position'],
                    'eliminated_by': row['eliminated_by'],
                    'ended_at': row['ended_at']
                })

            return history

    def get_eliminated_personalities(self, player_name: str) -> List[Dict[str, Any]]:
        """Get all unique personalities eliminated by this player across all games.

        Returns a list of personalities with the first time they were eliminated.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Get unique personalities eliminated by this player, with first elimination date
            cursor = conn.execute("""
                SELECT
                    ts.player_name as personality_name,
                    MIN(tr.ended_at) as first_eliminated_at,
                    COUNT(*) as times_eliminated
                FROM tournament_standings ts
                JOIN tournament_results tr ON ts.game_id = tr.game_id
                WHERE ts.eliminated_by = ? AND ts.is_human = 0
                GROUP BY ts.player_name
                ORDER BY MIN(tr.ended_at) ASC
            """, (player_name,))

            personalities = []
            for row in cursor.fetchall():
                personalities.append({
                    'name': row['personality_name'],
                    'first_eliminated_at': row['first_eliminated_at'],
                    'times_eliminated': row['times_eliminated']
                })

            return personalities

    # Avatar Image Persistence Methods
    def save_avatar_image(self, personality_name: str, emotion: str,
                          image_data: bytes, width: int = 256, height: int = 256,
                          content_type: str = 'image/png',
                          full_image_data: Optional[bytes] = None,
                          full_width: Optional[int] = None,
                          full_height: Optional[int] = None) -> None:
        """Save an avatar image to the database.

        Args:
            personality_name: The personality name (e.g., "Bob Ross")
            emotion: The emotion (confident, happy, thinking, nervous, angry, shocked)
            image_data: The circular icon PNG bytes (256x256)
            width: Icon width (default 256)
            height: Icon height (default 256)
            content_type: MIME type (default image/png)
            full_image_data: Optional full uncropped image bytes for CSS cropping
            full_width: Width of full image
            full_height: Height of full image
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO avatar_images
                (personality_name, emotion, image_data, content_type, width, height, file_size,
                 full_image_data, full_width, full_height, full_file_size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                personality_name,
                emotion,
                image_data,
                content_type,
                width,
                height,
                len(image_data),
                full_image_data,
                full_width,
                full_height,
                len(full_image_data) if full_image_data else None
            ))

    def load_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load avatar image data from database.

        Args:
            personality_name: The personality name
            emotion: The emotion

        Returns:
            Image bytes if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT image_data FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            return row[0] if row else None

    def load_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load avatar image with metadata from database.

        Returns:
            Dict with image_data, content_type, width, height, file_size or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT image_data, content_type, width, height, file_size
                FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'image_data': row['image_data'],
                'content_type': row['content_type'],
                'width': row['width'],
                'height': row['height'],
                'file_size': row['file_size']
            }

    def load_full_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load full uncropped avatar image from database.

        Args:
            personality_name: The personality name
            emotion: The emotion

        Returns:
            Full image bytes if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT full_image_data FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def load_full_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load full avatar image with metadata from database.

        Returns:
            Dict with full_image_data, content_type, full_width, full_height, full_file_size or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT full_image_data, content_type, full_width, full_height, full_file_size
                FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            if not row or not row['full_image_data']:
                return None

            return {
                'image_data': row['full_image_data'],
                'content_type': row['content_type'],
                'width': row['full_width'],
                'height': row['full_height'],
                'file_size': row['full_file_size']
            }

    def has_full_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if a full avatar image exists for the given personality and emotion."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ? AND full_image_data IS NOT NULL
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def has_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the given personality and emotion."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def get_available_avatar_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have avatar images for a personality."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT emotion FROM avatar_images
                WHERE personality_name = ?
                ORDER BY emotion
            """, (personality_name,))
            return [row[0] for row in cursor.fetchall()]

    def has_all_avatar_emotions(self, personality_name: str) -> bool:
        """Check if a personality has all 6 emotion avatars."""
        emotions = self.get_available_avatar_emotions(personality_name)
        required = {'confident', 'happy', 'thinking', 'nervous', 'angry', 'shocked'}
        return required.issubset(set(emotions))

    def delete_avatar_images(self, personality_name: str) -> int:
        """Delete all avatar images for a personality.

        Returns:
            Number of images deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM avatar_images WHERE personality_name = ?
            """, (personality_name,))
            return cursor.rowcount

    def list_personalities_with_avatars(self) -> List[Dict[str, Any]]:
        """Get list of all personalities that have at least one avatar image.

        Returns:
            List of dicts with personality_name and emotion_count
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT personality_name, COUNT(*) as emotion_count
                FROM avatar_images
                GROUP BY personality_name
                ORDER BY personality_name
            """)
            return [
                {'personality_name': row['personality_name'], 'emotion_count': row['emotion_count']}
                for row in cursor.fetchall()
            ]

    def get_avatar_stats(self) -> Dict[str, Any]:
        """Get statistics about avatar images in the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Total count
            cursor = conn.execute("SELECT COUNT(*) as count FROM avatar_images")
            total_count = cursor.fetchone()['count']

            # Total size
            cursor = conn.execute("SELECT SUM(file_size) as total_size FROM avatar_images")
            total_size = cursor.fetchone()['total_size'] or 0

            # Unique personalities
            cursor = conn.execute("SELECT COUNT(DISTINCT personality_name) as count FROM avatar_images")
            personality_count = cursor.fetchone()['count']

            # Personalities with all 6 emotions
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM (
                    SELECT personality_name FROM avatar_images
                    GROUP BY personality_name
                    HAVING COUNT(DISTINCT emotion) = 6
                )
            """)
            complete_count = cursor.fetchone()['count']

            return {
                'total_images': total_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'personality_count': personality_count,
                'complete_personality_count': complete_count
            }

    # Personality Seeding Methods
    def seed_personalities_from_json(self, json_path: str, overwrite: bool = False) -> Dict[str, int]:
        """Seed database with personalities from JSON file.

        Args:
            json_path: Path to personalities.json file
            overwrite: If True, overwrite existing personalities

        Returns:
            Dict with counts: {'added': N, 'skipped': M, 'updated': P}
        """
        import json as json_module
        from pathlib import Path

        json_file = Path(json_path)
        if not json_file.exists():
            logger.warning(f"Personalities JSON file not found: {json_path}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': 'File not found'}

        try:
            with open(json_file, 'r') as f:
                data = json_module.load(f)
        except Exception as e:
            logger.error(f"Error reading personalities JSON: {e}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': str(e)}

        personalities = data.get('personalities', {})
        added = 0
        skipped = 0
        updated = 0

        for name, config in personalities.items():
            existing = self.load_personality(name)

            if existing and not overwrite:
                skipped += 1
                continue

            if existing:
                updated += 1
            else:
                added += 1

            self.save_personality(name, config, source='personalities.json')

        logger.info(f"Seeded personalities from JSON: {added} added, {updated} updated, {skipped} skipped")
        return {'added': added, 'skipped': skipped, 'updated': updated}

    # Prompt Capture Methods (for AI decision debugging)
    def save_prompt_capture(self, capture: Dict[str, Any]) -> int:
        """Save a prompt capture for debugging AI decisions.

        Args:
            capture: Dict containing capture data with keys:
                - game_id, player_name, hand_number, phase
                - system_prompt, user_message, ai_response
                - pot_total, cost_to_call, pot_odds, player_stack
                - community_cards, player_hand, valid_actions
                - action_taken, raise_amount
                - model, latency_ms, input_tokens, output_tokens

        Returns:
            The ID of the inserted capture.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO prompt_captures (
                    -- Identity
                    game_id, player_name, hand_number,
                    -- Game State
                    phase, pot_total, cost_to_call, pot_odds, player_stack,
                    community_cards, player_hand, valid_actions,
                    -- Decision
                    action_taken, raise_amount,
                    -- Prompts (INPUT)
                    system_prompt, conversation_history, user_message, raw_request,
                    -- Response (OUTPUT)
                    ai_response, raw_api_response,
                    -- LLM Config
                    provider, model, reasoning_effort,
                    -- Metrics
                    latency_ms, input_tokens, output_tokens,
                    -- Tracking
                    original_request_id,
                    -- Prompt Versioning
                    prompt_template, prompt_version, prompt_hash,
                    -- User Annotations
                    tags, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                # Identity
                capture.get('game_id'),
                capture.get('player_name'),
                capture.get('hand_number'),
                # Game State
                capture.get('phase'),
                capture.get('pot_total'),
                capture.get('cost_to_call'),
                capture.get('pot_odds'),
                capture.get('player_stack'),
                json.dumps(capture.get('community_cards')) if capture.get('community_cards') else None,
                json.dumps(capture.get('player_hand')) if capture.get('player_hand') else None,
                json.dumps(capture.get('valid_actions')) if capture.get('valid_actions') else None,
                # Decision
                capture.get('action_taken'),
                capture.get('raise_amount'),
                # Prompts (INPUT)
                capture.get('system_prompt'),
                json.dumps(capture.get('conversation_history')) if capture.get('conversation_history') else None,
                capture.get('user_message'),
                capture.get('raw_request'),
                # Response (OUTPUT)
                capture.get('ai_response'),
                capture.get('raw_api_response'),
                # LLM Config
                capture.get('provider', 'openai'),
                capture.get('model'),
                capture.get('reasoning_effort'),
                # Metrics
                capture.get('latency_ms'),
                capture.get('input_tokens'),
                capture.get('output_tokens'),
                # Tracking
                capture.get('original_request_id'),
                # Prompt Versioning
                capture.get('prompt_template'),
                capture.get('prompt_version'),
                capture.get('prompt_hash'),
                # User Annotations
                json.dumps(capture.get('tags', [])),
                capture.get('notes'),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_prompt_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get a single prompt capture by ID.

        Joins with api_usage to get cached_tokens, reasoning_tokens, and estimated_cost.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Join with api_usage to get usage metrics (cached tokens, reasoning tokens, cost)
            cursor = conn.execute("""
                SELECT pc.*,
                       au.cached_tokens,
                       au.reasoning_tokens,
                       au.estimated_cost
                FROM prompt_captures pc
                LEFT JOIN api_usage au ON pc.original_request_id = au.request_id
                WHERE pc.id = ?
            """, (capture_id,))
            row = cursor.fetchone()
            if not row:
                return None

            capture = dict(row)
            # Parse JSON fields
            capture_id_for_log = capture.get('id')
            for field in ['community_cards', 'player_hand', 'valid_actions', 'tags', 'conversation_history']:
                if capture.get(field):
                    try:
                        capture[field] = json.loads(capture[field])
                    except json.JSONDecodeError:
                        logger.debug(f"Failed to parse JSON for field '{field}' in prompt capture {capture_id_for_log}")
            return capture

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        tags: Optional[List[str]] = None,
        call_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List prompt captures with optional filtering.

        Returns:
            Dict with 'captures' list and 'total' count.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)
        if action:
            conditions.append("action_taken = ?")
            params.append(action)
        if phase:
            conditions.append("phase = ?")
            params.append(phase)
        if min_pot_odds is not None:
            conditions.append("pot_odds >= ?")
            params.append(min_pot_odds)
        if max_pot_odds is not None:
            conditions.append("pot_odds <= ?")
            params.append(max_pot_odds)
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if tags:
            # Match any of the provided tags
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM prompt_captures {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get captures with pagination
            query = f"""
                SELECT id, created_at, game_id, player_name, hand_number, phase,
                       action_taken, pot_total, cost_to_call, pot_odds, player_stack,
                       community_cards, player_hand, model, provider, latency_ms, tags, notes
                FROM prompt_captures
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                capture_id_for_log = capture.get('id')
                for field in ['community_cards', 'player_hand', 'tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            logger.debug(f"Failed to parse JSON for field '{field}' in prompt capture {capture_id_for_log}")
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def get_prompt_capture_stats(
        self,
        game_id: Optional[str] = None,
        call_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get aggregate statistics for prompt captures."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            # Count by action (use 'unknown' for NULL to avoid JSON serialization issues)
            cursor = conn.execute(f"""
                SELECT action_taken, COUNT(*) as count
                FROM prompt_captures {where_clause}
                GROUP BY action_taken
            """, params)
            by_action = {(row[0] or 'unknown'): row[1] for row in cursor.fetchall()}

            # Count by phase (use 'unknown' for NULL)
            cursor = conn.execute(f"""
                SELECT phase, COUNT(*) as count
                FROM prompt_captures {where_clause}
                GROUP BY phase
            """, params)
            by_phase = {(row[0] or 'unknown'): row[1] for row in cursor.fetchall()}

            # Suspicious folds (high pot odds)
            suspicious_params = params + [5.0]  # pot odds > 5:1
            suspicious_where = f"{where_clause} {'AND' if where_clause else 'WHERE'} action_taken = 'fold' AND pot_odds > ?"
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM prompt_captures
                {suspicious_where}
            """, suspicious_params)
            suspicious_folds = cursor.fetchone()[0]

            # Total captures
            cursor = conn.execute(f"SELECT COUNT(*) FROM prompt_captures {where_clause}", params)
            total = cursor.fetchone()[0]

            return {
                'total': total,
                'by_action': by_action,
                'by_phase': by_phase,
                'suspicious_folds': suspicious_folds
            }

    def update_prompt_capture_tags(
        self,
        capture_id: int,
        tags: List[str],
        notes: Optional[str] = None
    ) -> bool:
        """Update tags and notes for a prompt capture."""
        with sqlite3.connect(self.db_path) as conn:
            if notes is not None:
                conn.execute(
                    "UPDATE prompt_captures SET tags = ?, notes = ? WHERE id = ?",
                    (json.dumps(tags), notes, capture_id)
                )
            else:
                conn.execute(
                    "UPDATE prompt_captures SET tags = ? WHERE id = ?",
                    (json.dumps(tags), capture_id)
                )
            conn.commit()
            return conn.total_changes > 0

    def delete_prompt_captures(self, game_id: Optional[str] = None, before_date: Optional[str] = None) -> int:
        """Delete prompt captures, optionally filtered by game or date.

        Args:
            game_id: Delete captures for a specific game
            before_date: Delete captures before this date (ISO format)

        Returns:
            Number of captures deleted.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if before_date:
            conditions.append("created_at < ?")
            params.append(before_date)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"DELETE FROM prompt_captures {where_clause}", params)
            conn.commit()
            return cursor.rowcount

    # ========== Playground Capture Methods ==========

    def list_playground_captures(
        self,
        call_type: Optional[str] = None,
        provider: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List captures for the playground (filtered by call_type).

        This method is similar to list_prompt_captures but focuses on
        non-game captures identified by call_type.

        Args:
            call_type: Filter by call type (e.g., 'commentary', 'personality_generation')
            provider: Filter by LLM provider
            limit: Max results to return
            offset: Pagination offset
            date_from: Filter by start date (ISO format)
            date_to: Filter by end date (ISO format)

        Returns:
            Dict with 'captures' list and 'total' count
        """
        conditions = []  # Show all captures (including legacy ones without call_type)
        params = []

        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if provider:
            conditions.append("provider = ?")
            params.append(provider)
        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM prompt_captures {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get captures with pagination
            query = f"""
                SELECT id, created_at, game_id, player_name, hand_number,
                       phase, call_type, action_taken,
                       model, provider, reasoning_effort,
                       latency_ms, input_tokens, output_tokens,
                       tags, notes
                FROM prompt_captures
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                for field in ['tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            logger.warning(
                                "Failed to decode JSON for field '%s' on capture id=%s; keeping raw value",
                                field,
                                capture.get("id"),
                            )
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def get_playground_capture_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics for all prompt captures."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Count by call_type (legacy captures without call_type shown as 'player_decision')
            cursor = conn.execute("""
                SELECT COALESCE(call_type, 'player_decision') as call_type, COUNT(*) as count
                FROM prompt_captures
                GROUP BY COALESCE(call_type, 'player_decision')
                ORDER BY count DESC
            """)
            by_call_type = {row['call_type']: row['count'] for row in cursor.fetchall()}

            # Count by provider
            cursor = conn.execute("""
                SELECT COALESCE(provider, 'openai') as provider, COUNT(*) as count
                FROM prompt_captures
                GROUP BY COALESCE(provider, 'openai')
                ORDER BY count DESC
            """)
            by_provider = {row['provider']: row['count'] for row in cursor.fetchall()}

            # Total count
            cursor = conn.execute("""
                SELECT COUNT(*) FROM prompt_captures
            """)
            total = cursor.fetchone()[0]

            return {
                'total': total,
                'by_call_type': by_call_type,
                'by_provider': by_provider,
            }

    def cleanup_old_captures(self, retention_days: int) -> int:
        """Delete captures older than the retention period.

        Args:
            retention_days: Delete captures older than this many days.
                           If 0, no deletion occurs (unlimited retention).

        Returns:
            Number of captures deleted.
        """
        if retention_days <= 0:
            return 0  # Unlimited retention

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM prompt_captures
                WHERE call_type IS NOT NULL
                  AND created_at < datetime('now', '-' || ? || ' days')
            """, (retention_days,))
            conn.commit()

            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} playground captures older than {retention_days} days")
            return deleted

    # ========== Decision Analysis Methods ==========

    def save_decision_analysis(self, analysis) -> int:
        """Save a decision analysis to the database.

        Args:
            analysis: DecisionAnalysis dataclass or dict with analysis data

        Returns:
            The ID of the inserted row.
        """
        # Convert dataclass to dict if needed
        if hasattr(analysis, 'to_dict'):
            data = analysis.to_dict()
        else:
            data = analysis

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO player_decision_analysis (
                    request_id, capture_id,
                    game_id, player_name, hand_number, phase, player_position,
                    pot_total, cost_to_call, player_stack, num_opponents,
                    player_hand, community_cards,
                    action_taken, raise_amount,
                    equity, required_equity, ev_call,
                    optimal_action, decision_quality, ev_lost,
                    hand_rank, relative_strength,
                    equity_vs_ranges, opponent_positions,
                    analyzer_version, processing_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('request_id'),
                data.get('capture_id'),
                data.get('game_id'),
                data.get('player_name'),
                data.get('hand_number'),
                data.get('phase'),
                data.get('player_position'),
                data.get('pot_total'),
                data.get('cost_to_call'),
                data.get('player_stack'),
                data.get('num_opponents'),
                data.get('player_hand'),
                data.get('community_cards'),
                data.get('action_taken'),
                data.get('raise_amount'),
                data.get('equity'),
                data.get('required_equity'),
                data.get('ev_call'),
                data.get('optimal_action'),
                data.get('decision_quality'),
                data.get('ev_lost'),
                data.get('hand_rank'),
                data.get('relative_strength'),
                data.get('equity_vs_ranges'),
                data.get('opponent_positions'),
                data.get('analyzer_version'),
                data.get('processing_time_ms'),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_decision_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        """Get a single decision analysis by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE id = ?",
                (analysis_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_decision_analysis_by_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get decision analysis by api_usage request_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE request_id = ?",
                (request_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_decision_analysis_by_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get decision analysis linked to a prompt capture.

        Links via request_id: prompt_captures.original_request_id = player_decision_analysis.request_id
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # First try direct capture_id link
            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE capture_id = ?",
                (capture_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # Fall back to request_id link
            cursor = conn.execute("""
                SELECT pda.*
                FROM player_decision_analysis pda
                JOIN prompt_captures pc ON pc.original_request_id = pda.request_id
                WHERE pc.id = ?
            """, (capture_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def list_decision_analyses(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        decision_quality: Optional[str] = None,
        min_ev_lost: Optional[float] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List decision analyses with optional filtering.

        Returns:
            Dict with 'analyses' list and 'total' count.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)
        if decision_quality:
            conditions.append("decision_quality = ?")
            params.append(decision_quality)
        if min_ev_lost is not None:
            conditions.append("ev_lost >= ?")
            params.append(min_ev_lost)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM player_decision_analysis {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get analyses with pagination
            query = f"""
                SELECT *
                FROM player_decision_analysis
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            analyses = [dict(row) for row in cursor.fetchall()]

            return {
                'analyses': analyses,
                'total': total
            }

    def get_decision_analysis_stats(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregate statistics for decision analyses.

        Args:
            game_id: Optional filter by game

        Returns:
            Dict with aggregate stats including:
            - total: Total number of analyses
            - by_quality: Count by decision quality
            - by_action: Count by action taken
            - total_ev_lost: Sum of EV lost
            - avg_equity: Average equity across decisions
            - avg_processing_ms: Average processing time
        """
        where_clause = "WHERE game_id = ?" if game_id else ""
        params = [game_id] if game_id else []

        with sqlite3.connect(self.db_path) as conn:
            # Count by quality
            cursor = conn.execute(f"""
                SELECT decision_quality, COUNT(*) as count
                FROM player_decision_analysis {where_clause}
                GROUP BY decision_quality
            """, params)
            by_quality = {row[0]: row[1] for row in cursor.fetchall()}

            # Count by action
            cursor = conn.execute(f"""
                SELECT action_taken, COUNT(*) as count
                FROM player_decision_analysis {where_clause}
                GROUP BY action_taken
            """, params)
            by_action = {row[0]: row[1] for row in cursor.fetchall()}

            # Aggregate stats
            cursor = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(ev_lost) as total_ev_lost,
                    AVG(equity) as avg_equity,
                    AVG(equity_vs_ranges) as avg_equity_vs_ranges,
                    AVG(processing_time_ms) as avg_processing_ms,
                    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistakes,
                    SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) as correct
                FROM player_decision_analysis {where_clause}
            """, params)
            row = cursor.fetchone()

            return {
                'total': row[0] or 0,
                'total_ev_lost': row[1] or 0,
                'avg_equity': row[2],
                'avg_equity_vs_ranges': row[3],
                'avg_processing_ms': row[4],
                'mistakes': row[5] or 0,
                'correct': row[6] or 0,
                'by_quality': by_quality,
                'by_action': by_action,
            }

    # ==================== Experiment Methods ====================

    def create_experiment(self, config: Dict) -> int:
        """Create a new experiment record.

        Args:
            config: Dictionary containing experiment configuration with keys:
                - name: Unique experiment name (required)
                - description: Experiment description (optional)
                - hypothesis: What we're testing (optional)
                - tags: List of tags (optional)
                - notes: Additional notes (optional)
                - Additional config fields stored as config_json

        Returns:
            The experiment_id of the created record

        Raises:
            sqlite3.IntegrityError: If experiment name already exists
        """
        name = config.get('name')
        if not name:
            raise ValueError("Experiment name is required")

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO experiments (name, description, hypothesis, tags, notes, config_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name,
                config.get('description'),
                config.get('hypothesis'),
                json.dumps(config.get('tags', [])),
                config.get('notes'),
                json.dumps(config),
            ))
            conn.commit()
            experiment_id = cursor.lastrowid
            logger.info(f"Created experiment '{name}' with id {experiment_id}")
            return experiment_id

    def link_game_to_experiment(
        self,
        experiment_id: int,
        game_id: str,
        variant: Optional[str] = None,
        variant_config: Optional[Dict] = None,
        tournament_number: Optional[int] = None
    ) -> int:
        """Link a game to an experiment.

        Args:
            experiment_id: The experiment ID
            game_id: The game ID to link
            variant: Optional variant label (e.g., 'baseline', 'treatment')
            variant_config: Optional variant-specific configuration
            tournament_number: Optional tournament sequence number

        Returns:
            The experiment_games record ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO experiment_games (experiment_id, game_id, variant, variant_config_json, tournament_number)
                VALUES (?, ?, ?, ?, ?)
            """, (
                experiment_id,
                game_id,
                variant,
                json.dumps(variant_config) if variant_config else None,
                tournament_number,
            ))
            conn.commit()
            return cursor.lastrowid

    def complete_experiment(self, experiment_id: int, summary: Optional[Dict] = None) -> None:
        """Mark an experiment as completed and store summary.

        Args:
            experiment_id: The experiment ID
            summary: Optional summary dictionary with aggregated results
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE experiments
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    summary_json = ?
                WHERE id = ?
            """, (json.dumps(summary) if summary else None, experiment_id))
            conn.commit()
            logger.info(f"Completed experiment {experiment_id}")

    def get_experiment(self, experiment_id: int) -> Optional[Dict]:
        """Get experiment details by ID.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dictionary with experiment details or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT id, name, description, hypothesis, tags, notes, config_json,
                       status, created_at, completed_at, summary_json
                FROM experiments WHERE id = ?
            """, (experiment_id,))
            row = cursor.fetchone()
            if not row:
                return None

            return {
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'hypothesis': row[3],
                'tags': json.loads(row[4]) if row[4] else [],
                'notes': row[5],
                'config': json.loads(row[6]) if row[6] else {},
                'status': row[7],
                'created_at': row[8],
                'completed_at': row[9],
                'summary': json.loads(row[10]) if row[10] else None,
            }

    def get_experiment_by_name(self, name: str) -> Optional[Dict]:
        """Get experiment details by name.

        Args:
            name: The experiment name

        Returns:
            Dictionary with experiment details or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id FROM experiments WHERE name = ?", (name,))
            row = cursor.fetchone()
            if not row:
                return None
            return self.get_experiment(row[0])

    def get_experiment_games(self, experiment_id: int) -> List[Dict]:
        """Get all games linked to an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of dictionaries with game link details
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT eg.id, eg.game_id, eg.variant, eg.variant_config_json,
                       eg.tournament_number, eg.created_at
                FROM experiment_games eg
                WHERE eg.experiment_id = ?
                ORDER BY eg.tournament_number, eg.created_at
            """, (experiment_id,))

            return [
                {
                    'id': row[0],
                    'game_id': row[1],
                    'variant': row[2],
                    'variant_config': json.loads(row[3]) if row[3] else None,
                    'tournament_number': row[4],
                    'created_at': row[5],
                }
                for row in cursor.fetchall()
            ]

    def get_experiment_decision_stats(
        self,
        experiment_id: int,
        variant: Optional[str] = None
    ) -> Dict:
        """Get aggregated decision analysis stats for an experiment.

        Args:
            experiment_id: The experiment ID
            variant: Optional variant filter

        Returns:
            Dictionary with aggregated decision statistics:
                - total: Total decisions analyzed
                - correct: Number of correct decisions
                - marginal: Number of marginal decisions
                - mistake: Number of mistakes
                - correct_pct: Percentage of correct decisions
                - avg_ev_lost: Average EV lost per decision
                - by_player: Stats broken down by player
        """
        with sqlite3.connect(self.db_path) as conn:
            # Build query with optional variant filter
            variant_clause = "AND eg.variant = ?" if variant else ""
            params = [experiment_id]
            if variant:
                params.append(variant)

            # Aggregate stats
            cursor = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                    SUM(CASE WHEN pda.decision_quality = 'marginal' THEN 1 ELSE 0 END) as marginal,
                    SUM(CASE WHEN pda.decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                    AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause}
            """, params)

            row = cursor.fetchone()
            total = row[0] or 0

            result = {
                'total': total,
                'correct': row[1] or 0,
                'marginal': row[2] or 0,
                'mistake': row[3] or 0,
                'correct_pct': round((row[1] or 0) * 100 / total, 1) if total else 0,
                'avg_ev_lost': round(row[4] or 0, 2),
            }

            # Stats by player
            cursor = conn.execute(f"""
                SELECT
                    pda.player_name,
                    COUNT(*) as total,
                    SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                    AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause}
                GROUP BY pda.player_name
            """, params)

            result['by_player'] = {
                row[0]: {
                    'total': row[1],
                    'correct': row[2] or 0,
                    'correct_pct': round((row[2] or 0) * 100 / row[1], 1) if row[1] else 0,
                    'avg_ev_lost': round(row[3] or 0, 2),
                }
                for row in cursor.fetchall()
            }

            return result

    def list_experiments(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """List experiments with optional status filter.

        Args:
            status: Optional status filter ('pending', 'running', 'completed', 'failed')
            limit: Maximum number of experiments to return
            offset: Number of experiments to skip for pagination

        Returns:
            List of experiment dictionaries with basic info and progress
        """
        with sqlite3.connect(self.db_path) as conn:
            # Build query with optional status filter
            where_clause = "WHERE status = ?" if status else ""
            params = [status] if status else []

            cursor = conn.execute(f"""
                SELECT
                    e.id, e.name, e.description, e.hypothesis,
                    e.tags, e.status, e.created_at, e.completed_at,
                    e.config_json, e.summary_json,
                    (SELECT COUNT(*) FROM experiment_games WHERE experiment_id = e.id) as games_count
                FROM experiments e
                {where_clause}
                ORDER BY e.created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            experiments = []
            for row in cursor.fetchall():
                config = json.loads(row[8]) if row[8] else {}
                summary = json.loads(row[9]) if row[9] else None

                # Calculate total expected games accounting for A/B variants
                num_tournaments = config.get('num_tournaments', 1)
                variants = config.get('variants', [])
                control = config.get('control')

                # For A/B experiments, total games = num_tournaments * num_variants
                if control and variants:
                    num_variants = len(variants) + 1  # +1 for control
                    total_expected = num_tournaments * num_variants
                else:
                    total_expected = num_tournaments

                experiments.append({
                    'id': row[0],
                    'name': row[1],
                    'description': row[2],
                    'hypothesis': row[3],
                    'tags': json.loads(row[4]) if row[4] else [],
                    'status': row[5],
                    'created_at': row[6],
                    'completed_at': row[7],
                    'games_count': row[10],
                    'num_tournaments': total_expected,
                    'model': config.get('model'),
                    'provider': config.get('provider'),
                    'summary': summary,
                })

            return experiments

    def update_experiment_status(
        self,
        experiment_id: int,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """Update experiment status.

        Args:
            experiment_id: The experiment ID
            status: New status ('pending', 'running', 'completed', 'failed')
            error_message: Optional error message if status is 'failed'
        """
        valid_statuses = {'pending', 'running', 'completed', 'failed', 'paused', 'interrupted'}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        with sqlite3.connect(self.db_path) as conn:
            if status == 'completed':
                conn.execute("""
                    UPDATE experiments
                    SET status = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, experiment_id))
            elif status == 'failed' and error_message:
                # Store error in notes field
                conn.execute("""
                    UPDATE experiments
                    SET status = ?, notes = COALESCE(notes || '\n', '') || ?
                    WHERE id = ?
                """, (status, f"Error: {error_message}", experiment_id))
            else:
                conn.execute("""
                    UPDATE experiments
                    SET status = ?
                    WHERE id = ?
                """, (status, experiment_id))
            conn.commit()
            logger.info(f"Updated experiment {experiment_id} status to {status}")

    def mark_running_experiments_interrupted(self) -> int:
        """Mark all 'running' experiments as 'interrupted'.

        Called on startup to handle experiments that were running when the
        server was stopped. Users can manually resume these experiments.

        Returns:
            Number of experiments marked as interrupted.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                UPDATE experiments
                SET status = 'interrupted',
                    notes = COALESCE(notes || '\n', '') || 'Server restarted while experiment was running.'
                WHERE status = 'running'
            """)
            count = cursor.rowcount
            conn.commit()
            if count > 0:
                logger.info(f"Marked {count} running experiment(s) as interrupted")
            return count

    def get_incomplete_tournaments(self, experiment_id: int) -> List[Dict]:
        """Get game_ids for tournaments that haven't completed (no tournament_results entry).

        Used when resuming a paused experiment to identify which tournaments need to continue.

        Args:
            experiment_id: The experiment ID to check

        Returns:
            List of dicts with game info for incomplete tournaments:
            [{'game_id': str, 'variant': str|None, 'variant_config': dict|None, 'tournament_number': int}]
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT eg.game_id, eg.variant, eg.variant_config_json, eg.tournament_number
                FROM experiment_games eg
                LEFT JOIN tournament_results tr ON eg.game_id = tr.game_id
                WHERE eg.experiment_id = ?
                AND tr.id IS NULL
                ORDER BY eg.tournament_number
            """, (experiment_id,))

            incomplete = []
            for row in cursor.fetchall():
                variant_config = None
                if row['variant_config_json']:
                    try:
                        variant_config = json.loads(row['variant_config_json'])
                    except (json.JSONDecodeError, TypeError):
                        pass

                incomplete.append({
                    'game_id': row['game_id'],
                    'variant': row['variant'],
                    'variant_config': variant_config,
                    'tournament_number': row['tournament_number'],
                })

            return incomplete

    def get_experiment_live_stats(self, experiment_id: int) -> Dict:
        """Get real-time unified stats per variant for running/completed experiments.

        Returns all metrics per variant in one call: latency, decision quality, and progress.
        This is designed to be called on every 5s refresh for running experiments.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dictionary with structure:
            {
                'by_variant': {
                    'Variant Label': {
                        'latency_metrics': { avg_ms, p50_ms, p95_ms, p99_ms, count },
                        'decision_quality': { total, correct, correct_pct, mistakes, avg_ev_lost },
                        'progress': { current_hands, max_hands, progress_pct }
                    },
                    ...
                },
                'overall': { ... same structure ... }
            }
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get experiment config for max_hands calculation
            exp = self.get_experiment(experiment_id)
            if not exp:
                return {'by_variant': {}, 'overall': None}

            config = exp.get('config', {})
            max_hands = config.get('hands_per_tournament', 100)
            num_tournaments = config.get('num_tournaments', 1)

            # Determine number of variants from control/variants config
            control = config.get('control')
            variants = config.get('variants', [])
            if control is not None:
                # A/B testing mode: control + variants
                num_variant_configs = 1 + len(variants or [])
            else:
                # Legacy mode: single variant
                num_variant_configs = 1

            result = {'by_variant': {}, 'overall': None}

            # Get all variants for this experiment from actual games
            cursor = conn.execute("""
                SELECT DISTINCT variant FROM experiment_games
                WHERE experiment_id = ?
            """, (experiment_id,))
            variant_labels = [row[0] for row in cursor.fetchall()]

            # If no games yet, create placeholder entries from config
            if not variant_labels:
                if control is not None:
                    variant_labels = [control.get('label', 'Control')]
                    for v in (variants or []):
                        variant_labels.append(v.get('label', 'Variant'))
                else:
                    variant_labels = [None]  # Legacy single variant

            # Aggregate stats for overall calculation
            all_latencies = []
            overall_decision = {'total': 0, 'correct': 0, 'mistake': 0, 'ev_lost_sum': 0}
            overall_progress = {'current_hands': 0, 'max_hands': 0}

            for variant in variant_labels:
                variant_key = variant or 'default'

                # Build variant clause
                if variant is None:
                    variant_clause = "AND (eg.variant IS NULL OR eg.variant = '')"
                    variant_params = []
                else:
                    variant_clause = "AND eg.variant = ?"
                    variant_params = [variant]

                # 1. Latency metrics from api_usage
                cursor = conn.execute(f"""
                    SELECT au.latency_ms FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.latency_ms IS NOT NULL
                """, [experiment_id] + variant_params)
                latencies = [row[0] for row in cursor.fetchall()]

                if latencies:
                    latency_metrics = {
                        'avg_ms': round(float(np.mean(latencies)), 2),
                        'p50_ms': round(float(np.percentile(latencies, 50)), 2),
                        'p95_ms': round(float(np.percentile(latencies, 95)), 2),
                        'p99_ms': round(float(np.percentile(latencies, 99)), 2),
                        'count': len(latencies),
                    }
                    all_latencies.extend(latencies)
                else:
                    latency_metrics = None

                # 2. Decision quality from player_decision_analysis
                cursor = conn.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                        SUM(CASE WHEN pda.decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                        AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                    FROM player_decision_analysis pda
                    JOIN experiment_games eg ON pda.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                row = cursor.fetchone()
                total = row[0] or 0

                if total > 0:
                    decision_quality = {
                        'total': total,
                        'correct': row[1] or 0,
                        'correct_pct': round((row[1] or 0) * 100 / total, 1),
                        'mistakes': row[2] or 0,
                        'avg_ev_lost': round(row[3] or 0, 2),
                    }
                    overall_decision['total'] += total
                    overall_decision['correct'] += row[1] or 0
                    overall_decision['mistake'] += row[2] or 0
                    overall_decision['ev_lost_sum'] += (row[3] or 0) * total
                else:
                    decision_quality = None

                # 3. Progress - count games and max hand number per variant
                cursor = conn.execute(f"""
                    SELECT
                        COUNT(DISTINCT eg.game_id) as games_count,
                        MAX(au.hand_number) as max_hand
                    FROM experiment_games eg
                    LEFT JOIN api_usage au ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                row = cursor.fetchone()
                games_count = row[0] or 0
                current_max_hand = row[1] or 0

                # Calculate progress: completed games * max_hands + current hand
                # For a variant, expected tournaments = num_tournaments
                variant_max_hands = num_tournaments * max_hands
                if games_count > 0:
                    # Estimate: (completed_games - 1) * max_hands + current_max_hand
                    # But we don't know which games are complete, so use games_count directly
                    # Approximation: if max_hand is less than max_hands, game is in progress
                    completed_games = max(0, games_count - 1) if current_max_hand < max_hands else games_count
                    current_hands = completed_games * max_hands + (current_max_hand if current_max_hand < max_hands else 0)
                    # Simpler approach: just use games_count * average hands if we have a summary
                    # For now, use a rough estimate
                    current_hands = min(games_count * max_hands, variant_max_hands)
                    # Refine: if game is in progress, add current hand
                    if current_max_hand > 0 and current_max_hand < max_hands:
                        current_hands = (games_count - 1) * max_hands + current_max_hand
                else:
                    current_hands = 0

                progress = {
                    'current_hands': current_hands,
                    'max_hands': variant_max_hands,
                    'games_count': games_count,
                    'games_expected': num_tournaments,
                    'progress_pct': round(current_hands * 100 / variant_max_hands, 1) if variant_max_hands else 0,
                }

                overall_progress['current_hands'] += current_hands
                overall_progress['max_hands'] += variant_max_hands

                # 4. Cost metrics from api_usage
                cursor = conn.execute(f"""
                    SELECT
                        COALESCE(SUM(au.estimated_cost), 0) as total_cost,
                        COUNT(*) as total_calls,
                        COALESCE(AVG(au.estimated_cost), 0) as avg_cost_per_call
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                cost_row = cursor.fetchone()

                # Cost by model
                cursor = conn.execute(f"""
                    SELECT
                        au.provider || '/' || au.model as model_key,
                        SUM(au.estimated_cost) as cost,
                        COUNT(*) as calls
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.estimated_cost IS NOT NULL
                    GROUP BY au.provider, au.model
                """, [experiment_id] + variant_params)
                by_model = {row[0]: {'cost': row[1], 'calls': row[2]} for row in cursor.fetchall()}

                # Cost per decision (player_decision call type)
                cursor = conn.execute(f"""
                    SELECT AVG(au.estimated_cost), COUNT(*)
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.call_type = 'player_decision'
                """, [experiment_id] + variant_params)
                decision_cost_row = cursor.fetchone()

                # Count hands for normalized cost (use api_usage since hand_history may be empty)
                cursor = conn.execute(f"""
                    SELECT COUNT(DISTINCT au.game_id || '-' || au.hand_number) as total_hands
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.hand_number IS NOT NULL
                """, [experiment_id] + variant_params)
                hand_row = cursor.fetchone()
                total_hands_for_cost = hand_row[0] or 1

                cost_metrics = {
                    'total_cost': round(cost_row[0] or 0, 6),
                    'total_calls': cost_row[1] or 0,
                    'avg_cost_per_call': round(cost_row[2] or 0, 8),
                    'by_model': by_model,
                    'avg_cost_per_decision': round(decision_cost_row[0] or 0, 8) if decision_cost_row[0] else 0,
                    'total_decisions': decision_cost_row[1] or 0,
                    'cost_per_hand': round((cost_row[0] or 0) / total_hands_for_cost, 6),
                    'total_hands': total_hands_for_cost,
                }

                result['by_variant'][variant_key] = {
                    'latency_metrics': latency_metrics,
                    'decision_quality': decision_quality,
                    'progress': progress,
                    'cost_metrics': cost_metrics,
                }

            # Compute overall stats
            if all_latencies:
                overall_latency = {
                    'avg_ms': round(float(np.mean(all_latencies)), 2),
                    'p50_ms': round(float(np.percentile(all_latencies, 50)), 2),
                    'p95_ms': round(float(np.percentile(all_latencies, 95)), 2),
                    'p99_ms': round(float(np.percentile(all_latencies, 99)), 2),
                    'count': len(all_latencies),
                }
            else:
                overall_latency = None

            if overall_decision['total'] > 0:
                overall_decision_quality = {
                    'total': overall_decision['total'],
                    'correct': overall_decision['correct'],
                    'correct_pct': round(overall_decision['correct'] * 100 / overall_decision['total'], 1),
                    'mistakes': overall_decision['mistake'],
                    'avg_ev_lost': round(overall_decision['ev_lost_sum'] / overall_decision['total'], 2),
                }
            else:
                overall_decision_quality = None

            overall_progress_result = {
                'current_hands': overall_progress['current_hands'],
                'max_hands': overall_progress['max_hands'],
                'progress_pct': round(overall_progress['current_hands'] * 100 / overall_progress['max_hands'], 1) if overall_progress['max_hands'] else 0,
            }

            # Overall cost metrics
            cursor = conn.execute("""
                SELECT
                    COALESCE(SUM(au.estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls,
                    COALESCE(AVG(au.estimated_cost), 0) as avg_cost_per_call
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ?
            """, (experiment_id,))
            overall_cost_row = cursor.fetchone()

            cursor = conn.execute("""
                SELECT
                    au.provider || '/' || au.model as model_key,
                    SUM(au.estimated_cost) as cost,
                    COUNT(*) as calls
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.estimated_cost IS NOT NULL
                GROUP BY au.provider, au.model
            """, (experiment_id,))
            overall_by_model = {row[0]: {'cost': row[1], 'calls': row[2]} for row in cursor.fetchall()}

            cursor = conn.execute("""
                SELECT AVG(au.estimated_cost), COUNT(*)
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.call_type = 'player_decision'
            """, (experiment_id,))
            overall_decision_cost_row = cursor.fetchone()

            cursor = conn.execute("""
                SELECT COUNT(DISTINCT au.game_id || '-' || au.hand_number) as total_hands
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.hand_number IS NOT NULL
            """, (experiment_id,))
            overall_hand_row = cursor.fetchone()
            overall_total_hands = overall_hand_row[0] or 1

            overall_cost_metrics = {
                'total_cost': round(overall_cost_row[0] or 0, 6),
                'total_calls': overall_cost_row[1] or 0,
                'avg_cost_per_call': round(overall_cost_row[2] or 0, 8),
                'by_model': overall_by_model,
                'avg_cost_per_decision': round(overall_decision_cost_row[0] or 0, 8) if overall_decision_cost_row[0] else 0,
                'total_decisions': overall_decision_cost_row[1] or 0,
                'cost_per_hand': round((overall_cost_row[0] or 0) / overall_total_hands, 6),
                'total_hands': overall_total_hands,
            }

            result['overall'] = {
                'latency_metrics': overall_latency,
                'decision_quality': overall_decision_quality,
                'progress': overall_progress_result,
                'cost_metrics': overall_cost_metrics,
            }

            return result

    def get_experiment_game_snapshots(self, experiment_id: int) -> List[Dict]:
        """Load current game states for all running games in an experiment.

        This method provides live game snapshots for the monitoring view,
        including player states, community cards, pot, and psychology data.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of dictionaries with game snapshots:
            [
                {
                    'game_id': str,
                    'variant': str | None,
                    'phase': str,
                    'hand_number': int,
                    'pot': int,
                    'community_cards': [...],
                    'players': [
                        {
                            'name': str,
                            'stack': int,
                            'bet': int,
                            'hole_cards': [...],  # Always visible
                            'is_folded': bool,
                            'is_all_in': bool,
                            'is_current': bool,
                            'psychology': {...},
                            'llm_debug': {...}
                        }
                    ]
                }
            ]
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get all games for this experiment
            cursor = conn.execute("""
                SELECT eg.game_id, eg.variant, g.game_state_json, g.phase, g.updated_at
                FROM experiment_games eg
                JOIN games g ON eg.game_id = g.game_id
                WHERE eg.experiment_id = ?
                ORDER BY g.updated_at DESC
            """, (experiment_id,))

            games = []
            for row in cursor.fetchall():
                game_id = row['game_id']
                variant = row['variant']

                try:
                    state_dict = json.loads(row['game_state_json'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse game state for {game_id}")
                    continue

                # Extract basic game info
                phase = row['phase']
                pot = state_dict.get('pot', {})
                pot_total = pot.get('total', 0) if isinstance(pot, dict) else pot

                # Get community cards
                community_cards = state_dict.get('community_cards', [])

                # Get current player index
                current_player_idx = state_dict.get('current_player_idx', 0)

                # Load psychology data for all players in this game
                psychology_data = self.load_all_controller_states(game_id)
                emotional_data = self.load_all_emotional_states(game_id)

                # Load LLM debug info from most recent api_usage records per player
                llm_debug_cursor = conn.execute("""
                    SELECT player_name, provider, model, reasoning_effort,
                           COUNT(*) as total_calls,
                           AVG(latency_ms) as avg_latency_ms,
                           AVG(estimated_cost) as avg_cost
                    FROM api_usage
                    WHERE game_id = ?
                    GROUP BY player_name
                """, (game_id,))
                llm_debug_by_player = {}
                for llm_row in llm_debug_cursor.fetchall():
                    if llm_row['player_name']:
                        llm_debug_by_player[llm_row['player_name']] = {
                            'provider': llm_row['provider'],
                            'model': llm_row['model'],
                            'reasoning_effort': llm_row['reasoning_effort'],
                            'total_calls': llm_row['total_calls'],
                            'avg_latency_ms': round(llm_row['avg_latency_ms'] or 0, 2),
                            'avg_cost_per_call': round(llm_row['avg_cost'] or 0, 6),
                        }

                # Build player list
                players = []
                players_data = state_dict.get('players', [])
                for idx, p in enumerate(players_data):
                    player_name = p.get('name', f'Player_{idx}')

                    # Get psychology for this player
                    ctrl_state = psychology_data.get(player_name, {})
                    emo_state = emotional_data.get(player_name, {})

                    # Merge tilt and emotional data into psychology
                    tilt_state = ctrl_state.get('tilt_state', {}) if ctrl_state else {}
                    psychology = {
                        'narrative': emo_state.get('narrative', ''),
                        'inner_voice': emo_state.get('inner_voice', ''),
                        # Convert tilt_level from 0.0-1.0 to 0-100 percentage
                        'tilt_level': round((tilt_state.get('tilt_level', 0) if tilt_state else 0) * 100),
                        'tilt_category': tilt_state.get('category', 'none') if tilt_state else 'none',
                        'tilt_source': tilt_state.get('source', '') if tilt_state else '',
                    }

                    players.append({
                        'name': player_name,
                        'stack': p.get('stack', 0),
                        'bet': p.get('bet', 0),
                        'hole_cards': p.get('hand', []),  # Always show cards in monitoring mode
                        'is_folded': p.get('is_folded', False),
                        'is_all_in': p.get('is_all_in', False),
                        'is_current': idx == current_player_idx,
                        'psychology': psychology,
                        'llm_debug': llm_debug_by_player.get(player_name, {}),
                    })

                # Get hand number from api_usage (most recent)
                hand_cursor = conn.execute("""
                    SELECT MAX(hand_number) as hand_number
                    FROM api_usage WHERE game_id = ?
                """, (game_id,))
                hand_row = hand_cursor.fetchone()
                hand_number = hand_row['hand_number'] if hand_row and hand_row['hand_number'] else 1

                games.append({
                    'game_id': game_id,
                    'variant': variant,
                    'phase': phase,
                    'hand_number': hand_number,
                    'pot': pot_total,
                    'community_cards': community_cards,
                    'players': players,
                })

            return games

    def get_experiment_player_detail(
        self,
        experiment_id: int,
        game_id: str,
        player_name: str
    ) -> Optional[Dict]:
        """Get detailed player info for the drill-down panel.

        Args:
            experiment_id: The experiment ID
            game_id: The game ID
            player_name: The player name

        Returns:
            Dictionary with detailed player info or None if not found:
            {
                'player': { name, stack, cards },
                'psychology': { narrative, inner_voice, tilt_level, tilt_category, tilt_source },
                'llm_debug': { provider, model, reasoning_effort, total_calls, avg_latency_ms, avg_cost_per_call },
                'play_style': { vpip, pfr, aggression_factor, summary },
                'recent_decisions': [ { hand_number, phase, action, decision_quality, ev_lost } ]
            }
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Verify game belongs to experiment and get variant config
            cursor = conn.execute("""
                SELECT eg.id, eg.variant_config_json FROM experiment_games eg
                WHERE eg.experiment_id = ? AND eg.game_id = ?
            """, (experiment_id, game_id))
            eg_row = cursor.fetchone()
            if not eg_row:
                return None

            # Check if psychology is enabled for this variant
            variant_config = {}
            if eg_row['variant_config_json']:
                try:
                    variant_config = json.loads(eg_row['variant_config_json'])
                except (json.JSONDecodeError, TypeError):
                    pass
            psychology_enabled = variant_config.get('enable_psychology', False)

            # Load game state for player info
            cursor = conn.execute(
                "SELECT game_state_json FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            try:
                state_dict = json.loads(row['game_state_json'])
            except json.JSONDecodeError:
                return None

            # Find player in game state
            player_data = None
            for p in state_dict.get('players', []):
                if p.get('name') == player_name:
                    player_data = p
                    break

            if not player_data:
                return None

            # Get psychology data
            ctrl_state = self.load_controller_state(game_id, player_name)
            emo_state = self.load_emotional_state(game_id, player_name)

            tilt_state = ctrl_state.get('tilt_state', {}) if ctrl_state else {}
            psychology = {
                'narrative': emo_state.get('narrative', '') if emo_state else '',
                'inner_voice': emo_state.get('inner_voice', '') if emo_state else '',
                # Convert tilt_level from 0.0-1.0 to 0-100 percentage
                'tilt_level': round((tilt_state.get('tilt_level', 0) if tilt_state else 0) * 100),
                'tilt_category': tilt_state.get('category', 'none') if tilt_state else 'none',
                'tilt_source': tilt_state.get('source', '') if tilt_state else '',
            }

            # Get LLM debug info
            cursor = conn.execute("""
                SELECT provider, model, reasoning_effort,
                       COUNT(*) as total_calls,
                       AVG(latency_ms) as avg_latency_ms,
                       AVG(estimated_cost) as avg_cost
                FROM api_usage
                WHERE game_id = ? AND player_name = ?
                GROUP BY provider, model
            """, (game_id, player_name))
            llm_row = cursor.fetchone()

            llm_debug = {}
            if llm_row:
                # Also get percentile latencies
                cursor = conn.execute("""
                    SELECT latency_ms FROM api_usage
                    WHERE game_id = ? AND player_name = ? AND latency_ms IS NOT NULL
                    ORDER BY latency_ms
                """, (game_id, player_name))
                latencies = [r['latency_ms'] for r in cursor.fetchall()]

                p95 = 0
                p99 = 0
                if latencies:
                    p95 = round(float(np.percentile(latencies, 95)), 2) if len(latencies) >= 5 else max(latencies)
                    p99 = round(float(np.percentile(latencies, 99)), 2) if len(latencies) >= 10 else max(latencies)

                llm_debug = {
                    'provider': llm_row['provider'],
                    'model': llm_row['model'],
                    'reasoning_effort': llm_row['reasoning_effort'],
                    'total_calls': llm_row['total_calls'],
                    'avg_latency_ms': round(llm_row['avg_latency_ms'] or 0, 2),
                    'p95_latency_ms': p95,
                    'p99_latency_ms': p99,
                    'avg_cost_per_call': round(llm_row['avg_cost'] or 0, 6),
                }

            # Get play style from opponent models (observed by any player)
            cursor = conn.execute("""
                SELECT hands_observed, vpip, pfr, aggression_factor
                FROM opponent_models
                WHERE game_id = ? AND opponent_name = ?
                ORDER BY hands_observed DESC
                LIMIT 1
            """, (game_id, player_name))
            opp_row = cursor.fetchone()

            play_style = {}
            if opp_row:
                vpip = round(opp_row['vpip'] * 100, 1)
                pfr = round(opp_row['pfr'] * 100, 1)
                af = round(opp_row['aggression_factor'], 2)

                # Classify play style
                if vpip < 25:
                    tightness = 'tight'
                elif vpip > 35:
                    tightness = 'loose'
                else:
                    tightness = 'balanced'

                if af > 2:
                    aggression = 'aggressive'
                elif af < 1:
                    aggression = 'passive'
                else:
                    aggression = 'balanced'

                summary = f'{tightness}-{aggression}'

                play_style = {
                    'vpip': vpip,
                    'pfr': pfr,
                    'aggression_factor': af,
                    'hands_observed': opp_row['hands_observed'],
                    'summary': summary,
                }

            # Get recent decisions
            cursor = conn.execute("""
                SELECT hand_number, phase, action_taken, decision_quality, ev_lost
                FROM player_decision_analysis
                WHERE game_id = ? AND player_name = ?
                ORDER BY created_at DESC
                LIMIT 5
            """, (game_id, player_name))

            recent_decisions = [
                {
                    'hand_number': r['hand_number'],
                    'phase': r['phase'],
                    'action': r['action_taken'],
                    'decision_quality': r['decision_quality'],
                    'ev_lost': round(r['ev_lost'] or 0, 2) if r['ev_lost'] else None,
                }
                for r in cursor.fetchall()
            ]

            return {
                'player': {
                    'name': player_name,
                    'stack': player_data.get('stack', 0),
                    'cards': player_data.get('hand', []),
                },
                'psychology': psychology,
                'psychology_enabled': psychology_enabled,
                'llm_debug': llm_debug,
                'play_style': play_style,
                'recent_decisions': recent_decisions,
            }

    # ========== App Settings Methods ==========

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get an app setting by key, with optional default.

        Args:
            key: The setting key (e.g., 'LLM_PROMPT_CAPTURE')
            default: Default value if setting doesn't exist

        Returns:
            The setting value, or default if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT value FROM app_settings WHERE key = ?",
                    (key,)
                )
                row = cursor.fetchone()
                return row[0] if row else default
        except sqlite3.OperationalError:
            # Table doesn't exist yet (e.g., during startup)
            return default

    def set_setting(self, key: str, value: str, description: Optional[str] = None) -> bool:
        """Set an app setting.

        Args:
            key: The setting key
            value: The setting value (stored as string)
            description: Optional description for the setting

        Returns:
            True if successful
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO app_settings (key, value, description, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        description = COALESCE(excluded.description, app_settings.description),
                        updated_at = CURRENT_TIMESTAMP
                """, (key, value, description))
                conn.commit()
                logger.info(f"Setting '{key}' updated to '{value}'")
                return True
        except Exception as e:
            logger.error(f"Failed to set setting '{key}': {e}")
            return False

    def get_all_settings(self) -> Dict[str, Dict[str, Any]]:
        """Get all app settings.

        Returns:
            Dict mapping setting keys to their values and metadata
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT key, value, description, updated_at
                    FROM app_settings
                    ORDER BY key
                """)
                return {
                    row['key']: {
                        'value': row['value'],
                        'description': row['description'],
                        'updated_at': row['updated_at'],
                    }
                    for row in cursor.fetchall()
                }
        except sqlite3.OperationalError:
            return {}

    def delete_setting(self, key: str) -> bool:
        """Delete an app setting.

        Args:
            key: The setting key to delete

        Returns:
            True if the setting was deleted, False if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM app_settings WHERE key = ?",
                    (key,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete setting '{key}': {e}")
            return False
