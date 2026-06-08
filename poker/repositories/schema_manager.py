"""Schema management for the poker database.

Handles table creation and schema migrations.
"""

import json
import logging
import os
import random
import sqlite3
import tempfile
from typing import Dict

from poker.personality_id import (
    assign_unique_personality_id as _assign_unique_personality_id,
    slugify_personality_name as _slugify_personality_name,
)

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
        """Create tables and run migrations. Idempotent."""
        # Test-only fast path: seed a fresh DB from a cached, fully-migrated
        # template instead of re-running the whole migration chain. Only fires
        # for empty databases, so schema-migration tests (which build an OLD
        # schema then assert forward migration) are untouched. Inert in prod.
        seeded = self._maybe_seed_from_template()
        started_empty = (not seeded) and self._db_is_empty()
        self._enable_wal_mode()
        self._init_db()  # CREATE TABLE IF NOT EXISTS: no-ops on a seeded DB
        self._run_migrations()  # legacy integer chain; early-returns when at SCHEMA_VERSION
        self._run_file_migrations()  # post-v154 per-file migrations (applied-set model)
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_game_id ON game_messages(game_id, timestamp)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_player_game ON ai_player_state(game_id, player_name)"
            )

            # 5. Personality snapshots (v84 added UNIQUE constraint so retried
            #    save_personality_snapshot writes can be deduplicated by INSERT OR IGNORE)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personality_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER,
                    personality_traits TEXT,
                    pressure_levels TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE (game_id, player_name, hand_number)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_personality_snapshots ON personality_snapshots(game_id, hand_number)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pressure_events_game ON pressure_events(game_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pressure_events_player ON pressure_events(player_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pressure_events_type ON pressure_events(event_type)"
            )

            # 7. Personalities (v5 added elasticity_config, v85 added personality_id;
            #    bankroll knobs live inside config_json as a `bankroll_knobs` sub-dict,
            #    matching how `anchors` already nests inside config_json)
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
                    elasticity_config TEXT,
                    personality_id TEXT UNIQUE
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
                    deck_seed INTEGER,
                    community_cards_by_phase_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_history_game ON hand_history(game_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_history_timestamp ON hand_history(timestamp DESC)"
            )

            # 8b. Hand equity (v68) - equity snapshots for pressure detection and analytics
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hand_equity (
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
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hand_equity_game ON hand_equity(game_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_equity_hand ON hand_equity(hand_history_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_equity_player ON hand_equity(player_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_equity_street_equity ON hand_equity(street, equity)"
            )

            # 9. Opponent models (v21 added game_id, v25 added notes, v27 fixed constraint)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS opponent_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    observer_id TEXT,
                    opponent_id TEXT,
                    hands_observed INTEGER DEFAULT 0,
                    vpip REAL DEFAULT 0.5,
                    pfr REAL DEFAULT 0.5,
                    aggression_factor REAL DEFAULT 1.0,
                    fold_to_cbet REAL DEFAULT 0.5,
                    bluff_frequency REAL DEFAULT 0.3,
                    showdown_win_rate REAL DEFAULT 0.5,
                    recent_trend TEXT,
                    notes TEXT,
                    tendencies_json TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(game_id, observer_name, opponent_name)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_opponent_models_observer ON opponent_models(observer_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_opponent_models_game ON opponent_models(game_id)"
            )
            # The observer_id / opponent_id indexes are created by the
            # v86 migration. We deliberately do NOT create them here:
            # _init_db() runs before migrations, so on a pre-v86 database
            # the columns don't exist yet — and creating indexes on
            # missing columns fails. The v86 migration (a) is idempotent,
            # (b) checks column existence via PRAGMA table_info before
            # ALTER, and (c) creates the indexes with CREATE INDEX IF
            # NOT EXISTS, so it correctly handles both fresh installs
            # (columns present from CREATE TABLE above) and upgrades.

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memorable_observer ON memorable_hands(observer_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memorable_opponent ON memorable_hands(opponent_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memorable_hands_game ON memorable_hands(game_id)"
            )

            # 10b. Relationship states (v87) — cross-session, cross-game affinity axes.
            #      Keyed on (observer_id, opponent_id) which come from
            #      personalities.personality_id (or, for human players, the user-id
            #      surface). Read-paths apply project_heat on the `heat` column;
            #      respect and likability don't decay.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relationship_states (
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
                )
            """)

            # 10c. Cash pair stats (v87, v109) — cumulative cash-mode PnL
            #      between two personalities. Distinct from relationship_states
            #      because PnL is cash-mode-specific (resets in tournaments).
            #      Observer-POV cumulative_pnl; the mirror pair gets the
            #      negation in a single write transaction so views can't drift.
            #      v109 added sandbox_id to the PK so the admin Chip Economy
            #      panel can scope Won/Lost/Net by sandbox.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cash_pair_stats (
                    sandbox_id TEXT NOT NULL,
                    observer_id TEXT NOT NULL,
                    opponent_id TEXT NOT NULL,
                    cumulative_pnl INTEGER NOT NULL DEFAULT 0,
                    hands_played_cash INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (sandbox_id, observer_id, opponent_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cash_pair_stats_observer
                    ON cash_pair_stats(observer_id)
            """)

            # 10c-bis. AI table hand/net counts (v153/v154) — how many hands an
            #      AI has played at each table, and its cumulative net there,
            #      per sandbox. Incremented ONCE per AI per hand (NOT bilateral
            #      like cash_pair_stats). `net_chips` feeds the success-weighted
            #      table-affinity attractiveness lever (an AI drifts back to the
            #      rooms it wins at); `hands` also backs the Career-M2 home-table
            #      resolver — an AI vouches the player into its most-played room.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_table_hand_counts (
                    sandbox_id   TEXT NOT NULL,
                    ai_id        TEXT NOT NULL,
                    table_id     TEXT NOT NULL,
                    hands        INTEGER NOT NULL DEFAULT 0,
                    net_chips    INTEGER NOT NULL DEFAULT 0,
                    last_hand_at TIMESTAMP,
                    PRIMARY KEY (sandbox_id, ai_id, table_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_table_hand_counts_ai
                    ON ai_table_hand_counts(sandbox_id, ai_id)
            """)

            # 10d. AI bankroll state (v88) — per-personality persistent bankroll.
            #      Keyed on (personalities.personality_id, sandbox_id)
            #      after the v102 per-sandbox scoping migration. `chips`
            #      is the "as of last_regen_tick" snapshot; live reads
            #      project through elapsed wall-clock time via
            #      `cash_mode.project_bankroll`. Writes only happen on
            #      real events (sit-down, win, loss).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_bankroll_state (
                    personality_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    chips INTEGER NOT NULL DEFAULT 0,
                    last_regen_tick TIMESTAMP,
                    emotional_state_json TEXT,
                    aspiration_cooldown_until TEXT,
                    PRIMARY KEY (personality_id, sandbox_id)
                )
            """)
            # On legacy DBs (≤ v101) ai_bankroll_state exists from v88
            # without `sandbox_id`; the IF NOT EXISTS table create above
            # is a no-op there and this index would fail. Migration v102
            # drops+recreates the table with sandbox_id and re-creates
            # the index, so skip silently on pre-v102 schemas.
            try:
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ai_bankroll_sandbox
                        ON ai_bankroll_state(sandbox_id)
                """)
            except sqlite3.OperationalError as exc:
                if 'no such column: sandbox_id' not in str(exc):
                    raise

            # 10e. Player bankroll state (v88) — per-player persistent
            #      bankroll. `starting_bankroll` is the seed grant on
            #      first entry. The legacy `active_loan_*` columns
            #      (v89/v90) were dropped in v99 once the stakes-table
            #      cutover (v98) completed; stake state now lives in
            #      `stakes` via `StakeRepository`. Fresh-DB creation
            #      lands on the post-v99 shape directly.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_bankroll_state (
                    player_id TEXT PRIMARY KEY,
                    chips INTEGER NOT NULL DEFAULT 0,
                    starting_bankroll INTEGER NOT NULL DEFAULT 0
                )
            """)

            # 10f. AI vice state (v112) — per-sandbox vice status. One row
            #      while an AI is on a vice. Deleted at expiry by the
            #      lobby refresh's `tick_vice_expirations` pass.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_vice_state (
                    personality_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    ends_at TIMESTAMP NOT NULL,
                    amount INTEGER NOT NULL,
                    duration_bucket TEXT NOT NULL,
                    narration TEXT NOT NULL,
                    PRIMARY KEY (personality_id, sandbox_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_vice_ends_at
                    ON ai_vice_state(sandbox_id, ends_at)
            """)

            # 10g. AI side-hustle state (v114) — the mirror of vice. One
            #      row while a broke AI is off-grid earning a lump from the
            #      bank pool. Deleted at expiry by the lobby refresh's
            #      `tick_side_hustle_expirations` pass (which credits the
            #      payout). See `docs/plans/CASH_MODE_SIDE_HUSTLE.md`.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_side_hustle_state (
                    personality_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    started_at TIMESTAMP NOT NULL,
                    ends_at TIMESTAMP NOT NULL,
                    amount INTEGER NOT NULL,
                    duration_bucket TEXT NOT NULL,
                    narration TEXT NOT NULL,
                    PRIMARY KEY (personality_id, sandbox_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_side_hustle_ends_at
                    ON ai_side_hustle_state(sandbox_id, ends_at)
            """)

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_commentary_game ON hand_commentary(game_id, player_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hand_commentary_player_recent ON hand_commentary(game_id, player_name, hand_number DESC)"
            )

            # 12. Emotional state table removed in v136 — the deprecated 4D
            #     model (valence/arousal/control/focus) is gone; emotion is the
            #     quadrant model and narrative/inner_voice ride on controller_state.

            # 13. Controller state (v3, v40 added prompt_config_json,
            #     v83 added psychology_json for v2.1 unified PlayerPsychology)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS controller_state (
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
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_controller_state_game ON controller_state(game_id, player_name)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournament_results_winner ON tournament_results(winner_name)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournament_standings_game ON tournament_standings(game_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournament_standings_player ON tournament_standings(player_name)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_career_stats_player ON player_career_stats(player_name)"
            )

            # 17. Avatar images (v5; v28 added full_image columns; v137 added
            # personality_id; v147 made it the SOLE key + dropped personality_name)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS avatar_images (
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
                )
            """)
            # v147: avatars are keyed SOLELY by the stable `personality_id` (the
            # slug everything else uses); the legacy `personality_name` display-name
            # column + dual-key reads are gone. On a fresh DB the table is created
            # in this final shape here; on a pre-v147 DB the `CREATE TABLE IF NOT
            # EXISTS` is a no-op and the v147 migration rebuilds the old table into
            # this shape. The index guard tolerates BOTH orderings (init runs before
            # migrations): a pre-v147 table still has `personality_name` and no
            # NOT-NULL `personality_id`, so only build the pid index once the table
            # is in the new shape — the migration builds it otherwise.
            avatar_cols = {row[1] for row in conn.execute("PRAGMA table_info(avatar_images)")}
            if 'personality_name' not in avatar_cols:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_avatar_pid ON avatar_images(personality_id)"
                )
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_owner_created ON api_usage(owner_id, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_owner_call_type ON api_usage(owner_id, call_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_game_call_type ON api_usage(game_id, call_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_model_created ON api_usage(model, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_model_effort ON api_usage(model, reasoning_effort)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_request_id ON api_usage(request_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_cost ON api_usage(estimated_cost)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup ON model_pricing(provider, model)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_pricing_validity ON model_pricing(provider, model, unit, valid_from, valid_until)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_enabled_models_provider ON enabled_models(provider, enabled)"
            )

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
                    metadata_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id) ON DELETE SET NULL,
                    FOREIGN KEY (parent_id) REFERENCES prompt_captures(id) ON DELETE SET NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)"
            )
            # These indexes are on columns added by migrations v33, v39, v52, and v53
            # Use try-except to handle older databases that haven't been migrated yet
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_prompt_captures_is_image ON prompt_captures(is_image_capture)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_prompt_captures_parent ON prompt_captures(parent_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_prompt_captures_owner ON prompt_captures(owner_id)"
                )
            except sqlite3.OperationalError:
                pass  # Columns don't exist yet, will be created by migrations

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_player ON prompt_captures(player_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_action ON prompt_captures(action_taken)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_pot_odds ON prompt_captures(pot_odds)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_created ON prompt_captures(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_phase ON prompt_captures(phase)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reference_images_owner ON reference_images(owner_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reference_images_expires ON reference_images(expires_at)"
            )

            # 22. Player decision analysis (v20, v22, v23, v67, v70, v71)
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
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_game ON player_decision_analysis(game_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_request ON player_decision_analysis(request_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_quality ON player_decision_analysis(decision_quality)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_ev_lost ON player_decision_analysis(ev_lost DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_player ON player_decision_analysis(player_name)"
            )
            # Bridges a prompt_capture back to its decision row — used by the
            # decision-keyed label store (decision_labels) when the Prompt
            # Playground tags/searches in capture-id space.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_capture ON player_decision_analysis(capture_id)"
            )
            # Zone indexes may fail on existing databases before migration v71 adds columns
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_decision_analysis_zone_penalty ON player_decision_analysis(zone_primary_penalty)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_decision_analysis_zone_sweet_spot ON player_decision_analysis(zone_primary_sweet_spot)"
                )
            except Exception:
                pass  # Columns will be added by migration v71, which creates these indexes

            # 23. Tournament tracker (v29) — DROPPED in v131. TournamentTracker
            # was retired by the tournament-unification work (every game is now a
            # TournamentSession; a single game is a 1-table session). Fresh DBs
            # never create it; existing DBs drop it in
            # `_migrate_v131_drop_tournament_tracker`.

            # 23b. Tournaments (v130) — durable multi-table tournament meta-state
            # (serialized TournamentSession + live game_id + status + resolver_kind),
            # re-enterable across navigation / TTL eviction / restart.
            # Economy columns (v132): real-chip layer over the funny-money field
            # — buy-in, rake, bank overlay, prize-pool snapshot, and the
            # payout_status idempotency guard (skipped|pending|in_progress|
            # complete). See `docs/plans/TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
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
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournaments_owner ON tournaments(owner_id, status)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tournaments_game ON tournaments(game_id)")

            # 23c. Tournament invites (v135) — the circuit Main Event offer the
            # player accepts (→ they play it) / declines / lets expire (→ it runs
            # autonomously). One open invite per owner at a time; durable so a
            # scheduled "open until 8pm" survives navigation / restart.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournament_invites (
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
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournament_invites_owner "
                "ON tournament_invites(owner_id, status)"
            )
            # v136: structurally enforce one OPEN invite per owner (partial unique
            # index over the 'offered' rows only — resolved invites coexist).
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_invites_one_open "
                "ON tournament_invites(owner_id) WHERE status = 'offered'"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiment_games_experiment ON experiment_games(experiment_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiment_games_game ON experiment_games(game_id)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON experiment_chat_sessions(owner_id, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON experiment_chat_sessions(owner_id, is_archived)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_linked_guest ON users(linked_guest_id)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_presets_owner ON prompt_presets(owner_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_presets_name ON prompt_presets(name)"
            )

            # 29. Decision labels (v48 capture_labels, repointed to the decision
            # spine in v156) - tags/labels keyed on player_decision_analysis so
            # EVERY decision (human, tiered, rule, LLM) is taggable, not just
            # LLM captures.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS decision_labels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id INTEGER NOT NULL REFERENCES player_decision_analysis(id) ON DELETE CASCADE,
                    label TEXT NOT NULL,
                    label_type TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(decision_id, label)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_labels_label ON decision_labels(label)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_labels_decision_id ON decision_labels(decision_id)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_captures_experiment ON replay_experiment_captures(experiment_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_captures_capture ON replay_experiment_captures(capture_id)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_results_experiment ON replay_results(experiment_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_results_capture ON replay_results(capture_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_results_variant ON replay_results(variant)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_results_quality ON replay_results(quality_change)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_groups_group ON user_groups(group_id)"
            )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_group_permissions_group ON group_permissions(group_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_group_permissions_permission ON group_permissions(permission_id)"
            )

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
                    window_decisions TEXT DEFAULT '[]',
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
