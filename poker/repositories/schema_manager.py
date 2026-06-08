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
        """Run any pending schema migrations."""
        current_version = self._get_current_schema_version()

        if current_version >= SCHEMA_VERSION:
            return

        logger.info(
            f"Running database migrations from version {current_version} to {SCHEMA_VERSION}"
        )

        migrations: Dict[int, tuple] = {
            1: (self._migrate_v1_add_owner_columns, "Add owner_id and owner_name to games table"),
            2: (self._migrate_v2_add_memory_tables, "Add AI memory and learning tables"),
            3: (
                self._migrate_v3_add_controller_state_tables,
                "Add emotional state and controller state tables",
            ),
            4: (
                self._migrate_v4_add_tournament_tables,
                "Add tournament results and career stats tables",
            ),
            5: (
                self._migrate_v5_add_avatar_images_table,
                "Add avatar_images table for storing character images",
            ),
            6: (self._migrate_v6_add_api_usage_table, "Add api_usage table for LLM cost tracking"),
            7: (
                self._migrate_v7_add_reasoning_effort,
                "Add reasoning_effort column to api_usage table",
            ),
            8: (self._migrate_v8_add_request_id, "Add request_id column for vendor correlation"),
            9: (self._migrate_v9_add_max_tokens, "Add max_tokens column for token limit tracking"),
            10: (
                self._migrate_v10_add_conversation_metrics,
                "Add message_count and system_prompt_length columns",
            ),
            11: (
                self._migrate_v11_add_system_prompt_tokens,
                "Add system_prompt_tokens column for accurate token tracking",
            ),
            12: (
                self._migrate_v12_drop_system_prompt_length,
                "Drop unused system_prompt_length column",
            ),
            13: (
                self._migrate_v13_add_pricing_tables,
                "Add model_pricing table and estimated_cost column",
            ),
            14: (self._migrate_v14_sku_based_pricing, "Redesign model_pricing as SKU-based rows"),
            15: (
                self._migrate_v15_add_pricing_validity_dates,
                "Add valid_from and valid_until to model_pricing",
            ),
            16: (
                self._migrate_v16_add_pricing_id_to_usage,
                "Add pricing_id foreign key to api_usage",
            ),
            17: (
                self._migrate_v17_consolidate_pricing_ids,
                "Replace 4 pricing_id columns with single JSON column",
            ),
            18: (
                self._migrate_v18_add_prompt_captures,
                "Add prompt_captures table for debugging AI decisions",
            ),
            19: (
                self._migrate_v19_add_conversation_history,
                "Add conversation_history column to prompt_captures",
            ),
            20: (
                self._migrate_v20_add_decision_analysis,
                "Add player_decision_analysis table for quality monitoring",
            ),
            21: (
                self._migrate_v21_add_game_id_to_opponent_models,
                "Add game_id to opponent_models for game-specific tracking",
            ),
            22: (
                self._migrate_v22_add_position_equity,
                "Add position-based equity fields to decision analysis",
            ),
            23: (self._migrate_v23_add_player_position, "Add player_position to decision analysis"),
            24: (
                self._migrate_v24_add_prompt_versioning,
                "Add prompt version tracking to prompt_captures",
            ),
            25: (
                self._migrate_v25_add_opponent_notes,
                "Add notes column to opponent_models for player observations",
            ),
            26: (
                self._migrate_v26_add_debug_capture,
                "Add debug_capture_enabled column to games table",
            ),
            27: (
                self._migrate_v27_fix_opponent_models_constraint,
                "Fix opponent_models unique constraint to include game_id",
            ),
            28: (
                self._migrate_v28_add_full_image_column,
                "Add full_image_data column for uncropped avatar images",
            ),
            29: (
                self._migrate_v29_add_tournament_tracker,
                "Add tournament_tracker table for persisting elimination history",
            ),
            30: (
                self._migrate_v30_add_prompt_capture_columns,
                "Add raw_request and reasoning columns to prompt_captures",
            ),
            31: (
                self._migrate_v31_add_provider_pricing,
                "Add Groq and Claude 4.5 pricing to model_pricing",
            ),
            32: (
                self._migrate_v32_add_more_providers,
                "Add DeepSeek, Mistral, and Google Gemini pricing",
            ),
            33: (
                self._migrate_v33_add_provider_to_captures,
                "Add provider column to prompt_captures",
            ),
            34: (self._migrate_v34_add_llm_configs, "Add llm_configs_json column to games table"),
            35: (
                self._migrate_v35_add_provider_index,
                "Add index on provider column in prompt_captures",
            ),
            36: (self._migrate_v36_add_xai_pricing, "Add xAI Grok pricing to model_pricing"),
            37: (self._migrate_v37_add_gpt5_pricing, "Add OpenAI GPT-5 pricing"),
            38: (
                self._migrate_v38_add_enabled_models,
                "Add enabled_models table for model management",
            ),
            39: (
                self._migrate_v39_playground_capture_support,
                "Make game_id nullable and add call_type to prompt_captures for playground",
            ),
            40: (
                self._migrate_v40_add_prompt_config,
                "Add prompt_config_json column for toggleable prompt components",
            ),
            41: (
                self._migrate_v41_add_hand_commentary,
                "Add hand_commentary table for AI reflection persistence",
            ),
            42: (
                self._migrate_v42_schema_consolidation,
                "Schema consolidation - all tables now in _init_db, pricing from YAML",
            ),
            43: (
                self._migrate_v43_add_experiments,
                "Add experiments and experiment_games tables for experiment tracking",
            ),
            44: (
                self._migrate_v44_add_app_settings,
                "Add app_settings table for dynamic configuration",
            ),
            45: (
                self._migrate_v45_add_users_table,
                "Add users table for Google OAuth authentication",
            ),
            46: (
                self._migrate_v46_experiment_manager_features,
                "Add experiment manager features (error tracking, chat sessions, image models, experiment lineage, image capture support)",
            ),
            47: (
                self._migrate_v47_add_prompt_presets,
                "Add prompt_presets table for reusable prompt configurations",
            ),
            48: (
                self._migrate_v48_add_capture_labels,
                "Add capture_labels table for tagging captured AI decisions",
            ),
            49: (
                self._migrate_v49_add_replay_experiment_tables,
                "Add replay experiment tables and experiment_type column",
            ),
            50: (
                self._migrate_v50_add_prompt_config_to_captures,
                "Add prompt_config_json to prompt_captures for analysis",
            ),
            51: (
                self._migrate_v51_add_stack_bb_columns,
                "Add stack_bb and already_bet_bb to prompt_captures for auto-labels",
            ),
            52: (
                self._migrate_v52_add_rbac_tables,
                "Add RBAC tables (groups, user_groups, permissions, group_permissions)",
            ),
            53: (
                self._migrate_v53_add_resilience_columns,
                "Add AI decision resilience columns to prompt_captures",
            ),
            54: (
                self._migrate_v54_squashed_features,
                "Add heartbeat tracking, outcome columns, and system presets",
            ),
            55: (
                self._migrate_v55_add_last_game_created_at,
                "Add last_game_created_at to users for duplicate prevention",
            ),
            56: (
                self._migrate_v56_add_exploitative_guidance,
                "Add exploitative guidance to pro and competitive presets",
            ),
            57: (
                self._migrate_v57_add_raise_amount_bb,
                "Add raise_amount_bb to player_decision_analysis for BB-normalized mode",
            ),
            58: (
                self._migrate_v58_fix_squashed_features,
                "Fix v54 squash - apply missing heartbeat, outcome, and system preset columns",
            ),
            59: (
                self._migrate_v59_add_owner_id_to_captures,
                "Add owner_id to prompt_captures for user tracking",
            ),
            60: (
                self._migrate_v60_add_psychology_snapshot,
                "Add psychology snapshot columns to player_decision_analysis",
            ),
            61: (
                self._migrate_v61_guest_tracking_and_owner_id,
                "Add guest_usage_tracking table, owner_id to career stats/tournament tables",
            ),
            62: (self._migrate_v62_add_coach_mode, "Add coach_mode column to games table"),
            63: (self._migrate_v63_coach_progression, "Add coach progression tables"),
            64: (
                self._migrate_v64_add_personality_ownership,
                "Add owner_id and visibility to personalities",
            ),
            65: (self._migrate_v65_add_coach_permission, "Add can_access_coach permission"),
            66: (
                self._migrate_v66_add_window_decisions,
                "Add window_decisions column for sliding window",
            ),
            67: (
                self._migrate_v67_add_range_tracking,
                "Add range tracking columns to player_decision_analysis",
            ),
            68: (
                self._migrate_v68_add_onboarding_completed,
                "Add onboarding_completed_at to player_coach_profile",
            ),
            69: (
                self._migrate_v69_add_hand_equity,
                "Add hand_equity table for equity-based pressure detection",
            ),
            70: (
                self._migrate_v70_add_range_targets,
                "Add range_targets JSON column to player_coach_profile",
            ),
            71: (
                self._migrate_v71_add_5trait_columns,
                "Add 5-trait psychology columns to player_decision_analysis",
            ),
            72: (
                self._migrate_v72_add_zone_tracking,
                "Add zone detection and effects tracking columns to player_decision_analysis",
            ),
            73: (
                self._migrate_v73_pressure_events_hand_number,
                "Add hand_number column to pressure_events",
            ),
            74: (
                self._migrate_v74_add_bet_sizing,
                "Add bet_sizing column to player_decision_analysis",
            ),
            75: (
                self._migrate_v75_add_deck_seed_to_hand_history,
                "Add deck_seed column to hand_history",
            ),
            76: (
                self._migrate_v76_add_metadata_json,
                "Add metadata_json column to prompt_captures for enricher data",
            ),
            77: (
                self._migrate_v77_add_bounded_replay_results,
                "Add bounded_replay_results table for multi-sample replay experiments",
            ),
            78: (
                self._migrate_v78_add_quality_scores,
                "Add quality_score and menu compliance columns to player_decision_analysis",
            ),
            79: (
                self._migrate_v79_add_opponent_tendencies_json,
                "Add tendencies_json to opponent_models for full tendency persistence",
            ),
            80: (
                self._migrate_v80_add_community_cards_by_phase,
                "Add community_cards_by_phase_json column to hand_history",
            ),
            81: (
                self._migrate_v81_add_intervention_trace_json,
                "Add intervention_trace_json to player_decision_analysis for Phase 7.6",
            ),
            82: (
                self._migrate_v82_add_strategy_pipeline_snapshot_json,
                "Add strategy_pipeline_snapshot_json to player_decision_analysis for Phase 7.6 Mode 1",
            ),
            83: (
                self._migrate_v83_add_psychology_json,
                "Add psychology_json to controller_state for v2.1 unified psychology persistence",
            ),
            84: (
                self._migrate_v84_add_personality_snapshots_unique,
                "Add UNIQUE(game_id, player_name, hand_number) to personality_snapshots so INSERT OR IGNORE deduplicates retries",
            ),
            85: (
                self._migrate_v85_add_personality_id,
                "Add personality_id TEXT UNIQUE to personalities and backfill with slugified names",
            ),
            86: (
                self._migrate_v86_add_opponent_model_ids,
                "Add observer_id + opponent_id to opponent_models and backfill via personality name lookup",
            ),
            87: (
                self._migrate_v87_add_relationship_tables,
                "Add relationship_states + cash_pair_stats tables for cross-session affinity and cash-mode PnL",
            ),
            88: (
                self._migrate_v88_add_bankroll_tables,
                "Add ai_bankroll_state + player_bankroll_state tables and bankroll knob columns on personalities for cash mode v1",
            ),
            89: (
                self._migrate_v89_add_loan_fields_to_player_bankroll,
                "Add active_loan_amount, active_loan_floor, active_loan_rate to player_bankroll_state for cash mode sponsorship",
            ),
            90: (
                self._migrate_v90_add_lender_id_to_player_bankroll,
                "Add active_loan_lender_id to player_bankroll_state for cash mode Path B (AI sponsorship)",
            ),
            91: (
                self._migrate_v91_add_cash_tables,
                "Add cash_tables table for persistent multi-table lobby (cash mode v1.5)",
            ),
            92: (
                self._migrate_v92_add_cash_idle_pool,
                "Add cash_idle_pool table for AIs between cash sessions (cash mode v1.5)",
            ),
            93: (
                self._migrate_v93_add_chip_ledger,
                "Add chip_ledger_entries table for chip economy observability (v0: append-only ledger)",
            ),
            94: (
                self._migrate_v94_seed_pre_ledger_universe,
                "Seed pre_ledger_universe entries so day-1 audit drift is 0",
            ),
            95: (
                self._migrate_v95_add_relationship_notes,
                "Add notes column to relationship_states for player-authored opponent notes (cross-session, cash mode)",
            ),
            96: (
                self._migrate_v96_add_dealer_idx_to_cash_tables,
                "Add dealer_idx column to cash_tables so the lobby dealer button survives backend restart (full sim Commit 2)",
            ),
            97: (
                self._migrate_v97_add_emotional_state_to_ai_bankroll,
                "Add emotional_state_json column to ai_bankroll_state so sim-hand psychology persists across cache evictions and restarts (full sim Commit 3)",
            ),
            98: (
                self._migrate_v98_add_stakes_table,
                "Add stakes table for backing-system stake model (Phase 1) and rename legacy house_loan_* ledger reasons to house_stake_*",
            ),
            99: (
                self._migrate_v99_drop_active_loan_columns,
                "Drop legacy active_loan_* columns from player_bankroll_state — stakes table is now the sole source-of-truth",
            ),
            100: (
                self._migrate_v100_add_sandboxes_table,
                "Add sandboxes table (Phase 2.5) — first-class scoping unit for cash-mode runtime state, per-owner save-file model",
            ),
            101: (
                self._migrate_v101_add_relationship_nickname_override,
                "Add nickname_override column to relationship_states so players can rename opponents privately from the dossier",
            ),
            102: (
                self._migrate_v102_scope_runtime_tables_to_sandbox,
                "Phase 2.5 Commit 2 — drop+recreate ai_bankroll_state, cash_tables, cash_idle_pool with sandbox_id in PK (pre-launch destructive migration)",
            ),
            103: (
                self._migrate_v103_add_sandbox_id_to_chip_ledger,
                "Phase 2.5 Commit 6 — add nullable sandbox_id column to chip_ledger_entries for per-sandbox audit scoping",
            ),
            104: (
                self._migrate_v104_add_forgiveness_last_asked,
                "Phase 3 Commit 3 — add nullable forgiveness_last_asked column to stakes for per-stake 24h rate-limit on forgiveness requests",
            ),
            105: (
                self._migrate_v105_rename_bankroll_cap_to_starting_bankroll,
                "Rename bankroll_knobs.bankroll_cap → starting_bankroll in personality config_json and drop the vestigial personalities.bankroll_cap column",
            ),
            106: (
                self._migrate_v106_add_stake_payouts,
                "Phase 5 refinement — add nullable staker_payout / borrower_payout columns to stakes so history can show per-stake P&L",
            ),
            107: (
                self._migrate_v107_add_aspiration_cooldown,
                "Aspiration-ask Commit 3 — add nullable aspiration_cooldown_until to ai_bankroll_state for per-AI rate limit on aspiration_ask triggers",
            ),
            108: (
                self._migrate_v108_add_cash_sessions,
                "Add cash_sessions table — durable per-session record (buy-in, time-at-table, staking, final stats) so the leave-table summary survives Flask restart / TTL eviction and stays correct across top-ups, rebuys, and staked sessions",
            ),
            109: (
                self._migrate_v109_scope_cash_pair_stats_to_sandbox,
                "Drop+recreate cash_pair_stats with sandbox_id in PK so admin Chip Economy Won/Lost/Net can scope per sandbox (matches v102 destructive precedent)",
            ),
            110: (
                self._migrate_v110_add_pending_forgiveness_ask,
                "Add nullable pending_forgiveness_ask column to stakes — AIs holding human-staker carries surface a forgiveness request the player decides on (replaces auto-grant which silently void chips)",
            ),
            111: (
                self._migrate_v111_add_multi_table_lobby_columns,
                "Add name + table_type columns to cash_tables and table_id column to stakes for multi-table-per-tier lobby (named tables + future private/casino types)",
            ),
            112: (
                self._migrate_v112_create_ai_vice_state,
                "Create ai_vice_state table for AI vice spending (per-sandbox vice status with bounded duration)",
            ),
            113: (
                self._migrate_v113_add_casino_closing_countdown,
                "Add nullable closing_hand_countdown column to cash_tables for the casino smooth-shutdown lifecycle (NULL = active or non-casino, N = closing with N hands remaining)",
            ),
            114: (
                self._migrate_v114_create_ai_side_hustle_state,
                "Create ai_side_hustle_state table for the side-hustle mechanic (per-sandbox off-grid earning status; mirror of ai_vice_state)",
            ),
            115: (
                self._migrate_v115_create_user_preferences,
                "Create user_preferences table for per-user settings (first: world_pace for the realtime background ticker)",
            ),
            116: (
                self._migrate_v116_create_holdings_snapshots,
                "Create holdings_snapshots table — per-entity net-worth points captured by the background ticker so the admin Player Holdings chart plots real net worth over time",
            ),
            117: (
                self._migrate_v117_add_recent_events,
                "Add nullable recent_events_json column to ai_bankroll_state — a small per-AI ring buffer of recent notable hand events (bust/suckout) so the world carries recent memories without the pressure_events firehose",
            ),
            118: (
                self._migrate_v118_add_user_profile,
                "Create user_avatars table (human player avatar blobs keyed by user_id, opaque public_id serve key) and add user_preferences.bio (the human's AI-visible self-description)",
            ),
            119: (
                self._migrate_v119_add_session_state,
                "Add session_state + last_load_error to cash_sessions — the explicit lifecycle state machine (active/paused/abandoning/closed/broken) the sit guard reads instead of inferring 'active' from a lingering cash-* games row, plus a stash for the last cold-load failure",
            ),
            120: (
                self._migrate_v120_create_cash_session_events,
                "Create cash_session_events table — persisted lifecycle telemetry (started/resumed/left_clean/left_ghost/swept/broken) for ops queries and the admin orphan-counter, separate from the cosmetic in-memory activity ring buffer",
            ),
            121: (
                self._migrate_v121_create_coach_session_evaluations,
                "Create coach_session_evaluations table — per-game persistence of the coach's per-hand skill evaluations so hand-review history survives restart/TTL-eviction (PRH-15)",
            ),
            122: (
                self._migrate_v122_create_prestige_snapshots,
                "Create prestige_snapshots table — sandbox-scoped human-player reputation (renown ratchets, regard swings) captured by the ticker with component breakdown; add idx_relationship_states_opponent for the inbound-edge aggregate",
            ),
            123: (
                self._migrate_v123_add_personality_circulating,
                "Add circulating flag to personalities — decouple visibility (who can see/pick) from auto-seeding into the opponent pool; backfill preserves current behavior (all public rows circulate)",
            ),
            124: (
                self._migrate_v124_create_opponent_observation_lifetime,
                "Create opponent_observation_lifetime — per-sandbox cumulative behavioral counts (the Circuit's scouting memory); add opponent_models.lifetime_applied_json high-water mark for the resume-safe delta-fold",
            ),
            125: (
                self._migrate_v125_create_dossier_informant_unlocks,
                "Create dossier_informant_unlocks — sections the player paid the informant to reveal per (sandbox, observer, opponent); unioned with grind unlocks to bypass the floor",
            ),
            126: (
                self._migrate_v126_add_deep_postflop_lifetime_counts,
                "Add deep postflop count/sum columns to opponent_observation_lifetime (fold-to-cbet, c-bet %, barreling, all-in freq, postflop aggression, polarization equity sums) — Tier-2 dossier reads; rates derive on read",
            ),
            127: (
                self._migrate_v127_add_preflop_opportunity_lifetime_counts,
                "Add preflop opportunity-count columns to opponent_observation_lifetime (voluntary action/opportunities + open raise/opportunities) so vpip_per_voluntary_opportunity / pfr_per_open_opportunity derive — the signals the station/nit exploitation detectors gate on (dossier 'the read')",
            ),
            128: (
                self._migrate_v128_create_entity_presence,
                "Create entity_presence — single authoritative presence row per (entity_id, sandbox_id) for the Presence state machine (Cut 3); compound PK + partial unique seat index make seated_and_idle / double_seat unrepresentable. Additive and dormant.",
            ),
            129: (
                self._migrate_v129_create_cash_idle_metadata,
                "Create cash_idle_metadata — satellite for the idle-pool routing payload (reason/target_stake/left_at) that entity_presence's pure machine deliberately doesn't carry. At the Presence authority flip, entity_presence owns the IDLE state and this table carries the movement metadata. Additive; cash_idle_pool stays a written cache (view-conversion deferred).",
            ),
            130: (
                self._migrate_v130_add_preflop_node_key,
                "Add preflop_node_key to player_decision_analysis — exact solver-chart node (scenario|position|opener|hand) captured at decision time for chart-graded coach leaks",
            ),
            131: (
                self._migrate_v131_create_coach_tips,
                "Create coach_tips table — log proactive in-decision coach tips (and which leak nudge fired, if any) so the coach's effect on play can be measured by joining to player_decision_analysis",
            ),
            132: (
                self._migrate_v132_add_limp_lifetime_count,
                "Add limp_count to opponent_observation_lifetime — numerator for OpponentTendencies.limp_rate (limps preflop in an open spot); denominator preflop_open_opportunities already folded, rate derives on read",
            ),
            133: (
                self._migrate_v133_add_sizing_aware_lifetime_counts,
                "Add sizing-aware count/sum columns to opponent_observation_lifetime (equity_betting_big/small sums+counts, fold_to_big_bet/big_bet_faced counts) so sizing_polarization_score + fold_to_big_bet accumulate cross-game; rates derive on read",
            ),
            134: (
                self._migrate_v134_add_postflop_axis_lifetime_counts,
                "Add postflop aggression-axis counters to opponent_observation_lifetime (facing_bet_opportunities, all_ins_facing_bet, postflop_open_opportunities, postflop_jam_opens) so all_in_per_facing_bet + postflop_jam_open_rate accumulate cross-game; rates derive on read",
            ),
            135: (
                self._migrate_v135_add_flop_check_barrel_lifetime_counts,
                "Add flop-check-then-barrel counters to opponent_observation_lifetime (flop_check_barrel_count, flop_check_barrel_opportunity_count) so flop_check_then_barrel_rate accumulates cross-game; rate derives on read",
            ),
            136: (
                self._migrate_v136_drop_4d_emotion,
                "Retire the deprecated 4D emotion model: DROP the emotional_state table (consolidated into controller_state.psychology_json) and the valence/arousal/control/focus columns on player_decision_analysis. Emotion is now the quadrant model (trait-aware family x quadrant). NOTE: numbered 136 (not 130) because this branch (polish, schema v129) diverged before development reached v135; 130-135 belong to development and are skipped here if absent.",
            ),
            137: (
                self._migrate_v137_create_cash_scalps,
                "Create cash_scalps table — durable sandbox-scoped attributed 'who busted whom' counter (per eliminator→victim pair), the shared prerequisite for the Renown-v2 scalp driver and bounty achievements. Forward-only, AI-symmetric. Renumbered from v132 on the renown→development merge.",
            ),
            138: (
                self._migrate_v138_add_prestige_v2_columns,
                "Extend prestige_snapshots with the Renown-v2 columns (formula_version, uncapped renown_v2, victim_percentile, field-wide high_cut, v2 component JSON, field_size) so the human's field-relative uncapped score persists ADDITIVELY alongside v1. Computed-but-unconsumed until RENOWN_V2_ENABLED flips. Non-destructive ADD COLUMNs. Renumbered from v133 on the renown→development merge.",
            ),
            139: (
                self._migrate_v139_add_prestige_entity_kind,
                "Add entity_kind to prestige_snapshots ('player'|'ai', existing rows default 'player') + an (sandbox_id, entity_kind, owner_id, renown_v2) index, so AI entities get their own persisted field-relative renown rows. owner_id is the universal subject id (human owner_id or AI personality_id); entity_kind disambiguates so the human's load_latest never matches AI rows. Stage A of the AI-wiring plan. Non-destructive ADD COLUMN.",
            ),
            140: (
                self._migrate_v140_add_holdings_peak_index,
                "Add covering index holdings_snapshots(sandbox_id, entity_id, net_worth) so the Renown-v2 field build's MAX(net_worth) GROUP BY entity_id is index-only (no per-row table lookup) — cuts ~200ms off the ~520ms field build on the real field. Additive index, idempotent.",
            ),
            # Tournament + avatar migrations, renumbered 132–138 → 141–147 on the
            # development merge to clear the number collision (dev claimed 132–140).
            # Dict order is irrelevant (the runner looks up by int); kept ascending.
            141: (
                self._migrate_v141_create_tournaments,
                "Create tournaments table — durable multi-table tournament meta-state (serialized TournamentSession + live game_id + status + resolver_kind) so a tournament survives navigation / TTL eviction / restart. Renumbered 132→141 to clear the development collision.",
            ),
            142: (
                self._migrate_v142_drop_tournament_tracker,
                "Drop legacy tournament_tracker table — TournamentTracker retired by the unification (every game is a TournamentSession); brute-force cut drops any games that still depended on it (pre-release, no real user data). Renumbered 133→142.",
            ),
            143: (
                self._migrate_v143_add_tournament_economy,
                "Add the tournament real-chip economy columns (buy_in, rake, bank_overlay, prize_pool, payout_status) to `tournaments`. Additive ALTER TABLE; existing rows default to payout_status='skipped' so the payout idempotency guard never fires on pre-economy tournaments. Renumbered 134→143.",
            ),
            144: (
                self._migrate_v144_create_tournament_invites,
                "Create tournament_invites table — the circuit Main Event offer (P3): one open invite per owner, accepted (→ play) / declined / expired (→ autonomous). Durable so a scheduled window survives navigation / restart. Renumbered 135→144.",
            ),
            145: (
                self._migrate_v145_one_open_invite_per_owner,
                "Enforce one open invite per owner structurally — partial UNIQUE index on tournament_invites(owner_id) WHERE status='offered'. Backs the app-level offer() guard against a cross-worker race. Defensive pre-step collapses any pre-existing duplicate open invites (keep newest, expire the rest) so index creation can't fail on live data. Renumbered 136→145.",
            ),
            146: (
                self._migrate_v146_avatar_personality_id,
                "Re-key avatar_images on the stable `personality_id` (the slug used by bankrolls/ledger/relationships/dossiers/tournaments) instead of `personality_name` (the display name). Adds the column + backfills it by joining personalities on name (names are unique), so existing avatars keep matching while tournaments — which look up by id — stop missing + regenerating. Renumbered 137→146.",
            ),
            147: (
                self._migrate_v147_avatar_drop_personality_name,
                "Complete the avatar re-key: make `personality_id` the SOLE key of avatar_images (NOT NULL, UNIQUE(personality_id, emotion)) and DROP the legacy `personality_name` column + the dual-key `OR personality_name` reads. Rebuilds the table; rows whose personality_id is NULL (orphans the v146 name-join couldn't match) are dropped — they were already unreachable by the id-keyed path. Idempotent: a no-op once personality_name is gone. Renumbered 138→147.",
            ),
            148: (
                self._migrate_v148_invite_reserved_pids,
                "Add reserved_pids + vacated_pids JSON columns to tournament_invites (tournaments-as-a-draw): the draw-selected field locked at offer time and the subset that has vacated cash en route. Additive nullable ALTERs; existing offers read NULL (random-shuffle field at spawn, unchanged).",
            ),
            149: (
                self._migrate_v149_add_bankruptcy_history,
                "Add bankruptcy_count (INTEGER DEFAULT 0) + last_bankruptcy_at (TEXT NULL) to ai_bankroll_state for the carry-resolution bankruptcy valve: the per-sandbox credit-history that drives post-bankruptcy loan-term penalties (with time-decay off last_bankruptcy_at) and the dossier's lifetime count. Additive; existing rows read 0 / NULL (never bankrupt).",
            ),
            150: (
                self._migrate_v150_add_stake_resolution,
                "Add nullable resolution (TEXT) to stakes — the display label distinguishing HOW a closed stake resolved when bare status isn't specific ('bankruptcy' for carries discharged by the insolvency valve; status stays 'defaulted' so default-counting consumers are unaffected). Additive; existing rows read NULL.",
            ),
            151: (
                self._migrate_v151_chip_ledger_source_sink_index,
                "Add (source, sandbox_id) + (sink, sandbox_id) indexes to chip_ledger_entries so `balance_of` (Σ where source=? OR sink=?) stops full-scanning. Speeds the per-account reconcile (audit_ledger_completeness) and the derive-reads path as the ledger grows. Index-only (CREATE INDEX IF NOT EXISTS); no data change.",
            ),
            152: (
                self._migrate_v152_drop_cash_idle_pool,
                "Drop the legacy `cash_idle_pool` cache — the Presence cutover is complete. Idle AIs are now read from `entity_presence` (state='idle') joined with the `cash_idle_metadata` satellite (reason/target_stake/left_at); the pool was a redundant dual-written copy. `cash_idle_metadata` is retained (the satellite). `DROP TABLE IF EXISTS` — idempotent, a no-op on a DB that never had the table.",
            ),
            153: (
                self._migrate_v153_create_ai_table_hand_counts,
                "Create ai_table_hand_counts — per-(sandbox, ai, table) hand counter. Incremented once per AI per hand (not bilateral). Foundation for the table-affinity lever (net added in v154) and per-room activity reads. Additive/idempotent.",
            ),
            154: (
                self._migrate_v154_add_ai_table_net_chips,
                "Add net_chips to ai_table_hand_counts — cumulative per-(sandbox, ai, table) PnL feeding the success-weighted table-affinity attractiveness term (TABLE_AFFINITY_ENABLED): AIs drift back to rooms they win at, concentrating play into a home room. Guarded ALTER, additive.",
            ),
            155: (
                self._migrate_v155_rebaseline_regard_neutral,
                "Re-baseline existing relationship_states regard from the old neutral 0.5 to REGARD_NEUTRAL (0.35): subtract 0.15 from every respect/likability (clamped to [0,1]); heat untouched. Preserves each edge's offset-from-neutral so renown contributions / hints / offers are unchanged — the data-side mirror of the code rebaseline. ONE-TIME data transform (NOT idempotent if re-run); the version gate guarantees once-only. Fresh DBs are built at SCHEMA_VERSION and skip it (rows already at 0.35).",
            ),
            156: (
                self._migrate_v156_repoint_labels_to_decisions,
                "Repoint the label store from prompt_captures to the decision spine: create decision_labels(decision_id → player_decision_analysis.id), backfill from capture_labels via pda.capture_id, rescue user labels stranded on pre-spine player_decision captures by synthesizing a thin decision row, drop capture_labels. Makes EVERY decision (human/tiered/rule/LLM) taggable instead of LLM captures only. Auto-label-only orphans (non-decision captures) are dropped and counted.",
            ),
            157: (
                self._migrate_v157_create_career_progress,
                "Create career_progress table — per-(sandbox, owner) Act-1 narrative state: keyring (revealed_table_ids), Scene-0 tutorial flags, home court, and the per-AI one-vouch ledger. Renumbered 141→152→155→156→157 to land after main's v156 (label-store repoint) on the main sync.",
            ),
        }

        with self._get_connection() as conn:
            # Renumber-collision self-heal. A DB migrated on the `training-room`
            # branch recorded v123/v124 as the coach migrations (renumbered to
            # v130/v131 on the development merge), so its version counter skipped
            # development's real v123 (`circulating`) and v124
            # (`opponent_observation_lifetime`). Re-assert those two effects
            # idempotently before the forward loop — otherwise the v126/v127
            # ALTERs against `opponent_observation_lifetime` crash on the missing
            # table. No-op on clean DBs (both methods are existence-guarded) and
            # only reachable while migrations are still pending.
            collision = (
                current_version >= 124
                and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='opponent_observation_lifetime'"
                ).fetchone()
                is None
            )
            if collision:
                logger.warning(
                    "Detected training-room renumber collision (version %d but "
                    "opponent_observation_lifetime missing) — re-asserting the "
                    "skipped v123/v124 development migrations idempotently",
                    current_version,
                )
                self._migrate_v123_add_personality_circulating(conn)
                self._migrate_v124_create_opponent_observation_lifetime(conn)
                conn.commit()

            # Tournament-economy renumber self-heal. A DB migrated on the
            # `tournaments` branch BEFORE the development→tournaments economy
            # merge recorded v130/v131(/v132) as the tournament create / tracker-
            # drop / economy migrations. The merge re-assigned v130/v131 to
            # development's coach work (preflop_node_key + coach_tips) and bumped
            # the tournament migrations to v132–134, so such a DB's version
            # counter skipped the coach migrations. Re-assert them idempotently
            # (both existence-guarded) so the coach tables exist. No-op on clean
            # DBs and only reachable while migrations are still pending.
            coach_collision = (
                current_version >= 131
                and conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' " "AND name='coach_tips'"
                ).fetchone()
                is None
            )
            if coach_collision:
                logger.warning(
                    "Detected tournament-economy renumber collision (version %d "
                    "but coach_tips missing) — re-asserting the skipped v130/v131 "
                    "coach migrations idempotently",
                    current_version,
                )
                self._migrate_v130_add_preflop_node_key(conn)
                self._migrate_v131_create_coach_tips(conn)
                conn.commit()

            # Tournament→development renumber self-heal. A DB migrated on the old
            # `tournaments` branch recorded v132–v138 as the OLD tournament
            # migrations (create_tournaments … avatar drop). The merge re-assigned
            # 132–138 to development's work (lifetime counts, drop_4d_emotion,
            # cash_scalps, prestige v2) and bumped the tournament migrations to
            # 141–147, so such a DB's version counter SKIPPED development's 132–138
            # (and the forward loop, having already passed 138, never runs them —
            # even after it force-advances the counter to 147). Re-assert them
            # idempotently (all existence-guarded; prestige_snapshots predates the
            # fork at v122 so the v138 ALTER lands). Sentinel: `limp_count`
            # (development's v132) is ABSENT — it's added only by the v132 ALTER,
            # never by `_init_db`, so it cleanly discriminates old-tournament
            # lineage from a fresh build (version < 132 → loop adds it) or a
            # development DB (limp_count already present → no fire).
            lifetime_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)")
            }
            tourney_renumber_collision = (
                current_version >= 132 and 'limp_count' not in lifetime_cols
            )
            if tourney_renumber_collision:
                logger.warning(
                    "Detected tournament→development renumber collision (version %d "
                    "but development's 132–138 artifacts missing) — re-asserting "
                    "lifetime counts, 4D-emotion drop, cash_scalps, and prestige-v2 "
                    "idempotently",
                    current_version,
                )
                self._migrate_v132_add_limp_lifetime_count(conn)
                self._migrate_v133_add_sizing_aware_lifetime_counts(conn)
                self._migrate_v134_add_postflop_axis_lifetime_counts(conn)
                self._migrate_v135_add_flop_check_barrel_lifetime_counts(conn)
                self._migrate_v136_drop_4d_emotion(conn)
                self._migrate_v137_create_cash_scalps(conn)
                self._migrate_v138_add_prestige_v2_columns(conn)
                conn.commit()

            for version in range(current_version + 1, SCHEMA_VERSION + 1):
                if version in migrations:
                    migrate_func, description = migrations[version]
                    try:
                        migrate_func(conn)
                        conn.execute(
                            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                            (version, description),
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
            ('hand_history', 'opponent_models', 'memorable_hands'),
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

        logger.info(
            "Created tournament_results, tournament_standings, and player_career_stats tables"
        )

    def _migrate_v5_add_avatar_images_table(self, conn: sqlite3.Connection) -> None:
        """Migration v5: Add avatar_images table for storing character images in DB.

        On a real pre-v5 DB this creates the original name-keyed table. On a FRESH
        DB (which runs every migration 1→N over the CURRENT `_init_db` schema), the
        table already exists in the v147 pid-only shape, so the CREATE is a no-op
        and the legacy `personality_name` index is guarded on the column existing
        (v147 dropped it — see `_migrate_v147_avatar_drop_personality_name`)."""
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

        avatar_cols = {row[1] for row in conn.execute("PRAGMA table_info(avatar_images)")}
        if 'personality_name' in avatar_cols:
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_call_type ON api_usage(call_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_usage_cost ON api_usage(estimated_cost)"
            )

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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)"
        )
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
        from core.llm.config import DEFAULT_ENABLED_MODELS, PROVIDER_CAPABILITIES, PROVIDER_MODELS

        for provider, models in PROVIDER_MODELS.items():
            capabilities = PROVIDER_CAPABILITIES.get(provider, {})
            supports_reasoning = 1 if capabilities.get('supports_reasoning', False) else 0
            supports_json = 1 if capabilities.get('supports_json_mode', True) else 0
            supports_image = 1 if capabilities.get('supports_image_generation', False) else 0

            # Determine which models should be enabled by default
            # If DEFAULT_ENABLED_MODELS is empty/None, enable all (backwards compatible)
            # Otherwise, only enable models explicitly listed for this provider
            enabled_whitelist = (
                DEFAULT_ENABLED_MODELS.get(provider, []) if DEFAULT_ENABLED_MODELS else []
            )
            enable_all = not DEFAULT_ENABLED_MODELS

            for sort_order, model in enumerate(models):
                enabled = 1 if (enable_all or model in enabled_whitelist) else 0
                conn.execute(
                    """
                    INSERT OR IGNORE INTO enabled_models
                    (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        provider,
                        model,
                        enabled,
                        supports_reasoning,
                        supports_json,
                        supports_image,
                        sort_order,
                    ),
                )

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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_captures_game ON prompt_captures(game_id)"
        )
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_captures_provider ON prompt_captures(provider)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_captures_call_type ON prompt_captures(call_type)"
        )

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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiment_games_experiment ON experiment_games(experiment_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiment_games_game ON experiment_games(game_id)"
        )

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
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON experiment_chat_sessions(owner_id, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_active ON experiment_chat_sessions(owner_id, is_archived)"
        )

        # 3. Add chat columns to experiments table
        experiments_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(experiments)").fetchall()
        ]
        if 'design_chat_json' not in experiments_cols:
            conn.execute("ALTER TABLE experiments ADD COLUMN design_chat_json TEXT")
        if 'assistant_chat_json' not in experiments_cols:
            conn.execute("ALTER TABLE experiments ADD COLUMN assistant_chat_json TEXT")
        if 'parent_experiment_id' not in experiments_cols:
            conn.execute(
                "ALTER TABLE experiments ADD COLUMN parent_experiment_id INTEGER REFERENCES experiments(id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_experiments_parent ON experiments(parent_experiment_id)"
            )

        # 4. Add Pollinations image models
        pollinations_default_enabled = {"flux", "zimage"}
        for sort_order, model in enumerate(POLLINATIONS_AVAILABLE_MODELS):
            enabled = 1 if model in pollinations_default_enabled else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO enabled_models
                (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
                ("pollinations", model, enabled, 0, 0, 1, sort_order),
            )

        # 5. Add Runware image models
        runware_default_enabled = {"runware:101@1"}
        for sort_order, model in enumerate(RUNWARE_AVAILABLE_MODELS):
            enabled = 1 if model in runware_default_enabled else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO enabled_models
                (provider, model, enabled, supports_reasoning, supports_json_mode, supports_image_gen, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
                ("runware", model, enabled, 0, 0, 1, sort_order),
            )

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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reference_images_owner ON reference_images(owner_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reference_images_expires ON reference_images(expires_at)"
        )

        # 8. Add image capture columns to prompt_captures
        prompt_captures_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()
        ]
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

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_prompt_captures_is_image ON prompt_captures(is_image_capture)"
        )

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_presets_owner ON prompt_presets(owner_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_presets_name ON prompt_presets(name)"
            )
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_capture_labels_label ON capture_labels(label)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_capture_labels_capture_id ON capture_labels(capture_id)"
            )
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
            conn.execute(
                "ALTER TABLE experiments ADD COLUMN experiment_type TEXT DEFAULT 'tournament'"
            )
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_captures_experiment ON replay_experiment_captures(experiment_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_captures_capture ON replay_experiment_captures(capture_id)"
            )
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
            logger.info("Created replay_results table")

        logger.info("Migration v49 complete: Added replay experiment tables")

    def _migrate_v50_add_prompt_config_to_captures(self, conn: sqlite3.Connection) -> None:
        """Migration v50: Add prompt_config_json to prompt_captures.

        This column stores the PromptConfig settings active when the capture was made,
        making it easy to analyze how different configs affect AI behavior.
        """
        prompt_captures_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(prompt_captures)").fetchall()
        ]

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
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='groups'")
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
            conn.execute(
                "CREATE INDEX idx_group_permissions_permission ON group_permissions(permission_id)"
            )
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
            conn.execute(
                "ALTER TABLE prompt_captures ADD COLUMN parent_id INTEGER REFERENCES prompt_captures(id) ON DELETE SET NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_parent ON prompt_captures(parent_id)"
            )
            logger.info("Added parent_id column to prompt_captures")

        if 'error_type' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN error_type TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_error_type ON prompt_captures(error_type)"
            )
            logger.info("Added error_type column to prompt_captures")

        if 'error_description' not in columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN error_description TEXT")
            logger.info("Added error_description column to prompt_captures")

        if 'correction_attempt' not in columns:
            conn.execute(
                "ALTER TABLE prompt_captures ADD COLUMN correction_attempt INTEGER DEFAULT 0"
            )
            logger.info("Added correction_attempt column to prompt_captures")

        logger.info(
            "Migration v53 complete: AI decision resilience columns added to prompt_captures"
        )

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
                conn.execute(
                    """
                    INSERT INTO prompt_presets (name, description, prompt_config, is_system, owner_id)
                    VALUES (?, ?, ?, TRUE, 'system')
                """,
                    (
                        preset['name'],
                        preset['description'],
                        json.dumps(preset['prompt_config']),
                    ),
                )
                logger.info(f"Created system preset '{preset['name']}'")
            except sqlite3.IntegrityError:
                conn.execute(
                    """
                    UPDATE prompt_presets
                    SET description = ?, prompt_config = ?, is_system = TRUE, owner_id = 'system'
                    WHERE name = ?
                """,
                    (
                        preset['description'],
                        json.dumps(preset['prompt_config']),
                        preset['name'],
                    ),
                )
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
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(player_decision_analysis)").fetchall()
        ]

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompt_captures_owner ON prompt_captures(owner_id)"
            )
            logger.info("Added owner_id column to prompt_captures")

        logger.info("Migration v59 complete: owner_id added to prompt_captures")

    def _migrate_v60_add_psychology_snapshot(self, conn: sqlite3.Connection) -> None:
        """Migration v60: Add psychology snapshot columns to player_decision_analysis.

        Captures emotional state, tilt, and elastic trait values at the moment
        each AI decision is made, enabling analysis of how psychology impacts
        decision quality.
        """
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(player_decision_analysis)").fetchall()
        ]

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
                conn.execute(
                    f"ALTER TABLE player_decision_analysis ADD COLUMN {col_name} {col_type}"
                )
                logger.info(f"Added {col_name} column to player_decision_analysis")

        logger.info(
            "Migration v60 complete: psychology snapshot columns added to player_decision_analysis"
        )

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
        career_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(player_career_stats)").fetchall()
        ]
        if 'owner_id' not in career_cols:
            conn.execute("ALTER TABLE player_career_stats ADD COLUMN owner_id TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_career_stats_owner ON player_career_stats(owner_id)"
            )
            logger.info("Added owner_id column to player_career_stats")

        # Add owner_id to tournament_standings
        standings_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(tournament_standings)").fetchall()
        ]
        if 'owner_id' not in standings_cols:
            conn.execute("ALTER TABLE tournament_standings ADD COLUMN owner_id TEXT")
            logger.info("Added owner_id column to tournament_standings")

        # Add human_owner_id to tournament_results
        results_cols = [
            row[1] for row in conn.execute("PRAGMA table_info(tournament_results)").fetchall()
        ]
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

    def _migrate_v64_add_personality_ownership(self, conn: sqlite3.Connection) -> None:
        """Migration v64: Add owner_id and visibility to personalities for user-scoped access."""
        columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()]

        if 'owner_id' not in columns:
            conn.execute("ALTER TABLE personalities ADD COLUMN owner_id TEXT")
        if 'visibility' not in columns:
            conn.execute("ALTER TABLE personalities ADD COLUMN visibility TEXT DEFAULT 'public'")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personalities_owner ON personalities(owner_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personalities_visibility ON personalities(visibility)"
        )

        # Disable the 33 unsafe personalities (living celebrities, active IP, living politicians)
        unsafe_names = [
            'Ace Ventura',
            'Donald Trump',
            'Batman',
            'The Hulk',
            'The Rock',
            'Hulk Hogan',
            'Tyler Durden',
            'Crocodile Dundee',
            'R2-D2',
            'C3PO',
            'Sarah Silverman',
            'Chris Rock',
            'Dave Chappelle',
            'Whoopi Goldberg',
            'Lance Armstrong',
            'Deadpool',
            'Triumph the Insult Comic Dog',
            'Barack Obama',
            'Bill Clinton',
            'Lizzo',
            'Marjorie Taylor Greene',
            'Jim Cramer',
            'Jon Stewart',
            'James Bond',
            'Tom Cruise',
            'Fred Durst',
            'Khloe and Kim Khardashian',
            'Eeyore',
            'Gordon Ramsay',
            'Shaq',
            'Sydney Sweeney',
            'Ruth Bader Ginsburg',
            'Dr. Oz',
        ]
        placeholders = ','.join('?' * len(unsafe_names))
        conn.execute(
            f"UPDATE personalities SET visibility = 'disabled' WHERE name IN ({placeholders})",
            unsafe_names,
        )

        disabled_count = conn.execute(
            "SELECT COUNT(*) FROM personalities WHERE visibility = 'disabled'"
        ).fetchone()[0]
        logger.info(
            f"Migration v64 complete: added owner_id/visibility columns, disabled {disabled_count} unsafe personalities"
        )

    def _migrate_v65_add_coach_permission(self, conn: sqlite3.Connection) -> None:
        """Migration v65: Add can_access_coach permission for RBAC gating.

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
        logger.info("Migration v65 complete: can_access_coach permission added")

    def _migrate_v66_add_window_decisions(self, conn: sqlite3.Connection) -> None:
        """Migration v66: Add window_decisions column for sliding window tracking."""
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(player_skill_progress)").fetchall()
        ]
        if 'window_decisions' not in columns:
            conn.execute(
                "ALTER TABLE player_skill_progress " "ADD COLUMN window_decisions TEXT DEFAULT '[]'"
            )
        # Backfill existing rows: approximate from aggregate counters
        rows = conn.execute(
            "SELECT user_id, skill_id, window_opportunities, window_correct "
            "FROM player_skill_progress WHERE window_opportunities > 0"
        ).fetchall()
        for user_id, skill_id, opps, correct in rows:
            incorrect = opps - correct
            decisions = [True] * correct + [False] * incorrect
            # Use local Random instance to avoid modifying global state
            rng = random.Random(hash((user_id, skill_id)))
            rng.shuffle(decisions)
            conn.execute(
                "UPDATE player_skill_progress SET window_decisions = ? "
                "WHERE user_id = ? AND skill_id = ?",
                (json.dumps(decisions), user_id, skill_id),
            )
        logger.info("Migration v66 complete: window_decisions column added")

    def _migrate_v67_add_range_tracking(self, conn: sqlite3.Connection) -> None:
        """Migration v67: Add range tracking columns to player_decision_analysis.

        Adds columns for:
        - opponent_ranges_json: Captured opponent ranges at decision time
        - board_texture_json: Board texture analysis
        - player_hand_canonical: Player's hand in canonical notation (AKs, Q7o)
        - player_hand_in_range: Whether hand is in standard range for position
        - player_hand_tier: Hand tier (premium, strong, playable, marginal, trash)
        - standard_range_pct: Expected range % for position
        """
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(player_decision_analysis)").fetchall()
        ]

        new_columns = [
            ("opponent_ranges_json", "TEXT"),
            ("board_texture_json", "TEXT"),
            ("player_hand_canonical", "TEXT"),
            ("player_hand_in_range", "BOOLEAN"),
            ("player_hand_tier", "TEXT"),
            ("standard_range_pct", "REAL"),
        ]

        for col_name, col_type in new_columns:
            if col_name not in columns:
                conn.execute(
                    f"ALTER TABLE player_decision_analysis ADD COLUMN {col_name} {col_type}"
                )

        logger.info(
            "Migration v67 complete: range tracking columns added to player_decision_analysis"
        )

    def _migrate_v68_add_onboarding_completed(self, conn: sqlite3.Connection) -> None:
        """Migration v68: Add onboarding_completed_at to player_coach_profile.

        This field tracks when a player explicitly completed onboarding
        (choosing their experience level), so the frontend doesn't need to
        rely solely on localStorage for the onboarding dismissal state.
        """
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(player_coach_profile)").fetchall()
        ]
        if 'onboarding_completed_at' not in columns:
            conn.execute(
                "ALTER TABLE player_coach_profile " "ADD COLUMN onboarding_completed_at TEXT"
            )
        logger.info("Migration v68 complete: onboarding_completed_at column added")

    def _migrate_v69_add_hand_equity(self, conn: sqlite3.Connection) -> None:
        """Migration v69: Add hand_equity table for equity-based pressure detection.

        Stores equity snapshots for all players at all streets, enabling:
        - Cooler/suckout/got_sucked_out detection
        - Coaching opportunities ("You folded the best hand")
        - AI decision tuning analytics
        - Drama enhancement based on equity swings
        """
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

        logger.info("Migration v69 complete: hand_equity table added")

    def _migrate_v70_add_range_targets(self, conn: sqlite3.Connection) -> None:
        """Migration v70: Add range_targets JSON column to player_coach_profile.

        Stores personalized range targets per position as JSON:
        {"UTG": 0.10, "UTG+1": 0.12, "MP": 0.15, "CO": 0.22, "BTN": 0.30, "BB": 0.30}

        Range targets evolve as players progress through gates, starting tight
        and expanding as they demonstrate skill mastery.
        """
        columns = [
            row[1] for row in conn.execute("PRAGMA table_info(player_coach_profile)").fetchall()
        ]

        if 'range_targets' not in columns:
            conn.execute(
                "ALTER TABLE player_coach_profile " "ADD COLUMN range_targets TEXT DEFAULT NULL"
            )

        logger.info("Migration v70 complete: range_targets column added to player_coach_profile")

    def _migrate_v71_add_5trait_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v71: Add new 5-trait psychology columns to player_decision_analysis.

        The new poker-native psychology model uses 5 traits:
        - tightness: Range selectivity (0=loose, 1=tight)
        - aggression: Bet frequency (0=passive, 1=aggressive)
        - confidence: Sizing/commitment (0=scared, 1=fearless)
        - composure: Decision quality (0=tilted, 1=focused)
        - table_talk: Chat frequency (0=silent, 1=chatty)

        This migration adds the elastic_* columns for the new traits while
        keeping elastic_bluff_tendency for backward compatibility with historical data.
        """
        # Get existing columns
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        # Add new trait columns
        new_columns = [
            ('elastic_tightness', 'REAL'),
            ('elastic_confidence', 'REAL'),
            ('elastic_composure', 'REAL'),
            ('elastic_table_talk', 'REAL'),
        ]

        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE player_decision_analysis ADD COLUMN {col_name} {col_type}"
                )
                logger.debug(f"Added column {col_name} to player_decision_analysis")

        logger.info("Migration v71 complete: 5-trait psychology columns added")

    def _migrate_v72_add_zone_tracking(self, conn: sqlite3.Connection) -> None:
        """Migration v72: Add zone detection and effects tracking columns.

        Adds columns to player_decision_analysis for tracking:
        - Zone detection state (confidence, composure, energy, manifestation)
        - Sweet spot and penalty zone membership (JSON dicts + primary values)
        - Zone effects instrumentation (intrusive thoughts, bad advice, info degradation)

        This enables experiment analysis of the psychology zone system.
        """
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        new_columns = [
            # Zone detection state
            ('zone_confidence', 'REAL'),
            ('zone_composure', 'REAL'),
            ('zone_energy', 'REAL'),
            ('zone_manifestation', 'TEXT'),
            ('zone_sweet_spots_json', 'TEXT'),
            ('zone_penalties_json', 'TEXT'),
            ('zone_primary_sweet_spot', 'TEXT'),
            ('zone_primary_penalty', 'TEXT'),
            ('zone_total_penalty_strength', 'REAL'),
            ('zone_in_neutral_territory', 'BOOLEAN'),
            # Zone effects instrumentation
            ('zone_intrusive_thoughts_injected', 'BOOLEAN'),
            ('zone_intrusive_thoughts_json', 'TEXT'),
            ('zone_penalty_strategy_applied', 'TEXT'),
            ('zone_info_degraded', 'BOOLEAN'),
            ('zone_strategy_selected', 'TEXT'),
        ]

        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE player_decision_analysis ADD COLUMN {col_name} {col_type}"
                )
                logger.debug(f"Added column {col_name} to player_decision_analysis")

        # Add indexes for fast aggregation queries
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_zone_penalty ON player_decision_analysis(zone_primary_penalty)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_analysis_zone_sweet_spot ON player_decision_analysis(zone_primary_sweet_spot)"
            )
        except Exception as e:
            logger.debug(f"Index creation failed (may already exist): {e}")

        logger.info("Migration v72 complete: zone tracking columns added")

    def _migrate_v73_pressure_events_hand_number(self, conn: sqlite3.Connection) -> None:
        """Migration v73: Add hand_number column to pressure_events table."""
        cursor = conn.execute("PRAGMA table_info(pressure_events)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'hand_number' not in existing_columns:
            conn.execute("ALTER TABLE pressure_events ADD COLUMN hand_number INTEGER")
            logger.debug("Added hand_number column to pressure_events")

        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pressure_events_hand ON pressure_events(game_id, hand_number)"
            )
        except Exception as e:
            logger.debug(f"Index creation failed (may already exist): {e}")

        logger.info("Migration v73 complete: pressure_events hand_number added")

    def _migrate_v74_add_bet_sizing(self, conn: sqlite3.Connection) -> None:
        """Migration v74: Add bet_sizing to player_decision_analysis.

        Stores the AI's stated sizing strategy (e.g., '2/3 pot value bet')
        to track whether explicit sizing reasoning improves bet quality.
        """
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'bet_sizing' not in existing_columns:
            conn.execute("ALTER TABLE player_decision_analysis ADD COLUMN bet_sizing TEXT")
            logger.debug("Added bet_sizing column to player_decision_analysis")

        logger.info("Migration v74 complete: bet_sizing added to player_decision_analysis")

    def _migrate_v75_add_deck_seed_to_hand_history(self, conn: sqlite3.Connection) -> None:
        """Migration v75: Add deck_seed to hand_history for deterministic replay."""
        cursor = conn.execute("PRAGMA table_info(hand_history)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'deck_seed' not in existing_columns:
            conn.execute("ALTER TABLE hand_history ADD COLUMN deck_seed INTEGER")
            logger.debug("Added deck_seed column to hand_history")

        logger.info("Migration v75 complete: deck_seed added to hand_history")

    def _migrate_v76_add_metadata_json(self, conn: sqlite3.Connection) -> None:
        """Migration v76: Add metadata_json to prompt_captures for enricher data.

        Stores enricher-provided fields (bounded_options, equity, style_profile, etc.)
        as a single JSON blob, avoiding per-field schema changes.
        """
        cursor = conn.execute("PRAGMA table_info(prompt_captures)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'metadata_json' not in existing_columns:
            conn.execute("ALTER TABLE prompt_captures ADD COLUMN metadata_json TEXT")
            logger.debug("Added metadata_json column to prompt_captures")

        logger.info("Migration v76 complete: metadata_json added to prompt_captures")

    def _migrate_v77_add_bounded_replay_results(self, conn: sqlite3.Connection) -> None:
        """Migration v77: Add bounded_replay_results table for multi-sample replay experiments.

        Stores results from replaying captured decisions through different option-framing
        configs (raw-ev, nudges, rangegate) with multiple LLM samples per variant.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bounded_replay_results (
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
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bounded_replay_experiment ON bounded_replay_results(experiment_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bounded_replay_capture ON bounded_replay_results(capture_id)"
        )
        logger.info("Migration v77 complete: bounded_replay_results table created")

    def _migrate_v78_add_quality_scores(self, conn: sqlite3.Connection) -> None:
        """Migration v78: Add quality_score and menu compliance columns to player_decision_analysis.

        quality_score: Composite GTO score (correct=100, marginal=50, mistake=0)
        menu_*: Menu compliance tracking — did AI pick the best bounded option?
        """
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        new_columns = [
            ('quality_score', 'REAL'),
            ('menu_best_ev', 'TEXT'),
            ('menu_chosen_ev', 'TEXT'),
            ('menu_picked_best', 'INTEGER'),
            ('menu_num_options', 'INTEGER'),
        ]

        for col_name, col_type in new_columns:
            if col_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE player_decision_analysis ADD COLUMN {col_name} {col_type}"
                )
                logger.debug(f"Added {col_name} column to player_decision_analysis")

        logger.info("Migration v78 complete: quality_score and menu compliance columns added")

    def _migrate_v79_add_opponent_tendencies_json(self, conn: sqlite3.Connection) -> None:
        """Migration v79: Persist full opponent tendency state as JSON."""
        cursor = conn.execute("PRAGMA table_info(opponent_models)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'tendencies_json' not in existing_columns:
            conn.execute("ALTER TABLE opponent_models ADD COLUMN tendencies_json TEXT")
            logger.debug("Added tendencies_json column to opponent_models")

        logger.info("Migration v79 complete: opponent tendency JSON added")

    def _migrate_v80_add_community_cards_by_phase(self, conn: sqlite3.Connection) -> None:
        """Migration v80: Add community_cards_by_phase_json to hand_history for phase-level card tracking."""
        cursor = conn.execute("PRAGMA table_info(hand_history)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'community_cards_by_phase_json' not in existing_columns:
            conn.execute("ALTER TABLE hand_history ADD COLUMN community_cards_by_phase_json TEXT")
            logger.debug("Added community_cards_by_phase_json column to hand_history")

        logger.info("Migration v80 complete: community_cards_by_phase_json added to hand_history")

    def _migrate_v81_add_intervention_trace_json(self, conn: sqlite3.Connection) -> None:
        """Migration v81: Add intervention_trace_json to player_decision_analysis.

        Phase 7.6 (Step 3b): per-decision intervention trace persistence.
        Stores the controller's `_last_intervention_trace` as a JSON
        array of trace objects (one per pipeline layer/rule, 12 entries
        per postflop decision). Nullable — existing rows lack the
        column and analysis code treats NULL as "no trace available."
        """
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'intervention_trace_json' not in existing_columns:
            conn.execute(
                "ALTER TABLE player_decision_analysis " "ADD COLUMN intervention_trace_json TEXT"
            )
            logger.debug("Added intervention_trace_json column to player_decision_analysis")

        logger.info(
            "Migration v81 complete: intervention_trace_json added to player_decision_analysis"
        )

    def _migrate_v82_add_strategy_pipeline_snapshot_json(self, conn: sqlite3.Connection) -> None:
        """Migration v82: Add strategy_pipeline_snapshot_json to player_decision_analysis.

        Phase 7.6 (Step 6): per-decision strategy pipeline snapshot for
        Mode 1 (shadow-eval) replay. Stores the inputs the controller
        passed to each strategy layer at decision time so the pipeline
        can be re-invoked post-hoc with `disable_rules={target}` to
        compute shadow distributions for per-decision attribution.

        Nullable — existing rows lack the column and Mode 1 skips them
        with a `no_snapshot_coverage` count in the report.
        """
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'strategy_pipeline_snapshot_json' not in existing_columns:
            conn.execute(
                "ALTER TABLE player_decision_analysis "
                "ADD COLUMN strategy_pipeline_snapshot_json TEXT"
            )
            logger.debug(
                "Added strategy_pipeline_snapshot_json column to " "player_decision_analysis"
            )

        logger.info("Migration v82 complete: strategy_pipeline_snapshot_json added")

    def _migrate_v83_add_psychology_json(self, conn: sqlite3.Connection) -> None:
        """Migration v83: Add psychology_json to controller_state.

        Stores the full v2.1 PlayerPsychology snapshot (anchors, axes,
        composure_state, hand_count, optional emotional + playstyle
        substates). Replaces the legacy split between tilt_state_json
        and elastic_personality_json — both columns remain in place so
        existing rows continue to load, but new writes only populate
        psychology_json.
        """
        existing = {row[1] for row in conn.execute("PRAGMA table_info(controller_state)")}
        if 'psychology_json' not in existing:
            conn.execute("ALTER TABLE controller_state ADD COLUMN psychology_json TEXT")
            logger.info("Added psychology_json column to controller_state")

    def _migrate_v84_add_personality_snapshots_unique(self, conn: sqlite3.Connection) -> None:
        """Migration v84: enforce uniqueness on personality_snapshots.

        ``save_personality_snapshot`` uses INSERT OR IGNORE so retried
        writes after a database-locked failure don't duplicate the
        elasticity snapshot timeline. SQLite needs an actual UNIQUE
        constraint to enforce IGNORE semantics; without it the OR IGNORE
        clause is a no-op.

        SQLite can't add a UNIQUE constraint via ALTER TABLE. Use the
        documented table-rebuild dance: drop pre-existing duplicates,
        then rebuild via temp table + rename. Only one duplicate per
        ``(game_id, player_name, hand_number)`` is preserved (the row
        with the lowest id, i.e. the first write).
        """
        # Pre-deduplicate so the rebuild can apply UNIQUE without conflict.
        conn.execute(
            """
            DELETE FROM personality_snapshots
            WHERE id NOT IN (
                SELECT MIN(id) FROM personality_snapshots
                GROUP BY game_id, player_name, hand_number
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE personality_snapshots_v84 (
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
            """
        )
        conn.execute(
            """
            INSERT INTO personality_snapshots_v84
                (id, player_name, game_id, hand_number, personality_traits, pressure_levels, timestamp)
            SELECT id, player_name, game_id, hand_number, personality_traits, pressure_levels, timestamp
            FROM personality_snapshots
            """
        )
        conn.execute("DROP TABLE personality_snapshots")
        conn.execute("ALTER TABLE personality_snapshots_v84 RENAME TO personality_snapshots")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personality_snapshots "
            "ON personality_snapshots(game_id, hand_number)"
        )
        logger.info(
            "Migration v84 complete: personality_snapshots now has "
            "UNIQUE(game_id, player_name, hand_number)"
        )

    def _migrate_v85_add_personality_id(self, conn: sqlite3.Connection) -> None:
        """Migration v85: Add personality_id TEXT UNIQUE to personalities.

        Display names are human-facing, can be edited, and have historically
        been used as persistence keys. That's brittle for cross-session
        state (the relationship layer's heat/respect/likability axes, cash
        mode's per-personality AI bankrolls). A separate `personality_id`
        column gives each personality a stable, immutable identifier that
        survives renames.

        Identifier scheme: slugified display name at the time of backfill.
        Collisions resolve via `_v2`, `_v3` suffix. Once assigned to a row,
        the `personality_id` never changes — even if `name` is later edited.

        Future inserts (AI-generated personalities, user-created via the
        create_personality endpoint) populate this column at row creation
        time via PersonalityRepository.save_personality. The
        seed_personalities_from_json bridge reads `id` from the JSON
        entries (set by scripts/backfill_personality_ids.py) so the seed
        path stays consistent with the DB.

        Schema change: adds nullable `personality_id` column then backfills
        the existing rows. Adds a UNIQUE index on personality_id once
        backfill completes. The column is added as nullable rather than
        NOT NULL so the migration is robust against partial states; the
        UNIQUE index enforces uniqueness without forbidding NULL (SQLite
        treats NULLs as distinct in UNIQUE indexes).
        """
        # 1. Add the column if missing.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(personalities)")}
        if 'personality_id' not in existing:
            conn.execute("ALTER TABLE personalities ADD COLUMN personality_id TEXT")
            logger.info("v85: added personality_id column to personalities")

        # 2. Backfill rows where personality_id IS NULL.
        #    Uses the same slugify rule as scripts/backfill_personality_ids.py
        #    so the JSON seed source and DB-stored IDs stay aligned.
        rows = conn.execute(
            "SELECT id, name FROM personalities WHERE personality_id IS NULL"
        ).fetchall()
        if rows:
            taken = {
                row[0]
                for row in conn.execute(
                    "SELECT personality_id FROM personalities " "WHERE personality_id IS NOT NULL"
                ).fetchall()
            }
            assigned = 0
            for row in rows:
                row_id, name = row[0], row[1]
                base_slug = _slugify_personality_name(name)
                if not base_slug:
                    logger.warning(
                        "v85: personality id=%s name=%r slugifies to empty; "
                        "leaving personality_id NULL — needs manual fix",
                        row_id,
                        name,
                    )
                    continue
                new_id = _assign_unique_personality_id(base_slug, taken)
                taken.add(new_id)
                conn.execute(
                    "UPDATE personalities SET personality_id = ? WHERE id = ?",
                    (new_id, row_id),
                )
                assigned += 1
            logger.info(f"v85: backfilled personality_id for {assigned} rows")

        # 3. Create a UNIQUE index (idempotent — IF NOT EXISTS).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_personalities_personality_id "
            "ON personalities(personality_id)"
        )
        logger.info("v85: UNIQUE index on personalities.personality_id ensured")

    def _migrate_v86_add_opponent_model_ids(self, conn: sqlite3.Connection) -> None:
        """Migration v86: Add observer_id + opponent_id to opponent_models.

        Adds stable personality_id surfaces to the in-game-relative
        opponent model rows. Each row already has observer_name +
        opponent_name (display names); v86 adds observer_id +
        opponent_id (stable slugs from the personalities table).

        Backfill strategy: for every existing row, try to look up the
        opponent's personality_id by matching opponent_name to
        personalities.name. Same for observer_name. Rows whose names
        don't map to any personality (e.g. ad-hoc names, deleted
        personalities, human-player names like guests) stay with NULL
        ids; the join is opportunistic, not enforced. The UNIQUE
        constraint on (game_id, observer_name, opponent_name) stays as
        the authoritative lookup key for now — adding a NOT NULL or
        UNIQUE on the new columns would block live games that have
        names without DB-side personalities.

        Idempotent: re-running the migration is a no-op (existing ids
        preserved; NULL rows attempt backfill again, harmless if the
        name lookup still fails).

        Indexes: separate non-unique indexes on observer_id and
        opponent_id support future relationship/cash-mode queries
        without forcing data shape changes here.
        """
        # 1. Add the columns if missing.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(opponent_models)")}
        for col in ("observer_id", "opponent_id"):
            if col not in existing:
                conn.execute(f"ALTER TABLE opponent_models ADD COLUMN {col} TEXT")
                logger.info(f"v86: added {col} column to opponent_models")

        # 2. Backfill via name lookup against personalities.personality_id.
        #    Only updates rows where the new id column is currently NULL.
        backfilled = conn.execute(
            """
            UPDATE opponent_models
            SET opponent_id = (
                SELECT personality_id FROM personalities
                WHERE personalities.name = opponent_models.opponent_name
                  AND personalities.personality_id IS NOT NULL
            )
            WHERE opponent_id IS NULL
            """
        ).rowcount
        logger.info(f"v86: backfilled opponent_id for {backfilled} rows via name lookup")

        backfilled_obs = conn.execute(
            """
            UPDATE opponent_models
            SET observer_id = (
                SELECT personality_id FROM personalities
                WHERE personalities.name = opponent_models.observer_name
                  AND personalities.personality_id IS NOT NULL
            )
            WHERE observer_id IS NULL
            """
        ).rowcount
        logger.info(f"v86: backfilled observer_id for {backfilled_obs} rows via name lookup")

        # 3. Indexes (idempotent).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_opponent_models_observer_id "
            "ON opponent_models(observer_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_opponent_models_opponent_id "
            "ON opponent_models(opponent_id)"
        )
        logger.info("v86: opponent_models id indexes ensured")

    def _migrate_v87_add_relationship_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v87: Add relationship_states + cash_pair_stats tables.

        Foundation tables for the relationship layer (Track B step 2)
        and cash mode v1 (Track B step 3). Both tables key on
        (observer_id, opponent_id) — stable personality_ids from v85
        — and persist cross-session / cross-game state that doesn't
        belong on `opponent_models` (which is per-game-id).

        relationship_states
          Per-pair affinity axes. `heat` decays per `project_heat`
          on read; `respect` and `likability` are earned state and
          don't decay. `last_decay_tick` anchors the decay schedule;
          set to the timestamp of the most recent record_event apply.

        cash_pair_stats
          Cumulative cash-mode PnL between two personalities.
          Observer-POV `cumulative_pnl` — chips this observer has won
          net from this opponent over every cash-mode hand. Distinct
          from relationship_states because PnL is meaningless in
          tournaments where chips reset; the split keeps tournament-
          mode reads clean.

        Pure additions — no existing tables touched. Idempotent.
        CREATE TABLE IF NOT EXISTS is the standard pattern for new
        tables; running this migration on a DB that already has them
        (fresh install path) is a no-op.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relationship_states (
                observer_id TEXT NOT NULL,
                opponent_id TEXT NOT NULL,
                heat REAL NOT NULL DEFAULT 0.0,
                -- 0.35 == REGARD_NEUTRAL (opponent_model.py); see the canonical
                -- relationship_states CREATE for the rationale.
                respect REAL NOT NULL DEFAULT 0.35,
                likability REAL NOT NULL DEFAULT 0.35,
                last_seen TIMESTAMP,
                last_decay_tick TIMESTAMP,
                PRIMARY KEY (observer_id, opponent_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_pair_stats (
                observer_id TEXT NOT NULL,
                opponent_id TEXT NOT NULL,
                cumulative_pnl INTEGER NOT NULL DEFAULT 0,
                hands_played_cash INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (observer_id, opponent_id)
            )
        """)
        logger.info("v87: created relationship_states + cash_pair_stats tables")

    def _migrate_v88_add_bankroll_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v88: Add bankroll persistence tables for cash mode v1.

        Two new tables. All additions; nothing existing is modified.
        Per-personality bankroll knobs are NOT new columns — they
        live inside `config_json` as a `bankroll_knobs` sub-dict
        (matching how `anchors` and other knob bundles already nest
        inside config_json). BankrollRepository reads them with
        per-field fallback to BANKROLL_KNOB_DEFAULTS, so a
        personality whose JSON doesn't carry `bankroll_knobs` lands
        at sane defaults without any migration step.

        ai_bankroll_state
          Per-personality persistent bankroll. Keyed on
          personality_id (stable v85 slug). `chips` is the snapshot
          at `last_regen_tick`; live reads project through
          `cash_mode.project_bankroll`. No rows are created here —
          the BankrollRepository inserts on first sit-down with a
          starting grant.

        player_bankroll_state
          Per-player persistent bankroll. `starting_bankroll` is the
          fresh-grant value on full bust. No rows created here —
          BankrollRepository inserts on first sit-down with a
          starting grant (Part 2 §"Bust semantics" — player gets a
          fresh bankroll automatically).

        Idempotent: CREATE TABLE IF NOT EXISTS. Safe to re-run.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_bankroll_state (
                personality_id TEXT PRIMARY KEY,
                chips INTEGER NOT NULL DEFAULT 0,
                last_regen_tick TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS player_bankroll_state (
                player_id TEXT PRIMARY KEY,
                chips INTEGER NOT NULL DEFAULT 0,
                starting_bankroll INTEGER NOT NULL DEFAULT 0
            )
        """)
        logger.info("v88: created ai_bankroll_state + player_bankroll_state")

    def _migrate_v89_add_loan_fields_to_player_bankroll(self, conn: sqlite3.Connection) -> None:
        """Migration v89: Add sponsor-loan columns to player_bankroll_state.

        Three columns added for the cash-mode sponsorship mechanic:
          - active_loan_amount: principal in chips (0 = no active loan)
          - active_loan_floor:  repayment multiplier on principal
                                (e.g., 1.30 = repay 130% before split)
          - active_loan_rate:   sponsor's cut of post-floor remaining

        All session-scoped; reset to defaults on `/api/cash/leave`.
        Legacy rows default cleanly (0 amount → no loan flow triggers).

        Idempotent: each ALTER is PRAGMA-guarded so re-running is safe.
        """
        cursor = conn.execute("PRAGMA table_info(player_bankroll_state)")
        cols = {row[1] for row in cursor}
        if "active_loan_amount" not in cols:
            conn.execute(
                "ALTER TABLE player_bankroll_state "
                "ADD COLUMN active_loan_amount INTEGER NOT NULL DEFAULT 0"
            )
        if "active_loan_floor" not in cols:
            conn.execute(
                "ALTER TABLE player_bankroll_state "
                "ADD COLUMN active_loan_floor REAL NOT NULL DEFAULT 0.0"
            )
        if "active_loan_rate" not in cols:
            conn.execute(
                "ALTER TABLE player_bankroll_state "
                "ADD COLUMN active_loan_rate REAL NOT NULL DEFAULT 0.0"
            )
        logger.info("v89: added active_loan_* columns to player_bankroll_state")

    def _migrate_v90_add_lender_id_to_player_bankroll(self, conn: sqlite3.Connection) -> None:
        """Migration v90: Add `active_loan_lender_id` to player_bankroll_state.

        Path B (AI sponsorship) extension: when a player accepts a loan
        from a named AI personality (vs. the anonymous house sponsor pool),
        the personality_id of the lender lands here. NULL means anonymous
        house loan — backward-compatible with v1 sponsorship rows.

        At leave-time, `settle_loan_on_leave` reads this column; when
        non-NULL, it credits `sponsor_total` back to the lender's
        persistent AI bankroll via `credit_ai_cash_out` (Path A helper).
        NULL routes sponsor_total to the ether (anonymous house).

        Reset to NULL on `/api/cash/leave` after settlement — same
        session-scoping invariant as the rest of the loan fields.

        Idempotent: ALTER is PRAGMA-guarded so re-running is safe.
        """
        cursor = conn.execute("PRAGMA table_info(player_bankroll_state)")
        cols = {row[1] for row in cursor}
        if "active_loan_lender_id" not in cols:
            conn.execute(
                "ALTER TABLE player_bankroll_state "
                "ADD COLUMN active_loan_lender_id TEXT DEFAULT NULL"
            )
        logger.info("v90: added active_loan_lender_id column to player_bankroll_state")

    def _migrate_v91_add_cash_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v91: Add `cash_tables` for the persistent lobby (v1.5).

        One row per "named" cash table. v1.5 ships one table per stake
        (5 rows total — `$2`, `$10`, `$50`, `$200`, `$1000`); the schema
        admits more without redesign so future "two $10 tables when the
        lobby looks quiet" growth doesn't need a migration step.

          - `table_id`: stable id, e.g., `cash-table-2-001`. Slug-safe
            (no dollar sign).
          - `stake_label`: matches `cash_mode.stakes_ladder.STAKES_LADDER` keys.
          - `seats_json`: JSON array of 6 slot dicts. Slot kinds are
            `open` (empty), `ai` (`{personality_id, chips}`), or
            `human` (set transiently while the player is seated).
          - `created_at` / `last_activity_at`: bumped by the lobby
            refresh hook so stale-table admin views work.

        No rows are seeded here — `cash_mode.lobby.ensure_lobby_seeded`
        creates them at app boot.

        Idempotent: CREATE TABLE IF NOT EXISTS. Safe to re-run.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_tables (
                table_id TEXT PRIMARY KEY,
                stake_label TEXT NOT NULL,
                seats_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("v91: created cash_tables for persistent multi-table lobby")

    def _migrate_v92_add_cash_idle_pool(self, conn: sqlite3.Connection) -> None:
        """Migration v92: Add `cash_idle_pool` for AIs between cash sessions (v1.5).

        Personalities not currently seated at any cash table land here.
        Their re-entry tick (called from the lobby read endpoint) decides
        when they walk back up to a table.

          - `personality_id`: stable v85 slug. Primary key — an AI is
            either at one table or in the idle pool, never both.
          - `left_at`: wall-clock timestamp of when they left a table
            (or were seeded into the pool at boot). Read by the
            re-entry tick to enforce the 3-10 minute idle window.
          - `reason`: why they're idle. One of
            `forced_leave` | `stake_up_queued` | `take_break` |
            `bored_move`. The re-entry tick uses this to bias the
            target stake (stake_up_queued → walk up a tier).
          - `target_stake`: optional preferred stake label for
            re-entry. Set when `reason == 'stake_up_queued'` to
            preserve the AI's intent; otherwise NULL and re-entry
            picks the highest stake their bankroll affords.

        Idempotent: CREATE TABLE IF NOT EXISTS. Safe to re-run.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_idle_pool (
                personality_id TEXT PRIMARY KEY,
                left_at TIMESTAMP NOT NULL,
                reason TEXT NOT NULL,
                target_stake TEXT
            )
        """)
        logger.info("v92: created cash_idle_pool for AIs between cash sessions")

    def _migrate_v93_add_chip_ledger(self, conn: sqlite3.Connection) -> None:
        """Migration v93: Add `chip_ledger_entries` for chip-economy observability.

        Append-only ledger. One row per chip creation or destruction event
        — i.e. any transfer where `central_bank` is on one side. Pure
        transfers between non-bank entities (sit-down debits, pot
        payouts, fake-sim shuffles, personality-loan principal flow) are
        NOT recorded in v0; they don't change the size of the universe.

        Endpoint shape (v0):
          - source/sink — `'central_bank'` | `'player:<owner_id>'` | `'ai:<personality_id>'`.
          - amount — non-negative integer.
          - reason — free-form string drawn from a fixed vocabulary in
            `core.economy.ledger.LEDGER_REASONS`; new reasons can be
            added without a migration. Annotation rows (e.g.
            `forgive_balance`) carry `amount = 0` and rely on
            `context_json` to record the forgiven principal.
          - context_json — optional JSON blob with game_id, loan_lender,
            projected_chips, cap, etc.

        Indexes match the audit query: `created_at DESC` for window
        scans, `reason` for breakdowns.

        Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT
        EXISTS. Safe to re-run.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chip_ledger_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT NOT NULL,
                sink TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount >= 0),
                reason TEXT NOT NULL,
                context_json TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chip_ledger_created "
            "ON chip_ledger_entries(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chip_ledger_reason " "ON chip_ledger_entries(reason)"
        )
        # NB: the per-account (source, sandbox_id)/(sink, sandbox_id) indexes for
        # `balance_of` are created by migration v151 — `sandbox_id` doesn't exist
        # on this table until a later migration, and fresh DBs run the migration
        # chain in order, so v151 covers both fresh and existing DBs.
        logger.info("v93: created chip_ledger_entries for chip-economy observability")

    def _migrate_v94_seed_pre_ledger_universe(self, conn: sqlite3.Connection) -> None:
        """Migration v94: write `pre_ledger_universe` entries so day-1 drift is 0.

        The v93 ledger starts empty. The audit endpoint sums the
        actual chip-bearing surfaces (player bankrolls, AI bankrolls,
        cash table seats, active loan principal) and reports `drift =
        ledger.outstanding - actual.outstanding`. Without a baseline,
        drift on first audit is the entire pre-existing chip
        universe (~hundreds of thousands of chips), drowning out
        the bypass signal the audit is designed to catch.

        This migration inserts one entry per pre-existing chip
        location, all under reason `pre_ledger_universe`. Future
        events (player_seed, ai_regen, cap_clamp, etc.) appear
        on top of the baseline. As long as instrumentation is
        complete, drift stays at 0.

        Idempotent: skipped if any `pre_ledger_universe` rows
        already exist. Re-running won't double-seed.

        Coverage:
          - player_bankroll_state.chips → central_bank → player:<id>
          - ai_bankroll_state.chips (stored, not projected) →
            central_bank → ai:<pid>
          - cash_tables.seats_json kind=ai chips → central_bank →
            ai:<pid>
          - player_bankroll_state.active_loan_amount (anonymous /
            house loans only — personality loans are pure transfers
            and don't need a central_bank seed) → central_bank →
            player:<id>

        Live-session AI table stacks aren't seeded — they're
        transient and resolve to AI bankrolls at session end via
        the credit_ai_cash_out ledger writes.
        """
        existing = conn.execute(
            "SELECT COUNT(*) FROM chip_ledger_entries " "WHERE reason = 'pre_ledger_universe'"
        ).fetchone()[0]
        if existing > 0:
            logger.info(
                "v94: pre_ledger_universe entries already present (%d); skipping seed",
                existing,
            )
            return

        seeded = 0

        # Player bankrolls.
        try:
            rows = conn.execute(
                "SELECT player_id, chips FROM player_bankroll_state " "WHERE chips > 0"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []  # table doesn't exist yet on a very fresh DB
        for row in rows:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason, context_json) VALUES (?, ?, ?, ?, ?)",
                (
                    'central_bank',
                    f"player:{row[0]}",
                    int(row[1]),
                    'pre_ledger_universe',
                    json.dumps({'kind': 'player_bankroll'}),
                ),
            )
            seeded += 1

        # AI bankrolls — stored value, not projected. The audit also
        # reads stored (after the v94 commit), so the two agree.
        try:
            rows = conn.execute(
                "SELECT personality_id, chips FROM ai_bankroll_state " "WHERE chips > 0"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason, context_json) VALUES (?, ?, ?, ?, ?)",
                (
                    'central_bank',
                    f"ai:{row[0]}",
                    int(row[1]),
                    'pre_ledger_universe',
                    json.dumps({'kind': 'ai_bankroll'}),
                ),
            )
            seeded += 1

        # Cash table AI seats.
        try:
            rows = conn.execute("SELECT table_id, seats_json FROM cash_tables").fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            try:
                seats = json.loads(row[1])
            except (TypeError, ValueError):
                continue
            for slot in seats:
                if not isinstance(slot, dict) or slot.get('kind') != 'ai':
                    continue
                pid = slot.get('personality_id')
                chips = int(slot.get('chips') or 0)
                if not pid or chips <= 0:
                    continue
                conn.execute(
                    "INSERT INTO chip_ledger_entries "
                    "(source, sink, amount, reason, context_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        'central_bank',
                        f"ai:{pid}",
                        chips,
                        'pre_ledger_universe',
                        json.dumps(
                            {
                                'kind': 'cash_table_seat',
                                'table_id': row[0],
                            }
                        ),
                    ),
                )
                seeded += 1

        # Outstanding loan principal — both anonymous (house) and
        # named (personality). The audit sums every row with
        # active_loan_amount > 0 into actual_outstanding, so we seed
        # the same set to keep drift at 0 at baseline.
        #
        # For personality loans, *attributing* the chips to
        # central_bank in the seed is fictional — historically those
        # chips came from the AI lender's bankroll. But the lender's
        # bankroll is also seeded (with its current, post-debit
        # value), so seeding the loan here doesn't double-count: the
        # universe total is the same either way, and the audit
        # endpoint asks "is the ledger consistent with the actual
        # chip locations?", not "where did each chip come from?".
        try:
            rows = conn.execute(
                "SELECT player_id, active_loan_amount, active_loan_lender_id "
                "FROM player_bankroll_state "
                "WHERE active_loan_amount > 0"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            lender_id = row[2]
            kind = 'house_loan_principal' if lender_id is None else 'personality_loan_principal'
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason, context_json) VALUES (?, ?, ?, ?, ?)",
                (
                    'central_bank',
                    f"player:{row[0]}",
                    int(row[1]),
                    'pre_ledger_universe',
                    json.dumps({'kind': kind, 'lender_id': lender_id}),
                ),
            )
            seeded += 1

        logger.info("v94: seeded %d pre_ledger_universe entries", seeded)

    def _migrate_v95_add_relationship_notes(self, conn: sqlite3.Connection) -> None:
        """Migration v95: Add `notes` TEXT to relationship_states.

        Player-authored note about an opponent (e.g., "calls light on
        the turn", "tilts after losing big"). Persistent across cash
        sessions because it's keyed on the same (observer_id,
        opponent_id) pair the affinity axes already use.

        Idempotent: only adds the column if it doesn't already exist.
        Existing rows keep their other axes; `notes` defaults to NULL.
        """
        cursor = conn.execute("PRAGMA table_info(relationship_states)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'notes' not in existing_columns:
            conn.execute("ALTER TABLE relationship_states ADD COLUMN notes TEXT")
            logger.debug("Added notes column to relationship_states")

        logger.info("Migration v95 complete: relationship_states.notes added")

    def _migrate_v96_add_dealer_idx_to_cash_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v96: Add `dealer_idx` INTEGER to cash_tables.

        The lobby's dealer-button indicator is load-bearing for seat-
        choice UX (a player picking an open seat needs to know what
        position — UTG / CO / BTN / blinds — that seat would be in
        for the upcoming hand). Previously this lived in an in-memory
        dict in `cash_mode/lobby.py`, which reset on every backend
        restart. Persisting to the same row as `seats_json` makes the
        rotation survive deploys.

        Default 0 = seat index 0 holds the button — matches the
        previous in-memory default for a freshly initialized table.

        Idempotent: only adds the column if it doesn't already exist.
        """
        cursor = conn.execute("PRAGMA table_info(cash_tables)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'dealer_idx' not in existing_columns:
            conn.execute("ALTER TABLE cash_tables ADD COLUMN dealer_idx INTEGER NOT NULL DEFAULT 0")
            logger.debug("Added dealer_idx column to cash_tables")

        logger.info("Migration v96 complete: cash_tables.dealer_idx added")

    def _migrate_v97_add_emotional_state_to_ai_bankroll(self, conn: sqlite3.Connection) -> None:
        """Migration v97: Add `emotional_state_json` TEXT NULL to ai_bankroll_state.

        Sim hands at unseated tables mutate AI psychology — tilt after
        a bad beat, confidence after a hot streak. Before this column
        the state lived only on the in-memory TieredBotController
        instance held by the cash_mode controller cache; LRU eviction
        or a backend restart wiped the state and the AI re-entered as
        "fresh confident" regardless of recent history.

        Persisting here mirrors v83's `controller_state.psychology_json`
        precedent (per-session psychology serialization), but keyed on
        `personality_id` so the state survives sessions. The
        controller-cache discipline (full-sim Commit 3) hydrates from
        this column on miss and writes back on LRU eviction + a
        periodic flush cadence.

        NULL means "no persisted state yet" — the controller is built
        from defaults, identical to pre-v97 behavior. Existing rows
        backfill cleanly with NULL.

        Idempotent: only adds the column if it doesn't already exist.
        """
        cursor = conn.execute("PRAGMA table_info(ai_bankroll_state)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'emotional_state_json' not in existing_columns:
            conn.execute("ALTER TABLE ai_bankroll_state ADD COLUMN emotional_state_json TEXT")
            logger.debug("Added emotional_state_json column to ai_bankroll_state")

        logger.info("Migration v97 complete: ai_bankroll_state.emotional_state_json added")

    def _migrate_v98_add_stakes_table(self, conn: sqlite3.Connection) -> None:
        """Migration v98: Create the `stakes` table and rename legacy
        ledger reason strings to the stake vocabulary.

        Two coupled changes ship together because they're both halves
        of the Phase 1 vocabulary shift (handoff doc Commit 2):

          1. CREATE TABLE stakes — one row per session-scoped stake
             deal, replacing the `active_loan_*` columns on
             `player_bankroll_state` as the persistence surface for
             stakes and their post-bust carries. The cutover is
             code-side only: readers/writers switched to
             `StakeRepository`, and the now-dead `active_loan_*`
             columns are dropped outright in v99. There is NO data
             backfill from `active_loan_*` into `stakes` — none was
             needed because cash mode never shipped, so those columns
             never held production data. (Earlier plans referenced a
             "Phase 1 Commit 3" backfill; it was never implemented and
             isn't required.)

          2. UPDATE chip_ledger_entries SET reason = ... — renames any
             existing `house_loan_issue` / `house_loan_settle` ledger
             rows to `house_stake_issue` / `house_stake_settle`. The
             code-side vocabulary rename (Phase 1 Commit 1) replaced
             these strings everywhere new writes happen; without this
             one-shot UPDATE the audit's per-reason buckets would
             split between old and new names. Pre-launch system — we
             purge old names entirely rather than carry a compat shim.

        The audit's drift math (`chips_created - chips_destroyed -
        actual_outstanding`) is unaffected by the reason rename — it
        sums all entries regardless of bucket. Only `by_reason` and
        `by_reason_window_24h` shift, and those become correct.

        Idempotent: CREATE TABLE IF NOT EXISTS + INDEX IF NOT EXISTS;
        the UPDATE is a no-op once the old strings are gone.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stakes (
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
            )
        """)
        # Partial indexes on status='carry' so the per-borrower /
        # per-staker carry lookups (Net Worth view in Phase 3, the
        # tier-resolution pass in Phase 2) stay cheap as the table
        # grows. The session index covers the active-stake-by-session
        # lookup used at settlement time (Commit 4).
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stakes_borrower_carry
                ON stakes(borrower_id, borrower_kind, status)
                WHERE status = 'carry'
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stakes_staker_carry
                ON stakes(staker_id, status)
                WHERE status = 'carry'
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stakes_session
                ON stakes(session_id)
        """)

        # Ledger reason rename — only needed for DBs that have legacy
        # rows. Fresh installs (which never wrote the old names)
        # no-op cleanly because zero rows match the WHERE clause.
        try:
            conn.execute(
                "UPDATE chip_ledger_entries SET reason = 'house_stake_issue' "
                "WHERE reason = 'house_loan_issue'"
            )
            conn.execute(
                "UPDATE chip_ledger_entries SET reason = 'house_stake_settle' "
                "WHERE reason = 'house_loan_settle'"
            )
        except sqlite3.OperationalError:
            # chip_ledger_entries doesn't exist (pre-v93 install path
            # that somehow skipped the table create). Defensive — the
            # rename has nothing to operate on.
            pass

        logger.info(
            "Migration v98 complete: stakes table created + indexes; "
            "legacy house_loan_* ledger reasons renamed to house_stake_*"
        )

    def _migrate_v99_drop_active_loan_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v99: drop the legacy `active_loan_*` columns from
        `player_bankroll_state`.

        The stakes-table cutover (v98 + the backing-system handoff
        Cleanup A/B) moved all readers and writers to `StakeRepository`.
        v99 finishes the cleanup: the columns are dead weight on every
        bankroll row and confuse anyone reading the schema.

        SQLite 3.35+ supports `ALTER TABLE ... DROP COLUMN`. Each DROP
        is PRAGMA-guarded so re-running on a partially-migrated DB
        (or a fresh DB that landed on the post-v99 shape via
        `create_initial_schema`) is a no-op.

        Order matters: `active_loan_lender_id` first because it's a
        nullable text column (no constraints), then the numeric ones.
        The whole batch is one statement-sequence; SQLite rewrites
        the table per DROP under the hood, but that's fine for a
        small table that gets touched on every cash-mode hit.
        """
        cursor = conn.execute("PRAGMA table_info(player_bankroll_state)")
        cols = {row[1] for row in cursor}
        for col in (
            "active_loan_lender_id",
            "active_loan_amount",
            "active_loan_floor",
            "active_loan_rate",
        ):
            if col in cols:
                conn.execute(f"ALTER TABLE player_bankroll_state DROP COLUMN {col}")
        logger.info(
            "Migration v99 complete: active_loan_* columns dropped from " "player_bankroll_state"
        )

    def _migrate_v100_add_sandboxes_table(self, conn: sqlite3.Connection) -> None:
        """Migration v100: create the `sandboxes` table.

        First commit of Phase 2.5 — introduces sandboxes as a first-
        class scoping unit for cash-mode runtime state without yet
        scoping any *existing* runtime-state tables. That comes in
        v101+ (see the per-player-sandbox handoff Commit 2). Landing
        the table by itself first means the repo + resolver can be
        wired through routes before any existing surface changes
        shape, keeping the diff per commit small.

        Schema:
          - `sandbox_id` (PK): opaque UUID4 generated by the repo.
            Decoupled from `owner_id` so future multi-sandbox UI can
            rename / fork / share without identity churn.
          - `owner_id`: who created / owns the sandbox. v1 enforces
            1:1 ownership; future ACL flows can add a join table
            without disturbing this column.
          - `name`: user-renamable; defaults to 'My Casino' for the
            auto-created default sandbox.
          - `created_at` / `archived_at`: created stamps on insert;
            archived stamps on soft-delete. The partial index on
            `WHERE archived_at IS NULL` keeps the hot-path
            `list_for_owner` cheap.

        Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF
        NOT EXISTS. No seed row — first cash-mode access per owner
        triggers `SandboxRepository.create` lazily.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sandboxes (
                sandbox_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                archived_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sandboxes_owner
                ON sandboxes(owner_id)
                WHERE archived_at IS NULL
        """)
        logger.info("Migration v100 complete: sandboxes table created")

    def _migrate_v101_add_relationship_nickname_override(self, conn: sqlite3.Connection) -> None:
        """Migration v101: Add `nickname_override` TEXT to relationship_states.

        Lets a player privately rename an opponent from the dossier
        (e.g., re-label "Batman" as "the tight one on my left"). The
        override is per-viewer because it sits on the same
        (observer_id, opponent_id) row as `notes`; no other observer
        sees it. When NULL the dossier falls back to the personality's
        canonical nickname.

        Idempotent: only adds the column if it doesn't already exist.
        """
        cursor = conn.execute("PRAGMA table_info(relationship_states)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'nickname_override' not in existing_columns:
            conn.execute("ALTER TABLE relationship_states ADD COLUMN nickname_override TEXT")
            logger.debug("Added nickname_override column to relationship_states")

        logger.info("Migration v101 complete: relationship_states.nickname_override added")

    def _migrate_v102_scope_runtime_tables_to_sandbox(self, conn: sqlite3.Connection) -> None:
        """Migration v102: drop+recreate cash-mode runtime-state tables
        with `sandbox_id` as part of the primary key.

        Pre-launch destructive migration; existing rows in
        `ai_bankroll_state`, `cash_tables`, `cash_idle_pool` are
        dropped. The chip ledger survives (its rows are append-only
        audit history). Per-sandbox scoping is the load-bearing
        change for Phase 2.5 — every repo method that touches these
        three tables now requires `sandbox_id`.

        SQLite doesn't support altering a primary key in-place, so
        the migration drops + recreates. Per the design lock
        (per-player-sandbox handoff + 2026-05-20 decision), single
        environment, no real production data to preserve.
        """
        conn.execute("DROP TABLE IF EXISTS ai_bankroll_state")
        conn.execute("""
            CREATE TABLE ai_bankroll_state (
                personality_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                chips INTEGER NOT NULL DEFAULT 0,
                last_regen_tick TIMESTAMP,
                emotional_state_json TEXT,
                PRIMARY KEY (personality_id, sandbox_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_bankroll_sandbox
                ON ai_bankroll_state(sandbox_id)
        """)

        conn.execute("DROP TABLE IF EXISTS cash_tables")
        conn.execute("""
            CREATE TABLE cash_tables (
                table_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                stake_label TEXT NOT NULL,
                seats_json TEXT NOT NULL,
                dealer_idx INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (table_id, sandbox_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_tables_sandbox
                ON cash_tables(sandbox_id)
        """)

        conn.execute("DROP TABLE IF EXISTS cash_idle_pool")
        conn.execute("""
            CREATE TABLE cash_idle_pool (
                personality_id TEXT NOT NULL,
                sandbox_id TEXT NOT NULL,
                left_at TIMESTAMP NOT NULL,
                reason TEXT NOT NULL,
                target_stake TEXT,
                PRIMARY KEY (personality_id, sandbox_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_idle_sandbox
                ON cash_idle_pool(sandbox_id)
        """)

        logger.info(
            "Migration v102 complete: ai_bankroll_state, cash_tables, "
            "cash_idle_pool dropped+recreated with sandbox_id in PK"
        )

    def _migrate_v103_add_sandbox_id_to_chip_ledger(self, conn: sqlite3.Connection) -> None:
        """Migration v103: add nullable `sandbox_id` to `chip_ledger_entries`.

        Non-destructive ALTER: every existing row keeps `sandbox_id=NULL`
        (the pre-v103 legacy bucket). New writes always stamp the
        column so the audit's per-sandbox scoping kicks in for ledger
        events too.

        The audit treats NULL as the cross-sandbox "pre-v103" bucket
        when running per-sandbox queries: scoped audits filter
        `WHERE sandbox_id = ?`, which excludes NULL rows; admin /
        cross-sandbox audits aggregate every row including NULLs. This
        is the same shape the spec calls out — the legacy bucket is
        carried as a single line item rather than redistributed.

        Idempotent: PRAGMA-guarded ADD COLUMN so re-running is a
        no-op (matches the v89 / v95 pattern).
        """
        cursor = conn.execute("PRAGMA table_info(chip_ledger_entries)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'sandbox_id' not in cols:
            conn.execute("ALTER TABLE chip_ledger_entries ADD COLUMN sandbox_id TEXT")
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chip_ledger_sandbox
                ON chip_ledger_entries(sandbox_id)
                WHERE sandbox_id IS NOT NULL
        """)
        logger.info("Migration v103 complete: chip_ledger_entries.sandbox_id added")

    def _migrate_v105_rename_bankroll_cap_to_starting_bankroll(
        self, conn: sqlite3.Connection
    ) -> None:
        """Migration v105: bankroll_cap → starting_bankroll rename.

        Two parts:

          1. For every row in `personalities`, parse `config_json`,
             rewrite `bankroll_knobs.bankroll_cap` to
             `bankroll_knobs.starting_bankroll`, and save. Idempotent:
             rows already using the new key are skipped.

          2. Drop the `personalities.bankroll_cap` column if present.
             It was always NULL in production data — never wired to
             any read or write — so the drop is non-destructive.
             SQLite 3.35+ supports `ALTER TABLE ... DROP COLUMN`;
             the docker python image ships 3.37+.

        The runtime accepts both keys on read (see
        `BankrollRepository.load_personality_knobs`), so this
        migration is purely a normalization — it doesn't change any
        observable behavior, just collapses the two-name regime to
        one name in persistence.
        """
        import json

        rows = conn.execute(
            "SELECT id, config_json FROM personalities WHERE config_json IS NOT NULL"
        ).fetchall()
        n_rewritten = 0
        for row in rows:
            try:
                cfg = json.loads(row[1])
            except (TypeError, ValueError):
                continue
            knobs = cfg.get("bankroll_knobs")
            if not isinstance(knobs, dict):
                continue
            if "bankroll_cap" not in knobs:
                continue
            # Prefer the new key if both happen to exist; otherwise
            # promote the old value.
            if "starting_bankroll" not in knobs:
                knobs["starting_bankroll"] = knobs["bankroll_cap"]
            knobs.pop("bankroll_cap", None)
            cfg["bankroll_knobs"] = knobs
            conn.execute(
                "UPDATE personalities SET config_json = ? WHERE id = ?",
                (json.dumps(cfg), row[0]),
            )
            n_rewritten += 1
        logger.info(
            "Migration v105: rewrote bankroll_cap → starting_bankroll in %d "
            "personality config_json rows",
            n_rewritten,
        )

        # Drop the vestigial column. Guard with PRAGMA so re-running
        # is a no-op.
        cursor = conn.execute("PRAGMA table_info(personalities)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'bankroll_cap' in cols:
            conn.execute("ALTER TABLE personalities DROP COLUMN bankroll_cap")
            logger.info("Migration v105: dropped personalities.bankroll_cap column")

    def _migrate_v104_add_forgiveness_last_asked(self, conn: sqlite3.Connection) -> None:
        """Migration v104: add nullable `forgiveness_last_asked` to `stakes`.

        Phase 3 Commit 3 adds POST /api/cash/stakes/<id>/request-forgiveness
        with a "one ask per stake per 24h" rate-limit. The route checks
        the column against `datetime.utcnow() - 24h` before honoring
        the request; refused asks stamp the column so spam clicks
        don't accidentally cross the threshold.

        Non-destructive ALTER. Existing rows get NULL (no prior ask),
        which the route treats as "never asked — proceed."

        Idempotent: PRAGMA-guarded ADD COLUMN — re-running is a no-op
        (mirrors the v103 pattern).
        """
        cursor = conn.execute("PRAGMA table_info(stakes)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'forgiveness_last_asked' not in cols:
            conn.execute("ALTER TABLE stakes ADD COLUMN forgiveness_last_asked TIMESTAMP")
        logger.info("Migration v104 complete: stakes.forgiveness_last_asked added")

    def _migrate_v106_add_stake_payouts(self, conn: sqlite3.Connection) -> None:
        """Migration v106: add `staker_payout` / `borrower_payout` to stakes.

        Phase 5 refinement (2026-05-21). At settlement time,
        `settle_stake_on_leave` computes how many chips flow to the
        staker and to the borrower — but only the staker_id, borrower_id,
        principal, match_amount, and post-settle `carry_amount` are
        persisted. That makes the Net Worth history view structurally
        unable to answer "did I make or lose money on this stake?".

        These two columns capture the actual settlement chip flows so
        the history surface can compute net P&L from the persisted
        row alone:
          - `staker_payout`: chips returned to the staker at settle time
            (principal + cut × upside on clean; partial recovery on bust;
            0 on full bust)
          - `borrower_payout`: chips returned to the borrower at settle time
            (match + (1-cut) × upside on clean; leftover after staker
            recovery on partial bust; 0 on full bust)

        NULL on existing rows (active or settled pre-migration) — the
        route's history serializer returns null, and the UI hides the
        P&L line for those rows. New settlements going forward populate
        the values.

        Idempotent: PRAGMA-guarded ADDs.
        """
        cursor = conn.execute("PRAGMA table_info(stakes)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'staker_payout' not in cols:
            conn.execute("ALTER TABLE stakes ADD COLUMN staker_payout INTEGER")
        if 'borrower_payout' not in cols:
            conn.execute("ALTER TABLE stakes ADD COLUMN borrower_payout INTEGER")
        logger.info(
            "Migration v106 complete: stakes.staker_payout and " "stakes.borrower_payout added"
        )

    def _migrate_v107_add_aspiration_cooldown(self, conn: sqlite3.Connection) -> None:
        """Migration v107: add `aspiration_cooldown_until` to ai_bankroll_state.

        Aspiration-ask Commit 3. Per-AI cooldown after a triggered
        aspiration_ask (success or fail) — prevents ladder-climb spam
        from a single AI hammering the lobby every tick.

        Column is a nullable ISO-8601 UTC timestamp. NULL means "no
        cooldown active" (the common case). The trigger inside
        `refresh_table_roster` reads this column and skips AIs whose
        cooldown hasn't expired vs the current `now`. The cooldown
        lives on `ai_bankroll_state` (not a separate table) because
        the (sandbox_id, personality_id) row is already the canonical
        per-AI runtime state row — no separate table buys anything.

        NULL on existing rows — the column is purely additive.
        Idempotent: PRAGMA-guarded ADD.
        """
        cursor = conn.execute("PRAGMA table_info(ai_bankroll_state)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'aspiration_cooldown_until' not in cols:
            conn.execute(
                "ALTER TABLE ai_bankroll_state " "ADD COLUMN aspiration_cooldown_until TEXT"
            )
        logger.info("Migration v107 complete: ai_bankroll_state.aspiration_cooldown_until added")

    def _migrate_v149_add_bankruptcy_history(self, conn: sqlite3.Connection) -> None:
        """Migration v149: add bankruptcy credit-history to ai_bankroll_state.

        The carry-resolution bankruptcy valve discharges a hopelessly-
        underwater AI's carries (liquidate chips → pro-rata split →
        default the rest → zero) past a deadline. Two additive columns
        record the consequence so it isn't a free pass:

          - `bankruptcy_count` (INTEGER DEFAULT 0): lifetime bankruptcies
            in this sandbox. Surfaced on the dossier and drives the
            post-bankruptcy loan-term penalty (lenders quote a bankrupt
            borrower pricier money, not a lockout).
          - `last_bankruptcy_at` (TEXT NULL): ISO-8601 UTC stamp of the
            most recent bankruptcy. The hook for v1 time-decay — the
            economic penalty fades with recency while the lifetime count
            persists as history.

        Both live on `ai_bankroll_state` (per-sandbox) so credit history
        is scoped the same way chips/regen already are: an AI can be
        bankrupt-and-expensive in one casino and clean in another.

        Existing rows read 0 / NULL (never bankrupt). Idempotent:
        PRAGMA-guarded ADDs.
        """
        cursor = conn.execute("PRAGMA table_info(ai_bankroll_state)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'bankruptcy_count' not in cols:
            conn.execute(
                "ALTER TABLE ai_bankroll_state "
                "ADD COLUMN bankruptcy_count INTEGER NOT NULL DEFAULT 0"
            )
        if 'last_bankruptcy_at' not in cols:
            conn.execute("ALTER TABLE ai_bankroll_state ADD COLUMN last_bankruptcy_at TEXT")
        logger.info(
            "Migration v149 complete: ai_bankroll_state.bankruptcy_count + "
            "last_bankruptcy_at added"
        )

    def _migrate_v150_add_stake_resolution(self, conn: sqlite3.Connection) -> None:
        """Migration v150: add `resolution` to stakes.

        A nullable display label distinguishing HOW a closed stake
        resolved when the bare `status` isn't specific enough. The first
        value is 'bankruptcy' — carries discharged by the carry-
        resolution insolvency valve. Their `status` stays 'defaulted' so
        every default-counting consumer (Net Worth history inclusion,
        track-record aggregates, sponsor-offer default penalties) keeps
        treating them as defaults; `resolution` is read only by the
        history surface to render "bankruptcy" instead of a deliberate
        stiff.

        NULL on existing rows and on ordinary settles/defaults.
        Idempotent: PRAGMA-guarded ADD.
        """
        cursor = conn.execute("PRAGMA table_info(stakes)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'resolution' not in cols:
            conn.execute("ALTER TABLE stakes ADD COLUMN resolution TEXT")
        logger.info("Migration v150 complete: stakes.resolution added")

    def _migrate_v151_chip_ledger_source_sink_index(self, conn: sqlite3.Connection) -> None:
        """Migration v151: index chip_ledger_entries by (source, sandbox_id) and
        (sink, sandbox_id).

        `balance_of` sums `WHERE source=? OR sink=?` [AND sandbox_id=?]; with no
        index on those columns it full-scans the (ever-growing) ledger. That's
        tolerable on the O(1) int read path, but the per-account reconcile
        (`audit_ledger_completeness`) and the derive-reads tripwire scan every
        account, so they pay it N times. Two single-column-leading indexes let
        SQLite's OR-by-union satisfy each side of the OR; the trailing
        sandbox_id covers the scoped (per-AI) sum. Index-only — no data change;
        `IF NOT EXISTS` makes it idempotent and a no-op on fresh DBs (the base
        schema already creates them).
        """
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chip_ledger_source "
            "ON chip_ledger_entries(source, sandbox_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chip_ledger_sink "
            "ON chip_ledger_entries(sink, sandbox_id)"
        )
        # SQLite's planner only picks these for the `source=? OR sink=?` union
        # once it has stats — without ANALYZE it keeps full-scanning (or uses the
        # less-selective sandbox index), so the index would sit unused. Scope the
        # ANALYZE to this one table: instant on a fresh/small ledger, one-time on
        # a large upgrade (prod lands this while the ledger is still small).
        conn.execute("ANALYZE chip_ledger_entries")
        logger.info("Migration v151 complete: chip_ledger source/sink indexes + stats added")

    def _migrate_v152_drop_cash_idle_pool(self, conn: sqlite3.Connection) -> None:
        """Migration v152: drop the legacy `cash_idle_pool` cache.

        The Presence cutover is complete. `entity_presence` (state='idle') is the
        authoritative record of which AIs are between cash sessions, and the
        `cash_idle_metadata` satellite carries the routing payload
        (reason / target_stake / left_at). `cash_idle_pool` was a redundant copy
        that the seat/idle writers dual-wrote alongside presence; nothing reads it
        anymore (idle listing derives from presence — see
        `CashTableRepository.list_idle` / `_list_idle_from_presence`). Dropping it
        removes the last split-brain source for the `seated_and_idle` bug class.

        `cash_idle_metadata` is deliberately KEPT — it is the satellite, not the
        cache. `DROP TABLE IF EXISTS` is idempotent and a no-op on any DB that
        never created the pool (or already dropped it).
        """
        conn.execute("DROP TABLE IF EXISTS cash_idle_pool")
        logger.info("Migration v152 complete: dropped legacy cash_idle_pool cache")

    def _migrate_v153_create_ai_table_hand_counts(self, conn: sqlite3.Connection) -> None:
        """Migration v153: create `ai_table_hand_counts`.

        One row per (sandbox_id, ai_id, table_id). `ai_id` is the raw
        `personality_id` (no prefix), matching `cash_tables.seats` and the
        relationship graph. `hands` is incremented ONCE per hand for each AI
        seated at the table — NOT bilateral (cash_pair_stats writes N*(N-1)
        pair rows per hand; this writes N rows, one per seated AI).

        Foundation for the table-affinity lever (the `net_chips` column is added
        in v154) and per-room activity reads. Non-destructive, idempotent.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_table_hand_counts (
                sandbox_id   TEXT NOT NULL,
                ai_id        TEXT NOT NULL,
                table_id     TEXT NOT NULL,
                hands        INTEGER NOT NULL DEFAULT 0,
                last_hand_at TIMESTAMP,
                PRIMARY KEY (sandbox_id, ai_id, table_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_table_hand_counts_ai
                ON ai_table_hand_counts(sandbox_id, ai_id)
        """)
        logger.info("Migration v153 complete: ai_table_hand_counts table created")

    def _migrate_v154_add_ai_table_net_chips(self, conn: sqlite3.Connection) -> None:
        """Migration v154: add `net_chips` to `ai_table_hand_counts`.

        Cumulative signed PnL per (sandbox_id, ai_id, table_id), accumulated
        once per AI per hand alongside `hands` at the same sim + live hooks.
        Feeds the success-weighted **table-affinity** attractiveness term
        (`TABLE_AFFINITY_ENABLED`): an AI is drawn back to rooms it wins at and
        away from rooms it loses at, concentrating its hands into a real home
        room (counteracts the within-tier smear across the 2-3 tables per stake).

        Guarded ALTER (PRAGMA check) so it's safe on a partially-applied DB.
        Existing rows read net_chips = 0. Non-destructive, idempotent.
        """
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ai_table_hand_counts)")}
        if "net_chips" not in cols:
            conn.execute(
                "ALTER TABLE ai_table_hand_counts "
                "ADD COLUMN net_chips INTEGER NOT NULL DEFAULT 0"
            )
        logger.info("Migration v154 complete: ai_table_hand_counts.net_chips added")

    def _migrate_v155_rebaseline_regard_neutral(self, conn: sqlite3.Connection) -> None:
        """Migration v155: re-baseline existing regard from 0.5 → 0.35.

        The rebaseline drops the earned-regard neutral from the old hardcoded
        0.5 to REGARD_NEUTRAL (0.35). Every `relationship_states` row that
        predates this change was created against the 0.5 baseline, so we shift
        respect/likability DOWN by the same delta the neutral moved
        (`0.5 − 0.35 = 0.15`), clamped to the [0, 1] axis. This preserves each
        edge's *offset from neutral*: a row at exactly-neutral 0.5 becomes
        exactly-neutral 0.35, a "+0.2 above neutral" 0.7 becomes 0.55 (still
        +0.2), a "−0.3 below" 0.2 becomes 0.05. Renown contributions
        (`value − neutral`), relationship hints, and sponsor/staking offers
        therefore read identically before and after — the data-side mirror of
        the code rebaseline, so no live relationship suddenly warms or cools.

        Heat is NOT touched — it's one-sided and 0-based (0 = neutral), not a
        regard axis.

        IMPORTANT: this is a ONE-TIME data transform and is NOT idempotent if
        re-run (it would shift a second time). Correctness rests on the version
        gate in `_apply_migrations` running each migration exactly once. Fresh
        DBs are built directly at SCHEMA_VERSION and never enter the 154→155
        step, so their rows (already created at 0.35 by the new code) are left
        alone.
        """
        # 0.15 == old neutral (0.5) − REGARD_NEUTRAL (0.35). Kept as a literal
        # because SQL can't reference the Python constant; the migration is a
        # frozen historical record of THIS specific baseline move.
        conn.execute(
            """
            UPDATE relationship_states
            SET respect    = MAX(0.0, MIN(1.0, respect    - 0.15)),
                likability = MAX(0.0, MIN(1.0, likability - 0.15))
            """
        )
        logger.info(
            "Migration v155 complete: relationship_states regard re-baselined "
            "0.5 → 0.35 (respect/likability −0.15, clamped; heat untouched)"
        )

    def _migrate_v156_repoint_labels_to_decisions(self, conn: sqlite3.Connection) -> None:
        """Migration v156: move the label store onto the decision spine.

        `capture_labels` keyed labels off `prompt_captures(id)`, so only LLM
        decisions (the only player type that writes a capture row) could be
        tagged. Human, tiered/sharp, and rule-bot decisions live in
        `player_decision_analysis` with no capture, so they were untaggable.

        This recreates the store as `decision_labels(decision_id →
        player_decision_analysis.id)` and migrates existing labels:

        1. Clean remap — labels whose capture has a decision row (`pda.capture_id
           = cl.capture_id`) move straight onto that decision.
        2. Rescue — user-curated labels stranded on a real `player_decision`
           capture that predates the decision spine (no `pda` row) would
           otherwise be dropped. We synthesize a thin decision row from the
           capture (game_id/player/phase/action — enough to carry the label and
           surface the decision) so the hand-curated label survives. Only done
           for `label_type='user'` (auto-labels regenerate) and only when the
           capture has the NOT NULL identity fields.
        3. Drop — auto-label-only orphans (labels on non-decision captures:
           narration, image, etc.) have no decision to attach to. They are
           dropped and counted in the log; auto-labels recompute going forward.

        Fresh DBs are built at SCHEMA_VERSION with `decision_labels` already in
        `_init_db` and never enter this step.
        """
        # decision_labels normally exists already (created by _init_db, which
        # runs before migrations). Assert it here so the migration is also
        # correct if run in isolation.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL REFERENCES player_decision_analysis(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                label_type TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(decision_id, label)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_labels_label ON decision_labels(label)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_labels_decision_id ON decision_labels(decision_id)"
        )
        # The backfill JOIN + runtime capture→decision bridge both need this.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_analysis_capture ON player_decision_analysis(capture_id)"
        )

        has_old = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_labels'"
        ).fetchone()
        if not has_old:
            logger.info(
                "Migration v156: no capture_labels table — decision_labels ready, nothing to backfill"
            )
            return

        # 2. Rescue first, so the synthesized rows are picked up by step 1's join.
        #    A user label on a real player-decision capture with no decision row
        #    gets a thin decision row built from the capture. call_type IS NULL
        #    is included: pre-v39 captures predate the column and were all player
        #    decisions, and NULL is treated as 'player_decision' elsewhere — so
        #    legacy hand-curated labels survive instead of being dropped.
        rescued = conn.execute(
            """
            INSERT INTO player_decision_analysis
                (game_id, player_name, hand_number, phase, action_taken, capture_id, analyzer_version)
            SELECT pc.game_id, pc.player_name, pc.hand_number, pc.phase, pc.action_taken,
                   pc.id, 'backfill_v156'
            FROM prompt_captures pc
            WHERE (pc.call_type = 'player_decision' OR pc.call_type IS NULL)
              AND pc.game_id IS NOT NULL
              AND pc.player_name IS NOT NULL
              AND pc.id IN (
                  SELECT cl.capture_id FROM capture_labels cl
                  WHERE cl.label_type = 'user'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM player_decision_analysis pda WHERE pda.capture_id = pc.id
              )
            """
        ).rowcount

        # 1. Clean remap (now also covers the rescued rows). OR IGNORE absorbs
        #    the UNIQUE(decision_id, label) guard if a decision somehow shares a
        #    capture across two label rows.
        moved = conn.execute(
            """
            INSERT OR IGNORE INTO decision_labels (decision_id, label, label_type, created_at)
            SELECT pda.id, cl.label, cl.label_type, cl.created_at
            FROM capture_labels cl
            JOIN player_decision_analysis pda ON pda.capture_id = cl.capture_id
            """
        ).rowcount

        # 3. Anything left unmapped is dropped with the table. Count for the log.
        dropped = conn.execute(
            """
            SELECT COUNT(*) FROM capture_labels cl
            WHERE NOT EXISTS (
                SELECT 1 FROM player_decision_analysis pda WHERE pda.capture_id = cl.capture_id
            )
            """
        ).fetchone()[0]

        conn.execute("DROP TABLE capture_labels")

        logger.info(
            "Migration v156 complete: moved %s label(s) to decision_labels "
            "(rescued %s pre-spine user label(s) via synthesized decision rows; "
            "dropped %s orphan label(s) on non-decision captures); capture_labels removed",
            moved,
            rescued,
            dropped,
        )

    def _migrate_v108_add_cash_sessions(self, conn: sqlite3.Connection) -> None:
        """Migration v108: create the `cash_sessions` table.

        Durable per-session record. Replaces the in-memory-only
        `game_data["cash_buy_in"]` / `cash_started_at` / `cash_table_id`
        / `cash_seat_index` fields as the source of truth for the
        leave-table summary, the cold-load restore path, and the
        eventual session-history surface.

        Before this migration, the leave-table summary read those four
        fields off `game_state_service.games[game_id]` — a dict with a
        2h TTL that is also blown away by Flask restart. Symptoms:
          - Restart mid-session → summary returns `null`.
          - Cold-loaded session leaves → buy_in=0, duration_seconds=0.
          - Top-ups / rebuys never adjusted buy_in → P&L over-reported.
          - Staked sessions surfaced `cash_out - principal` as net P&L
            even though the player's actual take-home was much smaller
            after the sponsor's cut.

        Schema rationale:
          - `initial_buy_in`: chips the player put up at sit-down.
            0 for staked sessions (sponsor funded the principal).
          - `total_buy_in`: `initial_buy_in + Σ top-ups + Σ rebuys`.
            This is the denominator for P&L; without it, mid-session
            chip additions get silently counted as profit.
          - `sponsor_principal`: chips the sponsor put up. 0 for
            self-funded sessions. The split is explicit so the UI can
            label staked sessions correctly ("Sponsor put up $X" vs
            "Buy-in $X").
          - `stake_id`: FK-style link to the `stakes` row when staked.
            Nullable; not enforced as a FOREIGN KEY because SQLite's
            FK enforcement is off in this DB and the relationship is
            informational (leave-table re-loads via
            `stake_repo.load_active_for_session` for the actual math).
          - `started_at` / `ended_at`: the durable session timestamps.
            `duration_seconds` could be derived but is materialized
            so the history view doesn't need to subtract on read.
          - `final_chips_at_table`, `sponsor_repaid`, `player_take_home`:
            captured at leave-table so the row tells the complete P&L
            story without joining against the stake or hand history.
          - `hands_played` / `hands_won` / `biggest_pot_won`: derived
            from `hand_history` at leave but materialized here so the
            history view doesn't need to re-aggregate.
          - `closed_status`: 'left' (normal leave), 'ghost_cleanup'
            (memory-miss leave that hit the bankroll-loss fallback),
            or NULL while active.

        Indexes:
          - `(owner_id, started_at DESC)` for the history view (most
            recent sessions per player).
          - `(session_id)` is the PK so no separate index.
          - Filtered index on `ended_at IS NULL` for the (rare)
            "find my active session" lookup, which is otherwise
            served by `_find_active_cash_game_id` against the games
            table.

        Idempotent: CREATE TABLE IF NOT EXISTS + INDEXes IF NOT EXISTS.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_sessions (
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
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_sessions_owner_started
                ON cash_sessions(owner_id, started_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_sessions_active
                ON cash_sessions(owner_id)
                WHERE ended_at IS NULL
        """)
        logger.info("Migration v108 complete: cash_sessions table created")

    def _migrate_v109_scope_cash_pair_stats_to_sandbox(self, conn: sqlite3.Connection) -> None:
        """Migration v109: drop+recreate `cash_pair_stats` with `sandbox_id`
        as part of the primary key.

        Pre-launch destructive migration; existing rows are dropped.
        Same precedent as v102 (`ai_bankroll_state` / `cash_tables` /
        `cash_idle_pool`): per-sandbox scoping is the load-bearing
        change, and there is no production lifetime PnL to preserve.

        After this migration, every (observer_id, opponent_id) pair
        accumulates a separate row per sandbox, so the admin Chip
        Economy panel can filter Won/Lost/Net by the sandbox dropdown.
        The cross-sandbox dossier view (CharacterDetailCard "Track
        Record") aggregates by passing `sandbox_id=None` to
        `aggregate_cash_pnl_by_entity`.
        """
        conn.execute("DROP TABLE IF EXISTS cash_pair_stats")
        conn.execute("""
            CREATE TABLE cash_pair_stats (
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
        logger.info(
            "Migration v109 complete: cash_pair_stats dropped+recreated " "with sandbox_id in PK"
        )

    def _migrate_v110_add_pending_forgiveness_ask(self, conn: sqlite3.Connection) -> None:
        """Migration v110: add nullable `pending_forgiveness_ask` to `stakes`.

        Replaces the auto-grant path for human-staker carries: when an
        AI borrower would have rolled `try_ai_forgiveness_ask` against
        a human staker, instead of silently zeroing the carry it now
        stamps this column with the ask timestamp and surfaces an
        EVENT_AI_REQUESTS_FORGIVENESS lobby event. The player accepts
        or refuses via POST /api/cash/stakes/<id>/staker-forgive — that
        route clears the column on either branch.

        Non-destructive ALTER. Existing rows get NULL (no pending ask).
        AI-to-AI carries never use this column; the auto-grant path
        remains in place for those.

        Idempotent: PRAGMA-guarded ADD COLUMN — re-running is a no-op
        (mirrors the v104 pattern).
        """
        cursor = conn.execute("PRAGMA table_info(stakes)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'pending_forgiveness_ask' not in cols:
            conn.execute("ALTER TABLE stakes ADD COLUMN pending_forgiveness_ask TIMESTAMP")
        logger.info("Migration v110 complete: stakes.pending_forgiveness_ask added")

    def _migrate_v111_add_multi_table_lobby_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v111: add name + table_type to cash_tables and
        table_id to stakes for the multi-table-per-tier lobby.

        Three non-destructive ALTERs:
          - `cash_tables.name TEXT` — user-facing label ("The Lodge").
            NULL on existing rows; frontend falls back to the stake
            label. Backfill below sets the canonical -001 rows from
            the lobby_config dict so existing tables get a name on
            first boot after this migration runs.
          - `cash_tables.table_type TEXT DEFAULT 'lobby'` — discriminator
            for future private + casino table types. All current rows
            and new lobby seeds use 'lobby'.
          - `stakes.table_id TEXT` — captures which specific table
            the stake was created against (for per-table analytics).
            NULL on existing rows; sponsor_and_sit writes it on new
            stakes. Settlement remains session-id-keyed, so NULL
            doesn't break anything.

        Idempotent: PRAGMA-guarded ADD COLUMN matches the v104/v107/v110
        pattern.
        """
        cursor = conn.execute("PRAGMA table_info(cash_tables)")
        cash_table_cols = {row[1] for row in cursor.fetchall()}
        if 'name' not in cash_table_cols:
            conn.execute("ALTER TABLE cash_tables ADD COLUMN name TEXT")
        if 'table_type' not in cash_table_cols:
            # SQLite ADD COLUMN can't take a non-constant default; use
            # the literal 'lobby' so existing rows pick up the default
            # via the schema, and new rows persist explicitly.
            conn.execute(
                "ALTER TABLE cash_tables ADD COLUMN table_type TEXT " "NOT NULL DEFAULT 'lobby'"
            )

        cursor = conn.execute("PRAGMA table_info(stakes)")
        stake_cols = {row[1] for row in cursor.fetchall()}
        if 'table_id' not in stake_cols:
            conn.execute("ALTER TABLE stakes ADD COLUMN table_id TEXT")

        # Backfill canonical -001 table names from lobby_config so
        # existing lobby tables get human-friendly labels without
        # waiting for a re-seed (which is idempotent and never
        # mutates existing rows). We import inside the function to
        # avoid pulling cash_mode into the schema layer at import
        # time; this also keeps the migration self-contained.
        try:
            from cash_mode.lobby_config import LOBBY_TABLES
        except ImportError:
            # cash_mode not on path (e.g., schema-only test) — skip
            # the backfill. The columns are still added so subsequent
            # boots can write names.
            logger.info(
                "Migration v111: cash_mode.lobby_config not importable; " "skipping name backfill"
            )
            return

        for stake_label, entries in LOBBY_TABLES.items():
            for entry in entries:
                suffix = entry['id_suffix']
                name = entry['name']
                if stake_label.startswith('$'):
                    slug = stake_label[1:]
                else:
                    slug = stake_label
                table_id = f"cash-table-{slug}-{suffix}"
                # Only set name where it's currently NULL — preserves
                # any future per-row overrides without clobber.
                conn.execute(
                    "UPDATE cash_tables SET name = ? " "WHERE table_id = ? AND name IS NULL",
                    (name, table_id),
                )

        logger.info(
            "Migration v111 complete: cash_tables.name + table_type added, "
            "stakes.table_id added, lobby-config names backfilled"
        )

    def _migrate_v113_add_casino_closing_countdown(self, conn: sqlite3.Connection) -> None:
        """Migration v113: add `closing_hand_countdown` to cash_tables.

        Backs the casino smooth-shutdown lifecycle. Three states:
          - NULL: table is active (lobby tables OR active casino) — the
            common case for every row.
          - INTEGER >= 0: casino is in 'closing' state with N hands
            remaining. Decremented by hand-completion hooks (sim and
            human play paths) until 0, at which point the next
            provisioning resolution deletes the row.

        Non-destructive ADD COLUMN. Idempotent via PRAGMA guard.
        """
        cursor = conn.execute("PRAGMA table_info(cash_tables)")
        cash_table_cols = {row[1] for row in cursor.fetchall()}
        if 'closing_hand_countdown' not in cash_table_cols:
            conn.execute("ALTER TABLE cash_tables ADD COLUMN closing_hand_countdown INTEGER")
        logger.info("Migration v113 complete: cash_tables.closing_hand_countdown added")

    def _migrate_v112_create_ai_vice_state(self, conn: sqlite3.Connection) -> None:
        """Migration v112: create `ai_vice_state` for AI vice spending.

        One row per active vice, keyed `(personality_id, sandbox_id)`.
        Deleted when the vice expires. The index supports the per-
        refresh expiry scan (`SELECT ... WHERE sandbox_id = ? AND
        ends_at <= ?`).

        Non-destructive. Idempotent (CREATE TABLE IF NOT EXISTS +
        CREATE INDEX IF NOT EXISTS).
        """
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
        logger.info("Migration v112 complete: ai_vice_state table created")

    def _migrate_v114_create_ai_side_hustle_state(self, conn: sqlite3.Connection) -> None:
        """Migration v114: create `ai_side_hustle_state` for the side hustle.

        The mirror of `ai_vice_state` (v112). One row per active hustle,
        keyed `(personality_id, sandbox_id)`. A broke AI goes off-grid to
        earn a lump from the bank pool; the row is deleted when the
        hustle expires and the payout is credited. The index supports the
        per-refresh expiry scan (`SELECT ... WHERE sandbox_id = ? AND
        ends_at <= ?`).

        Non-destructive. Idempotent (CREATE TABLE IF NOT EXISTS +
        CREATE INDEX IF NOT EXISTS). See `docs/plans/CASH_MODE_SIDE_HUSTLE.md`.
        """
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
        logger.info("Migration v114 complete: ai_side_hustle_state table created")

    def _migrate_v115_create_user_preferences(self, conn: sqlite3.Connection) -> None:
        """Migration v115: create `user_preferences` for per-user settings.

        One row per user, keyed by `user_id`. The first concrete setting is
        `world_pace` (`subtle` / `lively` / `bustling`), which controls the
        realtime background ticker's hand-sim rate for that user's sandbox.
        `preferences_json` is reserved for future scalar prefs so adding the
        next setting doesn't need another migration.

        Non-destructive. Idempotent (CREATE TABLE IF NOT EXISTS). See
        `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                world_pace TEXT NOT NULL DEFAULT 'lively',
                preferences_json TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("Migration v115 complete: user_preferences table created")

    def _migrate_v116_create_holdings_snapshots(self, conn: sqlite3.Connection) -> None:
        """Migration v116: create `holdings_snapshots` for net-worth-over-time.

        One row per (entity, sandbox) per capture. The background world
        ticker writes a point roughly every 10 minutes per active sandbox
        so the admin Chip Economy "Player Holdings" chart can plot real
        net worth over time — the ledger can't reconstruct it because
        seat-to-seat chip flows never hit the ledger.

        `net_worth = chips + receivable - outstanding`; the components are
        stored alongside so the curve is explainable and future metric
        toggles are cheap. `captured_at` is written by the recorder as an
        explicit ISO-8601 UTC string (not the CURRENT_TIMESTAMP default)
        so the history read's lexical `captured_at >= since` comparison is
        format-consistent.

        Non-destructive. Idempotent (CREATE TABLE IF NOT EXISTS). See
        `docs/plans/CASH_MODE_NET_WORTH_HOLDINGS.md`.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS holdings_snapshots (
                snapshot_id  INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at  TIMESTAMP NOT NULL,
                sandbox_id   TEXT NOT NULL,
                entity_id    TEXT NOT NULL,
                kind         TEXT NOT NULL,
                net_worth    INTEGER NOT NULL,
                chips        INTEGER NOT NULL,
                receivable   INTEGER NOT NULL DEFAULT 0,
                outstanding  INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_holdings_snap_scope
                ON holdings_snapshots(sandbox_id, captured_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_holdings_snap_entity
                ON holdings_snapshots(sandbox_id, entity_id, captured_at)
        """)
        logger.info("Migration v116 complete: holdings_snapshots table created")

    def _migrate_v117_add_recent_events(self, conn: sqlite3.Connection) -> None:
        """Migration v117: add `recent_events_json` to ai_bankroll_state.

        A small per-AI ring buffer (capped JSON list) of recent notable hand
        events — bust, suckout, big pot — so the lobby/dossier can show "what
        recently happened to this character" and the world carries memory.
        Deliberately NOT the full pressure_events firehose: bounded size,
        event-driven writes (only on drama, which is rare per hand). Stored on
        the (sandbox_id, personality_id) runtime row rather than the
        psychology blob, which `PlayerPsychology.from_dict` would drop.

        NULL on existing rows — purely additive. Idempotent: PRAGMA-guarded ADD.
        """
        cursor = conn.execute("PRAGMA table_info(ai_bankroll_state)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'recent_events_json' not in cols:
            conn.execute("ALTER TABLE ai_bankroll_state ADD COLUMN recent_events_json TEXT")
        logger.info("Migration v117 complete: ai_bankroll_state.recent_events_json added")

    def _migrate_v118_add_user_profile(self, conn: sqlite3.Connection) -> None:
        """Migration v118: human-player profile — avatar + AI-visible bio.

        Two additive changes:

        1. `user_avatars` — one row per user (keyed by `user_id`; no FK so the
           cookie-only guest users work just like their guest-owned games). Holds
           the processed circular icon + square full PNG blobs and a stable
           opaque `public_id` UUID, which is the only id exposed in the public
           serve URL `/api/user-avatar/<public_id>` (the raw user_id is never
           leaked to other players in a multiplayer room).

        2. `user_preferences.bio` — a short free-text self-description the human
           writes for the AIs to read and riff on (trash talk / commentary).
           Reuses the existing per-user prefs row; NULL until the user sets it.

        Non-destructive. Idempotent (CREATE TABLE IF NOT EXISTS + PRAGMA-guarded
        ADD COLUMN).
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_avatars (
                user_id TEXT PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                icon_data BLOB NOT NULL,
                full_data BLOB NOT NULL,
                content_type TEXT NOT NULL DEFAULT 'image/png',
                source TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_avatars_public_id ON user_avatars(public_id)"
        )

        cursor = conn.execute("PRAGMA table_info(user_preferences)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'bio' not in cols:
            conn.execute("ALTER TABLE user_preferences ADD COLUMN bio TEXT")
        logger.info("Migration v118 complete: user_avatars table + user_preferences.bio added")

    def _migrate_v119_add_session_state(self, conn: sqlite3.Connection) -> None:
        """Migration v119: explicit lifecycle state on `cash_sessions`.

        Two additive columns (see
        `docs/plans/CASH_MODE_SESSION_LIFECYCLE_HARDENING.md` Tier 3):

        1. `session_state` — the coarse machine-state the sit guard reads:
           `active` (live or resumable), `paused` (resumable, de-memoized),
           `abandoning` (teardown in flight), `closed` (settled), `broken`
           (cleanup couldn't converge). Replaces inferring "is there an
           active session?" from *"a cash-* games row exists"* — a stale
           or broken row no longer wedges every new sit. Backfilled from
           `ended_at`: a finalised row becomes `closed`, everything else
           stays `active`.

        2. `last_load_error` — stash for the last cold-load failure (error
           class + timestamp) so production debugging of a wedged session
           skips log archaeology.

        Non-destructive. Idempotent (PRAGMA-guarded ADD COLUMN). The
        `NOT NULL DEFAULT 'active'` on session_state is applied via the
        column default so existing rows get a value, then the backfill
        corrects the closed ones.
        """
        cursor = conn.execute("PRAGMA table_info(cash_sessions)")
        cols = {row[1] for row in cursor.fetchall()}
        if 'session_state' not in cols:
            conn.execute(
                "ALTER TABLE cash_sessions ADD COLUMN session_state "
                "TEXT NOT NULL DEFAULT 'active'"
            )
            # Backfill: an already-finalised session is closed, not active.
            conn.execute(
                "UPDATE cash_sessions SET session_state = 'closed' " "WHERE ended_at IS NOT NULL"
            )
        if 'last_load_error' not in cols:
            conn.execute("ALTER TABLE cash_sessions ADD COLUMN last_load_error TEXT")
        # Partial index for the hot "does this owner have a blocking
        # session?" lookup — only active/paused rows can block a new sit.
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cash_sessions_blocking
                ON cash_sessions(owner_id)
                WHERE session_state IN ('active', 'paused', 'abandoning')
            """
        )
        logger.info("Migration v119 complete: cash_sessions.session_state + last_load_error added")

    def _migrate_v120_create_cash_session_events(self, conn: sqlite3.Connection) -> None:
        """Migration v120: persisted cash-session lifecycle telemetry.

        One row per lifecycle transition (`started`, `resumed`,
        `left_clean`, `left_ghost`, `swept`, `broken`, ...). Distinct from
        `cash_mode/activity.py`'s in-memory ring buffer, which is the
        cosmetic player-facing world ticker and isn't persisted. This
        table backs ops queries ("orphans swept per day?") and the planned
        admin orphan-counter widget (Tier 4.3).

        `detail_json` carries event-specific context (closed_status,
        sweep source, chips, etc.). Non-destructive, idempotent.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_session_events (
                event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                owner_id    TEXT,
                sandbox_id  TEXT,
                event       TEXT NOT NULL,
                detail_json TEXT,
                created_at  TIMESTAMP NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_session_events_session
                ON cash_session_events(session_id, created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_session_events_scope
                ON cash_session_events(sandbox_id, event, created_at)
        """)
        logger.info("Migration v120 complete: cash_session_events table created")

    def _migrate_v121_create_coach_session_evaluations(self, conn: sqlite3.Connection) -> None:
        """Migration v121: create `coach_session_evaluations` (PRH-15).

        Per-game persistence of the coach's per-hand skill evaluations. These
        previously lived only in `game_data['coach_session_memory']` (a
        `SessionMemory`), so a restart or TTL-eviction wiped a player's
        hand-review history mid-session. One row per game_id holds a JSON blob
        of `{hand_number: [evaluation, ...]}`; the read path rebuilds a
        `SessionMemory` from it on a memory miss.

        Non-destructive. Idempotent (CREATE TABLE IF NOT EXISTS). Renumbered
        from v118 on the prep-for-main→development merge (version collision).
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coach_session_evaluations (
                game_id          TEXT PRIMARY KEY,
                user_id          TEXT,
                evaluations_json TEXT NOT NULL,
                updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("Migration v121 complete: coach_session_evaluations table created")

    def _migrate_v122_create_prestige_snapshots(self, conn: sqlite3.Connection) -> None:
        """Migration v122: create `prestige_snapshots` for human reputation.

        One row per (sandbox, owner) per ticker capture. The background
        world ticker writes a point every few minutes per active sandbox so
        the cash lobby can surface the human's reputation as a scoreboard
        and so renown has a visible trajectory over time.

        Two axes:
          - `renown`  [0,1] — fame magnitude; behaviour-agnostic; RATCHETS
            (the recorder stores `max(computed, running peak)`), so it reads
            as a career record that downswings can't erase.
          - `regard`  [-1,1] — how the room feels (beloved ↔ reviled);
            swings with behaviour and partially decays as `heat` decays.

        The `renown_*` / `regard_*` component columns store the formula's
        contributions so the panel and debugging can show WHY, and so the
        (illustrative, not-locked) weights can be tuned against real history.
        `captured_at` is written by the recorder as an explicit ISO-8601 UTC
        string so the history read's lexical comparison is format-consistent.

        Also adds an index on `relationship_states(opponent_id)` — the
        inbound-edge direction (every AI's view OF the human) that regard
        aggregates over; the table was previously only indexed by its
        (observer_id, opponent_id) PK, so the inbound scan had no support.

        Read-only scoreboard: nothing here feeds core AI decision thresholds.
        Non-destructive. Idempotent (CREATE ... IF NOT EXISTS). See
        `docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prestige_snapshots (
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
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prestige_snap_scope
                ON prestige_snapshots(sandbox_id, owner_id, captured_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_relationship_states_opponent
                ON relationship_states(opponent_id)
        """)
        logger.info("Migration v122 complete: prestige_snapshots table created")

    def _migrate_v123_add_personality_circulating(self, conn: sqlite3.Connection) -> None:
        """Migration v123: add `circulating` to personalities.

        Decouples two ideas that `visibility` previously conflated:
          - `visibility` (public/private/disabled) — who can SEE / PICK a
            persona. Unchanged.
          - `circulating` (0/1) — whether the persona is AUTOMATICALLY
            seeded into the opponent pool (the cash-mode seat-filler) with
            nobody explicitly choosing it.

        Why: an ownerless persona — a sim seat, an admin/test creation, an
        unknown-name auto-generate — was written `visibility='public'` and
        therefore immediately auto-seated into EVERY player's cash games.
        That's the recurring "test/zombie persona pollutes the circuit"
        class (Test Player, Unknown Celebrity, AI 12-15, Fishy, … all
        leaked this way and racked up tens of thousands of seatings). The
        `RESERVED_PERSONA_NAMES` guard only caught a hardcoded list and
        only blocked the WRITE; this makes the safe behaviour structural —
        new ownerless personas are public-but-not-circulating, and entering
        the live pool becomes an explicit, curated act (`set_circulating`,
        or seeding with `circulating=1`).

        Backfill preserves CURRENT behaviour exactly: every row that is
        public today keeps circulating, so the whole seeded celebrity
        corpus (including the good `ai_generated` ones — Cthulhu, Snoop
        Dogg, Yoda, …) is unaffected. Only the forward default changes.
        Demoting the existing junk rows is a separate, explicit data step
        (it's environment-specific — prod has different junk than dev), not
        baked into this generic, reusable migration. Non-destructive,
        additive, reversible.
        """
        columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()]
        if 'circulating' not in columns:
            conn.execute(
                "ALTER TABLE personalities ADD COLUMN circulating INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_personalities_circulating "
            "ON personalities(circulating)"
        )
        # Preserve today's behaviour: everything currently public auto-seats.
        updated = conn.execute(
            "UPDATE personalities SET circulating = 1 WHERE visibility = 'public'"
        ).rowcount
        logger.info(
            f"Migration v123 complete: added circulating column, "
            f"marked {updated} public personas circulating"
        )

    def _migrate_v157_create_career_progress(self, conn: sqlite3.Connection) -> None:
        """Migration v157: create `career_progress` for the Act-1 spine.

        One row per (sandbox, owner). Holds the narrative keyring and tutorial
        state for `CASH_MODE_CAREER_PROGRESSION.md` as a single JSON blob so the
        shape can evolve without a migration per field:

          - `revealed_table_ids` — the keyring: which cardrooms the player has
            been vouched into and may therefore SEE in the lobby. New players
            start empty (only the Scene-0 table shows); each vouch appends one.
          - `scene0_seeded` / `scene0_table_id` / `scene0_fish_id` — the pinned
            intimate tutorial table (Sal + one fish + you), so the seeder is
            idempotent and the vouch trigger knows which fish to measure.
          - `tutorial_complete` — Scene-0 graduated (the first vouch fired).
          - `home_court_table_id` — the random cardroom the first vouch revealed.
          - `vouched_by` — append-only list of personality_ids that have already
            spent their one vouch (v1: one per AI).

        The world economy still runs across ALL tables (the lobby just filters
        what it renders), so nothing here gates the sim — it's a read-side
        visibility layer plus the scripted-graduation bookkeeping. Sandbox-keyed
        so a fresh save starts the keyring over. Non-destructive, idempotent.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS career_progress (
                sandbox_id    TEXT NOT NULL,
                owner_id      TEXT NOT NULL,
                progress_json TEXT NOT NULL DEFAULT '{}',
                updated_at    TIMESTAMP NOT NULL,
                PRIMARY KEY (sandbox_id, owner_id)
            )
        """)
        logger.info("Migration v157 complete: career_progress table created")

    def _migrate_v124_create_opponent_observation_lifetime(self, conn: sqlite3.Connection) -> None:
        """Migration v124: create `opponent_observation_lifetime` + add the
        `opponent_models.lifetime_applied_json` high-water mark.

        The Circuit's durable scouting memory. One row per
        (sandbox_id, observer_id, opponent_id) holding cumulative behavioral
        COUNTS summed across every game in that sandbox; rates (VPIP, PFR,
        aggression factor, showdown win-rate) are derived on read. Storing
        counts (not rates) is what lets games merge losslessly — a new game's
        tallies simply add to the running totals.

        Filled ONLY from sandbox-bound games (Circuit cash + Circuit
        tournaments). The legacy per-game `opponent_models` table is
        unchanged and keeps serving the live in-game AI as before — this is a
        Circuit-only feature layered on top, not a change to how any mode
        models opponents.

        `opponent_models.lifetime_applied_json` is the per-(game, observer,
        opponent) high-water mark of counts already folded into the lifetime
        row. The fold is a continuous delta-fold at each hand-boundary save
        (`delta = current − applied; lifetime += delta; applied = current`),
        which is resume-safe (cold-load reuses game_id) and never
        double-counts. The ALTER is guarded by a PRAGMA check so the
        migration is safe on a partially-applied DB.

        Non-destructive. Idempotent (CREATE ... IF NOT EXISTS, guarded ALTER).
        See `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS opponent_observation_lifetime (
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
                last_updated    TIMESTAMP,
                PRIMARY KEY (sandbox_id, observer_id, opponent_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_obs_lifetime_observer
                ON opponent_observation_lifetime(sandbox_id, observer_id)
        """)

        # Add the high-water mark column to opponent_models, guarded so the
        # migration is safe whether or not the column already exists (fresh
        # installs create opponent_models in _init_db without it; a
        # partially-applied DB may already have it).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(opponent_models)").fetchall()}
        if "lifetime_applied_json" not in cols:
            conn.execute("ALTER TABLE opponent_models ADD COLUMN lifetime_applied_json TEXT")

        logger.info(
            "Migration v124 complete: opponent_observation_lifetime created + "
            "opponent_models.lifetime_applied_json added"
        )

    def _migrate_v125_create_dossier_informant_unlocks(self, conn: sqlite3.Connection) -> None:
        """Migration v125: create `dossier_informant_unlocks` (Phase 3).

        Records sections the player has paid the informant to reveal on an
        opponent's dossier, per (sandbox_id, observer_id, opponent_id,
        section_id). The dossier's effective unlock state is the grind
        unlocks (derived from observed hands) UNION these purchased sections,
        so a purchase bypasses the grind floor and persists.

        `price_paid` is stored per row so the audit / future pricing tweaks
        can see what was actually charged. Non-destructive, idempotent
        (CREATE ... IF NOT EXISTS). See
        `docs/plans/OPPONENT_DOSSIER_PROGRESSION.md`.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dossier_informant_unlocks (
                sandbox_id   TEXT NOT NULL,
                observer_id  TEXT NOT NULL,
                opponent_id  TEXT NOT NULL,
                section_id   TEXT NOT NULL,
                price_paid   INTEGER NOT NULL DEFAULT 0,
                purchased_at TIMESTAMP NOT NULL,
                PRIMARY KEY (sandbox_id, observer_id, opponent_id, section_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_informant_unlocks_pair
                ON dossier_informant_unlocks(sandbox_id, observer_id, opponent_id)
        """)
        logger.info("Migration v125 complete: dossier_informant_unlocks table created")

    def _migrate_v126_add_deep_postflop_lifetime_counts(self, conn: sqlite3.Connection) -> None:
        """Migration v126: extend `opponent_observation_lifetime` with the deep
        postflop count/sum columns (Tier-2 dossier reads).

        v124 stored only the headline counts (VPIP/PFR/AF/showdown).
        `OpponentTendencies` already tracks far more (fold-to-cbet, c-bet
        attempt, barrel/3rd-barrel, all-in, postflop aggression, and the
        equity-at-action polarization sums). This promotes those counters into
        the durable per-sandbox store so they accumulate cross-game, feeding
        the new grind tiers past 180 hands. Same principle as v124: store
        COUNTS (and the equity SUMS), derive rates on read through the
        canonical `OpponentTendencies` formula so definitions never drift.

        Each derived rate needs both numerator AND denominator counts because
        the read reconstructs an `OpponentTendencies` and re-derives the rate.
        The equity polarization means are mean = sum / count, so we store the
        REAL sum alongside its integer count.

        Every ALTER is guarded by a PRAGMA check so the migration is safe on a
        partially-applied DB. Additive, idempotent. See
        `docs/plans/DOSSIER_ENRICHMENT_HANDOFF.md`.
        """
        # (column, sql_type) — integer counts, then the REAL equity sums.
        new_columns = [
            ('all_in_count', 'INTEGER'),
            ('fold_to_cbet_count', 'INTEGER'),
            ('cbet_faced_count', 'INTEGER'),
            ('cbet_attempt_count', 'INTEGER'),
            ('postflop_seen_as_pfr_count', 'INTEGER'),
            ('barrel_count', 'INTEGER'),
            ('barrel_opportunity_count', 'INTEGER'),
            ('third_barrel_count', 'INTEGER'),
            ('third_barrel_opportunity_count', 'INTEGER'),
            ('postflop_bet_raise_count', 'INTEGER'),
            ('postflop_call_count', 'INTEGER'),
            ('equity_betting_count', 'INTEGER'),
            ('equity_raising_count', 'INTEGER'),
            ('equity_calling_count', 'INTEGER'),
            ('equity_betting_sum', 'REAL'),
            ('equity_raising_sum', 'REAL'),
            ('equity_calling_sum', 'REAL'),
        ]
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)").fetchall()
        }
        for col, sql_type in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE opponent_observation_lifetime "
                    f"ADD COLUMN {col} {sql_type} NOT NULL DEFAULT 0"
                )

        logger.info(
            "Migration v126 complete: %d deep postflop column(s) added to "
            "opponent_observation_lifetime",
            len(new_columns),
        )

    def _migrate_v127_add_preflop_opportunity_lifetime_counts(
        self, conn: sqlite3.Connection
    ) -> None:
        """Migration v127: add the preflop opportunity-count columns to
        `opponent_observation_lifetime`.

        The Part-B2 dossier "the read" reuses the tiered-bot exploitation
        detectors (`poker.strategy.exploitation`). The station and tight-nit
        detectors gate on `vpip_per_voluntary_opportunity` (and the steal read
        on `pfr_per_open_opportunity`) — the player-count-stable, opportunity-
        normalized preflop rates, NOT the raw hands-dealt-normalized vpip/pfr.
        Those rates derive from preflop opportunity counters that v124 didn't
        store, so without these columns the station/nit reads could never fire
        from lifetime data. The counters are already serialized in
        `tendencies_json`, so the existing delta-fold picks them up once they
        join `_LIFETIME_COUNT_FIELDS`.

        Guarded ALTERs, additive, idempotent. See
        `docs/plans/DOSSIER_ENRICHMENT_HANDOFF.md`.
        """
        new_columns = [
            'preflop_voluntary_action_count',
            'preflop_voluntary_opportunities',
            'preflop_open_raise_count',
            'preflop_open_opportunities',
        ]
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)").fetchall()
        }
        for col in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE opponent_observation_lifetime "
                    f"ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )

        logger.info(
            "Migration v127 complete: %d preflop opportunity column(s) added "
            "to opponent_observation_lifetime",
            len(new_columns),
        )

    def _migrate_v128_create_entity_presence(self, conn: sqlite3.Connection) -> None:
        """Migration v128: create `entity_presence` (Cut 3 of the state-model plan).

        The single authoritative presence row per `(entity_id, sandbox_id)` for
        the Presence state machine (`cash_mode/presence.py`). The point of the
        table is *structural* impossibility of two bug classes:

          - **`seated_and_idle` / two-states-at-once.** The compound PRIMARY KEY
            `(entity_id, sandbox_id)` allows exactly one row — therefore exactly
            one `state` — per entity per sandbox. There is nowhere to record a
            second, contradictory state.
          - **`double_seat`.** A partial UNIQUE index over
            `(sandbox_id, table_id, seat_index)` WHERE `state = 'seated'` forbids
            two entities occupying the same physical seat. (Non-seated rows carry
            NULL table_id/seat_index and are excluded from the constraint.)

        `entity_id` uses the ledger convention (`player:<owner_id>` /
        `ai:<personality_id>`; pool-funded casino AI also live here with a `pool`
        origin state). `table_id` / `seat_index` are populated iff `state =
        'seated'` (enforced in the application layer by the pure machine and
        structurally by the CHECK constraint below).

        NOTE (cutover complete): the presence machine is now LIVE
        (`PRESENCE_AUTHORITY_ENABLED` defaults True), so this table is the
        authoritative seat source and the seat / idle-pool / hustle / vice writers
        route through it (see `docs/plans/CASH_MODE_PRESENCE_MIGRATION.md`). It was
        additive-and-dormant when first created at this migration; that is history,
        not current behaviour.

        Non-destructive. Idempotent (CREATE ... IF NOT EXISTS). See
        `docs/plans/CASH_MODE_STATE_MODEL.md` (§5.1, §6).
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_presence (
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
            )
        """)
        # Forbid two entities sharing one physical seat (the double_seat class).
        # Partial index: only seated rows participate; non-seated rows have NULL
        # seat fields and are excluded.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_presence_seat
                ON entity_presence(sandbox_id, table_id, seat_index)
                WHERE state = 'seated'
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entity_presence_sandbox
                ON entity_presence(sandbox_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entity_presence_sandbox_state
                ON entity_presence(sandbox_id, state)
        """)
        logger.info("Migration v128 complete: entity_presence table created")

    def _migrate_v129_create_cash_idle_metadata(self, conn: sqlite3.Connection) -> None:
        """Migration v129: create `cash_idle_metadata` (Presence cutover Phase 3).

        The Presence state machine (`entity_presence`) records WHERE an actor is
        as a single state value. For the IDLE state, the cash-mode mover also
        needs two pieces of routing payload that are meaningless for every other
        state: `reason` (why the AI left — take_break / forced_leave /
        stake_up_queued / bored_move) and `target_stake` (which stake it wants to
        re-sit at). Those drive the idle-candidate filter (`cash_mode/movement.py`).

        Putting them on `entity_presence` would pollute the pure machine with
        nullable, IDLE-only columns (the dataclass `__post_init__` already forbids
        non-seated rows from carrying seat fields — the same philosophy rejects
        idle-only payload on non-IDLE states). So they live here, in a satellite
        keyed the same way the old `cash_idle_pool` was: `(personality_id,
        sandbox_id)`. At the authority flip, `entity_presence` owns the IDLE
        *state* and this table carries the *metadata*; `cash_idle_pool` keeps
        being written as a derived cache (its hard view-conversion is a separate,
        later step — see `docs/plans/CASH_MODE_PRESENCE_PHASE3_FLIP.md` D2).

        Additive and dormant: nothing writes this until the flip wiring lands
        behind `economy_flags.PRESENCE_AUTHORITY_ENABLED` (default off). Idempotent.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_idle_metadata (
                personality_id TEXT NOT NULL,
                sandbox_id     TEXT NOT NULL,
                reason         TEXT,
                target_stake   TEXT,
                left_at        TEXT,
                PRIMARY KEY (personality_id, sandbox_id)
            )
        """)
        logger.info("Migration v129 complete: cash_idle_metadata table created")

    def _migrate_v141_create_tournaments(self, conn: sqlite3.Connection) -> None:
        """Migration v141 (renumbered 132→141): create `tournaments` — durable multi-table tournament
        (MTT) meta-state.

        One row per tournament holding the serialized `TournamentSession`
        (`session_json`, the source of truth for field/seating/standings), the
        human's live `game_id` (NULL until they sit), `status`
        ('active'|'complete'), and `resolver_kind` ('fake'|'engine', rebuilt on
        rehydrate — resolvers aren't serialized). Makes a tournament
        re-enterable across navigation / TTL eviction / server restart,
        mirroring how cash sessions cold-load. The live per-table hand state
        still lives in the `games` row.

        Non-destructive. Idempotent (CREATE ... IF NOT EXISTS). Renumbered from
        v130 on the development→tournaments economy merge. See
        `docs/plans/TOURNAMENT_PERSISTENCE_HANDOFF.md`.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                tournament_id TEXT PRIMARY KEY,
                owner_id      TEXT NOT NULL,
                game_id       TEXT,
                status        TEXT NOT NULL,
                resolver_kind TEXT NOT NULL DEFAULT 'fake',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                session_json  TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tournaments_owner ON tournaments(owner_id, status)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tournaments_game ON tournaments(game_id)")
        logger.info("Migration v141 complete: tournaments table created")

    def _migrate_v142_drop_tournament_tracker(self, conn: sqlite3.Connection) -> None:
        """Migration v142 (renumbered 133→142): drop the legacy `tournament_tracker` table.

        `TournamentTracker` was retired by the tournament-unification work (every
        game is now a `TournamentSession`; a single game is a 1-table session).
        The table lingered briefly, read-only, only to migrate legacy
        saved-tracker blobs into sessions on cold-load — that shim is now removed.

        Brute-force clean cut (pre-release, no real user data): any game that
        still depended on the tracker is dropped along with the table rather than
        migrated. Targeted by the tracker's own `game_id`s, so cash games and
        session-backed games are untouched. May leave a few orphan rows in
        related tables for those dropped games — acceptable for this cut.
        Renumbered from v124 on the development→tournaments merge.
        """
        try:
            conn.execute(
                "DELETE FROM games WHERE game_id IN (SELECT game_id FROM tournament_tracker)"
            )
        except sqlite3.OperationalError:
            pass  # table already absent on this DB — nothing to clean
        conn.execute("DROP INDEX IF EXISTS idx_tournament_tracker_game")
        conn.execute("DROP TABLE IF EXISTS tournament_tracker")
        logger.info("Migration v142 complete: tournament_tracker dropped (legacy tracker retired)")

    def _migrate_v143_add_tournament_economy(self, conn: sqlite3.Connection) -> None:
        """Migration v143 (renumbered 134→143): add the real-chip economy columns to `tournaments`.

        The funny-money field stays serialized in `session_json`; these columns
        are the real-chip layer (escrow → overlay → payout) on top:

          | Column | Meaning |
          |---|---|
          | `buy_in`        | per-entrant human buy-in (0 = freeroll) |
          | `rake`          | absolute rake skimmed to the bank pool |
          | `bank_overlay`  | house contribution beyond buy-ins (the drain dial) |
          | `prize_pool`    | Σ buy_ins + overlay − rake (display snapshot) |
          | `payout_status` | skipped \\| pending \\| in_progress \\| complete |

        Additive (`ALTER TABLE ... ADD COLUMN`), each guarded so the migration is
        idempotent against a DB where _init_db already created the column (fresh
        DBs build the full table; only older DBs reach the ALTERs). Existing rows
        default to `payout_status='skipped'` so the payout idempotency guard never
        fires on a pre-economy tournament.
        """
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tournaments)")}
        additions = (
            ("buy_in", "INTEGER NOT NULL DEFAULT 0"),
            ("rake", "INTEGER NOT NULL DEFAULT 0"),
            ("bank_overlay", "INTEGER NOT NULL DEFAULT 0"),
            ("prize_pool", "INTEGER NOT NULL DEFAULT 0"),
            ("payout_status", "TEXT NOT NULL DEFAULT 'skipped'"),
        )
        for name, decl in additions:
            if name not in existing:
                conn.execute(f"ALTER TABLE tournaments ADD COLUMN {name} {decl}")
        logger.info("Migration v143 complete: tournament economy columns added")

    def _migrate_v144_create_tournament_invites(self, conn: sqlite3.Connection) -> None:
        """Migration v144 (renumbered 135→144): create `tournament_invites` — the circuit Main Event offer.

        One open invite per owner that they accept (→ play it through the live
        bridge), decline, or let expire (→ it runs autonomously). Durable so a
        scheduled offer window survives navigation / TTL eviction / restart.
        Non-destructive, idempotent (CREATE ... IF NOT EXISTS). See
        `docs/plans/TOURNAMENT_CIRCUIT_SURFACING.md`.
        """
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
                updated_at    TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tournament_invites_owner "
            "ON tournament_invites(owner_id, status)"
        )
        logger.info("Migration v144 complete: tournament_invites table created")

    def _migrate_v146_avatar_personality_id(self, conn: sqlite3.Connection) -> None:
        """Migration v146 (renumbered 137→146): re-key `avatar_images` on the stable `personality_id`.

        Avatars were the ONE identity surface keyed by the persona's *display
        name* (`personality_name`, e.g. "Napoleon") while everything else keys on
        the slug `personality_id` (e.g. "napoleon") — so a lookup by id (every
        tournament seat) missed and regenerated, and a persona rename orphaned its
        avatars. Add `personality_id` and backfill it by joining `personalities`
        on the (unique) display name. `personality_name` is retained as a legacy/
        debug column; the repo reads tolerate either key during the transition.

        Additive + idempotent (guarded ALTER, backfill only touches NULLs). Rows
        whose name doesn't match any persona (orphans) keep `personality_id` NULL
        and are simply unreachable by the new id-keyed path — logged, not deleted.

        NOTE: the name-join backfill is guarded on `personality_name` existing.
        Fresh DBs run every migration 1→N over the CURRENT `_init_db` schema, and
        as of v147 `_init_db` builds avatar_images WITHOUT `personality_name` —
        so on a fresh DB this backfill is a skipped no-op (personality_id is
        already the canonical key); on a real pre-v146 DB the column is present
        and the backfill runs."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(avatar_images)")}
        if 'personality_id' not in existing:
            conn.execute("ALTER TABLE avatar_images ADD COLUMN personality_id TEXT")
        # Backfill from the unique display-name join (only fill the NULLs) —
        # only when the legacy display-name column is actually present.
        if 'personality_name' in existing:
            conn.execute(
                """
                UPDATE avatar_images
                   SET personality_id = (
                       SELECT p.personality_id FROM personalities p
                        WHERE p.name = avatar_images.personality_name
                   )
                 WHERE personality_id IS NULL
                """
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_avatar_pid ON avatar_images(personality_id)")
        orphans = conn.execute(
            "SELECT COUNT(*) FROM avatar_images WHERE personality_id IS NULL"
        ).fetchone()[0]
        logger.info(
            "Migration v146 complete: avatar_images re-keyed on personality_id "
            "(%d row(s) left unmatched/orphan, reachable only by legacy name)",
            orphans,
        )

    def _migrate_v147_avatar_drop_personality_name(self, conn: sqlite3.Connection) -> None:
        """Migration v147 (renumbered 138→147): make `personality_id` the SOLE key
        of avatar_images and drop the legacy `personality_name` column.

        v146 added `personality_id` + backfilled it but kept `personality_name`
        (and dual-key reads) for a safe transition. This completes the cut: rebuild
        the table keyed on `personality_id` (NOT NULL, UNIQUE(personality_id,
        emotion)), dropping the name column and its index. Rows with a NULL
        `personality_id` (orphans the v137 name-join couldn't resolve) are DROPPED
        — they were already unreachable by the id-keyed path.

        SQLite can't drop a column that's part of a UNIQUE constraint in-place, so
        this is the standard 12-step table rebuild. Idempotent: if
        `personality_name` is already gone (fresh DB created by `_init_db` in the
        new shape, or a re-run), it's a no-op."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(avatar_images)")}
        if 'personality_name' not in cols:
            return  # already in the new shape — nothing to do

        dropped = conn.execute(
            "SELECT COUNT(*) FROM avatar_images "
            "WHERE personality_id IS NULL OR personality_id = ''"
        ).fetchone()[0]

        conn.execute("""
            CREATE TABLE avatar_images_v147 (
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
        # Copy only rows that resolved to a real personality_id. INSERT OR IGNORE
        # guards the (unlikely) case of two legacy name rows that backfilled to the
        # same pid+emotion — keep the first, drop the dup.
        conn.execute("""
            INSERT OR IGNORE INTO avatar_images_v147
                (personality_id, emotion, image_data, content_type, width, height,
                 file_size, full_image_data, full_width, full_height, full_file_size,
                 created_at, updated_at)
            SELECT personality_id, emotion, image_data, content_type, width, height,
                   file_size, full_image_data, full_width, full_height, full_file_size,
                   created_at, updated_at
            FROM avatar_images
            WHERE personality_id IS NOT NULL AND personality_id != ''
        """)
        conn.execute("DROP TABLE avatar_images")
        conn.execute("ALTER TABLE avatar_images_v147 RENAME TO avatar_images")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_avatar_pid ON avatar_images(personality_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_avatar_emotion ON avatar_images(emotion)")
        logger.info(
            "Migration v147 complete: avatar_images keyed solely on personality_id "
            "(dropped personality_name column + %d orphan row(s) with no pid)",
            dropped,
        )

    def _migrate_v148_invite_reserved_pids(self, conn: sqlite3.Connection) -> None:
        """Migration v148: add reserved_pids + vacated_pids to tournament_invites
        (tournaments-as-a-draw). The draw-selected field locked at offer time
        (JSON array of personality_ids) and the subset that has vacated cash en
        route. Additive nullable columns — existing offers read NULL (no
        draw-selected field → random shuffle at spawn, unchanged). Guarded so
        re-running / a fresh DB (column already present from `_init_db`) is a
        no-op."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tournament_invites)")}
        if "reserved_pids" not in cols:
            conn.execute("ALTER TABLE tournament_invites ADD COLUMN reserved_pids TEXT")
        if "vacated_pids" not in cols:
            conn.execute("ALTER TABLE tournament_invites ADD COLUMN vacated_pids TEXT")
        logger.info("Migration v148 complete: tournament_invites reserved_pids/vacated_pids added")

    def _migrate_v145_one_open_invite_per_owner(self, conn: sqlite3.Connection) -> None:
        """Migration v145 (renumbered 136→145): structurally enforce one open invite per owner.

        A partial UNIQUE index over `owner_id` WHERE status = 'offered' makes a
        second OPEN invite for the same owner unrepresentable — backing the
        application-level guard in `tournament_invites.offer()` against a
        cross-worker race (the in-memory sandbox lock doesn't span gunicorn
        workers). Non-'offered' rows are excluded, so any number of resolved
        invites coexist (`last_created_at()` walks invite history).

        DEFENSIVE pre-step: collapse any pre-existing duplicate open invites (keep
        the newest by created_at, expire the rest) so the index creation can never
        fail with UNIQUE constraint on a live DB. The app already prevents dupes,
        so this is a no-op on clean data. Non-destructive, idempotent.
        """
        # Keep the newest 'offered' invite per owner; expire any older duplicates.
        conn.execute("""
            UPDATE tournament_invites
               SET status = 'expired', updated_at = CURRENT_TIMESTAMP
             WHERE status = 'offered'
               AND invite_id NOT IN (
                   SELECT invite_id FROM (
                       SELECT invite_id,
                              ROW_NUMBER() OVER (
                                  PARTITION BY owner_id
                                  ORDER BY created_at DESC, rowid DESC
                              ) AS rn
                         FROM tournament_invites
                        WHERE status = 'offered'
                   )
                   WHERE rn = 1
               )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tournament_invites_one_open "
            "ON tournament_invites(owner_id) WHERE status = 'offered'"
        )
        logger.info("Migration v145 complete: one-open-invite-per-owner partial unique index")

    def _migrate_v136_drop_4d_emotion(self, conn: sqlite3.Connection) -> None:
        """Migration v136: retire the deprecated 4D emotion model.

        The dimensional model (valence/arousal/control/focus) was superseded by
        the quadrant model (confidence x composure -> EmotionalQuadrant, then a
        trait-aware family x quadrant matrix picks the display emotion). The 4D
        scalars had no live readers — they were write-only analytics plus a dead
        table — so this removal is destructive but behaviourally inert.

        Two parts:
          1. DROP the `emotional_state` table (never read or written in the
             current codebase).
          2. DROP valence/arousal/control/focus from `player_decision_analysis`.

        SQLite 3.35+ supports `ALTER TABLE ... DROP COLUMN`; each DROP is
        PRAGMA-guarded so re-running (or hitting a fresh DB that already landed
        on the post-v136 shape via `_init_db`) is a no-op. Mirrors the v99
        `active_loan_*` drop.
        """
        conn.execute("DROP TABLE IF EXISTS emotional_state")

        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        cols = {row[1] for row in cursor}
        for col in ("valence", "arousal", "control", "focus"):
            if col in cols:
                conn.execute(f"ALTER TABLE player_decision_analysis DROP COLUMN {col}")
        logger.info(
            "Migration v136 complete: dropped emotional_state table and 4D "
            "(valence/arousal/control/focus) columns from player_decision_analysis"
        )

    def _migrate_v130_add_preflop_node_key(self, conn: sqlite3.Connection) -> None:
        """Migration v130: add `preflop_node_key` to player_decision_analysis.

        The exact solver-chart node — ``scenario|position|opener|hand`` — captured
        at decision time (via the tiered bot's `build_preflop_node`) so the
        chart-graded coach leak finder can grade against the precise spot,
        including the exact opener and `vs_3bet` scenarios that backfill
        reconstruction can only approximate. Nullable; old rows fall back to
        reconstruction. Non-destructive, idempotent.

        Renumbered from v123 on the training-room→development merge (collision:
        circulating took v123 on development).
        """
        cursor = conn.execute("PRAGMA table_info(player_decision_analysis)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'preflop_node_key' not in columns:
            conn.execute("ALTER TABLE player_decision_analysis ADD COLUMN preflop_node_key TEXT")
            logger.info("Added preflop_node_key column to player_decision_analysis")
        logger.info("Migration v130 complete: preflop_node_key added")

    def _migrate_v131_create_coach_tips(self, conn: sqlite3.Connection) -> None:
        """Migration v131: create `coach_tips` — proactive in-decision tip log.

        One row per proactive coach tip actually served to the player. Records
        the spot (game/hand/phase/position) and, when a recurring chart leak was
        recalled in that moment, which leak nudge fired (scenario/position/kind/
        status/granularity). Joins to `player_decision_analysis` on
        (game_id, hand_number, player_name, PRE_FLOP) so we can measure whether a
        leak nudge actually moved the player's next decision toward the solver
        line — the measurement prerequisite for "is the coach helping?".

        Pure instrumentation: nothing here feeds AI decisions. Non-destructive,
        idempotent.

        Renumbered from v124 on the training-room→development merge (collision:
        opponent_observation_lifetime took v124 on development).
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coach_tips (
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
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_coach_tips_join
                ON coach_tips(game_id, hand_number, player_name)
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_coach_tips_owner ON coach_tips(owner_id)")
        logger.info("Migration v131 complete: coach_tips table created")

    def _migrate_v132_add_limp_lifetime_count(self, conn: sqlite3.Connection) -> None:
        """Migration v132: add `limp_count` to `opponent_observation_lifetime`.

        The numerator for `OpponentTendencies.limp_rate` — how often an
        opponent limps preflop (voluntarily CALLS in an open spot, i.e. with
        no live raise above the blind in front of them). The denominator,
        `preflop_open_opportunities`, was already added (v127-era), so the
        rate `limp_count / preflop_open_opportunities` derives on read through
        the canonical `OpponentTendencies._recalculate_stats()` — same
        store-counts-derive-rates principle as v126/v127. A single additive
        column.

        Guarded ALTER, additive, idempotent.
        """
        new_columns = ['limp_count']
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)").fetchall()
        }
        for col in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE opponent_observation_lifetime "
                    f"ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )

        logger.info(
            "Migration v132 complete: %d column(s) added to " "opponent_observation_lifetime",
            len(new_columns),
        )

    def _migrate_v133_add_sizing_aware_lifetime_counts(self, conn: sqlite3.Connection) -> None:
        """Migration v133: persist the sizing-aware counters/sums.

        The sizing tells were tracked live on `OpponentTendencies` but never
        folded into the durable store, so they reset every game. This adds the
        raw counts (and the big/small equity SUMS) so they accumulate
        cross-game and the two derived reads come back on read through the
        canonical `OpponentTendencies`:

          - `sizing_polarization_score` = equity_when_betting_big −
            equity_when_betting_small (bets bigger with stronger hands), from
            the big/small equity sum+count bins.
          - `fold_to_big_bet` = fold_to_big_bet_count / big_bet_faced_count
            (over-folds to large/jam bets).

        Same store-counts-derive-rates principle as v126. Guarded ALTER,
        additive, idempotent.
        """
        # (column, sql_type) — integer counts, then the REAL equity sums.
        new_columns = [
            ('equity_betting_big_count', 'INTEGER'),
            ('equity_betting_small_count', 'INTEGER'),
            ('fold_to_big_bet_count', 'INTEGER'),
            ('big_bet_faced_count', 'INTEGER'),
            ('equity_betting_big_sum', 'REAL'),
            ('equity_betting_small_sum', 'REAL'),
        ]
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)").fetchall()
        }
        for col, sql_type in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE opponent_observation_lifetime "
                    f"ADD COLUMN {col} {sql_type} NOT NULL DEFAULT 0"
                )

        logger.info(
            "Migration v133 complete: %d sizing-aware column(s) added to "
            "opponent_observation_lifetime",
            len(new_columns),
        )

    def _migrate_v134_add_postflop_axis_lifetime_counts(self, conn: sqlite3.Connection) -> None:
        """Migration v134: persist the postflop aggression-axis counters.

        These four counters were tracked live on `OpponentTendencies` but never
        folded, so the two derived reads reset every game. Persisting the raw
        counts lets them accumulate cross-game and the rates come back on read
        through the canonical `OpponentTendencies._recalculate_postflop_stats`:

          - `all_in_per_facing_bet` = all_ins_facing_bet / facing_bet_opportunities
            (response aggression — jams into a bet).
          - `postflop_jam_open_rate` = postflop_jam_opens / postflop_open_opportunities
            (open aggression — donk-jams a no-bet pot).

        Player/coach-facing read only: the live exploitation clamp reads the
        per-game model, not this store, so AI behavior is unchanged. Same
        store-counts-derive-rates principle as v126. Guarded ALTER, additive,
        idempotent.
        """
        new_columns = [
            'facing_bet_opportunities',
            'all_ins_facing_bet',
            'postflop_open_opportunities',
            'postflop_jam_opens',
        ]
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)").fetchall()
        }
        for col in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE opponent_observation_lifetime "
                    f"ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )

        logger.info(
            "Migration v134 complete: %d postflop-axis column(s) added to "
            "opponent_observation_lifetime",
            len(new_columns),
        )

    def _migrate_v135_add_flop_check_barrel_lifetime_counts(self, conn: sqlite3.Connection) -> None:
        """Migration v135: persist the flop-check-then-barrel counters.

        `flop_check_then_barrel_rate` (checks flop OOP, then bets turn after a
        check-through — a delayed-cbet / trap pattern) was tracked live but
        never folded, so it reset every game. Persisting the count +
        opportunity lets it accumulate cross-game and derive on read through
        the canonical `OpponentTendencies._recalculate_stats`. The two counters
        (and the rate) were added to `_SERIAL_FIELDS` in the same change so
        they serialize per-game and the fold can read them.

        Player/coach-facing read only. Same store-counts-derive-rates principle
        as v126. Guarded ALTER, additive, idempotent.
        """
        new_columns = [
            'flop_check_barrel_count',
            'flop_check_barrel_opportunity_count',
        ]
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(opponent_observation_lifetime)").fetchall()
        }
        for col in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE opponent_observation_lifetime "
                    f"ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )

        logger.info(
            "Migration v135 complete: %d flop-check-barrel column(s) added to "
            "opponent_observation_lifetime",
            len(new_columns),
        )

    def _migrate_v137_create_cash_scalps(self, conn: sqlite3.Connection) -> None:
        """Migration v137: create `cash_scalps` — durable attributed bust counter.

        A sandbox-scoped cumulative count of eliminations, keyed per
        (eliminator, victim) pair so renown-weighting can read the *victim's*
        standing (busting a legend ≫ a nobody) rather than just a flat count.

        Ids are raw (no `player:`/`ai:` prefix), mirroring `cash_pair_stats`:
        `owner_id` for the human, `personality_id` for AIs. AI-symmetric (the
        eliminator may be an AI) and forward-only (counts start at 0; nothing
        backfilled). Populated by `cash_mode/scalps.py` attribution off the
        world-sim and the human's hand. Non-destructive, idempotent.

        Renumbered from v132 on the renown→development merge (development
        reached v136 first). See docs/plans/CASH_MODE_SCALP_TRACKER.md.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cash_scalps (
                sandbox_id     TEXT NOT NULL,
                eliminator_id  TEXT NOT NULL,
                victim_id      TEXT NOT NULL,
                count          INTEGER NOT NULL DEFAULT 0,
                last_at        TIMESTAMP,
                PRIMARY KEY (sandbox_id, eliminator_id, victim_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_scalps_eliminator
                ON cash_scalps(sandbox_id, eliminator_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cash_scalps_victim
                ON cash_scalps(sandbox_id, victim_id)
        """)
        logger.info("Migration v137 complete: cash_scalps table created")

    def _migrate_v138_add_prestige_v2_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v138: extend `prestige_snapshots` with the Renown-v2 columns.

        v1 persisted a CAPPED [0,1] renown with a fixed absolute quadrant cut
        (0.40). v2 is **uncapped, concave, and field-relative** — it scores the
        human against the whole field and classifies the quadrant by a
        self-scaling cut (`max(top-X%, k×median)`) instead of a constant. The
        validated v2 math already lives in `cash_mode/prestige.py`
        (`score_renown_field` et al.) behind the default-OFF `RENOWN_V2_ENABLED`
        flag; this migration gives it somewhere to land.

        The columns are ADDITIVE and nullable/defaulted so the v1 columns stay
        the stable baseline:
          - `formula_version` — which formula wrote the CONSUMED columns
            (`quadrant`, etc.) for this row: 'v1' (absolute) or 'v2' (relative).
            The 4 reputation hooks + the lobby read the `quadrant` STRING
            unchanged; `formula_version` only tells the panel which gauge to
            render. Existing rows default to 'v1'.
          - `renown_v2` — the uncapped lifetime renown points (NULL on v1 rows).
            Ratchets via its own `MAX(renown_v2)` peak load (the v1 `renown`
            column keeps its own independent ratchet; the two scales never mix).
          - `victim_percentile` — the human's own field renown percentile [0,1].
          - `high_cut` — the field-wide high-renown cut at capture time (same
            for every entity that cycle; persisted so the panel can show the
            gap to "figure" status).
          - `renown_v2_components` — JSON of the v2 driver breakdown (scalps,
            top1, peak_worth, backing, legendary, tenure, breadth, stakes,
            apex), the v2 analogue of the renown_*/regard_* columns.
          - `field_size` — entities scored that cycle (context for the panel and
            for debugging the relative cut).

        Every ALTER is PRAGMA-guarded so the migration is safe on a partially
        applied DB. Non-destructive, idempotent. Renown stays a read-only
        scoreboard — nothing here feeds AI decision thresholds. Renumbered from
        v133 on the renown→development merge. See
        docs/plans/CASH_MODE_PLAYER_PRESTIGE.md (Renown v2) and
        docs/plans/RENOWN_V2_HANDOFF.md.
        """
        # (column, sql_type, default_clause) — all nullable except the version
        # tag, which defaults to 'v1' so historical rows read as the old formula.
        new_columns = [
            ("formula_version", "TEXT", "NOT NULL DEFAULT 'v1'"),
            ("renown_v2", "REAL", ""),
            ("victim_percentile", "REAL", ""),
            ("high_cut", "REAL", ""),
            ("renown_v2_components", "TEXT", ""),
            ("field_size", "INTEGER", ""),
        ]
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(prestige_snapshots)").fetchall()
        }
        added = 0
        for col, sql_type, default_clause in new_columns:
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE prestige_snapshots "
                    f"ADD COLUMN {col} {sql_type} {default_clause}".rstrip()
                )
                added += 1
        logger.info(
            "Migration v138 complete: %d Renown-v2 column(s) added to " "prestige_snapshots",
            added,
        )

    def _migrate_v139_add_prestige_entity_kind(self, conn: sqlite3.Connection) -> None:
        """Migration v139: give `prestige_snapshots` an entity identity.

        Until now the table held only the human (keyed `(sandbox_id, owner_id)`,
        `owner_id` always the human user). To persist a **field-relative renown
        for every AI** (the field scorer already computes it each cycle and
        throws it away), we treat `owner_id` as the **universal subject id** —
        the human's `owner_id` *or* an AI's raw `personality_id`, matching the
        raw-id scheme `RenownFieldRepository`/`cash_scalps` already use — and add
        one discriminator column:

          - `entity_kind` TEXT NOT NULL DEFAULT 'player' — 'player' | 'ai'.

        Existing rows default to 'player', so every current
        `load_latest(sandbox, owner)` keeps returning exactly the human's rows
        (AI rows carry 'ai' and a distinct `owner_id`). The invariant the repo
        and tests enforce: **`owner_id` is the subject, `entity_kind`
        disambiguates** — never set an AI row's `owner_id` to the sandbox owner,
        or the human's `load_latest` would start matching AI rows.

        Also adds `idx_prestige_snap_kind(sandbox_id, entity_kind, owner_id,
        renown_v2)` to serve the batched per-AI v2-peak GROUP BY and a future
        leaderboard read.

        Additive, PRAGMA-guarded, idempotent. Non-destructive. See
        docs/plans/RENOWN_V2_AI_WIRING_PLAN.md (Stage A).
        """
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(prestige_snapshots)").fetchall()
        }
        added = 0
        if "entity_kind" not in existing:
            conn.execute(
                "ALTER TABLE prestige_snapshots "
                "ADD COLUMN entity_kind TEXT NOT NULL DEFAULT 'player'"
            )
            added += 1
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_prestige_snap_kind
                ON prestige_snapshots(sandbox_id, entity_kind, owner_id, renown_v2)
        """)
        logger.info(
            "Migration v139 complete: %d entity-kind column(s) added to " "prestige_snapshots",
            added,
        )

    def _migrate_v140_add_holdings_peak_index(self, conn: sqlite3.Connection) -> None:
        """Migration v140: covering index for the Renown-v2 peak-net-worth read.

        The v2 field build (`RenownFieldRepository`) aggregates the per-entity
        peak net worth as `MAX(net_worth) GROUP BY entity_id` over
        `holdings_snapshots` — the field's largest table (a per-tick time
        series). With only `idx_holdings_snap_entity(sandbox_id, entity_id,
        captured_at)` the grouping is indexed but `net_worth` is fetched per row
        from the table, so on the real field it cost ~200ms (one random page
        read per snapshot row). This covering index puts `net_worth` in the
        index so `MAX` is answered index-only (a single seek per entity group),
        dropping it to ~tens of ms. The sibling presence read
        (`COUNT(DISTINCT captured_at)`) is already covered by the entity index;
        the time-at-#1 window read uses `idx_holdings_snap_scope`.

        Additive index, idempotent. No write-path semantics change — only one
        extra index maintained on holdings inserts (a periodic ticker write, not
        the per-hand path). See docs/plans/RENOWN_V2_AI_WIRING_PLAN.md (Stage A
        stress gate) and RENOWN_V2_HANDOFF.md.
        """
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_holdings_snap_peak
                ON holdings_snapshots(sandbox_id, entity_id, net_worth)
        """)
        logger.info("Migration v140 complete: idx_holdings_snap_peak created")
