"""Schema management for the poker database.

Handles table creation and schema migrations.
"""

import logging
import os
import sqlite3
import tempfile

logger = logging.getLogger(__name__)

# Test-only schema-template cache. Building a fresh DB runs the full migration
# chain (~5.2s). When POKER_TEST_SCHEMA_TEMPLATE=1 (set by tests/conftest.py),
# the first empty-DB build per process is snapshotted here and every subsequent
# empty build is seeded from it via the sqlite backup API (~10ms). The resulting
# schema is identical to a real build. The flag is never set in production, so
# this path is inert there. See _maybe_seed_from_template / _maybe_save_as_template.
_TEST_SCHEMA_TEMPLATE_ENV = "POKER_TEST_SCHEMA_TEMPLATE"
_TEST_SCHEMA_TEMPLATE_SUFFIX = "_schema_template.db"
_test_schema_template_path = None

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
# v64: Add owner_id and visibility to personalities for user-scoped access
# v65: Add can_access_coach permission for RBAC gating
# v66: Add window_decisions column for true sliding window (fixes proportional trim bug)
# v67: Add range tracking columns to player_decision_analysis for coach integration
# v68: Add onboarding_completed_at to player_coach_profile for reliable onboarding tracking
# v69: Add hand_equity table for equity-based pressure event detection
# v70: Add range_targets JSON column to player_coach_profile for dynamic range coaching
# v71: Add new 5-trait psychology columns to player_decision_analysis
# v72: Add zone detection and effects tracking columns to player_decision_analysis
# v73: Add hand_number column to pressure_events
# v74: Add bet_sizing column to player_decision_analysis
# v75: Add deck_seed column to hand_history for deterministic replay
# v76: Add metadata_json to prompt_captures for enricher data (bounded_options, equity, etc.)
# v77: Add bounded_replay_results table for multi-sample option-framing replay experiments
# v78: Add quality_score and menu compliance columns to player_decision_analysis
# v79: Add tendencies_json to opponent_models for full opponent stat persistence
# v80: Add community_cards_by_phase_json to hand_history for phase-level card tracking
# v81: Add intervention_trace_json to player_decision_analysis for Phase 7.6 per-decision attribution
# v82: Add strategy_pipeline_snapshot_json to player_decision_analysis for Phase 7.6 Mode 1 (shadow-eval) replay
# v83: Add psychology_json to controller_state for v2.1 unified psychology persistence (T1-29)
# v84: Add UNIQUE(game_id, player_name, hand_number) on personality_snapshots so the
#      INSERT OR IGNORE in save_personality_snapshot can actually deduplicate retried writes (T1-32 follow-up)
# v85: Add personality_id TEXT UNIQUE to personalities for stable cross-session identity.
#      Backfills existing rows with slugified names. Display name becomes UI-only; personality_id
#      is the persistence key the relationship layer and cash-mode bankrolls key on.
# v86: Add observer_id + opponent_id TEXT columns to opponent_models. Backfills via name lookup
#      against personalities.personality_id. Display names stay (still UNIQUE lookup key for now)
#      so the migration is non-destructive; future work can transition the lookup constraint to
#      key on ids once all writers populate them.
# v87: Add relationship_states + cash_pair_stats tables. Cross-session/cross-game affinity axes
#      (heat/respect/likability) and cash-mode-specific PnL pair stats, both keyed on
#      (observer_id, opponent_id). Foundation for Relationship layer (Track B step 2) and Cash
#      mode v1 (Track B step 3). Pure additions — no changes to existing tables.
# v88: Add bankroll persistence for cash mode v1. Creates ai_bankroll_state (per personality_id)
#      and player_bankroll_state (per player_id) tables. Per-personality bankroll knobs
#      (starting_bankroll, bankroll_rate, buy_in_multiplier, stake_comfort_zone) live inside
#      config_json as a `bankroll_knobs` sub-dict — same convention as `anchors`. The
#      BankrollRepository falls back to BANKROLL_KNOB_DEFAULTS per-field, so personalities
#      without bankroll_knobs in their JSON land at sane defaults.
# v90: Add active_loan_lender_id column to player_bankroll_state for cash-mode Path B
#      (AI-personality sponsorship). NULL = anonymous house loan (v1 sponsorship);
#      non-NULL = personality_id of the named AI lender. Used by leave-time settlement to
#      credit sponsor_total back to the lender's persistent bankroll.
# v93: Add chip_ledger_entries — observability for chip creation/destruction events.
#      One row per central_bank ↔ X transfer (player_seed, ai_regen, cap_clamp,
#      house_stake_issue, house_stake_settle, forgive_balance — reasons
#      renamed from house_loan_* in Phase 1 of the backing-system handoff).
#      Pure transfers between non-bank entities are NOT recorded. Append-only;
#      no enforcement in v0. Spec: docs/plans/CASH_MODE_CHIP_LEDGER_HANDOFF.md.
# v94: Seed chip_ledger_entries with `pre_ledger_universe` entries so the audit
#      endpoint reports drift=0 at baseline. Without this, day-1 drift is the
#      entire pre-existing chip universe and the "is the ledger consistent?"
#      signal is unusable. Idempotent — skipped if any pre_ledger_universe
#      entries already exist.
# v95: Add `notes` TEXT column to relationship_states for player-authored
#      opponent notes. Cross-session, cross-game — keyed on the same
#      (observer_id, opponent_id) as the affinity axes. Stored as NULL
#      when empty so existing rows don't need a backfill. Cash mode is
#      the surface for now; tournaments use the per-game opponent_models
#      notes column for their own purposes.
# v98: Add `stakes` table for the backing-system stake model
#      (one row per session deal). Replaces the `active_loan_*` columns
#      on `player_bankroll_state` as the persistence surface for stakes
#      and their post-bust carries. Also runs a one-shot UPDATE that
#      renames legacy `house_loan_issue` / `house_loan_settle` ledger
#      reason strings to `house_stake_issue` / `house_stake_settle`
#      (paired with the Phase 1 code-side vocabulary rename so the
#      audit's per-reason buckets don't split between old and new
#      names). Spec: docs/plans/CASH_MODE_BACKING_SYSTEM_HANDOFF.md
#      Phase 1.
# v99: Drop legacy `active_loan_*` columns from `player_bankroll_state`
#      now that the stakes-table cutover (v98 + Cleanup A/B) is done.
#      Stake state lives in `stakes` via `StakeRepository`; the bankroll
#      row carries chips + starting_bankroll only.
# v100: Add `sandboxes` table — first-class scoping unit for cash-mode
#       runtime state. One row per save-file (1:1 per owner_id in v1;
#       the model admits N:1 / shared / archived sandboxes without
#       further migration). Phase 2.5 Commit 1 of the per-player-sandbox
#       handoff. Subsequent commits add `sandbox_id` to the runtime-
#       state tables; this commit only lands the new table + repo.
#       Spec: docs/plans/CASH_MODE_PER_PLAYER_SANDBOX_HANDOFF.md.
# v101: Add `nickname_override` column to `relationship_states` so a
#       player can rename an opponent privately from the dossier — the
#       override is keyed on the same (observer_id, opponent_id) pair as
#       `notes`, so it's per-viewer by construction. Display falls back
#       to the personality's canonical nickname when the override is NULL.
# v102: Phase 2.5 Commit 2 — drop and recreate ai_bankroll_state,
#       cash_tables, cash_idle_pool with `sandbox_id` in the primary
#       key. Pre-launch destructive migration; existing rows in those
#       three tables are nuked. The chip ledger survives. Repo
#       signatures gain `sandbox_id` as a required kwarg.
# v103: Phase 2.5 Commit 6 — add nullable `sandbox_id` column to
#       `chip_ledger_entries` so the audit can scope per sandbox.
#       Non-destructive ALTER; existing rows get `sandbox_id=NULL`
#       (pre-v103 legacy bucket — the audit's `_pre_v103` aggregation
#       lumps them together when running per-sandbox queries).
# v104: Phase 3 Commit 3 — add nullable `forgiveness_last_asked`
#       column to `stakes` so the forgiveness route can rate-limit
#       requests to "one per stake per 24h" without an in-memory map
#       (which wouldn't survive a backend restart).
# v105: Rename bankroll knob `bankroll_cap` → `starting_bankroll` in
#       every personality's `config_json.bankroll_knobs` sub-dict, and
#       drop the vestigial all-NULL `personalities.bankroll_cap`
#       column. The runtime treats the two names as aliases on read
#       (back-compat) but writes the new key going forward; this
#       migration normalises persisted data so the alias path becomes
#       cold.
# v106: Phase 5 refinement — add nullable `staker_payout` /
#       `borrower_payout` columns to `stakes`. Populated by
#       `settle_stake_on_leave` so the Net Worth history surface can
#       compute per-stake P&L ("you got back $X" or "you lost $Y").
#       NULL on legacy rows (settled pre-migration) means "unknown" —
#       the route returns null and the UI hides the P&L line.
# v109: Drop+recreate `cash_pair_stats` with `sandbox_id` as part of the
#       primary key, so the admin Chip Economy panel can scope the
#       Won/Lost/Net columns by sandbox. Matches the v102 destructive
#       precedent for cash-mode tables: no production data to preserve,
#       lifetime pair-PnL is rebuilt as sandboxes accumulate hands.
#       Repo signatures gain `sandbox_id` as a required kwarg; the
#       dossier endpoint still aggregates cross-sandbox by passing None.
# v112: Create `ai_vice_state` for the AI vice spending mechanic. AIs on
#       a vice are off-grid for a bounded duration (15min - 4hr per the
#       LLM-chosen bucket). One row per active vice keyed
#       `(personality_id, sandbox_id)`. Expired rows are deleted by the
#       lobby refresh's expiry pass. See
#       `docs/plans/CASH_MODE_AI_VICE_SPENDING.md`.
# v114: Create `ai_side_hustle_state` for the side-hustle mechanic — the
#       mirror of vice. Broke AIs go off-grid to earn a lump drawn from
#       the bank pool (replacing passive regen). Same shape as
#       `ai_vice_state`: one row per active hustle keyed
#       `(personality_id, sandbox_id)`, deleted at expiry when the payout
#       is credited. See `docs/plans/CASH_MODE_SIDE_HUSTLE.md`.
# v115: Create `user_preferences` for per-user settings. First setting is
#       `world_pace` (subtle/lively/bustling) controlling the realtime
#       background ticker's hand-sim rate. One row per user; a
#       `preferences_json` blob is reserved for future scalar prefs. See
#       `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
# v116: Create `holdings_snapshots` — per-entity net-worth points captured
#       by the background ticker (~10 min/sandbox), so the admin Chip
#       Economy "Player Holdings" chart can plot real net worth over time
#       instead of the ledger-derived bank-flow curve. net_worth = chips +
#       receivable - outstanding; components stored alongside. See
#       `docs/plans/CASH_MODE_NET_WORTH_HOLDINGS.md`.
# v118: (development) user_avatars table + user_preferences.bio.
# v119: (development) cash_sessions.session_state + last_load_error.
# v120: (development) cash_session_events lifecycle telemetry.
# v121: Create `coach_session_evaluations` — per-game persistence of the
#       coach's per-hand skill evaluations (PRH-15). Previously these lived
#       only in `game_data['coach_session_memory']` and were lost on
#       restart/TTL-eviction, so a returning player's hand-review history
#       vanished. One row per game_id with a JSON blob of {hand: [evals]}.
#       Renumbered from v118 on the prep-for-main→development merge (collision).
# v122: Create `prestige_snapshots` — sandbox-scoped human-player reputation
#       captures (renown + regard, two axes) written by the background
#       ticker. Renown ratchets (stored as the running peak); regard swings
#       and partially decays with heat. Component columns make the
#       (illustrative, tunable) formula inspectable, and the row history
#       gives a renown trajectory. Also add idx_relationship_states_opponent
#       so the inbound-edge aggregate (all AIs' view OF the human) is cheap.
#       Read-only scoreboard — never injected into core AI thresholds. See
#       `docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`.
#       Renumbered from v121 on the prestige→prep-for-main merge (collision).
# v123: Add `circulating` to personalities — decouple "visible/selectable"
#       (visibility) from "auto-seeded into the opponent pool" (circulating).
#       The cash-mode seat-filler now only auto-seats circulating=1 personas;
#       new ownerless auto-creations default to 0. Closes the "test/zombie
#       persona silently pollutes everyone's circuit" class structurally.
# v124: Create `opponent_observation_lifetime` — the Circuit's durable,
#       per-sandbox scouting memory: cumulative behavioral COUNTS (not rates)
#       per (sandbox_id, observer_id, opponent_id), summed across every game
#       in that sandbox. Rates (VPIP/PFR/AF/showdown) derive on read. Filled
#       only from sandbox-bound games (legacy per-game `opponent_models` stays
#       unchanged and serves live in-game AI as before). Also add
#       `opponent_models.lifetime_applied_json` — the per-game high-water mark
#       of counts already folded in, so the continuous delta-fold is
#       resume-safe and never double-counts. Additive/idempotent. See
#       `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`.
#       Renumbered from v123 on the dossiers→development merge (circulating
#       took v123 on development; the create-before-alter order is preserved).
# v125: Create `dossier_informant_unlocks` — sections the player paid the
#       informant (chip sink) to reveal on an opponent's dossier, per
#       (sandbox_id, observer_id, opponent_id, section_id). Unioned with the
#       grind unlocks (bypasses the floor). Additive/idempotent. See
#       `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`. Renumbered from v124.
# v126: Add deep postflop count/sum columns to `opponent_observation_lifetime`
#       (Tier-2 dossier reads — fold-to-cbet, c-bet %, barreling, all-in freq,
#       postflop aggression, polarization equity-at-action). Counts/sums only;
#       rates derive on read through the canonical OpponentTendencies. Guarded
#       ALTERs, additive/idempotent. See `docs/plans/DOSSIER_ENRICHMENT_HANDOFF.md`.
#       Renumbered from v125.
# v127: Add preflop opportunity-count columns to `opponent_observation_lifetime`
#       (preflop_voluntary_action/opportunities + open_raise/open_opportunities)
#       so vpip_per_voluntary_opportunity / pfr_per_open_opportunity derive on
#       read — the player-count-stable signals the station/nit exploitation
#       detectors gate on (dossier "the read", Part B2). Guarded ALTERs,
#       additive/idempotent. See `docs/plans/DOSSIER_ENRICHMENT_HANDOFF.md`.
#       Renumbered from v126.
# v128: Create `entity_presence` — the single authoritative presence row per
#       (entity_id, sandbox_id) for the Presence state machine (Cut 3). Compound
#       PK structurally forbids an entity being in two places, plus a partial
#       UNIQUE index forbids two entities sharing one (table_id, seat_index) seat,
#       making `seated_and_idle` / `double_seat` unrepresentable. ADDITIVE AND
#       DORMANT — nothing reads/writes it yet; a later human-reviewed phase
#       reroutes the seat/idle/hustle/vice writers through it. CREATE ... IF NOT
#       EXISTS, non-destructive, idempotent. See
#       `docs/plans/CASH_MODE_STATE_MODEL.md` (§5.1, §6) and
#       `docs/plans/CASH_MODE_PRESENCE_MIGRATION.md`.
# v130: Add `preflop_node_key` to player_decision_analysis — the exact
#       solver-chart node (scenario|position|opener|hand) captured at decision
#       time so the chart-graded coach leak finder grades the precise spot.
#       Nullable; old rows fall back to reconstruction. Renumbered from v123 on
#       the training-room→development merge (circulating took v123).
# v131: Create `coach_tips` — proactive in-decision coach tip log (and which
#       leak nudge fired) so the coach's effect on play can be measured by
#       joining to player_decision_analysis. Pure instrumentation. Renumbered
#       from v124 on the training-room→development merge.
# v132: Add `limp_count` to `opponent_observation_lifetime` — the numerator for
#       the new `OpponentTendencies.limp_rate` (times an opponent limped preflop
#       in an open spot). Its denominator (`preflop_open_opportunities`) is
#       already folded (v127-era), so this is a single additive column; the rate
#       derives on read via OpponentTendencies. Guarded ALTER, idempotent.
# v133: Add the sizing-aware count/sum columns to `opponent_observation_lifetime`
#       (equity_betting_big/small sums+counts, fold_to_big_bet/big_bet_faced
#       counts) so the sizing tells — `sizing_polarization_score` (bets bigger
#       with stronger hands) and `fold_to_big_bet` (over-folds to overbets) —
#       accumulate cross-game and derive on read, same store-counts-derive-rates
#       principle as v126. 4 INTEGER + 2 REAL columns. Guarded ALTER, idempotent.
# v134: Add the postflop aggression-axis counters to
#       `opponent_observation_lifetime` (facing_bet_opportunities,
#       all_ins_facing_bet, postflop_open_opportunities, postflop_jam_opens) so
#       the response-aggression (`all_in_per_facing_bet`) and open-aggression
#       (`postflop_jam_open_rate`) tells accumulate cross-game and surface in the
#       dossier + coach. Player/coach-facing read only — the live AI clamp reads
#       per-game models, not this store (v124 separation), so AI behavior is
#       unchanged. 4 INTEGER columns. Guarded ALTER, idempotent.
# v135: Add the flop-check-then-barrel counters to `opponent_observation_lifetime`
#       (flop_check_barrel_count, flop_check_barrel_opportunity_count) so the
#       trap read `flop_check_then_barrel_rate` (checks flop OOP then bets turn
#       after a check-through) accumulates cross-game and surfaces in the
#       dossier + coach. Required adding the two counters (+ the rate) to
#       OpponentTendencies._SERIAL_FIELDS first so they serialize per-game.
#       Player/coach-facing read only. 2 INTEGER columns. Guarded ALTER,
#       idempotent.
# v136: Retire the deprecated 4D emotion model — DROP the emotional_state table
#       (consolidated into controller_state.psychology_json) and the
#       valence/arousal/control/focus columns on player_decision_analysis.
# v137: Create `cash_scalps` — durable, sandbox-scoped, attributed "who busted
#       whom" counter (per eliminator→victim pair, so renown-weighting can read
#       the victim's standing). Forward-only; AI-symmetric. The shared
#       prerequisite for the Renown-v2 scalp driver (villain route) and the
#       bounty/double_knockout achievements. Renumbered from v132 on the
#       renown→development merge (development reached v136 first). See
#       CASH_MODE_SCALP_TRACKER.md.
# v139: add `entity_kind` to prestige_snapshots so AI entities get their own
#       persisted, field-relative renown rows (Stage A of the AI-wiring plan).
#       Existing rows default to 'player' (the human); AI rows write 'ai'. See
#       docs/plans/RENOWN_V2_AI_WIRING_PLAN.md.
# v140: add a covering index on holdings_snapshots(sandbox_id, entity_id,
#       net_worth) so the Renown-v2 field build's peak-net-worth aggregate
#       (MAX(net_worth) GROUP BY entity_id) is index-only instead of doing one
#       table lookup per snapshot row. On the real field that was ~200ms of the
#       ~520ms field build (the table is a large per-tick time series); covering
#       it drops it to ~tens of ms. Additive index, idempotent.
# v138: extend prestige_snapshots with the Renown-v2 columns (uncapped,
#       field-relative score) — additive, computed-but-unconsumed until
#       RENOWN_V2_ENABLED flips. Renumbered from v133 on the renown→development
#       merge. See CASH_MODE_PLAYER_PRESTIGE.md.
# --- Tournament + avatar circuit (renumbered 132–138 → 141–147 on the
#     development merge to clear the number collision; see the migrations dict) ---
# v141: Create `tournaments` — durable multi-table tournament (MTT) meta-state
#       (serialized TournamentSession + live game_id + status + resolver_kind),
#       re-enterable across navigation / TTL eviction / restart. (was v132)
# v142: Drop legacy `tournament_tracker` — retired by the tournament unification
#       (every game is a TournamentSession). Brute-force cut. (was v133)
# v143: Add the tournament real-chip economy columns (buy_in, rake, bank_overlay,
#       prize_pool, payout_status) to `tournaments`. (was v134)
# v144: Create `tournament_invites` — the circuit Main Event offer (P3): one open
#       invite per owner, accepted (→ play) / declined / expired (→ autonomous).
#       (was v135)
# v145: Enforce one open invite per owner — partial UNIQUE index on
#       tournament_invites(owner_id) WHERE status='offered'. (was v136)
# v146: Re-key `avatar_images` on the stable `personality_id` (add column +
#       backfill by the unique display-name join). (was v137)
# v147: Make `personality_id` the SOLE avatar key — drop the legacy
#       `personality_name` column + dual-key reads. (was v138)
# v152: Drop the legacy `cash_idle_pool` cache — the Presence cutover is
#       complete; `entity_presence` (state='idle') + `cash_idle_metadata` are
#       the authoritative idle store.
# v153: Create `ai_table_hand_counts` — per-(sandbox, ai, table) hand counter
#       (net added v154); foundation for the table-affinity lever + per-room reads.
# v154: Add `net_chips` to ai_table_hand_counts — cumulative per-room PnL feeding
#       the success-weighted table-affinity term (`TABLE_AFFINITY_ENABLED`).
# v155: Rebaseline the respect/likability neutral baseline 0.5 → 0.35 in
#       `relationship_states` (earned/asymmetric regard; see `REGARD_NEUTRAL`).
# v156: Repoint the label store onto the decision spine (main; decision_labels).
# v157: Create `career_progress` — per-(sandbox, owner) narrative state for the
#       Act-1 career-progression spine (`CASH_MODE_CAREER_PROGRESSION.md`). A
#       small JSON blob holds the keyring (`revealed_table_ids`), the Scene-0
#       tutorial flags (seeded / fish id / graduated), the chosen home court,
#       and the per-AI one-vouch ledger (`vouched_by`). The lobby renders only
#       revealed cardrooms; the world doesn't grow, the player's view does.
#       Renumbered (v124 → v132 → v141 → v152 → v155 → v156 → v157) to land
#       after main's v156 (label-store repoint) on the main→circuit sync. This
#       is the in-flight legacy `_migrate_vN` the comment below anticipates —
#       SCHEMA_VERSION bumped to 157 accordingly.
SCHEMA_VERSION = 157
# This is the head of the LEGACY integer chain, retained until the deploy-time
# squash (docs/plans/SCHEMA_BASELINE_PLAN.md). Prefer authoring NEW migrations as
# files under `migrations/` (migration_loader.FileMigrationLoader, applied-set
# model) — that path has no shared-line merge conflicts and is the going-forward
# system. A legacy `_migrate_vN` may still land from an in-flight branch before
# the squash; if so, bump this and the chain will be baselined at whatever
# SCHEMA_VERSION is current at squash time (the generator reads it dynamically).


