"""Schema management for the poker database.

Handles table creation and schema migrations.
"""
import sqlite3
import json
import logging

logger = logging.getLogger(__name__)

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
# v61: Add guest_usage_tracking table, owner_id to career stats/tournament tables
# v62: Add coach_mode column to games table for per-game coaching config
# v63: Add coach progression tables (player_skill_progress, player_gate_progress, player_coach_profile)
# v64: Add can_access_coach permission for RBAC gating
SCHEMA_VERSION = 64



class SchemaManager:
    """Manages database schema creation and migrations.

    Call ensure_schema() to create tables and run migrations.
    This is the single source of truth for database structure.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Create a database connection."""
        return sqlite3.connect(self.db_path, timeout=5.0)

    def _enable_wal_mode(self):
        """Enable WAL mode for concurrent read/write."""
        try:
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.execute("PRAGMA synchronous=NORMAL")
        except Exception as e:
            logger.warning(f"Could not enable WAL mode: {e}")

    def ensure_schema(self):
        """Create tables and run migrations. Idempotent."""
        self._enable_wal_mode()
        self._init_db()
        self._run_migrations()

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

            # Guest usage tracking (v61)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS guest_usage_tracking (
                    tracking_id TEXT PRIMARY KEY,
                    hands_played INTEGER DEFAULT 0,
                    last_hand_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Coach progression tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_skill_progress (
                    user_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'introduced',
                    total_opportunities INTEGER NOT NULL DEFAULT 0,
                    total_correct INTEGER NOT NULL DEFAULT 0,
                    window_opportunities INTEGER NOT NULL DEFAULT 0,
                    window_correct INTEGER NOT NULL DEFAULT 0,
                    streak_correct INTEGER NOT NULL DEFAULT 0,
                    streak_incorrect INTEGER NOT NULL DEFAULT 0,
                    last_evaluated_at TEXT,
                    first_seen_at TEXT,
                    PRIMARY KEY (user_id, skill_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_gate_progress (
                    user_id TEXT NOT NULL,
                    gate INTEGER NOT NULL,
                    unlocked BOOLEAN NOT NULL DEFAULT 0,
                    unlocked_at TEXT,
                    PRIMARY KEY (user_id, gate)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_coach_profile (
                    user_id TEXT PRIMARY KEY,
                    self_reported_level TEXT,
                    effective_level TEXT NOT NULL DEFAULT 'beginner',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)

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
            61: (self._migrate_v61_guest_tracking_and_owner_id, "Add guest_usage_tracking table, owner_id to career stats/tournament tables"),
            62: (self._migrate_v62_add_coach_mode, "Add coach_mode column to games table"),
            63: (self._migrate_v63_coach_progression, "Add coach progression tables"),
            64: (self._migrate_v64_add_coach_permission, "Add can_access_coach permission"),
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

    def _migrate_v61_guest_tracking_and_owner_id(self, conn: sqlite3.Connection) -> None:
        """Migration v61: Add guest_usage_tracking table and owner_id to stats tables.

        - guest_usage_tracking: tracks per-browser hand counts for guest rate limiting
        - owner_id on player_career_stats: links career stats to auth identity
        - owner_id on tournament_standings: links standings to auth identity
        - human_owner_id on tournament_results: links results to auth identity
        """
        # Create guest_usage_tracking table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guest_usage_tracking (
                tracking_id TEXT PRIMARY KEY,
                hands_played INTEGER DEFAULT 0,
                last_hand_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add owner_id to player_career_stats
        career_cols = [row[1] for row in conn.execute("PRAGMA table_info(player_career_stats)").fetchall()]
        if 'owner_id' not in career_cols:
            conn.execute("ALTER TABLE player_career_stats ADD COLUMN owner_id TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_career_stats_owner ON player_career_stats(owner_id)")
            logger.info("Added owner_id column to player_career_stats")

        # Add owner_id to tournament_standings
        standings_cols = [row[1] for row in conn.execute("PRAGMA table_info(tournament_standings)").fetchall()]
        if 'owner_id' not in standings_cols:
            conn.execute("ALTER TABLE tournament_standings ADD COLUMN owner_id TEXT")
            logger.info("Added owner_id column to tournament_standings")

        # Add human_owner_id to tournament_results
        results_cols = [row[1] for row in conn.execute("PRAGMA table_info(tournament_results)").fetchall()]
        if 'human_owner_id' not in results_cols:
            conn.execute("ALTER TABLE tournament_results ADD COLUMN human_owner_id TEXT")
            logger.info("Added human_owner_id column to tournament_results")

        logger.info("Migration v61 complete: guest tracking table and owner_id columns added")

    def _migrate_v62_add_coach_mode(self, conn: sqlite3.Connection) -> None:
        """Migration v62: Add coach_mode column to games table."""
        columns = [row[1] for row in conn.execute("PRAGMA table_info(games)").fetchall()]
        if 'coach_mode' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN coach_mode TEXT DEFAULT 'off'")
            logger.info("Added coach_mode column to games table")
        logger.info("Migration v62 complete: coach_mode column added to games")

    def _migrate_v63_coach_progression(self, conn: sqlite3.Connection) -> None:
        """Migration v63: Add coach progression tables."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_skill_progress (
                user_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'introduced',
                total_opportunities INTEGER NOT NULL DEFAULT 0,
                total_correct INTEGER NOT NULL DEFAULT 0,
                window_opportunities INTEGER NOT NULL DEFAULT 0,
                window_correct INTEGER NOT NULL DEFAULT 0,
                streak_correct INTEGER NOT NULL DEFAULT 0,
                streak_incorrect INTEGER NOT NULL DEFAULT 0,
                last_evaluated_at TEXT,
                first_seen_at TEXT,
                PRIMARY KEY (user_id, skill_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_gate_progress (
                user_id TEXT NOT NULL,
                gate INTEGER NOT NULL,
                unlocked BOOLEAN NOT NULL DEFAULT 0,
                unlocked_at TEXT,
                PRIMARY KEY (user_id, gate)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_coach_profile (
                user_id TEXT PRIMARY KEY,
                self_reported_level TEXT,
                effective_level TEXT NOT NULL DEFAULT 'beginner',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        logger.info("Migration v63 complete: coach progression tables added")

    def _migrate_v64_add_coach_permission(self, conn: sqlite3.Connection) -> None:
        """Migration v64: Add can_access_coach permission for RBAC gating.

        Grants the permission to both 'admin' and 'user' groups so
        authenticated users can access the coach. Guests (no group
        membership) are denied.
        """
        conn.execute("""
            INSERT OR IGNORE INTO permissions (name, description, category)
            VALUES ('can_access_coach', 'Access to the poker coaching feature', 'coach')
        """)
        conn.execute("""
            INSERT OR IGNORE INTO group_permissions (group_id, permission_id)
            SELECT g.id, p.id
            FROM groups g, permissions p
            WHERE g.name IN ('admin', 'user') AND p.name = 'can_access_coach'
        """)
        logger.info("Migration v64 complete: can_access_coach permission added")

