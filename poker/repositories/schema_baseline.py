"""Canonical head schema (the v157 baseline) — GENERATED, do not hand-edit.

Regenerate with ``python scripts/_gen_schema_baseline.py`` (force-added in
scripts/). ``_init_db`` replays these statements + seed rows instead of the
v1..vN ``_migrate_vN`` chain. Statements are idempotent (``IF NOT EXISTS``)
and seed rows use INSERT OR IGNORE, so replay on an existing DB is a no-op.

Proven equivalent to the chain by tests/test_schema_consistency.py.
"""

BASELINE_VERSION = 157

# 68 tables, 138 indexes — 206 statements.
BASELINE_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )""",
    """CREATE TABLE IF NOT EXISTS games (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_games_updated ON games(updated_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_games_owner ON games(owner_id)""",
    """CREATE TABLE IF NOT EXISTS game_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_type TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_messages_game_id ON game_messages(game_id, timestamp)""",
    """CREATE TABLE IF NOT EXISTS ai_player_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    conversation_history TEXT,
                    personality_state TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_ai_player_game ON ai_player_state(game_id, player_name)""",
    """CREATE TABLE IF NOT EXISTS pressure_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    details_json TEXT, hand_number INTEGER,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_pressure_events_game ON pressure_events(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_pressure_events_player ON pressure_events(player_name)""",
    """CREATE INDEX IF NOT EXISTS idx_pressure_events_type ON pressure_events(event_type)""",
    """CREATE TABLE IF NOT EXISTS personalities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_generated BOOLEAN DEFAULT 1,
                    source TEXT DEFAULT 'ai_generated',
                    times_used INTEGER DEFAULT 0,
                    elasticity_config TEXT,
                    personality_id TEXT UNIQUE
                , owner_id TEXT, visibility TEXT DEFAULT 'public', circulating INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS hand_history (
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
                    deck_seed INTEGER,
                    community_cards_by_phase_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_hand_history_game ON hand_history(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_hand_history_timestamp ON hand_history(timestamp DESC)""",
    """CREATE TABLE IF NOT EXISTS hand_equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hand_history_id INTEGER REFERENCES hand_history(id) ON DELETE SET NULL,
                    game_id TEXT REFERENCES games(game_id) ON DELETE SET NULL,
                    hand_number INTEGER NOT NULL,
                    street TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    player_hole_cards TEXT,
                    board_cards TEXT,
                    equity REAL NOT NULL,
                    was_active BOOLEAN DEFAULT 1,
                    sample_count INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(hand_history_id, street, player_name)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_hand_equity_game ON hand_equity(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_hand_equity_hand ON hand_equity(hand_history_id)""",
    """CREATE INDEX IF NOT EXISTS idx_hand_equity_player ON hand_equity(player_name)""",
    """CREATE INDEX IF NOT EXISTS idx_hand_equity_street_equity ON hand_equity(street, equity)""",
    """CREATE TABLE IF NOT EXISTS memorable_hands (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_memorable_observer ON memorable_hands(observer_name)""",
    """CREATE INDEX IF NOT EXISTS idx_memorable_opponent ON memorable_hands(opponent_name)""",
    """CREATE INDEX IF NOT EXISTS idx_memorable_hands_game ON memorable_hands(game_id)""",
    """CREATE TABLE IF NOT EXISTS relationship_states (
                    observer_id TEXT NOT NULL,
                    opponent_id TEXT NOT NULL,
                    heat REAL NOT NULL DEFAULT 0.0,
                    -- 0.35 == REGARD_NEUTRAL (opponent_model.py): the earned
                    -- regard neutral baseline. Keep in sync with that constant
                    -- so a default-axes row reads as a true neutral stranger.
                    respect REAL NOT NULL DEFAULT 0.35,
                    likability REAL NOT NULL DEFAULT 0.35,
                    last_seen TIMESTAMP,
                    last_decay_tick TIMESTAMP,
                    notes TEXT,
                    nickname_override TEXT,
                    PRIMARY KEY (observer_id, opponent_id)
                )""",
    """CREATE TABLE IF NOT EXISTS ai_table_hand_counts (
                    sandbox_id   TEXT NOT NULL,
                    ai_id        TEXT NOT NULL,
                    table_id     TEXT NOT NULL,
                    hands        INTEGER NOT NULL DEFAULT 0,
                    net_chips    INTEGER NOT NULL DEFAULT 0,
                    last_hand_at TIMESTAMP,
                    PRIMARY KEY (sandbox_id, ai_id, table_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_ai_table_hand_counts_ai
                    ON ai_table_hand_counts(sandbox_id, ai_id)
            """,
    """CREATE TABLE IF NOT EXISTS player_bankroll_state (
                    player_id TEXT PRIMARY KEY,
                    chips INTEGER NOT NULL DEFAULT 0,
                    starting_bankroll INTEGER NOT NULL DEFAULT 0
                )""",
    """CREATE TABLE IF NOT EXISTS ai_vice_state (
                    personality_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    ends_at TIMESTAMP NOT NULL,
                    amount INTEGER NOT NULL,
                    duration_bucket TEXT NOT NULL,
                    narration TEXT NOT NULL,
                    PRIMARY KEY (personality_id, sandbox_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_ai_vice_ends_at
                    ON ai_vice_state(sandbox_id, ends_at)
            """,
    """CREATE TABLE IF NOT EXISTS ai_side_hustle_state (
                    personality_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    ends_at TIMESTAMP NOT NULL,
                    amount INTEGER NOT NULL,
                    duration_bucket TEXT NOT NULL,
                    narration TEXT NOT NULL,
                    PRIMARY KEY (personality_id, sandbox_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_ai_side_hustle_ends_at
                    ON ai_side_hustle_state(sandbox_id, ends_at)
            """,
    """CREATE TABLE IF NOT EXISTS hand_commentary (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_hand_commentary_game ON hand_commentary(game_id, player_name)""",
    """CREATE INDEX IF NOT EXISTS idx_hand_commentary_player_recent ON hand_commentary(game_id, player_name, hand_number DESC)""",
    """CREATE TABLE IF NOT EXISTS controller_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    tilt_state_json TEXT,
                    elastic_personality_json TEXT,
                    prompt_config_json TEXT,
                    psychology_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_controller_state_game ON controller_state(game_id, player_name)""",
    """CREATE TABLE IF NOT EXISTS tournament_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL UNIQUE,
                    winner_name TEXT,
                    total_hands INTEGER DEFAULT 0,
                    biggest_pot INTEGER DEFAULT 0,
                    starting_player_count INTEGER,
                    human_player_name TEXT,
                    human_finishing_position INTEGER,
                    started_at TIMESTAMP,
                    ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, human_owner_id TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_tournament_results_winner ON tournament_results(winner_name)""",
    """CREATE TABLE IF NOT EXISTS tournament_standings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    is_human BOOLEAN DEFAULT 0,
                    finishing_position INTEGER,
                    eliminated_by TEXT,
                    eliminated_at_hand INTEGER, final_stack INTEGER, hands_won INTEGER, hands_played INTEGER, times_eliminated INTEGER, all_in_wins INTEGER, all_in_losses INTEGER, owner_id TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_tournament_standings_game ON tournament_standings(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_tournament_standings_player ON tournament_standings(player_name)""",
    """CREATE TABLE IF NOT EXISTS player_career_stats (
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
                , owner_id TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_career_stats_player ON player_career_stats(player_name)""",
    """CREATE TABLE IF NOT EXISTS avatar_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    personality_id TEXT NOT NULL,
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
                    UNIQUE(personality_id, emotion)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_avatar_pid ON avatar_images(personality_id)""",
    """CREATE INDEX IF NOT EXISTS idx_avatar_emotion ON avatar_images(emotion)""",
    """CREATE TABLE IF NOT EXISTS api_usage (
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
                , error_message TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_owner ON api_usage(owner_id)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_game ON api_usage(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_owner_created ON api_usage(owner_id, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_owner_call_type ON api_usage(owner_id, call_type)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_game_call_type ON api_usage(game_id, call_type)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_model_created ON api_usage(model, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_model_effort ON api_usage(model, reasoning_effort)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_request_id ON api_usage(request_id)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_cost ON api_usage(estimated_cost)""",
    """CREATE TABLE IF NOT EXISTS model_pricing (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup ON model_pricing(provider, model)""",
    """CREATE INDEX IF NOT EXISTS idx_model_pricing_validity ON model_pricing(provider, model, unit, valid_from, valid_until)""",
    """CREATE TABLE IF NOT EXISTS enabled_models (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_enabled_models_provider ON enabled_models(provider, enabled)""",
    """CREATE TABLE IF NOT EXISTS prompt_captures (
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
                    metadata_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE SET NULL,
                    FOREIGN KEY (parent_id) REFERENCES prompt_captures(id) ON DELETE SET NULL
                )""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_is_image ON prompt_captures(is_image_capture)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_parent ON prompt_captures(parent_id)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_owner ON prompt_captures(owner_id)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)""",
    """CREATE TABLE IF NOT EXISTS reference_images (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_reference_images_owner ON reference_images(owner_id)""",
    """CREATE INDEX IF NOT EXISTS idx_reference_images_expires ON reference_images(expires_at)""",
    """CREATE TABLE IF NOT EXISTS player_decision_analysis (
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
                    bet_sizing TEXT,
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
                    display_emotion TEXT,
                    elastic_aggression REAL,
                    elastic_bluff_tendency REAL,
                    elastic_tightness REAL,
                    elastic_confidence REAL,
                    elastic_composure REAL,
                    elastic_table_talk REAL,
                    opponent_ranges_json TEXT,
                    board_texture_json TEXT,
                    player_hand_canonical TEXT,
                    player_hand_in_range BOOLEAN,
                    player_hand_tier TEXT,
                    standard_range_pct REAL,
                    analyzer_version TEXT,
                    processing_time_ms INTEGER,
                    zone_confidence REAL,
                    zone_composure REAL,
                    zone_energy REAL,
                    zone_manifestation TEXT,
                    zone_sweet_spots_json TEXT,
                    zone_penalties_json TEXT,
                    zone_primary_sweet_spot TEXT,
                    zone_primary_penalty TEXT,
                    zone_total_penalty_strength REAL,
                    zone_in_neutral_territory BOOLEAN,
                    zone_intrusive_thoughts_injected BOOLEAN,
                    zone_intrusive_thoughts_json TEXT,
                    zone_penalty_strategy_applied TEXT,
                    zone_info_degraded BOOLEAN,
                    zone_strategy_selected TEXT,
                    quality_score REAL,
                    menu_best_ev TEXT,
                    menu_chosen_ev TEXT,
                    menu_picked_best INTEGER,
                    menu_num_options INTEGER,
                    intervention_trace_json TEXT,
                    strategy_pipeline_snapshot_json TEXT,
                    preflop_node_key TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE
                )""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_game ON player_decision_analysis(game_id)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_request ON player_decision_analysis(request_id)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_quality ON player_decision_analysis(decision_quality)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_ev_lost ON player_decision_analysis(ev_lost DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_player ON player_decision_analysis(player_name)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_capture ON player_decision_analysis(capture_id)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_zone_penalty ON player_decision_analysis(zone_primary_penalty)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_analysis_zone_sweet_spot ON player_decision_analysis(zone_primary_sweet_spot)""",
    """CREATE TABLE IF NOT EXISTS tournaments (
                    tournament_id TEXT PRIMARY KEY,
                    owner_id      TEXT NOT NULL,
                    game_id       TEXT,
                    status        TEXT NOT NULL,
                    resolver_kind TEXT NOT NULL DEFAULT 'fake',
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    session_json  TEXT NOT NULL,
                    buy_in        INTEGER NOT NULL DEFAULT 0,
                    rake          INTEGER NOT NULL DEFAULT 0,
                    bank_overlay  INTEGER NOT NULL DEFAULT 0,
                    prize_pool    INTEGER NOT NULL DEFAULT 0,
                    payout_status TEXT NOT NULL DEFAULT 'skipped'
                )""",
    """CREATE INDEX IF NOT EXISTS idx_tournaments_owner ON tournaments(owner_id, status)""",
    """CREATE INDEX IF NOT EXISTS idx_tournaments_game ON tournaments(game_id)""",
    """CREATE TABLE IF NOT EXISTS tournament_invites (
                    invite_id     TEXT PRIMARY KEY,
                    owner_id      TEXT NOT NULL,
                    sandbox_id    TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'offered',
                    buy_in        INTEGER NOT NULL DEFAULT 0,
                    field_size    INTEGER NOT NULL,
                    table_size    INTEGER NOT NULL,
                    starting_stack INTEGER NOT NULL,
                    seed          INTEGER NOT NULL DEFAULT 0,
                    expires_at    TEXT,
                    tournament_id TEXT,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    -- v148 (tournaments-as-a-draw): the draw-selected field locked
                    -- at offer time (JSON array of personality_ids), and the
                    -- subset that has already vacated cash en route. NULL on
                    -- pre-v148 / non-draw offers (random-shuffle field at spawn).
                    reserved_pids TEXT,
                    vacated_pids  TEXT
                )""",
    """CREATE INDEX IF NOT EXISTS idx_tournament_invites_owner ON tournament_invites(owner_id, status)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_invites_one_open ON tournament_invites(owner_id) WHERE status = 'offered'""",
    """CREATE TABLE IF NOT EXISTS experiments (
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
                , experiment_type TEXT DEFAULT 'tournament')""",
    """CREATE INDEX IF NOT EXISTS idx_experiments_name ON experiments(name)""",
    """CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)""",
    """CREATE TABLE IF NOT EXISTS experiment_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL,
                    game_id TEXT NOT NULL,
                    variant TEXT,
                    variant_config_json TEXT,
                    tournament_number INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, state TEXT DEFAULT 'idle', last_heartbeat_at TIMESTAMP, last_api_call_started_at TIMESTAMP, process_id INTEGER, resume_lock_acquired_at TIMESTAMP,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE CASCADE,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE CASCADE,
                    UNIQUE(experiment_id, game_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_experiment_games_experiment ON experiment_games(experiment_id)""",
    """CREATE INDEX IF NOT EXISTS idx_experiment_games_game ON experiment_games(game_id)""",
    """CREATE TABLE IF NOT EXISTS experiment_chat_sessions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    messages_json TEXT NOT NULL,
                    config_snapshot_json TEXT NOT NULL,
                    config_versions_json TEXT,
                    is_archived BOOLEAN DEFAULT 0
                )""",
    """CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON experiment_chat_sessions(owner_id, updated_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON experiment_chat_sessions(owner_id, is_archived)""",
    """CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    description TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""",
    """CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    picture TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP,
                    linked_guest_id TEXT,
                    is_guest BOOLEAN DEFAULT 0,
                    last_game_created_at REAL
                )""",
    """CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)""",
    """CREATE INDEX IF NOT EXISTS idx_users_linked_guest ON users(linked_guest_id)""",
    """CREATE TABLE IF NOT EXISTS prompt_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    prompt_config TEXT,
                    guidance_injection TEXT,
                    owner_id TEXT,
                    is_system BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_presets_owner ON prompt_presets(owner_id)""",
    """CREATE INDEX IF NOT EXISTS idx_prompt_presets_name ON prompt_presets(name)""",
    """CREATE TABLE IF NOT EXISTS decision_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id INTEGER NOT NULL REFERENCES player_decision_analysis(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    label_type TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(decision_id, label)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_decision_labels_label ON decision_labels(label)""",
    """CREATE INDEX IF NOT EXISTS idx_decision_labels_decision_id ON decision_labels(decision_id)""",
    """CREATE TABLE IF NOT EXISTS replay_experiment_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
                    capture_id INTEGER NOT NULL REFERENCES prompt_captures(id) ON DELETE CASCADE,
                    original_action TEXT,
                    original_quality TEXT,
                    original_ev_lost REAL,
                    UNIQUE(experiment_id, capture_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_replay_captures_experiment ON replay_experiment_captures(experiment_id)""",
    """CREATE INDEX IF NOT EXISTS idx_replay_captures_capture ON replay_experiment_captures(capture_id)""",
    """CREATE TABLE IF NOT EXISTS replay_results (
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
                )""",
    """CREATE INDEX IF NOT EXISTS idx_replay_results_experiment ON replay_results(experiment_id)""",
    """CREATE INDEX IF NOT EXISTS idx_replay_results_capture ON replay_results(capture_id)""",
    """CREATE INDEX IF NOT EXISTS idx_replay_results_variant ON replay_results(variant)""",
    """CREATE INDEX IF NOT EXISTS idx_replay_results_quality ON replay_results(quality_change)""",
    """CREATE TABLE IF NOT EXISTS groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    is_system BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""",
    """CREATE INDEX IF NOT EXISTS idx_groups_name ON groups(name)""",
    """CREATE TABLE IF NOT EXISTS user_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    assigned_by TEXT,
                    UNIQUE(user_id, group_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_user_groups_user ON user_groups(user_id)""",
    """CREATE INDEX IF NOT EXISTS idx_user_groups_group ON user_groups(group_id)""",
    """CREATE TABLE IF NOT EXISTS permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    category TEXT
                )""",
    """CREATE INDEX IF NOT EXISTS idx_permissions_name ON permissions(name)""",
    """CREATE TABLE IF NOT EXISTS group_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
                    UNIQUE(group_id, permission_id)
                )""",
    """CREATE INDEX IF NOT EXISTS idx_group_permissions_group ON group_permissions(group_id)""",
    """CREATE INDEX IF NOT EXISTS idx_group_permissions_permission ON group_permissions(permission_id)""",
    """CREATE TABLE IF NOT EXISTS guest_usage_tracking (
                    tracking_id TEXT PRIMARY KEY,
                    hands_played INTEGER DEFAULT 0,
                    last_hand_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )""",
    """CREATE TABLE IF NOT EXISTS player_skill_progress (
                    user_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'introduced',
                    total_opportunities INTEGER NOT NULL DEFAULT 0,
                    total_correct INTEGER NOT NULL DEFAULT 0,
                    window_opportunities INTEGER NOT NULL DEFAULT 0,
                    window_correct INTEGER NOT NULL DEFAULT 0,
                    window_decisions TEXT DEFAULT '[]',
                    streak_correct INTEGER NOT NULL DEFAULT 0,
                    streak_incorrect INTEGER NOT NULL DEFAULT 0,
                    last_evaluated_at TEXT,
                    first_seen_at TEXT,
                    PRIMARY KEY (user_id, skill_id)
                )""",
    """CREATE TABLE IF NOT EXISTS player_gate_progress (
                    user_id TEXT NOT NULL,
                    gate INTEGER NOT NULL,
                    unlocked BOOLEAN NOT NULL DEFAULT 0,
                    unlocked_at TEXT,
                    PRIMARY KEY (user_id, gate)
                )""",
    """CREATE TABLE IF NOT EXISTS player_coach_profile (
                    user_id TEXT PRIMARY KEY,
                    self_reported_level TEXT,
                    effective_level TEXT NOT NULL DEFAULT 'beginner',
                    created_at TEXT,
                    updated_at TEXT
                , onboarding_completed_at TEXT, range_targets TEXT DEFAULT NULL)""",
    """CREATE INDEX IF NOT EXISTS idx_experiment_games_state_heartbeat
            ON experiment_games(state, last_heartbeat_at)
        """,
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_career_stats_owner ON player_career_stats(owner_id)""",
    """CREATE INDEX IF NOT EXISTS idx_personalities_owner ON personalities(owner_id)""",
    """CREATE INDEX IF NOT EXISTS idx_personalities_visibility ON personalities(visibility)""",
    """CREATE INDEX IF NOT EXISTS idx_pressure_events_hand ON pressure_events(game_id, hand_number)""",
    """CREATE TABLE IF NOT EXISTS bounded_replay_results (
                id INTEGER PRIMARY KEY,
                experiment_id INTEGER NOT NULL,
                capture_id INTEGER NOT NULL,
                variant TEXT NOT NULL,
                sample_number INTEGER NOT NULL,
                option_config_json TEXT,
                generated_options_json TEXT,
                new_response TEXT,
                choice_number INTEGER,
                new_action TEXT,
                new_raise_amount INTEGER,
                reasoning TEXT,
                provider TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                latency_ms INTEGER,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(experiment_id, capture_id, variant, sample_number)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_bounded_replay_experiment ON bounded_replay_results(experiment_id)""",
    """CREATE INDEX IF NOT EXISTS idx_bounded_replay_capture ON bounded_replay_results(capture_id)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_personalities_personality_id ON personalities(personality_id)""",
    """CREATE TABLE IF NOT EXISTS chip_ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT NOT NULL,
                sink TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount >= 0),
                reason TEXT NOT NULL,
                context_json TEXT
            , sandbox_id TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_chip_ledger_created ON chip_ledger_entries(created_at DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_chip_ledger_reason ON chip_ledger_entries(reason)""",
    """CREATE TABLE IF NOT EXISTS stakes (
                stake_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                staker_id TEXT,
                staker_kind TEXT NOT NULL,
                borrower_id TEXT NOT NULL,
                borrower_kind TEXT NOT NULL,
                format TEXT NOT NULL,
                principal INTEGER NOT NULL,
                match_amount INTEGER NOT NULL DEFAULT 0,
                origination_fee INTEGER NOT NULL DEFAULT 0,
                cut REAL NOT NULL,
                status TEXT NOT NULL,
                carry_amount INTEGER NOT NULL DEFAULT 0,
                stake_tier TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                settled_at TIMESTAMP,
                -- forgiveness_last_asked must be UTC naive (written via
                -- datetime.utcnow().isoformat()) so the rate-limit's
                -- (now - last_asked) subtraction is timezone-consistent.
                forgiveness_last_asked TIMESTAMP,
                -- v106: settlement chip flows captured at settle time so
                -- the Net Worth history can show per-stake P&L. NULL on
                -- active rows and legacy settled-pre-v106 rows.
                staker_payout INTEGER,
                borrower_payout INTEGER
            , pending_forgiveness_ask TIMESTAMP, table_id TEXT, resolution TEXT, sandbox_id TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_stakes_borrower_carry
                ON stakes(borrower_id, borrower_kind, status)
                WHERE status = 'carry'
        """,
    """CREATE INDEX IF NOT EXISTS idx_stakes_staker_carry
                ON stakes(staker_id, status)
                WHERE status = 'carry'
        """,
    """CREATE INDEX IF NOT EXISTS idx_stakes_session
                ON stakes(session_id)
        """,
    """CREATE TABLE IF NOT EXISTS sandboxes (
                sandbox_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                archived_at TIMESTAMP
            )""",
    """CREATE INDEX IF NOT EXISTS idx_sandboxes_owner
                ON sandboxes(owner_id)
                WHERE archived_at IS NULL
        """,
    """CREATE INDEX IF NOT EXISTS idx_chip_ledger_sandbox
                ON chip_ledger_entries(sandbox_id)
                WHERE sandbox_id IS NOT NULL
        """,
    """CREATE TABLE IF NOT EXISTS cash_sessions (
                session_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                sandbox_id TEXT,
                stake_label TEXT NOT NULL,
                is_staked INTEGER NOT NULL DEFAULT 0,
                stake_id TEXT,
                initial_buy_in INTEGER NOT NULL,
                total_buy_in INTEGER NOT NULL,
                sponsor_principal INTEGER NOT NULL DEFAULT 0,
                cash_table_id TEXT,
                cash_seat_index INTEGER,
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP,
                final_chips_at_table INTEGER,
                sponsor_repaid INTEGER NOT NULL DEFAULT 0,
                player_take_home INTEGER,
                hands_played INTEGER,
                hands_won INTEGER,
                biggest_pot_won INTEGER,
                duration_seconds INTEGER,
                closed_status TEXT
            , session_state TEXT NOT NULL DEFAULT 'active', last_load_error TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_cash_sessions_owner_started
                ON cash_sessions(owner_id, started_at DESC)
        """,
    """CREATE INDEX IF NOT EXISTS idx_cash_sessions_active
                ON cash_sessions(owner_id)
                WHERE ended_at IS NULL
        """,
    """CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                world_pace TEXT NOT NULL DEFAULT 'lively',
                preferences_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , bio TEXT)""",
    """CREATE TABLE IF NOT EXISTS holdings_snapshots (
                snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at  TIMESTAMP NOT NULL,
                sandbox_id   TEXT NOT NULL,
                entity_id    TEXT NOT NULL,
                kind         TEXT NOT NULL,
                net_worth    INTEGER NOT NULL,
                chips        INTEGER NOT NULL,
                receivable   INTEGER NOT NULL DEFAULT 0,
                outstanding  INTEGER NOT NULL DEFAULT 0
            )""",
    """CREATE INDEX IF NOT EXISTS idx_holdings_snap_scope
                ON holdings_snapshots(sandbox_id, captured_at)
        """,
    """CREATE INDEX IF NOT EXISTS idx_holdings_snap_entity
                ON holdings_snapshots(sandbox_id, entity_id, captured_at)
        """,
    """CREATE TABLE IF NOT EXISTS user_avatars (
                user_id TEXT PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                icon_data BLOB NOT NULL,
                full_data BLOB NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'image/png',
                source TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
    """CREATE INDEX IF NOT EXISTS idx_user_avatars_public_id ON user_avatars(public_id)""",
    """CREATE INDEX IF NOT EXISTS idx_cash_sessions_blocking
                ON cash_sessions(owner_id)
                WHERE session_state IN ('active', 'paused', 'abandoning')
            """,
    """CREATE TABLE IF NOT EXISTS cash_session_events (
                event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                owner_id    TEXT,
                sandbox_id  TEXT,
                event       TEXT NOT NULL,
                detail_json TEXT,
                created_at  TIMESTAMP NOT NULL
            )""",
    """CREATE INDEX IF NOT EXISTS idx_cash_session_events_session
                ON cash_session_events(session_id, created_at)
        """,
    """CREATE INDEX IF NOT EXISTS idx_cash_session_events_scope
                ON cash_session_events(sandbox_id, event, created_at)
        """,
    """CREATE TABLE IF NOT EXISTS coach_session_evaluations (
                game_id          TEXT PRIMARY KEY,
                user_id          TEXT,
                evaluations_json TEXT NOT NULL,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
    """CREATE TABLE IF NOT EXISTS prestige_snapshots (
                snapshot_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at           TIMESTAMP NOT NULL,
                sandbox_id            TEXT NOT NULL,
                owner_id              TEXT NOT NULL,
                renown                REAL NOT NULL,
                regard                REAL NOT NULL,
                quadrant              TEXT NOT NULL,
                renown_breadth        REAL NOT NULL DEFAULT 0,
                renown_tenure         REAL NOT NULL DEFAULT 0,
                renown_stake_tier     REAL NOT NULL DEFAULT 0,
                renown_beat_respected REAL NOT NULL DEFAULT 0,
                renown_high_stakes    REAL NOT NULL DEFAULT 0,
                regard_likability     REAL NOT NULL DEFAULT 0,
                regard_respect        REAL NOT NULL DEFAULT 0,
                regard_heat           REAL NOT NULL DEFAULT 0,
                opponent_count        INTEGER NOT NULL DEFAULT 0
            , formula_version TEXT NOT NULL DEFAULT 'v1', renown_v2 REAL, victim_percentile REAL, high_cut REAL, renown_v2_components TEXT, field_size INTEGER, entity_kind TEXT NOT NULL DEFAULT 'player')""",
    """CREATE INDEX IF NOT EXISTS idx_prestige_snap_scope
                ON prestige_snapshots(sandbox_id, owner_id, captured_at)
        """,
    """CREATE INDEX IF NOT EXISTS idx_relationship_states_opponent
                ON relationship_states(opponent_id)
        """,
    """CREATE INDEX IF NOT EXISTS idx_personalities_circulating ON personalities(circulating)""",
    """CREATE TABLE IF NOT EXISTS opponent_observation_lifetime (
                sandbox_id      TEXT NOT NULL,
                observer_id     TEXT NOT NULL,
                opponent_id     TEXT NOT NULL,
                hands_dealt     INTEGER NOT NULL DEFAULT 0,
                hands_observed  INTEGER NOT NULL DEFAULT 0,
                vpip_count      INTEGER NOT NULL DEFAULT 0,
                pfr_count       INTEGER NOT NULL DEFAULT 0,
                bet_raise_count INTEGER NOT NULL DEFAULT 0,
                call_count      INTEGER NOT NULL DEFAULT 0,
                showdowns_seen  INTEGER NOT NULL DEFAULT 0,
                showdowns_won   INTEGER NOT NULL DEFAULT 0,
                first_seen      TIMESTAMP,
                last_updated    TIMESTAMP, all_in_count INTEGER NOT NULL DEFAULT 0, fold_to_cbet_count INTEGER NOT NULL DEFAULT 0, cbet_faced_count INTEGER NOT NULL DEFAULT 0, cbet_attempt_count INTEGER NOT NULL DEFAULT 0, postflop_seen_as_pfr_count INTEGER NOT NULL DEFAULT 0, barrel_count INTEGER NOT NULL DEFAULT 0, barrel_opportunity_count INTEGER NOT NULL DEFAULT 0, third_barrel_count INTEGER NOT NULL DEFAULT 0, third_barrel_opportunity_count INTEGER NOT NULL DEFAULT 0, postflop_bet_raise_count INTEGER NOT NULL DEFAULT 0, postflop_call_count INTEGER NOT NULL DEFAULT 0, equity_betting_count INTEGER NOT NULL DEFAULT 0, equity_raising_count INTEGER NOT NULL DEFAULT 0, equity_calling_count INTEGER NOT NULL DEFAULT 0, equity_betting_sum REAL NOT NULL DEFAULT 0, equity_raising_sum REAL NOT NULL DEFAULT 0, equity_calling_sum REAL NOT NULL DEFAULT 0, preflop_voluntary_action_count INTEGER NOT NULL DEFAULT 0, preflop_voluntary_opportunities INTEGER NOT NULL DEFAULT 0, preflop_open_raise_count INTEGER NOT NULL DEFAULT 0, preflop_open_opportunities INTEGER NOT NULL DEFAULT 0, limp_count INTEGER NOT NULL DEFAULT 0, equity_betting_big_count INTEGER NOT NULL DEFAULT 0, equity_betting_small_count INTEGER NOT NULL DEFAULT 0, fold_to_big_bet_count INTEGER NOT NULL DEFAULT 0, big_bet_faced_count INTEGER NOT NULL DEFAULT 0, equity_betting_big_sum REAL NOT NULL DEFAULT 0, equity_betting_small_sum REAL NOT NULL DEFAULT 0, facing_bet_opportunities INTEGER NOT NULL DEFAULT 0, all_ins_facing_bet INTEGER NOT NULL DEFAULT 0, postflop_open_opportunities INTEGER NOT NULL DEFAULT 0, postflop_jam_opens INTEGER NOT NULL DEFAULT 0, flop_check_barrel_count INTEGER NOT NULL DEFAULT 0, flop_check_barrel_opportunity_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (sandbox_id, observer_id, opponent_id)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_obs_lifetime_observer
                ON opponent_observation_lifetime(sandbox_id, observer_id)
        """,
    """CREATE TABLE IF NOT EXISTS dossier_informant_unlocks (
                sandbox_id   TEXT NOT NULL,
                observer_id  TEXT NOT NULL,
                opponent_id  TEXT NOT NULL,
                section_id   TEXT NOT NULL,
                price_paid   INTEGER NOT NULL DEFAULT 0,
                purchased_at TIMESTAMP NOT NULL,
                PRIMARY KEY (sandbox_id, observer_id, opponent_id, section_id)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_informant_unlocks_pair
                ON dossier_informant_unlocks(sandbox_id, observer_id, opponent_id)
        """,
    """CREATE TABLE IF NOT EXISTS entity_presence (
                entity_id   TEXT NOT NULL,
                sandbox_id  TEXT NOT NULL DEFAULT 'default',
                state       TEXT NOT NULL,
                table_id    TEXT,
                seat_index  INTEGER,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (entity_id, sandbox_id),
                CHECK (state IN ('offline','seated','idle','side_hustle','vice','pool')),
                CHECK (
                    (state = 'seated' AND table_id IS NOT NULL AND seat_index IS NOT NULL)
                    OR
                    (state <> 'seated' AND table_id IS NULL AND seat_index IS NULL)
                )
            )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_presence_seat
                ON entity_presence(sandbox_id, table_id, seat_index)
                WHERE state = 'seated'
        """,
    """CREATE INDEX IF NOT EXISTS idx_entity_presence_sandbox
                ON entity_presence(sandbox_id)
        """,
    """CREATE INDEX IF NOT EXISTS idx_entity_presence_sandbox_state
                ON entity_presence(sandbox_id, state)
        """,
    """CREATE TABLE IF NOT EXISTS cash_idle_metadata (
                personality_id TEXT NOT NULL,
                sandbox_id     TEXT NOT NULL,
                reason         TEXT,
                target_stake   TEXT,
                left_at        TEXT,
                PRIMARY KEY (personality_id, sandbox_id)
            )""",
    """CREATE TABLE IF NOT EXISTS coach_tips (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                game_id               TEXT,
                owner_id              TEXT,
                player_name           TEXT,
                hand_number           INTEGER,
                phase                 TEXT,
                tip_text              TEXT,
                leak_fired            INTEGER NOT NULL DEFAULT 0,
                leak_scenario         TEXT,
                leak_position         TEXT,
                leak_kind             TEXT,
                leak_status           TEXT,
                leak_granularity      TEXT,
                player_hand_canonical TEXT,
                player_position       TEXT
            )""",
    """CREATE INDEX IF NOT EXISTS idx_coach_tips_join
                ON coach_tips(game_id, hand_number, player_name)
        """,
    """CREATE INDEX IF NOT EXISTS idx_coach_tips_owner ON coach_tips(owner_id)""",
    """CREATE TABLE IF NOT EXISTS cash_scalps (
                sandbox_id     TEXT NOT NULL,
                eliminator_id  TEXT NOT NULL,
                victim_id      TEXT NOT NULL,
                count          INTEGER NOT NULL DEFAULT 0,
                last_at        TIMESTAMP,
                PRIMARY KEY (sandbox_id, eliminator_id, victim_id)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_cash_scalps_eliminator
                ON cash_scalps(sandbox_id, eliminator_id)
        """,
    """CREATE INDEX IF NOT EXISTS idx_cash_scalps_victim
                ON cash_scalps(sandbox_id, victim_id)
        """,
    """CREATE INDEX IF NOT EXISTS idx_prestige_snap_kind
                ON prestige_snapshots(sandbox_id, entity_kind, owner_id, renown_v2)
        """,
    """CREATE INDEX IF NOT EXISTS idx_holdings_snap_peak
                ON holdings_snapshots(sandbox_id, entity_id, net_worth)
        """,
    """CREATE INDEX IF NOT EXISTS idx_chip_ledger_source ON chip_ledger_entries(source, sandbox_id)""",
    """CREATE INDEX IF NOT EXISTS idx_chip_ledger_sink ON chip_ledger_entries(sink, sandbox_id)""",
    """CREATE TABLE IF NOT EXISTS career_progress (
                sandbox_id    TEXT NOT NULL,
                owner_id      TEXT NOT NULL,
                progress_json TEXT NOT NULL DEFAULT '{}',
                updated_at    TIMESTAMP NOT NULL,
                PRIMARY KEY (sandbox_id, owner_id)
            )""",
    """CREATE TABLE IF NOT EXISTS opponent_models (
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
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP, tendencies_json TEXT, observer_id TEXT, opponent_id TEXT, lifetime_applied_json TEXT,
                UNIQUE(game_id, observer_name, opponent_name)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_opponent_models_observer
            ON opponent_models(observer_name)
        """,
    """CREATE INDEX IF NOT EXISTS idx_opponent_models_game
            ON opponent_models(game_id)
        """,
    """CREATE TABLE IF NOT EXISTS personality_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                game_id TEXT NOT NULL,
                hand_number INTEGER,
                personality_traits TEXT,
                pressure_levels TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(game_id),
                UNIQUE (game_id, player_name, hand_number)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_personality_snapshots ON personality_snapshots(game_id, hand_number)""",
    """CREATE INDEX IF NOT EXISTS idx_opponent_models_observer_id ON opponent_models(observer_id)""",
    """CREATE INDEX IF NOT EXISTS idx_opponent_models_opponent_id ON opponent_models(opponent_id)""",
    """CREATE TABLE IF NOT EXISTS ai_bankroll_state (
                personality_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                chips INTEGER NOT NULL DEFAULT 0,
                last_regen_tick TIMESTAMP,
                emotional_state_json TEXT, aspiration_cooldown_until TEXT, recent_events_json TEXT, bankruptcy_count INTEGER NOT NULL DEFAULT 0, last_bankruptcy_at TEXT,
                PRIMARY KEY (personality_id, sandbox_id)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_ai_bankroll_sandbox
                ON ai_bankroll_state(sandbox_id)
        """,
    """CREATE TABLE IF NOT EXISTS cash_tables (
                table_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                stake_label TEXT NOT NULL,
                seats_json TEXT NOT NULL,
                dealer_idx INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, name TEXT, table_type TEXT NOT NULL DEFAULT 'lobby', closing_hand_countdown INTEGER,
                PRIMARY KEY (table_id, sandbox_id)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_cash_tables_sandbox
                ON cash_tables(sandbox_id)
        """,
    """CREATE TABLE IF NOT EXISTS cash_pair_stats (
                sandbox_id TEXT NOT NULL,
                observer_id TEXT NOT NULL,
                opponent_id TEXT NOT NULL,
                cumulative_pnl INTEGER NOT NULL DEFAULT 0,
                hands_played_cash INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (sandbox_id, observer_id, opponent_id)
            )""",
    """CREATE INDEX IF NOT EXISTS idx_cash_pair_stats_observer
                ON cash_pair_stats(observer_id)
        """,
]

# Seed rows the legacy chain inserts (5 tables); without these a
# fresh DDL-only DB would lack default groups/permissions/enabled models/etc.
BASELINE_SEED = [
    {
        "table": 'enabled_models',
        "columns": ['id', 'provider', 'model', 'enabled', 'user_enabled', 'display_name', 'notes', 'supports_reasoning', 'supports_json_mode', 'supports_image_gen', 'supports_img2img', 'sort_order', 'created_at', 'updated_at'],
        "rows": [
            [1, 'openai', 'gpt-5-nano', 1, 1, None, None, 1, 1, 1, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [2, 'openai', 'gpt-5-mini', 1, 1, None, None, 1, 1, 1, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [3, 'openai', 'gpt-5', 0, 0, None, None, 1, 1, 1, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [4, 'openai', 'dall-e-2', 1, 1, None, None, 1, 1, 1, 0, 3, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [5, 'groq', 'llama-3.3-70b-versatile', 0, 0, None, None, 0, 1, 0, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [6, 'groq', 'llama-3.1-8b-instant', 1, 1, None, None, 0, 1, 0, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [7, 'groq', 'openai/gpt-oss-20b', 0, 0, None, None, 0, 1, 0, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [8, 'groq', 'openai/gpt-oss-120b', 0, 0, None, None, 0, 1, 0, 0, 3, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [9, 'groq', 'meta-llama/llama-4-scout-17b-16e-instruct', 0, 0, None, None, 0, 1, 0, 0, 4, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [10, 'groq', 'qwen/qwen3-32b', 0, 0, None, None, 0, 1, 0, 0, 5, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [11, 'anthropic', 'claude-sonnet-4-5-20250929', 0, 0, None, None, 1, 1, 0, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [12, 'anthropic', 'claude-opus-4-5-20251101', 0, 0, None, None, 1, 1, 0, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [13, 'anthropic', 'claude-haiku-4-5-20251001', 0, 0, None, None, 1, 1, 0, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [14, 'deepseek', 'deepseek', 0, 0, None, None, 1, 1, 0, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [15, 'deepseek', 'deepseek-chat', 0, 0, None, None, 1, 1, 0, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [16, 'deepseek', 'deepseek-reasoner', 0, 0, None, None, 1, 1, 0, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [17, 'mistral', 'mistral-small-latest', 0, 0, None, None, 0, 1, 0, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [18, 'mistral', 'mistral-medium-latest', 0, 0, None, None, 0, 1, 0, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [19, 'mistral', 'mistral-large-latest', 0, 0, None, None, 0, 1, 0, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [20, 'mistral', 'labs-mistral-small-creative', 0, 0, None, None, 0, 1, 0, 0, 3, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [21, 'mistral', 'ministral-3b-latest', 0, 0, None, None, 0, 1, 0, 0, 4, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [22, 'mistral', 'ministral-8b-latest', 0, 0, None, None, 0, 1, 0, 0, 5, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [23, 'google', 'gemini-2.0-flash', 0, 0, None, None, 1, 1, 1, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [24, 'google', 'gemini-2.5-flash', 0, 0, None, None, 1, 1, 1, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [25, 'google', 'gemini-2.5-pro', 0, 0, None, None, 1, 1, 1, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [26, 'xai', 'grok-4-fast', 1, 1, None, None, 1, 1, 0, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [27, 'xai', 'grok-3-mini', 0, 0, None, None, 1, 1, 0, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [28, 'xai', 'grok-3', 0, 0, None, None, 1, 1, 0, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [29, 'xai', 'grok-4', 0, 0, None, None, 1, 1, 0, 0, 3, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [42, 'pollinations', 'flux', 1, 1, None, None, 0, 0, 1, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [43, 'pollinations', 'zimage', 1, 1, None, None, 0, 0, 1, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [44, 'pollinations', 'turbo', 0, 0, None, None, 0, 0, 1, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [45, 'pollinations', 'klein', 0, 0, None, None, 0, 0, 1, 0, 3, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [46, 'pollinations', 'seedream', 0, 0, None, None, 0, 0, 1, 0, 4, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [47, 'pollinations', 'kontext', 0, 0, None, None, 0, 0, 1, 0, 5, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [48, 'pollinations', 'gptimage', 0, 0, None, None, 0, 0, 1, 0, 6, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [49, 'pollinations', 'nanobanana', 0, 0, None, None, 0, 0, 1, 0, 7, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [50, 'runware', 'runware:100@1', 0, 0, None, None, 0, 0, 1, 0, 0, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [51, 'runware', 'runware:400@1', 0, 0, None, None, 0, 0, 1, 0, 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [52, 'runware', 'runware:400@4', 0, 0, None, None, 0, 0, 1, 0, 2, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [53, 'runware', 'runware:z-image@turbo', 0, 0, None, None, 0, 0, 1, 0, 3, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
        ],
    },
    {
        "table": 'prompt_presets',
        "columns": ['id', 'name', 'description', 'prompt_config', 'guidance_injection', 'owner_id', 'is_system', 'created_at', 'updated_at'],
        "rows": [
            [1, 'casual', 'Casual mode - personality-driven fun poker with full expressiveness', '{}', None, 'system', 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [2, 'standard', 'Standard mode - balanced personality with GTO awareness (shows equity comparisons)', '{"gto_equity": true}', None, 'system', 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [3, 'pro', 'Pro mode - GTO-focused analytical poker with explicit equity verdicts', '{"gto_equity": true, "gto_verdict": true, "chattiness": false, "dramatic_sequence": false}', None, 'system', 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
            [4, 'competitive', 'Competitive mode - full GTO guidance with personality and trash talk', '{"gto_equity": true, "gto_verdict": true}', None, 'system', 1, '2026-06-08 17:18:24', '2026-06-08 17:18:24'],
        ],
    },
    {
        "table": 'groups',
        "columns": ['id', 'name', 'description', 'is_system', 'created_at'],
        "rows": [
            [1, 'admin', 'Administrators with full access to admin tools', 1, '2026-06-08 17:18:24'],
            [2, 'user', 'Registered users with full game access', 1, '2026-06-08 17:18:24'],
        ],
    },
    {
        "table": 'permissions',
        "columns": ['id', 'name', 'description', 'category'],
        "rows": [
            [1, 'can_access_admin_tools', 'Access to the Admin Tools dashboard', 'admin'],
            [2, 'can_access_full_game', 'Access to full game features including menu and game selection', 'game'],
            [3, 'can_access_coach', 'Access to the poker coaching feature', 'coach'],
        ],
    },
    {
        "table": 'group_permissions',
        "columns": ['id', 'group_id', 'permission_id'],
        "rows": [
            [1, 1, 1],
            [2, 1, 2],
            [3, 2, 2],
            [4, 1, 3],
            [5, 2, 3],
        ],
    },
]