class SchemaManager:
    """Manages database schema creation and migrations.

    Call ensure_schema() to create tables and run migrations.
    This is the single source of truth for database structure.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Create a database connection.

        Sets busy_timeout to 5s so _init_db and migrations queue behind a
        WAL writer instead of failing immediately with `database is locked`
        when a Flask worker is already holding the write lock at startup.
        """
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

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
        """Create tables and run migrations. Idempotent.

        Post-squash routing:
          * empty / seeded-template / already-at-baseline DB → ``_init_db`` lays the
            head schema directly from the generated baseline and stamps the baseline
            version on a fresh DB, so the legacy chain never runs.
          * existing PRE-baseline DB (e.g. a restored old backup) → the archived
            legacy v1..v157 chain brings it up to the baseline.
        Per-file migrations (applied-set) then run in every case.
        """
        # Test-only fast path: seed a fresh DB from a cached, fully-built template
        # instead of building from scratch. Inert in prod.
        seeded = self._maybe_seed_from_template()
        started_empty = (not seeded) and self._db_is_empty()
        self._enable_wal_mode()
        current = self._get_current_schema_version()
        if current and current < SCHEMA_VERSION:
            # Existing PRE-baseline DB with a positive version stamp (e.g. a restored
            # old backup): bring it up via the archived legacy chain, which expects the
            # base tables such a DB already has. A version-0 DB (fresh or unversioned)
            # gets the head baseline directly instead.
            self._run_migrations()
        else:
            # Empty/fresh, unversioned, seeded template, or already at/above baseline:
            # lay the head schema directly. CREATE ... IF NOT EXISTS no-ops on a built
            # DB; a fresh DB is stamped at the baseline so the chain never runs.
            self._init_db()
        self._run_file_migrations()  # per-file migrations (applied-set model)
        if started_empty:
            self._maybe_save_as_template()

    def _fast_test_mode(self) -> bool:
        return os.environ.get(_TEST_SCHEMA_TEMPLATE_ENV) == "1"

    def _is_template_path(self) -> bool:
        return str(self.db_path).endswith(_TEST_SCHEMA_TEMPLATE_SUFFIX)

    def _db_is_empty(self) -> bool:
        """True if the DB has no user schema objects (a brand-new database file).

        Counts ANY user object (tables, views, triggers, indexes), not just
        tables, so a schema-migration test that prepares a DB with only a
        view/trigger/index is never silently overwritten by the template seed.
        """
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                (n,) = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchone()
            return n == 0
        except Exception:
            return False

    @staticmethod
    def _copy_db(src_path: str, dst_path: str) -> None:
        """Copy a sqlite DB via the backup API (correct even with WAL sidecars)."""
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(dst_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()

    def _maybe_seed_from_template(self) -> bool:
        """Seed an empty test DB from the cached template. Returns True if seeded."""
        if not self._fast_test_mode() or self._is_template_path():
            return False
        tpl = _test_schema_template_path
        if not tpl or not os.path.exists(tpl):
            return False
        if os.path.abspath(self.db_path) == os.path.abspath(tpl):
            return False
        if not self._db_is_empty():
            return False
        try:
            self._copy_db(tpl, self.db_path)
            return True
        except Exception as e:  # fall back to a normal build on any copy failure
            logger.warning(f"schema template seed skipped ({e}); building normally")
            return False

    def _maybe_save_as_template(self) -> None:
        """Snapshot the first clean, full build as the process-wide template."""
        global _test_schema_template_path
        if not self._fast_test_mode() or self._is_template_path():
            return
        if _test_schema_template_path and os.path.exists(_test_schema_template_path):
            return
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                (version,) = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            if version != SCHEMA_VERSION:  # only snapshot a complete schema
                return
            fd, tpl = tempfile.mkstemp(suffix=_TEST_SCHEMA_TEMPLATE_SUFFIX)
            os.close(fd)
            os.remove(tpl)  # backup() creates it fresh
            self._copy_db(self.db_path, tpl)
            _test_schema_template_path = tpl
        except Exception as e:
            logger.warning(f"schema template save skipped: {e}")

    def _run_file_migrations(self) -> None:
        """Apply post-v154 per-file migrations (applied-set model).

        Runs after the legacy integer chain has brought the DB to the v154
        baseline. New migrations are authored as files under ``migrations/``
        rather than ``_migrate_vN`` methods, so parallel branches no longer
        collide on ``SCHEMA_VERSION`` / the ``migrations`` dict. See
        ``poker/repositories/migration_loader.py``.
        """
        from poker.repositories.migration_loader import FileMigrationLoader

        migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
        FileMigrationLoader(migrations_dir).run(self._get_connection)

    def _init_db(self):
        """Build the head schema directly from the generated baseline.

        Replays ``schema_baseline.BASELINE_STATEMENTS`` (every statement is
        ``CREATE ... IF NOT EXISTS``) and ``BASELINE_SEED`` (the rows the legacy
        chain inserts — default groups/permissions/enabled models/presets — via
        ``INSERT OR IGNORE``), then stamps ``schema_version`` at the baseline on a
        fresh DB so the legacy v1..v157 chain in ``legacy_migrations.py`` is skipped
        on fresh installs. Every step is idempotent → a no-op on a DB already at head.

        The baseline is GENERATED from that chain (scripts/_gen_schema_baseline.py)
        and proven equivalent to it by tests/test_schema_consistency.py.
        """
        from poker.repositories import schema_baseline

        with self._get_connection() as conn:
            for statement in schema_baseline.BASELINE_STATEMENTS:
                conn.execute(statement)
            for entry in getattr(schema_baseline, "BASELINE_SEED", []):
                cols = ", ".join(entry["columns"])
                placeholders = ", ".join("?" for _ in entry["columns"])
                conn.executemany(
                    f'INSERT OR IGNORE INTO "{entry["table"]}" ({cols}) VALUES ({placeholders})',
                    entry["rows"],
                )
            already_versioned = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if not already_versioned:
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (
                        schema_baseline.BASELINE_VERSION,
                        f"baseline v{schema_baseline.BASELINE_VERSION}",
                    ),
                )

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
        """Bring a sub-baseline DB up to the baseline via the archived legacy chain.

        Post-squash, fresh DBs are stamped at SCHEMA_VERSION by ``_init_db`` and skip
        this entirely; it only fires for a restored pre-baseline backup. The frozen
        v1..v157 integer chain lives in ``poker/repositories/legacy_migrations.py``.
        """
        current_version = self._get_current_schema_version()
        if current_version >= SCHEMA_VERSION:
            return
        from poker.repositories.legacy_migrations import LegacyMigrations

        LegacyMigrations().run(self._get_connection, current_version, SCHEMA_VERSION)
