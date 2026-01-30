"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
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
# v46: Add experiment manager features (error tracking, chat sessions, image models,
#      experiment lineage, image capture support)
# v47: Add prompt_presets table for reusable prompt configurations
# v48: Add capture_labels table for tagging captured AI decisions
# v49: Add replay experiment tables and experiment_type column
# v50: Add prompt_config_json to prompt_captures for analysis
# v51: Add stack_bb and already_bet_bb to prompt_captures for auto-labels
# v52: Add RBAC tables (groups, user_groups, permissions, group_permissions)
# v53: Add AI decision resilience columns to prompt_captures (parent_id, error_type, correction_attempt)
# v54: Squashed features - heartbeat tracking, outcome columns, system presets
# v55: Add last_game_created_at column to users table for duplicate game prevention
# v56: Add exploitative guidance to pro and competitive presets
# v57: Add raise_amount_bb to player_decision_analysis for BB-normalized mode
# v58: Fix v54 squash - apply missing heartbeat, outcome, and system preset columns
# v59: Add owner_id to prompt_captures for multi-user tracking
# v60: Add psychology snapshot columns to player_decision_analysis
# v61: Add coach_mode column to games table for per-game coaching config
SCHEMA_VERSION = 61


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

    def __init__(self, db_path: str = "data/poker_games.db"):
        self.db_path = db_path
        # Ensure directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                logger.warning(f"Could not create database directory {db_dir}: {e}")
        
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

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new database connection with standard timeout."""
        return sqlite3.connect(self.db_path, timeout=5.0)

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
        with self._get_connection() as conn:
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
                    llm_configs_json TEXT,
                    coach_mode TEXT DEFAULT 'off'
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

            # 20. Enabled models (v38, v50 adds user_enabled, v52 adds supports_img2img)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enabled_models (
                    id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    user_enabled INTEGER DEFAULT 1,
                    display_name TEXT,
                    notes TEXT,
                    supports_reasoning INTEGER DEFAULT 0,
                    supports_json_mode INTEGER DEFAULT 1,
                    supports_image_gen INTEGER DEFAULT 0,
                    supports_img2img INTEGER DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider, model)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_enabled_models_provider ON enabled_models(provider, enabled)")

            # 21. Prompt captures (v18, v19, v24, v30, v33, v39, v53, v52)
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
                    is_image_capture INTEGER DEFAULT 0,
                    image_prompt TEXT,
                    image_url TEXT,
                    image_data BLOB,
                    image_size TEXT,
                    image_width INTEGER,
                    image_height INTEGER,
                    target_personality TEXT,
                    target_emotion TEXT,
                    reference_image_id TEXT,
                    prompt_config_json TEXT,
                    stack_bb REAL,
                    already_bet_bb REAL,
                    owner_id TEXT,
                    parent_id INTEGER,
                    error_type TEXT,
                    error_description TEXT,
                    correction_attempt INTEGER DEFAULT 0,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE SET NULL,
                    FOREIGN KEY (parent_id) REFERENCES prompt_captures(id) ON DELETE SET NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)")
            # These indexes are on columns added by migrations v33, v39, v52, and v53
            # Use try-except to handle older databases that haven't been migrated yet
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_is_image ON prompt_captures(is_image_capture)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_parent ON prompt_captures(parent_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_owner ON prompt_captures(owner_id)")
            except sqlite3.OperationalError:
                pass  # Columns don't exist yet, will be created by migrations

            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)")

            # 21b. Reference images (v53) - for image-to-image generation
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reference_images (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_data BLOB NOT NULL,
                    width INTEGER,
                    height INTEGER,
                    content_type TEXT DEFAULT 'image/png',
                    source TEXT,
                    original_url TEXT,
                    owner_id TEXT,
                    expires_at TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reference_images_owner ON reference_images(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reference_images_expires ON reference_images(expires_at)")

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
                    raise_amount_bb REAL,
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
                    tilt_level REAL,
                    tilt_source TEXT,
                    valence REAL,
                    arousal REAL,
                    control REAL,
                    focus REAL,
                    display_emotion TEXT,
                    elastic_aggression REAL,
                    elastic_bluff_tendency REAL,
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
                    summary_json TEXT,
                    design_chat_json TEXT,
                    assistant_chat_json TEXT,
                    parent_experiment_id INTEGER REFERENCES experiments(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_name ON experiments(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)")
            # Note: idx_experiments_parent is created in v48 migration (parent_experiment_id added there)

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

            # 26. Experiment chat sessions (v47) - Persists design chat history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiment_chat_sessions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    messages_json TEXT NOT NULL,
                    config_snapshot_json TEXT NOT NULL,
                    config_versions_json TEXT,
                    is_archived BOOLEAN DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON experiment_chat_sessions(owner_id, updated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON experiment_chat_sessions(owner_id, is_archived)")

            # 27. App settings (v44) - Dynamic configuration
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
                    is_guest BOOLEAN DEFAULT 0,
                    last_game_created_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_linked_guest ON users(linked_guest_id)")

            # 28. Prompt presets (v47, v57) - Saved, reusable prompt configurations
            # v57 adds is_system column for built-in game mode presets
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    prompt_config TEXT,
                    guidance_injection TEXT,
                    owner_id TEXT,
                    is_system BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_presets_owner ON prompt_presets(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_presets_name ON prompt_presets(name)")

            # 29. Capture labels (v48) - Tags/labels for captured AI decisions
            conn.execute("""
                CREATE TABLE IF NOT EXISTS capture_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    label_type TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(capture_id, label)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_capture_labels_label ON capture_labels(label)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_capture_labels_capture_id ON capture_labels(capture_id)")

            # 30. Replay experiment captures (v49) - Links captures to replay experiments
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_experiment_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    original_action TEXT,
                    original_quality TEXT,
                    original_ev_lost REAL,
                    UNIQUE(experiment_id, capture_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_captures_experiment ON replay_experiment_captures(experiment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_captures_capture ON replay_experiment_captures(capture_id)")

            # 31. Replay results (v49) - Results from replaying captures with variants
            conn.execute("""
                CREATE TABLE IF NOT EXISTS replay_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    variant TEXT NOT NULL,
                    new_response TEXT,
                    new_action TEXT,
                    new_raise_amount INTEGER,
                    new_quality TEXT,
                    new_ev_lost REAL,
                    action_changed BOOLEAN,
                    quality_change TEXT,
                    ev_delta REAL,
                    provider TEXT,
                    model TEXT,
                    reasoning_effort TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    latency_ms INTEGER,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(experiment_id, capture_id, variant)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_experiment ON replay_results(experiment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_capture ON replay_results(capture_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_variant ON replay_results(variant)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_quality ON replay_results(quality_change)")

            # 32. Groups table (v52) - RBAC groups
            conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    is_system BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_groups_name ON groups(name)")

            # 33. User-Group mapping (v52) - many-to-many
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    assigned_by TEXT,
                    UNIQUE(user_id, group_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_groups_user ON user_groups(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_groups_group ON user_groups(group_id)")

            # 34. Permissions table (v52) - Available permissions
            conn.execute("""
                CREATE TABLE IF NOT EXISTS permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    category TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_permissions_name ON permissions(name)")

            # 35. Group-Permission mapping (v52) - many-to-many
            conn.execute("""
                CREATE TABLE IF NOT EXISTS group_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                    UNIQUE(group_id, permission_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_permissions_group ON group_permissions(group_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group_permissions_permission ON group_permissions(permission_id)")

    def _get_current_schema_version(self) -> int:
        """Get the current schema version from the database."""
        with self._get_connection() as conn:
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
            46: (self._migrate_v46_experiment_manager_features, "Add experiment manager features (error tracking, chat sessions, image models, experiment lineage, image capture support)"),
            47: (self._migrate_v47_add_prompt_presets, "Add prompt_presets table for reusable prompt configurations"),
            48: (self._migrate_v48_add_capture_labels, "Add capture_labels table for tagging captured AI decisions"),
            49: (self._migrate_v49_add_replay_experiment_tables, "Add replay experiment tables and experiment_type column"),
            50: (self._migrate_v50_add_prompt_config_to_captures, "Add prompt_config_json to prompt_captures for analysis"),
            51: (self._migrate_v51_add_stack_bb_columns, "Add stack_bb and already_bet_bb to prompt_captures for auto-labels"),
            52: (self._migrate_v52_add_rbac_tables, "Add RBAC tables (groups, user_groups, permissions, group_permissions)"),
            53: (self._migrate_v53_add_resilience_columns, "Add AI decision resilience columns to prompt_captures"),
            54: (self._migrate_v54_squashed_features, "Add heartbeat tracking, outcome columns, and system presets"),
            55: (self._migrate_v55_add_last_game_created_at, "Add last_game_created_at to users for duplicate prevention"),
            56: (self._migrate_v56_add_exploitative_guidance, "Add exploitative guidance to pro and competitive presets"),
            57: (self._migrate_v57_add_raise_amount_bb, "Add raise_amount_bb to player_decision_analysis for BB-normalized mode"),
            58: (self._migrate_v58_fix_squashed_features, "Fix v54 squash - apply missing heartbeat, outcome, and system preset columns"),
            59: (self._migrate_v59_add_owner_id_to_captures, "Add owner_id to prompt_captures for user tracking"),
            60: (self._migrate_v60_add_psychology_snapshot, "Add psychology snapshot columns to player_decision_analysis"),
            61: (self._migrate_v61_add_coach_mode, "Add coach_mode column to games table"),
        }

        with self._get_connection() as conn:
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

    def _migrate_v46_experiment_manager_features(self, conn: sqlite3.Connection) -> None:
        """Migration v46: Add experiment manager features.

        This combined migration adds all experiment manager functionality:
        - error_message column to api_usage table
        - experiment_chat_sessions table for design chat persistence
        - design_chat_json and assistant_chat_json columns to experiments
        - Pollinations and Runware image models to enabled_models
        - user_enabled column to enabled_models for dual toggle
        - parent_experiment_id to experiments for lineage tracking
        - supports_img2img column to enabled_models
        - Image capture support (reference_images table, prompt_captures columns)
        """
        from core.llm.config import POLLINATIONS_AVAILABLE_MODELS, RUNWARE_AVAILABLE_MODELS

        # 1. Add error_message column to api_usage
        api_usage_cols = [row[1] for row in conn.execute("PRAGMA table_info(api_usage)").fetchall()]
        if 'error_message' not in api_usage_cols:
            conn.execute("ALTER TABLE api_usage ADD COLUMN error_message TEXT")
            logger.info("Added error_message column to api_usage table")

        # 2. Create experiment_chat_sessions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS experiment_chat_sessions (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                messages_json TEXT NOT NULL,
                config_snapshot_json TEXT NOT NULL,
                config_versions_json TEXT,
                is_archived BOOLEAN DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON experiment_chat_sessions(owner_id, updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON experiment_chat_sessions(owner_id, is_archived)")

        # 3. Add chat columns to experiments table
        experiments_cols = [row[1] for row in conn.execute("PRAGMA table_info(experiments)").fetchall()]
        if 'design_chat_json' not in experiments_cols:
            conn.execute("ALTER TABLE experiments ADD COLUMN design_chat_json TEXT")
        if 'assistant_chat_json' not in experiments_cols:
            conn.execute("ALTER TABLE experiments ADD COLUMN assistant_chat_json TEXT")
        if 'parent_experiment_id' not in experiments_cols:
            conn.execute("ALTER TABLE experiments ADD COLUMN parent_experiment_id INTEGER REFERENCES experiments(id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_parent ON experiments(parent_experiment_id)")

        # 4. Add Pollinations image models
        pollinations_default_enabled = {"flux", "zimage"}
        for sort_order, model in enumerate(POLLINATIONS_AVAILABLE_MODELS):
            enabled = 1 if model in pollinations_default_enabled else 0
            conn.execute("""
                INSERT OR REPLACE INTO enabled_models
                (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, ("pollinations", model, enabled, 0, 0, 1, sort_order))

        # 5. Add Runware image models
        runware_default_enabled = {"runware:101@1"}
        for sort_order, model in enumerate(RUNWARE_AVAILABLE_MODELS):
            enabled = 1 if model in runware_default_enabled else 0
            conn.execute("""
                INSERT OR REPLACE INTO enabled_models
                (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, ("runware", model, enabled, 0, 0, 1, sort_order))

        # 6. Add user_enabled and supports_img2img columns to enabled_models
        try:
            conn.execute("ALTER TABLE enabled_models ADD COLUMN user_enabled INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE enabled_models ADD COLUMN supports_img2img INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Sync user_enabled with enabled for existing models
        conn.execute("UPDATE enabled_models SET user_enabled = enabled")

        # 7. Create reference_images table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reference_images (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                image_data BLOB NOT NULL,
                width INTEGER,
                height INTEGER,
                content_type TEXT DEFAULT 'image/png',
                source TEXT,
                original_url TEXT,
                owner_id TEXT,
                expires_at TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reference_images_owner ON reference_images(owner_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reference_images_expires ON reference_images(expires_at)")

        # 8. Add image capture columns to prompt_captures
        prompt_captures_cols = [row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()]
        image_columns = [
            ("is_image_capture", "INTEGER DEFAULT 0"),
            ("image_prompt", "TEXT"),
            ("image_url", "TEXT"),
            ("image_data", "BLOB"),
            ("image_size", "TEXT"),
            ("image_width", "INTEGER"),
            ("image_height", "INTEGER"),
            ("target_personality", "TEXT"),
            ("target_emotion", "TEXT"),
            ("reference_image_id", "TEXT"),
        ]
        for col_name, col_type in image_columns:
            if col_name not in prompt_captures_cols:
                conn.execute(f"ALTER TABLE prompt_captures ADD COLUMN {col_name} {col_type}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_is_image ON prompt_captures(is_image_capture)")

        logger.info("Migration v46 complete: Added experiment manager features")

    def _migrate_v47_add_prompt_presets(self, conn: sqlite3.Connection) -> None:
        """Migration v47: Add prompt_presets table for reusable prompt configurations.

        This table stores saved prompt configurations that can be applied to
        tournament variants or replay experiments for A/B testing.
        """
        # Check if table already exists (for fresh databases)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_presets'"
        )
        if cursor.fetchone():
            logger.info("prompt_presets table already exists, skipping creation")
        else:
            conn.execute("""
                CREATE TABLE prompt_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    prompt_config TEXT,
                    guidance_injection TEXT,
                    owner_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_presets_owner ON prompt_presets(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_presets_name ON prompt_presets(name)")
            logger.info("Created prompt_presets table")

        logger.info("Migration v47 complete: Added prompt_presets table")

    def _migrate_v48_add_capture_labels(self, conn: sqlite3.Connection) -> None:
        """Migration v48: Add capture_labels table for tagging captured AI decisions.

        This table enables labeling/tagging of captured AI decisions for easier
        filtering and selection in replay experiments.
        """
        # Check if table already exists (for fresh databases)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='capture_labels'"
        )
        if cursor.fetchone():
            logger.info("capture_labels table already exists, skipping creation")
        else:
            conn.execute("""
                CREATE TABLE capture_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    label_type TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(capture_id, label)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_capture_labels_label ON capture_labels(label)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_capture_labels_capture_id ON capture_labels(capture_id)")
            logger.info("Created capture_labels table")

        logger.info("Migration v48 complete: Added capture_labels table")

    def _migrate_v49_add_replay_experiment_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v49: Add replay experiment tables and experiment_type column.

        This migration adds tables for replay experiments that re-run captured
        AI decisions with different variants (models, prompts, etc.).
        """
        # Add experiment_type column to experiments table
        cursor = conn.execute("PRAGMA table_info(experiments)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'experiment_type' not in columns:
            conn.execute("ALTER TABLE experiments ADD COLUMN experiment_type TEXT DEFAULT 'tournament'")
            logger.info("Added experiment_type column to experiments table")

        # Create replay_experiment_captures table
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='replay_experiment_captures'"
        )
        if not cursor.fetchone():
            conn.execute("""
                CREATE TABLE replay_experiment_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    original_action TEXT,
                    original_quality TEXT,
                    original_ev_lost REAL,
                    UNIQUE(experiment_id, capture_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_captures_experiment ON replay_experiment_captures(experiment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_captures_capture ON replay_experiment_captures(capture_id)")
            logger.info("Created replay_experiment_captures table")

        # Create replay_results table
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='replay_results'"
        )
        if not cursor.fetchone():
            conn.execute("""
                CREATE TABLE replay_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    variant TEXT NOT NULL,
                    new_response TEXT,
                    new_action TEXT,
                    new_raise_amount INTEGER,
                    new_quality TEXT,
                    new_ev_lost REAL,
                    action_changed BOOLEAN,
                    quality_change TEXT,
                    ev_delta REAL,
                    provider TEXT,
                    model TEXT,
                    reasoning_effort TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    latency_ms INTEGER,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(experiment_id, capture_id, variant)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_experiment ON replay_results(experiment_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_capture ON replay_results(capture_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_variant ON replay_results(variant)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_replay_results_quality ON replay_results(quality_change)")
            logger.info("Created replay_results table")

        logger.info("Migration v49 complete: Added replay experiment tables")

    def _migrate_v50_add_prompt_config_to_captures(self, conn: sqlite3.Connection) -> None:
        """Migration v50: Add prompt_config_json to prompt_captures.

        This column stores the PromptConfig settings active when the capture was made,
        making it easy to analyze how different configs affect AI behavior.
        """
        prompt_captures_cols = [row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()]

        if 'prompt_config_json' not in prompt_captures_cols:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN prompt_config_json TEXT")
            logger.info("Added prompt_config_json column to prompt_captures")

        logger.info("Migration v50 complete: prompt_config_json added to prompt_captures")

    def _migrate_v51_add_stack_bb_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v51: Add stack_bb and already_bet_bb to prompt_captures.

        These columns enable auto-labeling of decisions in the Decision Analyzer:
        - SHORT_STACK: Folding with < 3 BB
        - POT_COMMITTED: Folding after investing > remaining stack
        """
        columns = [row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()]

        if 'stack_bb' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN stack_bb REAL")
            logger.info("Added stack_bb column to prompt_captures")

        if 'already_bet_bb' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN already_bet_bb REAL")
            logger.info("Added already_bet_bb column to prompt_captures")

        logger.info("Migration v51 complete: stack_bb and already_bet_bb added to prompt_captures")

    def _migrate_v52_add_rbac_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v52: Add RBAC tables for role-based access control.

        Creates 4 tables for managing user groups and permissions:
        - groups: Admin, user, etc.
        - user_groups: Maps users to groups (many-to-many)
        - permissions: Available permissions like can_access_admin_tools
        - group_permissions: Maps groups to permissions (many-to-many)

        Also seeds initial data:
        - Groups: admin (system), user (system)
        - Permissions: can_access_admin_tools, can_access_full_game
        - admin group: both permissions
        - user group: can_access_full_game only
        """
        # Check if tables already exist (for fresh databases)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='groups'"
        )
        if not cursor.fetchone():
            # Create groups table
            conn.execute("""
                CREATE TABLE groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    is_system BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX idx_groups_name ON groups(name)")
            logger.info("Created groups table")

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_groups'"
        )
        if not cursor.fetchone():
            # Create user_groups table
            conn.execute("""
                CREATE TABLE user_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    assigned_by TEXT,
                    UNIQUE(user_id, group_id)
                )
            """)
            conn.execute("CREATE INDEX idx_user_groups_user ON user_groups(user_id)")
            conn.execute("CREATE INDEX idx_user_groups_group ON user_groups(group_id)")
            logger.info("Created user_groups table")

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='permissions'"
        )
        if not cursor.fetchone():
            # Create permissions table
            conn.execute("""
                CREATE TABLE permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    category TEXT
                )
            """)
            conn.execute("CREATE INDEX idx_permissions_name ON permissions(name)")
            logger.info("Created permissions table")

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='group_permissions'"
        )
        if not cursor.fetchone():
            # Create group_permissions table
            conn.execute("""
                CREATE TABLE group_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                    UNIQUE(group_id, permission_id)
                )
            """)
            conn.execute("CREATE INDEX idx_group_permissions_group ON group_permissions(group_id)")
            conn.execute("CREATE INDEX idx_group_permissions_permission ON group_permissions(permission_id)")
            logger.info("Created group_permissions table")

        # Seed initial data
        # Insert default groups if they don't exist
        conn.execute("""
            INSERT OR IGNORE INTO groups (name, description, is_system)
            VALUES ('admin', 'Administrators with full access to admin tools', 1)
        """)
        conn.execute("""
            INSERT OR IGNORE INTO groups (name, description, is_system)
            VALUES ('user', 'Registered users with full game access', 1)
        """)

        # Insert default permissions
        conn.execute("""
            INSERT OR IGNORE INTO permissions (name, description, category)
            VALUES ('can_access_admin_tools', 'Access to the Admin Tools dashboard', 'admin')
        """)
        conn.execute("""
            INSERT OR IGNORE INTO permissions (name, description, category)
            VALUES ('can_access_full_game', 'Access to full game features including menu and game selection', 'game')
        """)

        # Grant can_access_admin_tools to admin group
        conn.execute("""
            INSERT OR IGNORE INTO group_permissions (group_id, permission_id)
            SELECT g.id, p.id
            FROM groups g, permissions p
            WHERE g.name = 'admin' AND p.name = 'can_access_admin_tools'
        """)

        # Grant can_access_full_game to both admin and user groups
        conn.execute("""
            INSERT OR IGNORE INTO group_permissions (group_id, permission_id)
            SELECT g.id, p.id
            FROM groups g, permissions p
            WHERE g.name IN ('admin', 'user') AND p.name = 'can_access_full_game'
        """)

        logger.info("Migration v52 complete: RBAC tables added with initial data")

    def _migrate_v53_add_resilience_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v53: Add AI decision resilience columns to prompt_captures.

        These columns enable tracking of error recovery attempts:
        - parent_id: Links correction attempts to the original failed capture
        - error_type: Type of error detected (malformed_json, missing_field, invalid_action, semantic_error)
        - correction_attempt: 0 for original, 1+ for correction attempts
        """
        columns = [row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()]

        if 'parent_id' not in columns:
            # Note: SQLite doesn't enforce FK constraints added via ALTER TABLE, but we include
            # the REFERENCES clause for documentation. The actual constraint is enforced by
            # application logic. ON DELETE SET NULL matches the schema in _init_db().
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN parent_id INTEGER REFERENCES prompt_captures(id) ON DELETE SET NULL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_parent ON prompt_captures(parent_id)")
            logger.info("Added parent_id column to prompt_captures")

        if 'error_type' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN error_type TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_error_type ON prompt_captures(error_type)")
            logger.info("Added error_type column to prompt_captures")

        if 'error_description' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN error_description TEXT")
            logger.info("Added error_description column to prompt_captures")

        if 'correction_attempt' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN correction_attempt INTEGER DEFAULT 0")
            logger.info("Added correction_attempt column to prompt_captures")

        logger.info("Migration v53 complete: AI decision resilience columns added to prompt_captures")

    def _migrate_v54_squashed_features(self, conn: sqlite3.Connection) -> None:
        """Migration v54: Squashed features from baseline-prompt branch.

        Combines multiple migrations into one:
        - experiment_games: heartbeat tracking columns
        - tournament_standings: outcome columns, times_eliminated, all_in tracking
        - prompt_presets: is_system column and system presets (casual, standard, pro, competitive)
        """
        # === Heartbeat tracking columns (experiment_games) ===
        experiment_games_cols = [
            ("state", "TEXT DEFAULT 'idle'"),
            ("last_heartbeat_at", "TIMESTAMP"),
            ("last_api_call_started_at", "TIMESTAMP"),
            ("process_id", "INTEGER"),
            ("resume_lock_acquired_at", "TIMESTAMP"),
        ]

        for col_name, col_def in experiment_games_cols:
            try:
                conn.execute(f"ALTER TABLE experiment_games ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added {col_name} column to experiment_games")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    logger.debug(f"Column {col_name} already exists in experiment_games")
                else:
                    raise

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_experiment_games_state_heartbeat
            ON experiment_games(state, last_heartbeat_at)
        """)

        # === Outcome and tracking columns (tournament_standings) ===
        tournament_standings_cols = [
            ("final_stack", "INTEGER"),
            ("hands_won", "INTEGER"),
            ("hands_played", "INTEGER"),
            ("times_eliminated", "INTEGER"),
            ("all_in_wins", "INTEGER"),
            ("all_in_losses", "INTEGER"),
        ]

        for col_name, col_def in tournament_standings_cols:
            try:
                conn.execute(f"ALTER TABLE tournament_standings ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added {col_name} column to tournament_standings")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    logger.debug(f"Column {col_name} already exists in tournament_standings")
                else:
                    raise

        # === System presets (prompt_presets) ===
        try:
            conn.execute("ALTER TABLE prompt_presets ADD COLUMN is_system BOOLEAN DEFAULT FALSE")
            logger.info("Added is_system column to prompt_presets")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("Column is_system already exists in prompt_presets")
            else:
                raise

        system_presets = [
            {
                'name': 'casual',
                'description': 'Casual mode - personality-driven fun poker with full expressiveness',
                'prompt_config': {},
            },
            {
                'name': 'standard',
                'description': 'Standard mode - balanced personality with GTO awareness (shows equity comparisons)',
                'prompt_config': {'gto_equity': True},
            },
            {
                'name': 'pro',
                'description': 'Pro mode - GTO-focused analytical poker with explicit equity verdicts',
                'prompt_config': {
                    'gto_equity': True,
                    'gto_verdict': True,
                    'chattiness': False,
                    'dramatic_sequence': False,
                },
            },
            {
                'name': 'competitive',
                'description': 'Competitive mode - full GTO guidance with personality and trash talk',
                'prompt_config': {
                    'gto_equity': True,
                    'gto_verdict': True,
                },
            },
        ]

        for preset in system_presets:
            try:
                conn.execute("""
                    INSERT INTO prompt_presets (name, description, prompt_config, is_system, owner_id)
                    VALUES (?, ?, ?, TRUE, 'system')
                """, (
                    preset['name'],
                    preset['description'],
                    json.dumps(preset['prompt_config']),
                ))
                logger.info(f"Created system preset '{preset['name']}'")
            except sqlite3.IntegrityError:
                conn.execute("""
                    UPDATE prompt_presets
                    SET description = ?, prompt_config = ?, is_system = TRUE, owner_id = 'system'
                    WHERE name = ?
                """, (
                    preset['description'],
                    json.dumps(preset['prompt_config']),
                    preset['name'],
                ))
                logger.info(f"Updated existing preset '{preset['name']}' as system preset")

        logger.info("Migration v54 complete: squashed features added")

    def _migrate_v55_add_last_game_created_at(self, conn: sqlite3.Connection) -> None:
        """Migration v55: Add last_game_created_at column to users table for duplicate game prevention."""
        try:
            conn.execute("ALTER TABLE users ADD COLUMN last_game_created_at REAL")
            logger.info("Added last_game_created_at column to users table")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("Column last_game_created_at already exists in users")
            else:
                raise
        logger.info("Migration v55 complete: last_game_created_at added to users")

    def _migrate_v56_add_exploitative_guidance(self, conn: sqlite3.Connection) -> None:
        """Migration v56: Add exploitative guidance to pro and competitive presets.

        No-op — system presets are now managed by config/game_modes.yaml
        and synced on every app startup via sync_game_modes_from_yaml().
        """
        logger.info("Migration v56: no-op, YAML sync handles system preset updates")

    def _migrate_v57_add_raise_amount_bb(self, conn: sqlite3.Connection) -> None:
        """Migration v57: Add raise_amount_bb to player_decision_analysis.

        This column stores the BB-normalized raise amount when BB mode
        is enabled, allowing analysis of AI betting patterns in BB terms.
        """
        columns = [row[1] for row in conn.execute("PRAGMA table_info(player_decision_analysis)").fetchall()]

        if 'raise_amount_bb' not in columns:
            conn.execute("ALTER TABLE player_decision_analysis ADD COLUMN raise_amount_bb REAL")
            logger.info("Added raise_amount_bb column to player_decision_analysis")

        logger.info("Migration v57 complete: raise_amount_bb added to player_decision_analysis")

    def _migrate_v58_fix_squashed_features(self, conn: sqlite3.Connection) -> None:
        """Migration v58: Apply columns that v54 was supposed to add.

        The v54 squashed migration got its version number shuffled during
        a branch squash-merge, so it recorded as applied but the actual
        ALTER TABLEs never ran. This re-applies them idempotently.
        """
        # === Heartbeat tracking columns (experiment_games) ===
        for col_name, col_def in [
            ("state", "TEXT DEFAULT 'idle'"),
            ("last_heartbeat_at", "TIMESTAMP"),
            ("last_api_call_started_at", "TIMESTAMP"),
            ("process_id", "INTEGER"),
            ("resume_lock_acquired_at", "TIMESTAMP"),
        ]:
            try:
                conn.execute(f"ALTER TABLE experiment_games ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added {col_name} column to experiment_games")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    logger.debug(f"Column {col_name} already exists in experiment_games")
                else:
                    raise

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_experiment_games_state_heartbeat
            ON experiment_games(state, last_heartbeat_at)
        """)

        # === Outcome and tracking columns (tournament_standings) ===
        for col_name, col_def in [
            ("final_stack", "INTEGER"),
            ("hands_won", "INTEGER"),
            ("hands_played", "INTEGER"),
            ("times_eliminated", "INTEGER"),
            ("all_in_wins", "INTEGER"),
            ("all_in_losses", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE tournament_standings ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added {col_name} column to tournament_standings")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    logger.debug(f"Column {col_name} already exists in tournament_standings")
                else:
                    raise

        # === is_system column (prompt_presets) ===
        try:
            conn.execute("ALTER TABLE prompt_presets ADD COLUMN is_system BOOLEAN DEFAULT FALSE")
            logger.info("Added is_system column to prompt_presets")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.debug("Column is_system already exists in prompt_presets")
            else:
                raise

        logger.info("Migration v58 complete: fixed missing v54 squashed feature columns")

    def _migrate_v59_add_owner_id_to_captures(self, conn: sqlite3.Connection) -> None:
        """Migration v59: Add owner_id to prompt_captures.

        This column enables tracking which user generated an image or triggered
        an AI decision, even when the game is not associated with a specific user.
        """
        columns = [row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()]

        if 'owner_id' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN owner_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_captures_owner ON prompt_captures(owner_id)")
            logger.info("Added owner_id column to prompt_captures")

        logger.info("Migration v59 complete: owner_id added to prompt_captures")

    def _migrate_v60_add_psychology_snapshot(self, conn: sqlite3.Connection) -> None:
        """Migration v60: Add psychology snapshot columns to player_decision_analysis.

        Captures emotional state, tilt, and elastic trait values at the moment
        each AI decision is made, enabling analysis of how psychology impacts
        decision quality.
        """
        columns = [row[1] for row in conn.execute("PRAGMA table_info(player_decision_analysis)").fetchall()]

        new_columns = [
            ('tilt_level', 'REAL'),
            ('tilt_source', 'TEXT'),
            ('valence', 'REAL'),
            ('arousal', 'REAL'),
            ('control', 'REAL'),
            ('focus', 'REAL'),
            ('display_emotion', 'TEXT'),
            ('elastic_aggression', 'REAL'),
            ('elastic_bluff_tendency', 'REAL'),
        ]

        for col_name, col_type in new_columns:
            if col_name not in columns:
                conn.execute(f"ALTER TABLE player_decision_analysis ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added {col_name} column to player_decision_analysis")

        logger.info("Migration v60 complete: psychology snapshot columns added to player_decision_analysis")

    def _migrate_v61_add_coach_mode(self, conn: sqlite3.Connection) -> None:
        """Migration v61: Add coach_mode column to games table."""
        columns = [row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()]
        if 'coach_mode' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN coach_mode TEXT DEFAULT 'off'")
            logger.info("Added coach_mode column to games table")
        logger.info("Migration v61 complete: coach_mode column added to games")

    def save_coach_mode(self, game_id: str, mode: str) -> None:
        """Persist coach mode preference for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE games SET coach_mode = ? WHERE game_id = ?",
                (mode, game_id)
            )
            conn.commit()

    def load_coach_mode(self, game_id: str) -> str:
        """Load coach mode preference for a game. Defaults to 'off'."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT coach_mode FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else 'off'

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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
                logger.warning(f"[RESTORE] Could not restore phase {state_dict.get('current_phase')}, using INITIALIZING_HAND")
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM games WHERE owner_id = ?
            """, (owner_id,))
            return cursor.fetchone()[0]

    def get_last_game_creation_time(self, owner_id: str) -> Optional[float]:
        """Get the timestamp of the user's last game creation."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT last_game_created_at FROM users WHERE id = ?",
                (owner_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else None

    def update_last_game_creation_time(self, owner_id: str, timestamp: float) -> None:
        """Update the user's last game creation timestamp."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET last_game_created_at = ? WHERE id = ?",
                (timestamp, owner_id)
            )

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

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO users (id, email, name, picture, created_at, last_login, linked_guest_id, is_guest)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (user_id, email, name, picture, now, now, linked_guest_id))

            # Auto-assign to 'user' group for full game access
            conn.execute("""
                INSERT OR IGNORE INTO user_groups (user_id, group_id, assigned_by)
                SELECT ?, id, 'system' FROM groups WHERE name = 'user'
            """, (user_id,))

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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE games
                SET owner_id = ?, owner_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE owner_id = ?
            """, (to_owner_id, to_owner_name, from_owner_id))
            return cursor.rowcount

    # ==================== RBAC / Group Management Methods ====================

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users (both Google and guest users).

        Returns:
            List of user dicts with groups included.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get all users
            cursor = conn.execute("""
                SELECT id, email, name, picture, created_at, last_login, linked_guest_id, is_guest
                FROM users
                ORDER BY last_login DESC NULLS LAST, created_at DESC
            """)
            rows = cursor.fetchall()

            # Build enriched user dicts immutably (functional approach)
            return [
                {**dict(row), 'groups': self.get_user_groups(row['id'])}
                for row in rows
            ]

    def get_user_groups(self, user_id: str) -> List[str]:
        """Get all group names for a user.

        Args:
            user_id: The user's ID

        Returns:
            List of group names the user belongs to
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT g.name
                FROM groups g
                JOIN user_groups ug ON g.id = ug.group_id
                WHERE ug.user_id = ?
                ORDER BY g.name
            """, (user_id,))
            return [row[0] for row in cursor.fetchall()]

    def get_user_permissions(self, user_id: str) -> List[str]:
        """Get all permissions for a user via their groups.

        Args:
            user_id: The user's ID

        Returns:
            List of permission names the user has
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT DISTINCT p.name
                FROM permissions p
                JOIN group_permissions gp ON p.id = gp.permission_id
                JOIN user_groups ug ON gp.group_id = ug.group_id
                WHERE ug.user_id = ?
                ORDER BY p.name
            """, (user_id,))
            return [row[0] for row in cursor.fetchall()]

    def assign_user_to_group(self, user_id: str, group_name: str, assigned_by: Optional[str] = None) -> bool:
        """Assign a user to a group.

        Args:
            user_id: The user's ID
            group_name: The name of the group
            assigned_by: ID of the user making the assignment

        Returns:
            True if successful, False if group doesn't exist

        Raises:
            ValueError: If trying to assign a guest user to admin group (unless configured via INITIAL_ADMIN_EMAIL)
            ValueError: If user_id doesn't exist in database (for non-guest users)
        """
        # Guest users can only be assigned to admin if configured via INITIAL_ADMIN_EMAIL
        # (handled at startup by initialize_admin_from_env)
        # For runtime API calls, prevent guest admin assignment
        if group_name == 'admin' and user_id.startswith('guest_'):
            # Check if this guest is the configured initial admin
            initial_admin = os.environ.get('INITIAL_ADMIN_EMAIL', '')
            if user_id != initial_admin:
                raise ValueError("Guest users cannot be assigned to the admin group")

        with sqlite3.connect(self.db_path) as conn:
            # Validate user exists (skip for guest_ users - they're session-only)
            if not user_id.startswith('guest_'):
                cursor = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,))
                if not cursor.fetchone():
                    raise ValueError(f"User {user_id} does not exist")

            # Get group ID
            cursor = conn.execute("SELECT id FROM groups WHERE name = ?", (group_name,))
            row = cursor.fetchone()
            if not row:
                return False

            group_id = row[0]

            # Insert the mapping (or ignore if already exists)
            conn.execute("""
                INSERT OR IGNORE INTO user_groups (user_id, group_id, assigned_by)
                VALUES (?, ?, ?)
            """, (user_id, group_id, assigned_by))

            return True

    def remove_user_from_group(self, user_id: str, group_name: str) -> bool:
        """Remove a user from a group.

        Args:
            user_id: The user's ID
            group_name: The name of the group

        Returns:
            True if successfully removed, False if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM user_groups
                WHERE user_id = ? AND group_id = (SELECT id FROM groups WHERE name = ?)
            """, (user_id, group_name))
            return cursor.rowcount > 0

    def count_users_in_group(self, group_name: str) -> int:
        """Count the number of users in a group.

        Args:
            group_name: The name of the group

        Returns:
            Number of users in the group
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM user_groups ug
                JOIN groups g ON ug.group_id = g.id
                WHERE g.name = ?
            """, (group_name,))
            return cursor.fetchone()[0]

    def get_all_groups(self) -> List[Dict[str, Any]]:
        """Get all available groups.

        Returns:
            List of group dicts with id, name, description, is_system
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, name, description, is_system, created_at
                FROM groups
                ORDER BY is_system DESC, name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """Get statistics for a user from api_usage and games tables.

        Args:
            user_id: The user's ID

        Returns:
            Dict with total_cost, hands_played, games_completed, last_active
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get total cost from api_usage
            cursor = conn.execute("""
                SELECT COALESCE(SUM(estimated_cost), 0) as total_cost
                FROM api_usage
                WHERE owner_id = ?
            """, (user_id,))
            total_cost = cursor.fetchone()[0] or 0

            # Get hands played (count of player_decision calls)
            cursor = conn.execute("""
                SELECT COUNT(*) as hands_played
                FROM api_usage
                WHERE owner_id = ? AND call_type = 'player_decision'
            """, (user_id,))
            hands_played = cursor.fetchone()[0] or 0

            # Get games completed (distinct game_ids)
            cursor = conn.execute("""
                SELECT COUNT(DISTINCT game_id) as games_completed
                FROM games
                WHERE owner_id = ?
            """, (user_id,))
            games_completed = cursor.fetchone()[0] or 0

            # Get last active timestamp
            cursor = conn.execute("""
                SELECT MAX(created_at) as last_active
                FROM api_usage
                WHERE owner_id = ?
            """, (user_id,))
            last_active_row = cursor.fetchone()
            last_active = last_active_row[0] if last_active_row else None

            return {
                'total_cost': round(total_cost, 4),
                'hands_played': hands_played,
                'games_completed': games_completed,
                'last_active': last_active
            }

    def initialize_admin_from_env(self) -> Optional[str]:
        """Assign admin group to user with INITIAL_ADMIN_EMAIL.

        Called on startup to ensure the initial admin is configured.
        Supports both email addresses (for Google users) and guest IDs (e.g., "guest_jeff").

        Returns:
            User ID of the admin if found and assigned, None otherwise
        """
        admin_id = os.environ.get('INITIAL_ADMIN_EMAIL')
        if not admin_id:
            return None

        # Support guest format: if starts with "guest_", use as user_id directly
        if admin_id.startswith('guest_'):
            user_id = admin_id
            logger.info(f"INITIAL_ADMIN_EMAIL configured for guest user: {user_id}")
        else:
            # Regular email - look up user
            user = self.get_user_by_email(admin_id)
            if not user:
                logger.info(f"Initial admin email {admin_id} not found in users table yet")
                return None
            user_id = user['id']

        # Check if user already has admin group
        groups = self.get_user_groups(user_id)
        if 'admin' in groups:
            logger.debug(f"User {user_id} already has admin group")
            return user_id

        # Assign admin group
        if self.assign_user_to_group(user_id, 'admin', assigned_by='system'):
            logger.info(f"Assigned admin group to {user_id}")
            return user_id

        return None

    def get_available_providers(self) -> Set[str]:
        """Get the set of all providers in the system.

        Returns:
            Set of all provider names in enabled_models table.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT provider
                FROM enabled_models
            """)
            return {row[0] for row in cursor.fetchall()}

    def get_enabled_models(self) -> Dict[str, List[str]]:
        """Get all enabled models grouped by provider.

        Returns:
            Dict mapping provider name to list of enabled model names.
            Example: {'openai': ['gpt-4o', 'gpt-5-nano'], 'groq': ['llama-3.1-8b-instant']}
        """
        with self._get_connection() as conn:
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
            List of dicts with provider, model, enabled, user_enabled, display_name, etc.
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, provider, model, enabled, user_enabled, display_name, notes,
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            # Delete all associated data (order matters for foreign keys)
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_messages WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
    
    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO game_messages (game_id, message_type, message_text)
                VALUES (?, ?, ?)
            """, (game_id, message_type, message_text))
    
    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        with self._get_connection() as conn:
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
                has_acted=player_data['has_acted'],
                last_action=player_data.get('last_action')
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
        with self._get_connection() as conn:
            conversation_history = json.dumps(messages)
            personality_json = json.dumps(personality_state)
            
            conn.execute("""
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """, (game_id, player_name, conversation_history, personality_json))
    
    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE personalities 
                SET times_used = times_used + 1 
                WHERE name = ?
            """, (name,))
    
    def list_personalities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all personalities with metadata."""
        with self._get_connection() as conn:
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
            with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            conn.execute("DELETE FROM emotional_state WHERE game_id = ?", (game_id,))

    def delete_controller_state_for_game(self, game_id: str) -> None:
        """Delete all controller states for a game."""
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
                     eliminated_by, eliminated_at_hand, final_stack, hands_won, hands_played,
                     times_eliminated, all_in_wins, all_in_losses)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    game_id,
                    standing.get('player_name'),
                    standing.get('is_human', False),
                    standing.get('finishing_position'),
                    standing.get('eliminated_by'),
                    standing.get('eliminated_at_hand'),
                    standing.get('final_stack'),
                    standing.get('hands_won'),
                    standing.get('hands_played'),
                    standing.get('times_eliminated', 0),
                    standing.get('all_in_wins', 0),
                    standing.get('all_in_losses', 0),
                ))

    def get_tournament_result(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament result for a completed game."""
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ? AND full_image_data IS NOT NULL
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def has_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the given personality and emotion."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def get_available_avatar_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have avatar images for a personality."""
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM avatar_images WHERE personality_name = ?
            """, (personality_name,))
            return cursor.rowcount

    def list_personalities_with_avatars(self) -> List[Dict[str, Any]]:
        """Get list of all personalities that have at least one avatar image.

        Returns:
            List of dicts with personality_name and emotion_count
        """
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO prompt_captures (
                    -- Identity
                    game_id, player_name, hand_number,
                    -- Game State
                    phase, pot_total, cost_to_call, pot_odds, player_stack,
                    stack_bb, already_bet_bb,
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
                    tags, notes,
                    -- Prompt Config (for analysis)
                    prompt_config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                capture.get('stack_bb'),
                capture.get('already_bet_bb'),
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
                # Prompt Config (for analysis)
                json.dumps(capture.get('prompt_config')) if capture.get('prompt_config') else None,
            ))
            conn.commit()
            return cursor.lastrowid

    def get_prompt_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get a single prompt capture by ID.

        Joins with api_usage to get cached_tokens, reasoning_tokens, and estimated_cost.
        """
        with self._get_connection() as conn:
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

            # Handle image_data BLOB - convert to base64 data URL for JSON serialization
            if capture.get('is_image_capture') and capture.get('image_data'):
                import base64
                img_bytes = capture['image_data']
                if isinstance(img_bytes, bytes):
                    b64_data = base64.b64encode(img_bytes).decode('utf-8')
                    capture['image_url'] = f"data:image/png;base64,{b64_data}"
                # Remove raw bytes from response (not JSON serializable)
                del capture['image_data']
            elif 'image_data' in capture:
                # Remove even if None/empty to avoid serialization issues
                del capture['image_data']
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
        error_type: Optional[str] = None,
        has_error: Optional[bool] = None,
        is_correction: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List prompt captures with optional filtering.

        Args:
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)

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
        if error_type:
            conditions.append("error_type = ?")
            params.append(error_type)
        if has_error is True:
            conditions.append("error_type IS NOT NULL")
        elif has_error is False:
            conditions.append("error_type IS NULL")
        if is_correction is True:
            conditions.append("parent_id IS NOT NULL")
        elif is_correction is False:
            conditions.append("parent_id IS NULL")
        if tags:
            # Match any of the provided tags
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
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
                       community_cards, player_hand, model, provider, latency_ms, tags, notes,
                       error_type, error_description, parent_id, correction_attempt
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

        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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
                       tags, notes,
                       is_image_capture, image_size, image_width, image_height,
                       target_personality, target_emotion
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
        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO player_decision_analysis (
                    request_id, capture_id,
                    game_id, player_name, hand_number, phase, player_position,
                    pot_total, cost_to_call, player_stack, num_opponents,
                    player_hand, community_cards,
                    action_taken, raise_amount, raise_amount_bb,
                    equity, required_equity, ev_call,
                    optimal_action, decision_quality, ev_lost,
                    hand_rank, relative_strength,
                    equity_vs_ranges, opponent_positions,
                    tilt_level, tilt_source,
                    valence, arousal, control, focus,
                    display_emotion, elastic_aggression, elastic_bluff_tendency,
                    analyzer_version, processing_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                data.get('raise_amount_bb'),
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
                data.get('tilt_level'),
                data.get('tilt_source'),
                data.get('valence'),
                data.get('arousal'),
                data.get('control'),
                data.get('focus'),
                data.get('display_emotion'),
                data.get('elastic_aggression'),
                data.get('elastic_bluff_tendency'),
                data.get('analyzer_version'),
                data.get('processing_time_ms'),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_decision_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        """Get a single decision analysis by ID."""
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

        Links via capture_id (preferred) or request_id (fallback).
        Note: request_id fallback only works when request_id is non-empty,
        as some providers (Google/Gemini) don't return request IDs.
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            # First try direct capture_id link (preferred, always reliable)
            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE capture_id = ?",
                (capture_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # Fall back to request_id link, but ONLY if request_id is non-empty
            # Empty string matches would cause incorrect results
            cursor = conn.execute("""
                SELECT pda.*
                FROM player_decision_analysis pda
                JOIN prompt_captures pc ON pc.original_request_id = pda.request_id
                WHERE pc.id = ?
                  AND pc.original_request_id IS NOT NULL
                  AND pc.original_request_id != ''
                  AND pda.request_id IS NOT NULL
                  AND pda.request_id != ''
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

        with self._get_connection() as conn:
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

        with self._get_connection() as conn:
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

    def create_experiment(self, config: Dict, parent_experiment_id: Optional[int] = None) -> int:
        """Create a new experiment record.

        Args:
            config: Dictionary containing experiment configuration with keys:
                - name: Unique experiment name (required)
                - description: Experiment description (optional)
                - hypothesis: What we're testing (optional)
                - tags: List of tags (optional)
                - notes: Additional notes (optional)
                - Additional config fields stored as config_json
            parent_experiment_id: Optional ID of the parent experiment for lineage tracking

        Returns:
            The experiment_id of the created record

        Raises:
            sqlite3.IntegrityError: If experiment name already exists
        """
        name = config.get('name')
        if not name:
            raise ValueError("Experiment name is required")

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO experiments (name, description, hypothesis, tags, notes, config_json, parent_experiment_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                name,
                config.get('description'),
                config.get('hypothesis'),
                json.dumps(config.get('tags', [])),
                config.get('notes'),
                json.dumps(config),
                parent_experiment_id,
            ))
            conn.commit()
            experiment_id = cursor.lastrowid
            logger.info(f"Created experiment '{name}' with id {experiment_id}" +
                       (f" (parent: {parent_experiment_id})" if parent_experiment_id else ""))
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, name, description, hypothesis, tags, notes, config_json,
                       status, created_at, completed_at, summary_json, parent_experiment_id
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
                'parent_experiment_id': row[11],
            }

    def get_experiment_by_name(self, name: str) -> Optional[Dict]:
        """Get experiment details by name.

        Args:
            name: The experiment name

        Returns:
            Dictionary with experiment details or None if not found
        """
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

    def update_experiment_game_heartbeat(
        self,
        game_id: str,
        state: str,
        api_call_started: bool = False,
        process_id: Optional[int] = None
    ) -> None:
        """Update heartbeat for an experiment game.

        Args:
            game_id: The game ID (tournament_id)
            state: Current state ('idle', 'calling_api', 'processing')
            api_call_started: If True, also update last_api_call_started_at
            process_id: Optional process ID to record
        """
        with sqlite3.connect(self.db_path) as conn:
            if api_call_started:
                conn.execute("""
                    UPDATE experiment_games
                    SET state = ?,
                        last_heartbeat_at = CURRENT_TIMESTAMP,
                        last_api_call_started_at = CURRENT_TIMESTAMP,
                        process_id = COALESCE(?, process_id)
                    WHERE game_id = ?
                """, (state, process_id, game_id))
            else:
                conn.execute("""
                    UPDATE experiment_games
                    SET state = ?,
                        last_heartbeat_at = CURRENT_TIMESTAMP,
                        process_id = COALESCE(?, process_id)
                    WHERE game_id = ?
                """, (state, process_id, game_id))

    def get_stalled_variants(
        self,
        experiment_id: int,
        threshold_minutes: int = 5
    ) -> List[Dict]:
        """Get variants that appear to be stalled.

        A variant is considered stalled if:
        - state='calling_api' AND last_api_call_started_at < (NOW - threshold)
        - state='processing' AND last_heartbeat_at < (NOW - threshold)
        - NOT in tournament_results (not completed)

        Args:
            experiment_id: The experiment ID
            threshold_minutes: Minutes of inactivity before considered stalled

        Returns:
            List of stalled variant records
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT eg.id, eg.game_id, eg.variant, eg.variant_config_json,
                       eg.tournament_number, eg.state, eg.last_heartbeat_at,
                       eg.last_api_call_started_at, eg.process_id, eg.resume_lock_acquired_at
                FROM experiment_games eg
                WHERE eg.experiment_id = ?
                  AND eg.state IN ('calling_api', 'processing')
                  AND (
                      (eg.state = 'calling_api'
                       AND eg.last_api_call_started_at < datetime('now', ? || ' minutes'))
                      OR
                      (eg.state = 'processing'
                       AND eg.last_heartbeat_at < datetime('now', ? || ' minutes'))
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM tournament_results tr
                      WHERE tr.game_id = eg.game_id
                  )
                ORDER BY eg.last_heartbeat_at
            """, (experiment_id, -threshold_minutes, -threshold_minutes))

            return [
                {
                    'id': row[0],
                    'game_id': row[1],
                    'variant': row[2],
                    'variant_config': json.loads(row[3]) if row[3] else None,
                    'tournament_number': row[4],
                    'state': row[5],
                    'last_heartbeat_at': row[6],
                    'last_api_call_started_at': row[7],
                    'process_id': row[8],
                    'resume_lock_acquired_at': row[9],
                }
                for row in cursor.fetchall()
            ]

    # Resume lock timeout in minutes - lock expires after this period
    RESUME_LOCK_TIMEOUT_MINUTES = 5

    def acquire_resume_lock(self, experiment_game_id: int) -> bool:
        """Attempt to acquire a resume lock on an experiment game.

        Uses pessimistic locking to prevent race conditions when resuming.
        Lock expires after RESUME_LOCK_TIMEOUT_MINUTES.

        Args:
            experiment_game_id: The experiment_games.id

        Returns:
            True if lock was acquired, False if already locked
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"""
                UPDATE experiment_games
                SET resume_lock_acquired_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (resume_lock_acquired_at IS NULL
                       OR resume_lock_acquired_at < datetime('now', '-{self.RESUME_LOCK_TIMEOUT_MINUTES} minutes'))
            """, (experiment_game_id,))
            return cursor.rowcount == 1

    def release_resume_lock(self, game_id: str) -> None:
        """Release the resume lock for a game.

        Args:
            game_id: The game_id to release lock for
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE experiment_games
                SET resume_lock_acquired_at = NULL
                WHERE game_id = ?
            """, (game_id,))

    def release_resume_lock_by_id(self, experiment_game_id: int) -> None:
        """Release the resume lock by experiment_games.id.

        Args:
            experiment_game_id: The experiment_games.id to release lock for
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE experiment_games
                SET resume_lock_acquired_at = NULL
                WHERE id = ?
            """, (experiment_game_id,))

    def check_resume_lock_superseded(self, game_id: str) -> bool:
        """Check if this process has been superseded by a resume.

        A process is superseded if resume_lock_acquired_at > last_heartbeat_at,
        meaning another process has claimed the resume lock after our last heartbeat.

        Args:
            game_id: The game_id to check

        Returns:
            True if superseded (should exit), False otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT resume_lock_acquired_at, last_heartbeat_at
                FROM experiment_games
                WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()
            if not row:
                return False

            resume_lock, last_heartbeat = row
            if not resume_lock:
                return False
            if not last_heartbeat:
                return True  # No heartbeat but lock exists = superseded

            # Compare timestamps
            return resume_lock > last_heartbeat

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
        with self._get_connection() as conn:
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
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """List experiments with optional status filter.

        Args:
            status: Optional status filter ('pending', 'running', 'completed', 'failed')
            include_archived: If False (default), filter out experiments with _archived tag
            limit: Maximum number of experiments to return
            offset: Number of experiments to skip for pagination

        Returns:
            List of experiment dictionaries with basic info and progress
        """
        with self._get_connection() as conn:
            # Build query with optional filters
            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status)

            if not include_archived:
                # Filter out experiments with _archived tag
                conditions.append("(tags IS NULL OR tags NOT LIKE '%\"_archived\"%')")

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

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

        with self._get_connection() as conn:
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

    def update_experiment_tags(self, experiment_id: int, tags: List[str]) -> None:
        """Update experiment tags.

        Args:
            experiment_id: The experiment ID
            tags: List of tags to set (replaces existing tags)
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET tags = ? WHERE id = ?",
                (json.dumps(tags), experiment_id)
            )
            conn.commit()
            logger.info(f"Updated experiment {experiment_id} tags to {tags}")

    def mark_running_experiments_interrupted(self) -> int:
        """Mark all 'running' experiments as 'interrupted'.

        Called on startup to handle experiments that were running when the
        server was stopped. Users can manually resume these experiments.

        Returns:
            Number of experiments marked as interrupted.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE experiments
                SET status = 'interrupted',
                    notes = 'Server restarted while experiment was running. Click Resume to continue.'
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
        with self._get_connection() as conn:
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

    # ==================== Experiment Chat Session Methods ====================

    def save_chat_session(
        self,
        session_id: str,
        owner_id: str,
        messages: List[Dict],
        config_snapshot: Dict,
        config_versions: Optional[List[Dict]] = None
    ) -> None:
        """Save or update a chat session.

        Args:
            session_id: Unique session identifier
            owner_id: User/owner identifier
            messages: List of chat messages [{role, content, configDiff?}]
            config_snapshot: Current config state
            config_versions: List of config version snapshots
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO experiment_chat_sessions (id, owner_id, messages_json, config_snapshot_json, config_versions_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    messages_json = excluded.messages_json,
                    config_snapshot_json = excluded.config_snapshot_json,
                    config_versions_json = excluded.config_versions_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                session_id,
                owner_id,
                json.dumps(messages),
                json.dumps(config_snapshot),
                json.dumps(config_versions) if config_versions else None,
            ))
            conn.commit()
            logger.debug(f"Saved chat session {session_id} for owner {owner_id}")

    def get_chat_session(self, session_id: str) -> Optional[Dict]:
        """Get a chat session by its ID.

        Args:
            session_id: The session ID to retrieve

        Returns:
            Dict with session data or None if not found:
            {
                'session_id': str,
                'messages': List[Dict],
                'config': Dict,
                'config_versions': List[Dict] | None,
                'updated_at': str
            }
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, messages_json, config_snapshot_json, config_versions_json, updated_at
                FROM experiment_chat_sessions
                WHERE id = ?
            """, (session_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'session_id': row['id'],
                'messages': json.loads(row['messages_json']) if row['messages_json'] else [],
                'config': json.loads(row['config_snapshot_json']) if row['config_snapshot_json'] else {},
                'config_versions': json.loads(row['config_versions_json']) if row['config_versions_json'] else None,
                'updated_at': row['updated_at'],
            }

    def get_latest_chat_session(self, owner_id: str) -> Optional[Dict]:
        """Get the most recent non-archived chat session for an owner.

        Args:
            owner_id: User/owner identifier

        Returns:
            Dict with session data or None if no session exists:
            {
                'session_id': str,
                'messages': List[Dict],
                'config': Dict,
                'config_versions': List[Dict] | None,
                'updated_at': str
            }
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, messages_json, config_snapshot_json, config_versions_json, updated_at
                FROM experiment_chat_sessions
                WHERE owner_id = ? AND is_archived = 0
                ORDER BY updated_at DESC
                LIMIT 1
            """, (owner_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'session_id': row['id'],
                'messages': json.loads(row['messages_json']) if row['messages_json'] else [],
                'config': json.loads(row['config_snapshot_json']) if row['config_snapshot_json'] else {},
                'config_versions': json.loads(row['config_versions_json']) if row['config_versions_json'] else None,
                'updated_at': row['updated_at'],
            }

    def archive_chat_session(self, session_id: str) -> None:
        """Archive a chat session so it won't be returned by get_latest_chat_session.

        Args:
            session_id: The session ID to archive
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiment_chat_sessions SET is_archived = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,)
            )
            conn.commit()
            logger.debug(f"Archived chat session {session_id}")

    def delete_chat_session(self, session_id: str) -> None:
        """Delete a chat session entirely.

        Args:
            session_id: The session ID to delete
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM experiment_chat_sessions WHERE id = ?", (session_id,))
            conn.commit()
            logger.debug(f"Deleted chat session {session_id}")

    # ==================== Experiment Chat Storage Methods ====================

    def save_experiment_design_chat(self, experiment_id: int, chat_history: List[Dict]) -> None:
        """Store the design chat history with an experiment.

        Called when an experiment is created to preserve the conversation that led to its design.

        Args:
            experiment_id: The experiment ID
            chat_history: List of chat messages from the design session
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET design_chat_json = ? WHERE id = ?",
                (json.dumps(chat_history), experiment_id)
            )
            conn.commit()
            logger.info(f"Saved design chat ({len(chat_history)} messages) to experiment {experiment_id}")

    def get_experiment_design_chat(self, experiment_id: int) -> Optional[List[Dict]]:
        """Get the design chat history for an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of chat messages or None if no design chat stored
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT design_chat_json FROM experiments WHERE id = ?",
                (experiment_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None

    def save_experiment_assistant_chat(self, experiment_id: int, chat_history: List[Dict]) -> None:
        """Store the ongoing assistant chat history for an experiment.

        Used for the experiment-scoped assistant that can query results and answer questions.

        Args:
            experiment_id: The experiment ID
            chat_history: List of chat messages from the assistant session
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET assistant_chat_json = ? WHERE id = ?",
                (json.dumps(chat_history), experiment_id)
            )
            conn.commit()
            logger.debug(f"Saved assistant chat ({len(chat_history)} messages) to experiment {experiment_id}")

    def get_experiment_assistant_chat(self, experiment_id: int) -> Optional[List[Dict]]:
        """Get the assistant chat history for an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of chat messages or None if no assistant chat stored
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT assistant_chat_json FROM experiments WHERE id = ?",
                (experiment_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None

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
        with self._get_connection() as conn:
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

                # 3. Progress - sum hands across all games for this variant
                # Query gets max hand per game, then we sum them up
                # This correctly handles parallel execution where multiple games
                # may be in progress simultaneously
                cursor = conn.execute(f"""
                    SELECT
                        eg.game_id,
                        COALESCE(MAX(au.hand_number), 0) as max_hand
                    FROM experiment_games eg
                    LEFT JOIN api_usage au ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                    GROUP BY eg.game_id
                """, [experiment_id] + variant_params)
                games_data = cursor.fetchall()
                games_count = len(games_data)
                # Sum actual hands played in each game (capped at max_hands per game)
                current_hands = sum(min(row[1], max_hands) for row in games_data)

                # For a variant, expected tournaments = num_tournaments
                variant_max_hands = num_tournaments * max_hands

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

                # 5. Quality indicators from player_decision_analysis + prompt_captures
                # Detect degenerate play patterns with improved all-in detection
                cursor = conn.execute(f"""
                    SELECT
                        SUM(CASE WHEN action_taken = 'fold' AND decision_quality = 'mistake' THEN 1 ELSE 0 END) as fold_mistakes,
                        SUM(CASE WHEN action_taken = 'all_in' THEN 1 ELSE 0 END) as total_all_ins,
                        SUM(CASE WHEN action_taken = 'fold' THEN 1 ELSE 0 END) as total_folds,
                        COUNT(*) as total_decisions
                    FROM player_decision_analysis pda
                    JOIN experiment_games eg ON pda.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                qi_row = cursor.fetchone()

                # Query all-ins with AI response data for smarter categorization
                # Join prompt_captures to get bluff_likelihood, hand_strength, stack_bb
                cursor = conn.execute(f"""
                    SELECT
                        pc.stack_bb,
                        pc.ai_response,
                        pda.equity
                    FROM prompt_captures pc
                    JOIN experiment_games eg ON pc.game_id = eg.game_id
                    LEFT JOIN player_decision_analysis pda
                        ON pc.game_id = pda.game_id
                        AND pc.hand_number = pda.hand_number
                        AND pc.player_name = pda.player_name
                        AND pc.phase = pda.phase
                    WHERE eg.experiment_id = ? {variant_clause}
                      AND pc.action_taken = 'all_in'
                """, [experiment_id] + variant_params)

                # Use shared categorization logic
                from poker.quality_metrics import compute_allin_categorizations
                suspicious_allins, marginal_allins = compute_allin_categorizations(cursor.fetchall())

                # 6. Survival metrics from tournament_standings
                cursor = conn.execute(f"""
                    SELECT
                        SUM(COALESCE(ts.times_eliminated, 0)) as total_eliminations,
                        SUM(COALESCE(ts.all_in_wins, 0)) as total_all_in_wins,
                        SUM(COALESCE(ts.all_in_losses, 0)) as total_all_in_losses,
                        COUNT(*) as total_standings
                    FROM tournament_standings ts
                    JOIN experiment_games eg ON ts.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                survival_row = cursor.fetchone()

                quality_indicators = None
                if qi_row and qi_row[3] > 0:  # total_decisions > 0 (now at index 3)
                    fold_mistakes = qi_row[0] or 0
                    total_all_ins = qi_row[1] or 0
                    total_folds = qi_row[2] or 0
                    total_decisions = qi_row[3]

                    # Survival metrics
                    total_eliminations = survival_row[0] or 0 if survival_row else 0
                    total_all_in_wins = survival_row[1] or 0 if survival_row else 0
                    total_all_in_losses = survival_row[2] or 0 if survival_row else 0
                    total_all_in_showdowns = total_all_in_wins + total_all_in_losses

                    quality_indicators = {
                        'suspicious_allins': suspicious_allins,
                        'marginal_allins': marginal_allins,
                        'fold_mistakes': fold_mistakes,
                        'fold_mistake_rate': round(fold_mistakes * 100 / total_folds, 1) if total_folds > 0 else 0,
                        'total_all_ins': total_all_ins,
                        'total_folds': total_folds,
                        'total_decisions': total_decisions,
                        # Survival metrics
                        'total_eliminations': total_eliminations,
                        'all_in_wins': total_all_in_wins,
                        'all_in_losses': total_all_in_losses,
                        'all_in_survival_rate': round(total_all_in_wins * 100 / total_all_in_showdowns, 1) if total_all_in_showdowns > 0 else None,
                    }

                result['by_variant'][variant_key] = {
                    'latency_metrics': latency_metrics,
                    'decision_quality': decision_quality,
                    'progress': progress,
                    'cost_metrics': cost_metrics,
                    'quality_indicators': quality_indicators,
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

            # Overall quality indicators from player_decision_analysis + prompt_captures
            cursor = conn.execute("""
                SELECT
                    SUM(CASE WHEN action_taken = 'fold' AND decision_quality = 'mistake' THEN 1 ELSE 0 END) as fold_mistakes,
                    SUM(CASE WHEN action_taken = 'all_in' THEN 1 ELSE 0 END) as total_all_ins,
                    SUM(CASE WHEN action_taken = 'fold' THEN 1 ELSE 0 END) as total_folds,
                    COUNT(*) as total_decisions
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ?
            """, (experiment_id,))
            overall_qi_row = cursor.fetchone()

            # Query all-ins for smarter categorization (overall)
            cursor = conn.execute("""
                SELECT
                    pc.stack_bb,
                    pc.ai_response,
                    pda.equity
                FROM prompt_captures pc
                JOIN experiment_games eg ON pc.game_id = eg.game_id
                LEFT JOIN player_decision_analysis pda
                    ON pc.game_id = pda.game_id
                    AND pc.hand_number = pda.hand_number
                    AND pc.player_name = pda.player_name
                    AND pc.phase = pda.phase
                WHERE eg.experiment_id = ?
                  AND pc.action_taken = 'all_in'
            """, (experiment_id,))

            # Use shared categorization logic
            from poker.quality_metrics import compute_allin_categorizations
            overall_suspicious_allins, overall_marginal_allins = compute_allin_categorizations(cursor.fetchall())

            overall_quality_indicators = None
            if overall_qi_row and overall_qi_row[3] > 0:  # total_decisions at index 3
                fold_mistakes = overall_qi_row[0] or 0
                total_all_ins = overall_qi_row[1] or 0
                total_folds = overall_qi_row[2] or 0
                total_decisions = overall_qi_row[3]

                overall_quality_indicators = {
                    'suspicious_allins': overall_suspicious_allins,
                    'marginal_allins': overall_marginal_allins,
                    'fold_mistakes': fold_mistakes,
                    'fold_mistake_rate': round(fold_mistakes * 100 / total_folds, 1) if total_folds > 0 else 0,
                    'total_all_ins': total_all_ins,
                    'total_folds': total_folds,
                    'total_decisions': total_decisions,
                }

            result['overall'] = {
                'latency_metrics': overall_latency,
                'decision_quality': overall_decision_quality,
                'progress': overall_progress_result,
                'cost_metrics': overall_cost_metrics,
                'quality_indicators': overall_quality_indicators,
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
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Get all games for this experiment (stable order by game_id)
            cursor = conn.execute("""
                SELECT eg.game_id, eg.variant, g.game_state_json, g.phase, g.updated_at
                FROM experiment_games eg
                JOIN games g ON eg.game_id = g.game_id
                WHERE eg.experiment_id = ?
                ORDER BY eg.game_id
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
                        'is_eliminated': p.get('stack', 0) == 0,
                        'seat_index': idx,  # Fixed seat position for monitoring
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
                    'total_seats': len(players),  # Fixed seat count for positioning
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
        with self._get_connection() as conn:
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
            with self._get_connection() as conn:
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
            with self._get_connection() as conn:
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
            with self._get_connection() as conn:
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
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM app_settings WHERE key = ?",
                    (key,)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete setting '{key}': {e}")
            return False

    # ========================================
    # Prompt Preset Methods (v47)
    # ========================================

    def create_prompt_preset(
        self,
        name: str,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None,
        owner_id: Optional[str] = None
    ) -> int:
        """Create a new prompt preset.

        Args:
            name: Unique name for the preset
            description: Optional description of the preset
            prompt_config: PromptConfig toggles as dict
            guidance_injection: Extra guidance text to append to prompts
            owner_id: Optional owner ID for multi-tenant support

        Returns:
            The ID of the created preset

        Raises:
            ValueError: If a preset with the same name already exists
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO prompt_presets (name, description, prompt_config, guidance_injection, owner_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    name,
                    description,
                    json.dumps(prompt_config) if prompt_config else None,
                    guidance_injection,
                    owner_id
                ))
                conn.commit()
                preset_id = cursor.lastrowid
                logger.info(f"Created prompt preset '{name}' with ID {preset_id}")
                return preset_id
        except sqlite3.IntegrityError:
            raise ValueError(f"Prompt preset with name '{name}' already exists")

    def get_prompt_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        """Get a prompt preset by ID.

        Args:
            preset_id: The preset ID

        Returns:
            Preset data as dict, or None if not found
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, name, description, prompt_config, guidance_injection,
                       owner_id, is_system, created_at, updated_at
                FROM prompt_presets
                WHERE id = ?
            """, (preset_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'name': row['name'],
                    'description': row['description'],
                    'prompt_config': json.loads(row['prompt_config']) if row['prompt_config'] else None,
                    'guidance_injection': row['guidance_injection'],
                    'owner_id': row['owner_id'],
                    'is_system': bool(row['is_system']) if row['is_system'] is not None else False,
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                }
            return None

    def get_prompt_preset_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a prompt preset by name.

        Args:
            name: The preset name

        Returns:
            Preset data as dict, or None if not found
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, name, description, prompt_config, guidance_injection,
                       owner_id, is_system, created_at, updated_at
                FROM prompt_presets
                WHERE name = ?
            """, (name,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'name': row['name'],
                    'description': row['description'],
                    'prompt_config': json.loads(row['prompt_config']) if row['prompt_config'] else None,
                    'guidance_injection': row['guidance_injection'],
                    'owner_id': row['owner_id'],
                    'is_system': bool(row['is_system']) if row['is_system'] is not None else False,
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                }
            return None

    def list_prompt_presets(
        self,
        owner_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List all prompt presets.

        Args:
            owner_id: Optional filter by owner ID
            limit: Maximum number of results

        Returns:
            List of preset data dicts
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if owner_id:
                # Include system presets for all users, plus user's own presets
                cursor = conn.execute("""
                    SELECT id, name, description, prompt_config, guidance_injection,
                           owner_id, is_system, created_at, updated_at
                    FROM prompt_presets
                    WHERE owner_id = ? OR owner_id IS NULL OR is_system = TRUE
                    ORDER BY is_system DESC, updated_at DESC
                    LIMIT ?
                """, (owner_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT id, name, description, prompt_config, guidance_injection,
                           owner_id, is_system, created_at, updated_at
                    FROM prompt_presets
                    ORDER BY is_system DESC, updated_at DESC
                    LIMIT ?
                """, (limit,))

            return [
                {
                    'id': row['id'],
                    'name': row['name'],
                    'description': row['description'],
                    'prompt_config': json.loads(row['prompt_config']) if row['prompt_config'] else None,
                    'guidance_injection': row['guidance_injection'],
                    'owner_id': row['owner_id'],
                    'is_system': bool(row['is_system']) if row['is_system'] is not None else False,
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                }
                for row in cursor.fetchall()
            ]

    def update_prompt_preset(
        self,
        preset_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None
    ) -> bool:
        """Update a prompt preset.

        Args:
            preset_id: The preset ID to update
            name: Optional new name
            description: Optional new description
            prompt_config: Optional new prompt config
            guidance_injection: Optional new guidance text

        Returns:
            True if the preset was updated, False if not found

        Raises:
            ValueError: If the new name conflicts with an existing preset
        """
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if prompt_config is not None:
            updates.append("prompt_config = ?")
            params.append(json.dumps(prompt_config))
        if guidance_injection is not None:
            updates.append("guidance_injection = ?")
            params.append(guidance_injection)

        if not updates:
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(preset_id)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    f"UPDATE prompt_presets SET {', '.join(updates)} WHERE id = ?",
                    params
                )
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Updated prompt preset ID {preset_id}")
                    return True
                return False
        except sqlite3.IntegrityError:
            raise ValueError(f"Prompt preset with name '{name}' already exists")

    def delete_prompt_preset(self, preset_id: int) -> bool:
        """Delete a prompt preset.

        Args:
            preset_id: The preset ID to delete

        Returns:
            True if the preset was deleted, False if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM prompt_presets WHERE id = ?",
                    (preset_id,)
                )
                conn.commit()
                if cursor.rowcount > 0:
                    logger.info(f"Deleted prompt preset ID {preset_id}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to delete prompt preset {preset_id}: {e}")
            return False

    # ==================== Capture Labels Methods ====================
    # These methods support labeling/tagging captured AI decisions for
    # filtering and selection in replay experiments.
    # ================================================================

    def add_capture_labels(
        self,
        capture_id: int,
        labels: List[str],
        label_type: str = 'user'
    ) -> List[str]:
        """Add labels to a captured AI decision.

        Args:
            capture_id: The prompt_captures ID
            labels: List of label strings to add
            label_type: Type of label ('user' for manual, 'smart' for auto-generated)

        Returns:
            List of labels that were actually added (excludes duplicates)
        """
        added = []
        with self._get_connection() as conn:
            for label in labels:
                label = label.strip().lower()
                if not label:
                    continue
                try:
                    conn.execute("""
                        INSERT INTO capture_labels (capture_id, label, label_type)
                        VALUES (?, ?, ?)
                    """, (capture_id, label, label_type))
                    added.append(label)
                except sqlite3.IntegrityError:
                    # Label already exists for this capture, skip
                    pass
            conn.commit()
        if added:
            logger.debug(f"Added labels {added} to capture {capture_id}")
        return added

    def compute_and_store_auto_labels(self, capture_id: int, capture_data: Dict[str, Any]) -> List[str]:
        """Compute auto-labels for a capture based on rules and store them.

        Labels are computed based on the capture data at capture time.
        Stored with label_type='auto' to distinguish from user-added labels.

        Args:
            capture_id: The prompt_captures ID
            capture_data: Dict containing capture fields (action_taken, pot_odds, stack_bb, already_bet_bb, etc.)

        Returns:
            List of auto-labels that were added
        """
        labels = []
        action = capture_data.get('action_taken')
        pot_odds = capture_data.get('pot_odds')
        stack_bb = capture_data.get('stack_bb')
        already_bet_bb = capture_data.get('already_bet_bb')

        # SHORT_STACK: Folding with < 3 BB is almost always wrong
        if action == 'fold' and stack_bb is not None and stack_bb < 3:
            labels.append('short_stack_fold')

        # POT_COMMITTED: Folding after investing more than remaining stack
        if (action == 'fold' and
                already_bet_bb is not None and
                stack_bb is not None and
                already_bet_bb > stack_bb):
            labels.append('pot_committed_fold')

        # SUS_FOLD: Suspicious fold - high pot odds (getting good price)
        if action == 'fold' and pot_odds is not None and pot_odds >= 5:
            # Only add if not already flagged with more specific labels
            if 'short_stack_fold' not in labels and 'pot_committed_fold' not in labels:
                labels.append('suspicious_fold')

        # DRAMA: Add labels for notable drama situations
        drama = capture_data.get('drama_context')
        if drama:
            level = drama.get('level')
            tone = drama.get('tone')
            factors = drama.get('factors', [])

            # Label high-drama levels
            if level in ('climactic', 'high_stakes'):
                labels.append(f'drama:{level}')

            # Label non-neutral tones
            if tone and tone != 'neutral':
                labels.append(f'tone:{tone}')

            # Label specific dramatic factors
            for factor in factors:
                if factor in ('huge_raise', 'late_stage', 'all_in'):
                    labels.append(f'factor:{factor}')

        # Store labels if any were computed
        if labels:
            self.add_capture_labels(capture_id, labels, label_type='auto')
            logger.debug(f"Auto-labeled capture {capture_id}: {labels}")

        return labels

    def remove_capture_labels(
        self,
        capture_id: int,
        labels: List[str]
    ) -> int:
        """Remove labels from a captured AI decision.

        Args:
            capture_id: The prompt_captures ID
            labels: List of label strings to remove

        Returns:
            Number of labels that were removed
        """
        with self._get_connection() as conn:
            total_removed = 0
            for label in labels:
                label = label.strip().lower()
                if not label:
                    continue
                cursor = conn.execute("""
                    DELETE FROM capture_labels
                    WHERE capture_id = ? AND label = ?
                """, (capture_id, label))
                total_removed += cursor.rowcount
            conn.commit()
        if total_removed:
            logger.debug(f"Removed {total_removed} label(s) from capture {capture_id}")
        return total_removed

    def get_capture_labels(self, capture_id: int) -> List[Dict[str, Any]]:
        """Get all labels for a captured AI decision.

        Args:
            capture_id: The prompt_captures ID

        Returns:
            List of label dicts with 'label', 'label_type', 'created_at'
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT label, label_type, created_at
                FROM capture_labels
                WHERE capture_id = ?
                ORDER BY label
            """, (capture_id,))
            return [dict(row) for row in cursor.fetchall()]

    def list_all_labels(self, label_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all unique labels with counts.

        Args:
            label_type: Optional filter by label type ('user' or 'smart')

        Returns:
            List of dicts with 'name', 'count', 'label_type'
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            if label_type:
                cursor = conn.execute("""
                    SELECT label as name, label_type, COUNT(*) as count
                    FROM capture_labels
                    WHERE label_type = ?
                    GROUP BY label, label_type
                    ORDER BY count DESC, label
                """, (label_type,))
            else:
                cursor = conn.execute("""
                    SELECT label as name, label_type, COUNT(*) as count
                    FROM capture_labels
                    GROUP BY label, label_type
                    ORDER BY count DESC, label
                """)
            return [dict(row) for row in cursor.fetchall()]

    def get_label_stats(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        call_type: Optional[str] = None
    ) -> Dict[str, int]:
        """Get label counts filtered by game_id, player_name, and/or call_type.

        Args:
            game_id: Optional filter by game
            player_name: Optional filter by player
            call_type: Optional filter by call type

        Returns:
            Dict mapping label name to count
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("pc.game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("pc.player_name = ?")
            params.append(player_name)
        if call_type:
            conditions.append("pc.call_type = ?")
            params.append(call_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f"""
                SELECT cl.label, COUNT(*) as count
                FROM capture_labels cl
                JOIN prompt_captures pc ON cl.capture_id = pc.id
                {where_clause}
                GROUP BY cl.label
                ORDER BY count DESC, cl.label
            """, params)
            return {row['label']: row['count'] for row in cursor.fetchall()}

    def search_captures_with_labels(
        self,
        labels: List[str],
        match_all: bool = False,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        call_type: Optional[str] = None,
        min_pot_size: Optional[float] = None,
        max_pot_size: Optional[float] = None,
        min_big_blind: Optional[float] = None,
        max_big_blind: Optional[float] = None,
        error_type: Optional[str] = None,
        has_error: Optional[bool] = None,
        is_correction: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Search captures by labels and optional filters.

        Args:
            labels: List of labels to search for
            match_all: If True, captures must have ALL labels; if False, ANY label
            game_id: Optional filter by game
            player_name: Optional filter by player
            action: Optional filter by action taken
            phase: Optional filter by game phase
            min_pot_odds: Optional minimum pot odds filter
            max_pot_odds: Optional maximum pot odds filter
            call_type: Optional filter by call type (e.g., 'player_decision')
            min_pot_size: Optional minimum pot total filter
            max_pot_size: Optional maximum pot total filter
            min_big_blind: Optional minimum big blind filter (computed from stack_bb)
            max_big_blind: Optional maximum big blind filter (computed from stack_bb)
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'captures' list and 'total' count
        """
        # Normalize labels
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels:
            # No labels specified, fallback to regular listing
            return self.list_prompt_captures(
                game_id=game_id,
                player_name=player_name,
                action=action,
                phase=phase,
                min_pot_odds=min_pot_odds,
                max_pot_odds=max_pot_odds,
                call_type=call_type,
                error_type=error_type,
                has_error=has_error,
                is_correction=is_correction,
                limit=limit,
                offset=offset
            )

        # Build base conditions
        conditions = []
        params = []

        if game_id:
            conditions.append("pc.game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("pc.player_name = ?")
            params.append(player_name)
        if action:
            conditions.append("pc.action_taken = ?")
            params.append(action)
        if phase:
            conditions.append("pc.phase = ?")
            params.append(phase)
        if min_pot_odds is not None:
            conditions.append("pc.pot_odds >= ?")
            params.append(min_pot_odds)
        if max_pot_odds is not None:
            conditions.append("pc.pot_odds <= ?")
            params.append(max_pot_odds)
        if call_type:
            conditions.append("pc.call_type = ?")
            params.append(call_type)
        if min_pot_size is not None:
            conditions.append("pc.pot_total >= ?")
            params.append(min_pot_size)
        if max_pot_size is not None:
            conditions.append("pc.pot_total <= ?")
            params.append(max_pot_size)
        # Big blind filtering: compute BB from player_stack / stack_bb
        if min_big_blind is not None:
            conditions.append("pc.stack_bb > 0 AND (pc.player_stack / pc.stack_bb) >= ?")
            params.append(min_big_blind)
        if max_big_blind is not None:
            conditions.append("pc.stack_bb > 0 AND (pc.player_stack / pc.stack_bb) <= ?")
            params.append(max_big_blind)
        # Error/correction resilience filters
        if error_type:
            conditions.append("pc.error_type = ?")
            params.append(error_type)
        if has_error is True:
            conditions.append("pc.error_type IS NOT NULL")
        elif has_error is False:
            conditions.append("pc.error_type IS NULL")
        if is_correction is True:
            conditions.append("pc.parent_id IS NOT NULL")
        elif is_correction is False:
            conditions.append("pc.parent_id IS NULL")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Build label matching subquery
            label_placeholders = ','.join(['?' for _ in labels])
            params_for_labels = [l for l in labels]

            if match_all:
                # Must have ALL specified labels
                label_subquery = f"""
                    pc.id IN (
                        SELECT capture_id
                        FROM capture_labels
                        WHERE label IN ({label_placeholders})
                        GROUP BY capture_id
                        HAVING COUNT(DISTINCT label) = ?
                    )
                """
                params_for_labels.append(len(labels))
            else:
                # Must have ANY of the specified labels
                label_subquery = f"""
                    pc.id IN (
                        SELECT capture_id
                        FROM capture_labels
                        WHERE label IN ({label_placeholders})
                    )
                """

            # Combine label filter with other conditions
            if where_clause:
                full_where = f"{where_clause} AND {label_subquery}"
            else:
                full_where = f"WHERE {label_subquery}"

            # Count query
            count_query = f"""
                SELECT COUNT(DISTINCT pc.id)
                FROM prompt_captures pc
                {full_where}
            """
            count_params = params + params_for_labels
            cursor = conn.execute(count_query, count_params)
            total = cursor.fetchone()[0]

            # Data query
            data_query = f"""
                SELECT DISTINCT pc.id, pc.created_at, pc.game_id, pc.player_name,
                       pc.hand_number, pc.phase, pc.action_taken, pc.pot_total,
                       pc.cost_to_call, pc.pot_odds, pc.player_stack,
                       pc.community_cards, pc.player_hand, pc.model, pc.provider,
                       pc.latency_ms, pc.tags, pc.notes
                FROM prompt_captures pc
                {full_where}
                ORDER BY pc.created_at DESC
                LIMIT ? OFFSET ?
            """
            data_params = params + params_for_labels + [limit, offset]
            cursor = conn.execute(data_query, data_params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                for field in ['community_cards', 'player_hand', 'tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            logger.debug("Failed to parse JSON for field '%s' in capture id=%s", field, capture.get('id'))
                # Get labels for this capture
                capture['labels'] = self.get_capture_labels(capture['id'])
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def bulk_add_capture_labels(
        self,
        capture_ids: List[int],
        labels: List[str],
        label_type: str = 'user'
    ) -> Dict[str, int]:
        """Add labels to multiple captures at once.

        Args:
            capture_ids: List of prompt_captures IDs
            labels: Labels to add to all captures
            label_type: Type of label

        Returns:
            Dict with 'captures_affected' and 'labels_added' counts
        """
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels or not capture_ids:
            return {'captures_affected': 0, 'labels_added': 0}

        total_added = 0
        captures_touched = set()

        with self._get_connection() as conn:
            for capture_id in capture_ids:
                for label in labels:
                    try:
                        conn.execute("""
                            INSERT INTO capture_labels (capture_id, label, label_type)
                            VALUES (?, ?, ?)
                        """, (capture_id, label, label_type))
                        total_added += 1
                        captures_touched.add(capture_id)
                    except sqlite3.IntegrityError:
                        # Label already exists for this capture
                        pass
            conn.commit()

        logger.info(f"Bulk added {total_added} label(s) to {len(captures_touched)} capture(s)")
        return {
            'captures_affected': len(captures_touched),
            'labels_added': total_added
        }

    def bulk_remove_capture_labels(
        self,
        capture_ids: List[int],
        labels: List[str]
    ) -> Dict[str, int]:
        """Remove labels from multiple captures at once.

        Args:
            capture_ids: List of prompt_captures IDs
            labels: Labels to remove from all captures

        Returns:
            Dict with 'captures_affected' and 'labels_removed' counts
        """
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels or not capture_ids:
            return {'captures_affected': 0, 'labels_removed': 0}

        with self._get_connection() as conn:
            # Build query with multiple capture_ids
            id_placeholders = ','.join(['?' for _ in capture_ids])
            label_placeholders = ','.join(['?' for _ in labels])

            cursor = conn.execute(f"""
                DELETE FROM capture_labels
                WHERE capture_id IN ({id_placeholders})
                AND label IN ({label_placeholders})
            """, capture_ids + labels)
            conn.commit()
            removed = cursor.rowcount

        logger.info(f"Bulk removed {removed} label(s) from captures")
        return {
            'captures_affected': len(capture_ids),
            'labels_removed': removed
        }

    # ==================== Replay Experiment Methods ====================
    # These methods support replay experiments that re-run captured AI
    # decisions with different variants (models, prompts, etc.).
    # ===================================================================

    def create_replay_experiment(
        self,
        name: str,
        capture_ids: List[int],
        variants: List[Dict[str, Any]],
        description: Optional[str] = None,
        hypothesis: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_experiment_id: Optional[int] = None
    ) -> int:
        """Create a new replay experiment.

        Args:
            name: Unique experiment name
            capture_ids: List of prompt_captures IDs to replay
            variants: List of variant configurations
            description: Optional experiment description
            hypothesis: Optional hypothesis being tested
            tags: Optional list of tags
            parent_experiment_id: Optional parent for lineage tracking

        Returns:
            The experiment_id of the created record
        """
        config = {
            'name': name,
            'description': description,
            'hypothesis': hypothesis,
            'tags': tags or [],
            'experiment_type': 'replay',
            'capture_selection': {
                'mode': 'ids',
                'ids': capture_ids
            },
            'variants': variants
        }

        with self._get_connection() as conn:
            # Create the experiment record
            cursor = conn.execute("""
                INSERT INTO experiments (
                    name, description, hypothesis, tags, notes,
                    config_json, experiment_type, parent_experiment_id
                )
                VALUES (?, ?, ?, ?, ?, ?, 'replay', ?)
            """, (
                name,
                description,
                hypothesis,
                json.dumps(tags or []),
                None,  # notes
                json.dumps(config),
                parent_experiment_id,
            ))
            experiment_id = cursor.lastrowid

            # Link captures to the experiment
            for capture_id in capture_ids:
                # Get original capture info for reference
                capture_cursor = conn.execute("""
                    SELECT action_taken FROM prompt_captures WHERE id = ?
                """, (capture_id,))
                capture_row = capture_cursor.fetchone()
                original_action = capture_row[0] if capture_row else None

                # Get decision analysis if available
                analysis_cursor = conn.execute("""
                    SELECT decision_quality, ev_lost FROM player_decision_analysis
                    WHERE capture_id = ?
                """, (capture_id,))
                analysis_row = analysis_cursor.fetchone()
                original_quality = analysis_row[0] if analysis_row else None
                original_ev_lost = analysis_row[1] if analysis_row else None

                conn.execute("""
                    INSERT INTO replay_experiment_captures (
                        experiment_id, capture_id, original_action,
                        original_quality, original_ev_lost
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (experiment_id, capture_id, original_action, original_quality, original_ev_lost))

            conn.commit()
            logger.info(f"Created replay experiment '{name}' with id {experiment_id}, {len(capture_ids)} captures")
            return experiment_id

    def add_replay_result(
        self,
        experiment_id: int,
        capture_id: int,
        variant: str,
        new_response: str,
        new_action: str,
        new_raise_amount: Optional[int] = None,
        new_quality: Optional[str] = None,
        new_ev_lost: Optional[float] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> int:
        """Add a result from replaying a capture with a variant.

        Args:
            experiment_id: The experiment ID
            capture_id: The prompt_captures ID
            variant: The variant label
            new_response: The new AI response
            new_action: The new action taken
            new_raise_amount: Optional new raise amount
            new_quality: Optional quality assessment
            new_ev_lost: Optional EV lost calculation
            provider: LLM provider used
            model: Model used
            reasoning_effort: Reasoning effort setting
            input_tokens: Input token count
            output_tokens: Output token count
            latency_ms: Response latency
            error_message: Error if the replay failed

        Returns:
            The replay_results record ID
        """
        with self._get_connection() as conn:
            # Get original action and quality for comparison
            cursor = conn.execute("""
                SELECT original_action, original_quality, original_ev_lost
                FROM replay_experiment_captures
                WHERE experiment_id = ? AND capture_id = ?
            """, (experiment_id, capture_id))
            row = cursor.fetchone()
            original_action = row[0] if row else None
            original_quality = row[1] if row else None
            original_ev_lost = row[2] if row else None

            # Determine if action changed
            action_changed = new_action != original_action if original_action else None

            # Determine quality change
            quality_change = None
            if original_quality and new_quality:
                if original_quality == 'mistake' and new_quality != 'mistake':
                    quality_change = 'improved'
                elif original_quality != 'mistake' and new_quality == 'mistake':
                    quality_change = 'degraded'
                else:
                    quality_change = 'unchanged'

            # Calculate EV delta
            ev_delta = None
            if original_ev_lost is not None and new_ev_lost is not None:
                ev_delta = original_ev_lost - new_ev_lost  # Positive = improvement

            cursor = conn.execute("""
                INSERT INTO replay_results (
                    experiment_id, capture_id, variant, new_response, new_action,
                    new_raise_amount, new_quality, new_ev_lost, action_changed,
                    quality_change, ev_delta, provider, model, reasoning_effort,
                    input_tokens, output_tokens, latency_ms, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                experiment_id, capture_id, variant, new_response, new_action,
                new_raise_amount, new_quality, new_ev_lost, action_changed,
                quality_change, ev_delta, provider, model, reasoning_effort,
                input_tokens, output_tokens, latency_ms, error_message
            ))
            conn.commit()
            return cursor.lastrowid

    def get_replay_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """Get a replay experiment with its captures and progress.

        Args:
            experiment_id: The experiment ID

        Returns:
            Experiment data with capture count and result progress, or None
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Get experiment
            cursor = conn.execute("""
                SELECT * FROM experiments WHERE id = ? AND experiment_type = 'replay'
            """, (experiment_id,))
            row = cursor.fetchone()
            if not row:
                return None

            experiment = dict(row)

            # Parse JSON fields
            for field in ['config_json', 'summary_json', 'tags']:
                if experiment.get(field):
                    try:
                        experiment[field] = json.loads(experiment[field])
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse JSON for field '%s' in experiment id=%s", field, experiment_id)

            # Get capture count
            cursor = conn.execute("""
                SELECT COUNT(*) FROM replay_experiment_captures
                WHERE experiment_id = ?
            """, (experiment_id,))
            experiment['capture_count'] = cursor.fetchone()[0]

            # Get variants from config
            config = experiment.get('config_json', {})
            variants = config.get('variants', []) if isinstance(config, dict) else []
            experiment['variant_count'] = len(variants)

            # Get result progress
            cursor = conn.execute("""
                SELECT COUNT(*) FROM replay_results WHERE experiment_id = ?
            """, (experiment_id,))
            experiment['results_completed'] = cursor.fetchone()[0]
            experiment['results_total'] = experiment['capture_count'] * experiment['variant_count']

            return experiment

    def get_replay_results(
        self,
        experiment_id: int,
        variant: Optional[str] = None,
        quality_change: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Get replay results for an experiment.

        Args:
            experiment_id: The experiment ID
            variant: Optional filter by variant
            quality_change: Optional filter by quality change ('improved', 'degraded', 'unchanged')
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'results' list and 'total' count
        """
        conditions = ["replay_results.experiment_id = ?"]
        params = [experiment_id]

        if variant:
            conditions.append("replay_results.variant = ?")
            params.append(variant)
        if quality_change:
            conditions.append("replay_results.quality_change = ?")
            params.append(quality_change)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM replay_results {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # Get results with pagination
            # Note: Don't alias replay_results since where_clause uses full table name
            cursor = conn.execute(f"""
                SELECT replay_results.*, pc.player_name, pc.phase, pc.pot_odds,
                       rec.original_action, rec.original_quality, rec.original_ev_lost
                FROM replay_results
                JOIN replay_experiment_captures rec
                    ON rec.experiment_id = replay_results.experiment_id
                    AND rec.capture_id = replay_results.capture_id
                JOIN prompt_captures pc ON pc.id = replay_results.capture_id
                {where_clause}
                ORDER BY replay_results.created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            results = [dict(row) for row in cursor.fetchall()]

            return {
                'results': results,
                'total': total
            }

    def get_replay_results_summary(self, experiment_id: int) -> Dict[str, Any]:
        """Get summary statistics for replay experiment results.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dict with summary statistics by variant
        """
        with self._get_connection() as conn:
            # Overall stats
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_results,
                    SUM(CASE WHEN action_changed = 1 THEN 1 ELSE 0 END) as actions_changed,
                    SUM(CASE WHEN quality_change = 'improved' THEN 1 ELSE 0 END) as improved,
                    SUM(CASE WHEN quality_change = 'degraded' THEN 1 ELSE 0 END) as degraded,
                    SUM(CASE WHEN quality_change = 'unchanged' THEN 1 ELSE 0 END) as unchanged,
                    AVG(ev_delta) as avg_ev_delta,
                    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as errors
                FROM replay_results
                WHERE experiment_id = ?
            """, (experiment_id,))
            row = cursor.fetchone()

            overall = {
                'total_results': row[0] or 0,
                'actions_changed': row[1] or 0,
                'improved': row[2] or 0,
                'degraded': row[3] or 0,
                'unchanged': row[4] or 0,
                'avg_ev_delta': row[5],
                'errors': row[6] or 0,
            }

            # Stats by variant
            cursor = conn.execute("""
                SELECT
                    variant,
                    COUNT(*) as total,
                    SUM(CASE WHEN action_changed = 1 THEN 1 ELSE 0 END) as actions_changed,
                    SUM(CASE WHEN quality_change = 'improved' THEN 1 ELSE 0 END) as improved,
                    SUM(CASE WHEN quality_change = 'degraded' THEN 1 ELSE 0 END) as degraded,
                    AVG(ev_delta) as avg_ev_delta,
                    AVG(latency_ms) as avg_latency,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as errors
                FROM replay_results
                WHERE experiment_id = ?
                GROUP BY variant
            """, (experiment_id,))

            by_variant = {}
            for row in cursor.fetchall():
                by_variant[row[0]] = {
                    'total': row[1],
                    'actions_changed': row[2] or 0,
                    'improved': row[3] or 0,
                    'degraded': row[4] or 0,
                    'avg_ev_delta': row[5],
                    'avg_latency': row[6],
                    'total_input_tokens': row[7] or 0,
                    'total_output_tokens': row[8] or 0,
                    'errors': row[9] or 0,
                }

            return {
                'overall': overall,
                'by_variant': by_variant
            }

    def get_replay_experiment_captures(self, experiment_id: int) -> List[Dict[str, Any]]:
        """Get the captures linked to a replay experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of capture details with original info
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT rec.*, pc.player_name, pc.phase, pc.pot_odds,
                       pc.pot_total, pc.cost_to_call, pc.player_stack,
                       pc.model as original_model, pc.provider as original_provider
                FROM replay_experiment_captures rec
                JOIN prompt_captures pc ON pc.id = rec.capture_id
                WHERE rec.experiment_id = ?
                ORDER BY pc.created_at DESC
            """, (experiment_id,))

            return [dict(row) for row in cursor.fetchall()]

    def list_replay_experiments(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List replay experiments.

        Args:
            status: Optional filter by status
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'experiments' list and 'total' count
        """
        conditions = ["experiment_type = 'replay'"]
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Get total count
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM experiments {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # Get experiments with pagination
            cursor = conn.execute(f"""
                SELECT e.*,
                    (SELECT COUNT(*) FROM replay_experiment_captures rec WHERE rec.experiment_id = e.id) as capture_count,
                    (SELECT COUNT(*) FROM replay_results rr WHERE rr.experiment_id = e.id) as results_completed
                FROM experiments e
                {where_clause}
                ORDER BY e.created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            experiments = []
            for row in cursor.fetchall():
                exp = dict(row)
                # Parse JSON fields
                for field in ['config_json', 'summary_json', 'tags']:
                    if exp.get(field):
                        try:
                            exp[field] = json.loads(exp[field])
                        except json.JSONDecodeError:
                            logger.debug("Failed to parse JSON for field '%s' in experiment id=%s", field, exp.get('id'))

                # Calculate variant count from config
                config = exp.get('config_json', {})
                variants = config.get('variants', []) if isinstance(config, dict) else []
                exp['variant_count'] = len(variants)
                exp['results_total'] = exp['capture_count'] * exp['variant_count']

                experiments.append(exp)

            return {
                'experiments': experiments,
                'total': total
            }
