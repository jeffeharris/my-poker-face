"""Cash mode routes — sit at a cash table, leave, top up.

Cash games are a **flavor of the tournament game flow**, not a separate
orchestrator. The action route, SocketIO emits, progress_game, hand
engine, AI controllers, settlement, and React UI are all reused
identically. Cash-specific behavior is gated by a `cash_mode=True`
flag on the game_data dict:

  - `handle_eliminations` and `check_tournament_complete` no-op
    because cash games have no `tournament_tracker`.
  - `progress_game` continues until the hand engine yields awaiting
    human input. Cash hands run forever until the player leaves.
  - Sit / leave / top-up between hands flows through the
    BankrollRepository.

This keeps cash-mode delta tiny: a route to set up a game with the
cash flag + bankroll accounting, and a route to tear it down.

Spec: docs/plans/CASH_MODE_AND_RELATIONSHIPS.md Part 2.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from cash_mode.bankroll import (
    AIBankrollState,
    PlayerBankrollState,
    credit_ai_cash_out,
    project_bankroll,
)
from cash_mode.sponsor_offers import (
    PersonalitySponsorOffer,
    compute_offers_for_table,
    LenderRejection,
    compute_personality_offers,
    offer_for_archetype,
)
from core.economy import ledger as chip_ledger
from poker.memory.relationship_events import RelationshipEvent
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_MATCH_SHARE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    STAKER_KIND_HUMAN,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from cash_mode.stakes_ladder import (
    MAX_BUY_IN_BB,
    MIN_BUY_IN_BB,
    STAKES_LADDER,
    STAKES_ORDER,
    is_sponsor_eligible,
    table_buy_in_window,
)
from cash_mode.table import PLAYER_SEAT_ID
from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

logger = logging.getLogger(__name__)

cash_bp = Blueprint("cash", __name__)


# --- Stakes ladder (matches spec §"Stakes ladder") ---

DEFAULT_PLAYER_STARTING_BANKROLL = 200
"""Seed bankroll for first-time entry to cash mode. Tight by design:
200 chips covers ~2.5 min buy-ins at the $2 table (80 chips each)
and nothing else, so the player gets a brief warm-up before busting
into the sponsor flow. Existing players keep whatever bankroll
they've already accrued — this value only applies on first /api/cash/*
hit when no player_bankroll_state row exists yet."""


def _resolve_owner_id() -> str:
    """Return a stable identifier for the current request's user.

    Uses `auth_manager.get_current_user()['id']` — same path tournament
    routes use, so cash session owner_id matches the user id that
    `_authorize_game_access` checks against. Raises ValueError if no
    current user is resolvable.
    """
    from flask_app.extensions import auth_manager
    user = auth_manager.get_current_user() if auth_manager else None
    if user and user.get("id"):
        return user["id"]
    raise ValueError("No owner_id resolvable from request")


def _resolve_sandbox_id(owner_id: str) -> str:
    """Resolve the owner's default sandbox_id.

    Phase 2.5: every cash route resolves a sandbox at entry and threads
    it through to repo + cash_mode calls. v1 ships 1:1 default sandbox
    per owner; the resolver auto-creates on first access and caches
    per-process for hot-path O(1) lookups.
    """
    from flask_app.extensions import sandbox_repo
    return resolve_default_sandbox_for(owner_id, sandbox_repo=sandbox_repo)


def _resolve_player_name() -> str:
    """Display name for the human player at the cash table."""
    from flask_app.extensions import auth_manager
    user = auth_manager.get_current_user() if auth_manager else None
    if user and user.get("name"):
        return user["name"]
    return "You"


def _find_active_cash_game_id(owner_id: str) -> Optional[str]:
    """Locate the owner's active cash game.

    First checks `game_state_service.games` (hot, in-memory). On a
    miss, falls back to the persisted `games` table — `progress_game`
    auto-saves cash sessions, so a Flask reload that wiped the
    in-memory copy still leaves the row behind. Returning the id lets
    `/api/game-state/<id>` cold-load the game back into memory with
    cash-mode flags restored. Matches the user's mental model of "back
    arrow = pause; come back to the table as if time had been frozen."

    The free-money exploit that this DB fallback used to enable
    (resuming a stale prior-session row after a clean leave, then
    cashing out a second time) is now blocked by the
    one-cash-row-per-owner invariant: `/api/cash/start` purges any
    existing cash-* row for the owner before creating a new one, and
    `/api/cash/leave` deletes the row. So after a clean leave, the DB
    has zero cash rows for this owner — nothing to resume.
    """
    from flask_app.services import game_state_service
    for gid, gdata in list(game_state_service.games.items()):
        if gdata.get("cash_mode") and gdata.get("owner_id") == owner_id:
            return gid

    from flask_app.extensions import game_repo
    try:
        rows = game_repo.list_games(owner_id=owner_id, limit=50, offset=0)
    except Exception:
        return None
    for row in rows:
        if row.game_id.startswith("cash-"):
            return row.game_id
    return None


def _resolve_emotion_from_blob(blob: str, personality_id: str) -> str:
    """Translate a persisted emotional_state_json blob into a display
    emotion string ("tilted", "confident", "nervous", etc.).

    Used by the lobby route to surface real sim psychology on
    unseated AI seats (schema v97 + full-sim Commit 3 flush
    discipline). Best-effort: any JSON / schema mismatch falls back
    to "confident" so a bad blob doesn't break the lobby render.

    Decay-on-read is a TODO — for v1 we trust the most recent flush
    because (a) sim hands run every lobby refresh, so live tilt
    naturally fades through gameplay, and (b) the catch-up burst
    (full-sim Commit 7) bursts hands when the player returns after
    a long gap, applying the same gameplay-driven decay.
    """
    try:
        import json as _json

        from flask_app.extensions import personality_repo as _personality_repo

        state_dict = _json.loads(blob)
        personality = _personality_repo.load_personality_by_id(personality_id)
        if personality is None:
            return "confident"
        from poker.player_psychology import PlayerPsychology
        psych = PlayerPsychology.from_dict(state_dict, personality)
        return psych.get_display_emotion()
    except Exception as exc:  # noqa: BLE001 — emotion is best-effort UX
        logger.debug(
            f"[CASH][LOBBY] emotion resolution failed for {personality_id}: {exc}"
        )
        return "confident"


def cleanup_orphan_cash_games() -> int:
    """**Deprecated** (v1.5): use `cash_mode.lobby.kill_all_cash_sessions`.

    Subsumed by the lobby boot hook. Kept temporarily in case external
    code calls it directly. Per handoff §"Locked decisions" (3): the
    v1.5 deploy kills every in-flight cash session at boot, then seeds
    the persistent lobby. The new pass is more aggressive (drops every
    cash-* row, not just per-owner duplicates) but safe because v1.5
    moves persistent table state to `cash_tables`, not `games`.

    Original docstring below:

    Enforce one cash session per owner; delete older duplicates.

    The user's mental model is "back arrow freezes the game, leave
    table cashes out." Persistence makes the frozen-game path survive
    Flask reloads — `_find_active_cash_game_id` falls back to the DB
    on a memory miss, and `/api/game-state/<id>` cold-loads with cash
    flags restored.

    Invariant we have to enforce on every entry point: at most one
    `cash-*` row per owner at a time. Otherwise a clean leave (which
    deletes only the current row) can still leave a *different* stale
    row that `_find_active_cash_game_id` surfaces as an "active
    session" — the original free-money exploit. Two enforcement
    points keep this tight:

      - `_purge_other_cash_rows` runs from `_build_cash_game` so a
        new sit-down nukes any leftover row for this owner before
        creating its own.
      - This boot-time pass keeps the **most recent** row per owner
        (it's the legit frozen session the player is expected to
        resume) and drops any older duplicates left over from prior
        unclean shutdowns or pre-fix data.

    Returns the count of rows deleted so the caller can log it.
    """
    from flask_app.extensions import game_repo
    try:
        rows = game_repo.list_games(owner_id=None, limit=1000, offset=0)
    except Exception as e:
        logger.warning("[CASH] orphan cleanup: list_games failed: %s", e)
        return 0

    # list_games already orders by updated_at DESC, so the first cash
    # row per owner is the freshest and stays; everything after is a
    # stale duplicate.
    seen_owners: set[str] = set()
    to_delete: list[str] = []
    for row in rows:
        if not row.game_id.startswith("cash-"):
            continue
        owner = row.owner_id or ""
        if owner in seen_owners:
            to_delete.append(row.game_id)
        else:
            seen_owners.add(owner)

    for gid in to_delete:
        try:
            game_repo.delete_game(gid)
        except Exception as e:
            logger.warning(
                "[CASH] orphan cleanup: delete_game(%r) failed: %s", gid, e,
            )
    if to_delete:
        logger.info(
            "[CASH] orphan cleanup: deleted %d stale duplicate cash row(s): %s",
            len(to_delete), to_delete,
        )
    return len(to_delete)


def _purge_other_cash_rows(owner_id: str, except_game_id: Optional[str] = None) -> int:
    """Delete every `cash-*` row this owner has (except the named one).

    The one-cash-row-per-owner invariant's enforcement at sit-down
    time. Called from `_build_cash_game` before registering the new
    game so a fresh sit always starts from a clean slate. Defense in
    depth against:
      - `/api/cash/leave` having silently failed its `delete_game`
        on a previous session.
      - Legacy rows from before persistence enforcement existed.
    Both leave behind orphan cash rows that would otherwise surface
    via `_find_active_cash_game_id`'s DB fallback and re-enable the
    free-money exploit.

    TODO (chip-ledger v0 finding): this delete path skips the
    leave/cash-out / settle pipeline that would normally fire the
    `ai_regen`, `cap_clamp`, and `house_stake_settle` ledger entries
    for the abandoned session. Net effect on the audit:
      * AI chips still on the orphan game's live table stack are
        lost from the universe — they should have credited back
        to the AI's persistent bankroll via `credit_ai_cash_out`.
      * Any house stake principal on the borrower's stack is also
        silently dropped instead of getting a `house_stake_settle`
        or `forgive_balance` entry.
    The audit will surface this as drift (positive — ledger has
    more outstanding than actual) the first time a stale row is
    purged after v94 ships. Pre-existing bug; resolves at next
    instrumentation pass for the orphan-cleanup path.
    """
    from flask_app.extensions import cash_session_repo, game_repo
    try:
        rows = game_repo.list_games(owner_id=owner_id, limit=50, offset=0)
    except Exception as e:
        logger.warning("[CASH] purge other rows failed for %r: %s", owner_id, e)
        return 0
    purged: list[str] = []
    for row in rows:
        if not row.game_id.startswith("cash-"):
            continue
        if row.game_id == except_game_id:
            continue
        try:
            game_repo.delete_game(row.game_id)
            purged.append(row.game_id)
        except Exception as e:
            logger.warning(
                "[CASH] purge: delete_game(%r) failed: %s", row.game_id, e,
            )
            continue
        # Also drop the orphaned cash_sessions row. Without this,
        # `load_active_for_owner` keeps surfacing the row (ended_at
        # IS NULL) and a subsequent sit-down for the same owner sees
        # two open sessions. Best-effort — a missing row is fine.
        if cash_session_repo is not None:
            try:
                cash_session_repo.delete(row.game_id)
            except Exception as e:
                logger.warning(
                    "[CASH] purge: cash_sessions delete(%r) failed: %s",
                    row.game_id, e,
                )
    if purged:
        logger.info(
            "[CASH] purged %d prior cash row(s) for owner=%r: %s",
            len(purged), owner_id, purged,
        )
    return len(purged)


def _free_ghost_human_seats(owner_id: str, *, sandbox_id: str) -> int:
    """Reset any cash_tables human seat owned by `owner_id` to open.

    Used by the memory-miss leave path and the boot reconcile to catch
    the case where the cash-* game row is gone (purged or deleted) but
    the persisted seat survives. Without this, the lobby renders the
    player as still seated at a vanished table and won't let them
    actually enter the (deleted) game.

    No chip refund — the persisted seat's `chips` field is the last
    hand-boundary sync, not the true exit stack. The bankroll already
    reflects the buy-in debit; the game's final settlement path
    (full leave_table_locked) is the only source of truth for refund,
    and we only fall back here when that path can't run.
    """
    from cash_mode.tables import open_slot
    from flask_app.extensions import cash_table_repo

    try:
        tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
    except Exception as e:
        logger.warning(
            "[CASH] _free_ghost_human_seats: list_all_tables failed: %s", e,
        )
        return 0

    freed = 0
    for table in tables:
        for idx, slot in enumerate(table.seats):
            if slot.get("kind") != "human":
                continue
            if slot.get("personality_id") != owner_id:
                continue
            try:
                cash_table_repo.save_table(
                    table.with_seat(idx, open_slot()),
                    sandbox_id=sandbox_id,
                )
                logger.info(
                    "[CASH] _free_ghost_human_seats: freed table=%r seat=%d owner=%r",
                    table.table_id, idx, owner_id,
                )
                freed += 1
            except Exception as e:
                logger.warning(
                    "[CASH] _free_ghost_human_seats: save_table failed "
                    "for %r:%d: %s",
                    table.table_id, idx, e,
                )
    return freed


def _build_preselected_from_table(
    *,
    claimed_table,
    seat_index: int,
    personality_repo,
) -> tuple[list, Dict[str, int], int]:
    """Walk a claimed `cash_tables` roster to build the args
    `_build_cash_game` expects for the lobby-v1.5 preselected path.

    Returns `(preselected_ai, preselected_chips, dealer_player_idx)`.
    Walks seats in rotation order starting after the human so AI
    player indices match clockwise table position from the human's
    POV. Maps the lobby's dealer seat to its player index so the
    in-game dealer button matches the lobby.

    Shared between `sit_at_table` (self-funded) and
    `sponsor_and_sit` (sponsored) so both paths produce a game
    populated with the AIs the lobby actually showed at the
    clicked table — the alternative is the legacy fresh-sample
    path, which silently swaps the lineup.
    """
    from cash_mode.tables import TABLE_SEAT_COUNT
    preselected_ai: list = []
    preselected_chips: Dict[str, int] = {}
    seats = claimed_table.seats
    lobby_dealer_seat = claimed_table.dealer_idx
    dealer_player_idx = 0  # human (player 0) is the default fallback
    next_player_idx = 1
    for offset in range(TABLE_SEAT_COUNT):
        seat_i = (seat_index + offset) % TABLE_SEAT_COUNT
        slot = seats[seat_i]
        if seat_i == lobby_dealer_seat:
            if seat_i == seat_index:
                dealer_player_idx = 0
            elif slot["kind"] == "ai":
                dealer_player_idx = next_player_idx
        if slot["kind"] != "ai":
            continue
        pid = slot["personality_id"]
        personality = None
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        name = (personality or {}).get("name") if personality else pid
        preselected_ai.append({"personality_id": pid, "name": name})
        preselected_chips[pid] = int(slot.get("chips", 0))
        next_player_idx += 1
    return preselected_ai, preselected_chips, dealer_player_idx


def _load_or_seed_player_bankroll(
    owner_id: str, *, sandbox_id: Optional[str] = None,
) -> PlayerBankrollState:
    """Load the player's bankroll row or create a fresh seed on miss.

    Centralizes the "first-time entry" path so every cash route lands
    the same seed amount and writes the row immediately. Subsequent
    routes can assume `load_player_bankroll` returns non-None.

    `sandbox_id` (Phase 2.5 v103) is stamped onto the `player_seed`
    ledger entry so per-sandbox audits attribute the seed correctly.
    Player bankroll is NOT itself sandbox-scoped (it spans the owner's
    save-files); we tag the seed event with the sandbox the player
    was entering when first seeded so per-sandbox audits don't lose
    the line item. Callers that don't have a sandbox_id in scope (rare
    — admin paths only) can omit it; the entry then writes NULL into
    the legacy bucket.
    """
    from flask_app.extensions import bankroll_repo, chip_ledger_repo, sandbox_repo
    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if bankroll is not None:
        return bankroll
    # First seed — resolve sandbox lazily if the caller didn't provide
    # one so the ledger entry is attributed correctly even when this
    # path is reached from a route that hasn't called the resolver yet.
    if sandbox_id is None and sandbox_repo is not None:
        try:
            from flask_app.services.sandbox_resolver import (
                resolve_default_sandbox_for,
            )
            sandbox_id = resolve_default_sandbox_for(
                owner_id, sandbox_repo=sandbox_repo,
            )
        except Exception:
            sandbox_id = None  # fall through; entry writes NULL
    bankroll = PlayerBankrollState(
        player_id=owner_id,
        chips=DEFAULT_PLAYER_STARTING_BANKROLL,
        starting_bankroll=DEFAULT_PLAYER_STARTING_BANKROLL,
    )
    bankroll_repo.save_player_bankroll(bankroll)
    chip_ledger.record_player_seed(
        chip_ledger_repo,
        owner_id=owner_id,
        amount=DEFAULT_PLAYER_STARTING_BANKROLL,
        context={'reason_detail': 'first_cash_entry', 'sandbox_id': sandbox_id},
        sandbox_id=sandbox_id,
    )
    logger.info("[CASH] Seeded fresh bankroll for %r at %d chips (sandbox=%r)",
                owner_id, DEFAULT_PLAYER_STARTING_BANKROLL, sandbox_id)
    return bankroll


def _record_cash_session_start(**kwargs) -> None:
    """Thin wrapper over `cash_mode.cash_session_persistence.record_cash_session_start`
    that pulls the repo singleton off `flask_app.extensions`. See the
    underlying function for argument semantics.
    """
    from cash_mode.cash_session_persistence import record_cash_session_start
    from flask_app.extensions import cash_session_repo
    record_cash_session_start(cash_session_repo=cash_session_repo, **kwargs)


def _increment_cash_session_buy_in(game_id: str, amount: int) -> None:
    """Thin wrapper — see `cash_mode.cash_session_persistence.increment_cash_session_buy_in`."""
    from cash_mode.cash_session_persistence import increment_cash_session_buy_in
    from flask_app.extensions import cash_session_repo
    increment_cash_session_buy_in(cash_session_repo, game_id, amount)


def _load_human_stake_or_404(stake_id: str, owner_id: str):
    """Load a stake by id, gated on borrower == this human owner.

    Returns `(stake, None)` on success or `(None, (response, status))`
    on rejection — caller bubbles the response back to the client.

    The 404-on-both-missing-and-other-owner pattern prevents stake-id
    enumeration: a probing client can't tell apart "doesn't exist"
    from "belongs to a different player." The borrower_kind guard
    keeps the player-initiated routes off the AI-borrower path that
    Phase 4 will introduce.

    Shared by `/default`, `/payoff`, and `/request-forgiveness` — when
    that guard's policy changes (e.g., Phase 4's AI-borrower handling
    lands), all three routes update at one edit.
    """
    from flask_app.extensions import stake_repo
    stake = stake_repo.load_stake(stake_id)
    if (
        stake is None
        or stake.borrower_id != owner_id
        or stake.borrower_kind != BORROWER_KIND_HUMAN
    ):
        return None, (jsonify({"error": "Stake not found"}), 404)
    return stake, None


def _resolve_player_tier_stake_label(owner_id: str, bankroll_chips: int) -> str:
    """Pick the stake label that drives the player's current tier.

    Priority:
      1. Active cash session's stake_label (the tier the player is
         actively playing).
      2. Highest stake the bankroll can self-afford (`>= min_buy_in`).
      3. Cheapest stake — so a busted player still gets a meaningful
         tier reading at $2 rather than an empty answer.

    Shared by `/api/cash/lobby` (top-level tier indicator) and
    `/api/cash/net-worth` (carry-cap denominator). Keeping the two
    surfaces aligned means the player can't see "tier X here, tier Y
    there" for the same playing context.
    """
    active_game_id = _find_active_cash_game_id(owner_id)
    if active_game_id:
        from flask_app.services import game_state_service
        active_game = game_state_service.get_game(active_game_id)
        if active_game:
            label = active_game.get("cash_stake_label")
            if label:
                return label
    for label in reversed(STAKES_ORDER):
        _, this_min, _ = table_buy_in_window(label)
        if bankroll_chips >= this_min:
            return label
    return STAKES_ORDER[0]


def _build_cash_game(
    *,
    owner_id: str,
    sandbox_id: str,
    stake_label: str,
    player_starting_stack: int,
    welcome_message: str,
    opponent_count: int = 5,
    now: Optional[datetime] = None,
    preselected_ai: Optional[list] = None,
    preselected_ai_chips: Optional[Dict[str, int]] = None,
    dealer_player_idx: int = 0,
) -> tuple[Optional[str], Optional[tuple[dict, int]]]:
    """Create + register a cash game; return (game_id, None) or (None, (err, status)).

    Pure game-setup — AI selection, state machine, controllers, memory
    manager, registration. Does NOT touch the player bankroll: the
    caller decides whether to debit (start path) or write loan fields
    (sponsor path). AI bankrolls ARE debited here — they're symmetric
    across both paths.

    `preselected_ai` / `preselected_ai_chips` (lobby v1.5): when the
    caller already has a roster (from the persisted `cash_tables`
    row), pass `preselected_ai=[{personality_id, name}, ...]` and
    `preselected_ai_chips={personality_id: chips}`. AI stacks come
    from the table's persisted chip counts rather than fresh
    randomization. Falls back to the legacy "pick fresh eligible"
    path when both are None — preserves `/api/cash/start` and
    `/api/cash/sponsor-and-sit` behavior.

    The error tuple is `(json_body, http_status)` so the caller can
    `return jsonify(err), status` directly.
    """
    if now is None:
        now = datetime.utcnow()

    # Enforce the one-cash-row-per-owner invariant before this owner
    # gets a new cash- row. Belt-and-suspenders for `/api/cash/leave`
    # cleanup failures and pre-fix legacy data.
    _purge_other_cash_rows(owner_id)

    big_blind, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    from flask_app.extensions import (
        bankroll_repo, game_repo, hand_history_repo, personality_repo,
        persistence_db_path, relationship_repo,
        capture_label_repo, decision_analysis_repo,
    )

    selected_ai: list = []
    ai_buy_ins: Dict[str, int] = {}
    ai_states: Dict[str, AIBankrollState] = {}
    # Pre-regen stored chip counts, per personality_id. Populated only
    # on the legacy fresh-sample path (lobby v1.5 skips bankroll
    # writes here); used to emit ai_regen ledger entries that match
    # the size of the regen that this write commits.
    ai_stored_pre_regen: Dict[str, int] = {}

    if preselected_ai is not None:
        # Lobby v1.5 path: AI roster + chip counts come from the
        # persisted table. AI bankrolls are NOT debited because the
        # chips are already "on the table" — the AI never returned
        # them to bankroll on a prior leave (Path A's leave-time
        # cash_out credits the final stack back; in v1.5, the chips
        # persist with the seat instead).
        chip_map = preselected_ai_chips or {}
        for entry in preselected_ai:
            pid = entry.get("personality_id")
            name = entry.get("name") or pid
            if not pid:
                continue
            chips = int(chip_map.get(pid, 0))
            if chips <= 0:
                # Shouldn't happen — a seated AI with zero chips is a
                # bug elsewhere. Skip to avoid bad game state.
                continue
            selected_ai.append({"personality_id": pid, "name": name})
            ai_buy_ins[pid] = chips
        if not selected_ai:
            return None, (
                {"error": "No AI players on the table to sit against"},
                503,
            )
    else:
        # Legacy path: pick fresh eligible personalities.
        eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
        for entry in eligible:
            if len(selected_ai) >= opponent_count:
                break
            pid = entry["personality_id"]
            name = entry["name"]
            knobs = bankroll_repo.load_personality_knobs(pid)
            ai_threshold = round(min_buy_in * knobs.buy_in_multiplier)
            ai_buy_in = min(ai_threshold, max_buy_in)

            stored = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
            if stored is None:
                projected = knobs.starting_bankroll
                stored = AIBankrollState(personality_id=pid, chips=projected, last_regen_tick=None)
            else:
                projected = project_bankroll(
                    stored, knobs.starting_bankroll, knobs.bankroll_rate, now,
                )
            if projected < ai_threshold:
                continue
            selected_ai.append({"personality_id": pid, "name": name})
            ai_buy_ins[pid] = ai_buy_in
            ai_states[pid] = AIBankrollState(
                personality_id=pid, chips=projected, last_regen_tick=stored.last_regen_tick,
            )
            # Stash stored.chips (pre-projection) so the eventual
            # save_ai_bankroll can record the regen amount that just
            # entered the universe.
            ai_stored_pre_regen[pid] = stored.chips

        if not selected_ai:
            return None, (
                {"error": "No eligible AI opponents available for this stake"},
                503,
            )

    # 2. Build the game state.
    from poker.poker_game import initialize_game_state
    from poker.poker_state_machine import PokerStateMachine
    from poker.memory import AIMemoryManager
    from poker.pressure_detector import PressureEventDetector
    from poker.pressure_stats import PressureStatsTracker
    from poker.repositories.sqlite_repositories import PressureEventRepository
    from poker.hybrid_ai_controller import HybridAIController
    from poker.controllers import AIPlayerController
    from poker.cash_bot_assignment import assign_bot
    from flask_app.handlers.tiered_factory import build_tiered_controller
    from flask_app.game_adapter import StateMachineAdapter
    from flask_app.routes.game_routes import generate_game_id, load_game_mode_preset

    human_name = _resolve_player_name()
    ai_names = [a["name"] for a in selected_ai]

    game_state = initialize_game_state(
        player_names=ai_names,
        human_name=human_name,
        starting_stack=player_starting_stack,
        big_blind=big_blind,
        dealer_idx=dealer_player_idx,
    )
    # AI stacks may differ from the human's starting stack; adjust each.
    for idx, player in enumerate(game_state.players):
        if player.is_human:
            continue
        ai_entry = next((a for a in selected_ai if a["name"] == player.name), None)
        if ai_entry is None:
            continue
        ai_buy_in = ai_buy_ins[ai_entry["personality_id"]]
        if ai_buy_in != player.stack:
            game_state = game_state.update_player(idx, stack=ai_buy_in)

    base_state_machine = PokerStateMachine(
        game_state=game_state,
        blind_config={"growth": 1.0, "hands_per_level": 999999, "max_blind": big_blind},
    )
    state_machine = StateMachineAdapter(base_state_machine)
    game_id = f"cash-{generate_game_id()}"

    # 3. AI controllers — sandbox bucketing per personality.
    #
    # Each personality gets a sticky (bot_type, llm_config) from
    # `assign_bot`: explicit `config_json.bot_profile` first, then
    # poise-quantile fallback (sharp / standard / chaos). Tournament
    # mode lets the user pick per seat; cash mode hides the knob and
    # lets character anchors drive it.
    default_prompt_config = load_game_mode_preset("standard")
    default_llm_config: Dict[str, Any] = {}
    ai_controllers: Dict[str, Any] = {}
    bot_types: Dict[str, str] = {}
    player_llm_configs: Dict[str, Dict[str, Any]] = {}
    for player in state_machine.game_state.players:
        if player.is_human:
            continue
        ai_entry = next((a for a in selected_ai if a["name"] == player.name), None)
        pid = ai_entry["personality_id"] if ai_entry else None
        personality_config = (
            personality_repo.load_personality_by_id(pid) if pid else None
        )
        assignment = assign_bot(personality_config)
        bot_types[player.name] = assignment.bot_type
        player_llm_configs[player.name] = assignment.llm_config

        if assignment.bot_type == "chaos":
            controller = AIPlayerController(
                player_name=player.name,
                state_machine=state_machine,
                llm_config=assignment.llm_config,
                prompt_config=default_prompt_config,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo,
                decision_analysis_repo=decision_analysis_repo,
            )
        elif assignment.bot_type == "sharp":
            controller = build_tiered_controller(
                player_name=player.name,
                state_machine=state_machine,
                llm_config=assignment.llm_config,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo,
                decision_analysis_repo=decision_analysis_repo,
                expression_enabled=True,
            )
        else:
            controller = HybridAIController(
                player.name,
                state_machine,
                llm_config=assignment.llm_config,
                prompt_config=default_prompt_config,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo,
                decision_analysis_repo=decision_analysis_repo,
            )
        ai_controllers[player.name] = controller

    # 4. Memory manager (cash_mode=True wires Phase 3 cash_pair_stats).
    pressure_event_repo = PressureEventRepository(persistence_db_path)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, pressure_event_repo)

    memory_manager = AIMemoryManager(game_id, persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(hand_history_repo)
    memory_manager.set_relationship_repo(
        relationship_repo, cash_mode=True, sandbox_id=sandbox_id,
    )
    for player in state_machine.game_state.players:
        try:
            pid = personality_repo.resolve_name_to_personality_id(player.name)
        except Exception:
            pid = None
        if not player.is_human:
            memory_manager.initialize_for_player(player.name, personality_id=pid)
            controller = ai_controllers[player.name]
            controller.session_memory = memory_manager.get_session_memory(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
            controller.memory_manager = memory_manager
        else:
            # Register the human with their stable `owner_id` so per-hand
            # BIG_WIN/BIG_LOSS events write to (owner_id, ai_pid) rows —
            # the same key the loan flow and the dossier read use.
            # Without this, hand-flow events fall back to display_name,
            # creating an unreachable parallel history under the wrong
            # observer id.
            memory_manager.initialize_human_observer(player.name, personality_id=owner_id)

    # 5. Advance to first action so hole cards are dealt before recording.
    state_machine.run_until_player_action()
    memory_manager.on_hand_start(
        state_machine.game_state,
        hand_number=1,
        deck_seed=state_machine.current_hand_seed,
    )

    # 6. Debit AI bankrolls.
    # Only fires for the legacy "fresh sample" path. Lobby v1.5 sits
    # use the persisted table chips, which already represent chips
    # off-bankroll, so debiting here would double-charge the AI.
    from flask_app.extensions import chip_ledger_repo
    for pid, state in ai_states.items():
        debited = AIBankrollState(
            personality_id=pid,
            chips=state.chips - ai_buy_ins[pid],
            last_regen_tick=now,
        )
        bankroll_repo.save_ai_bankroll(debited, sandbox_id=sandbox_id)
        # Regen that this write commits = projected (state.chips) -
        # pre-regen stored. The transfer-to-table-stack portion is a
        # pure non-bank move and isn't ledger-worthy in v0.
        chip_ledger.record_ai_regen(
            chip_ledger_repo,
            personality_id=pid,
            stored_chips=ai_stored_pre_regen.get(pid, state.chips),
            projected_chips=state.chips,
            context={
                'game_id': game_id,
                'stake_label': stake_label,
                'site': 'sit_down_debit',
                'sandbox_id': sandbox_id,
            },
            sandbox_id=sandbox_id,
        )

    # 7. Register with game_state_service.
    game_data = {
        "state_machine": state_machine,
        "ai_controllers": ai_controllers,
        "pressure_detector": pressure_detector,
        "pressure_stats": pressure_stats,
        "memory_manager": memory_manager,
        "owner_id": owner_id,
        "sandbox_id": sandbox_id,
        "owner_name": human_name,
        "llm_config": default_llm_config,
        "player_llm_configs": player_llm_configs,
        "player_prompt_configs": {},
        "bot_types": bot_types,
        "default_game_mode": "standard",
        "last_announced_phase": None,
        "guest_tracking_id": None,
        "guest_messages_this_action": 0,
        "messages": [{
            "id": "1",
            "sender": "Table",
            "content": welcome_message,
            "timestamp": datetime.now().isoformat(),
            "type": "table",
        }],
        "hand_start_stacks": {
            p.name: p.stack for p in state_machine.game_state.players
        },
        "short_stack_players": set(),
        "cash_mode": True,
        "cash_stake_label": stake_label,
        "cash_personality_ids": {a["name"]: a["personality_id"] for a in selected_ai},
        "cash_buy_in": int(player_starting_stack),
        "cash_started_at": now.isoformat(),
    }

    from flask_app.services import game_state_service
    game_state_service.set_game(game_id, game_data)

    # Persist `llm_configs_json` so cold-load (post-reboot, post-eviction)
    # restores each AI to its assigned bot_type. Without this the column
    # stays NULL and `restore_ai_controllers` defaults every seat to
    # `standard` (HybridAIController) — silently downgrading `sharp`
    # personalities (tiered solver + expression) on the next reboot.
    # Mirrors the tournament path in game_routes.py:1314.
    saved_bot_types = dict(bot_types)
    for player in state_machine.game_state.players:
        if not player.is_human:
            saved_bot_types.setdefault(player.name, "standard")
    game_repo.save_game(
        game_id, state_machine._state_machine, owner_id, human_name,
        llm_configs={
            "player_llm_configs": player_llm_configs,
            "default_llm_config": default_llm_config,
            "bot_types": saved_bot_types,
        },
    )

    logger.info("[CASH] Created game_id=%r owner=%r stake=%r player_stack=%d ai=%r bot_types=%r",
                game_id, owner_id, stake_label, player_starting_stack,
                [a["name"] for a in selected_ai], saved_bot_types)
    return game_id, None


# --- Routes ---


@cash_bp.route("/api/cash/start", methods=["POST"])
def start_cash_session():
    """POST /api/cash/start  body: {stake_label, buy_in, opponents?}

    Creates a tournament-style game with `cash_mode=True` flagging on
    game_data. The standard tournament flow drives the rest — same
    state machine, same controllers, same UI, same action route.

    Bankroll accounting:
      - Player bankroll debited by buy_in at sit-down.
      - Each AI's bankroll debited by their per-personality buy-in.
      - All amounts persist via `BankrollRepository`.

    Returns the game_id; frontend navigates to /game/<game_id>.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    payload = request.get_json(silent=True) or {}
    stake_label = payload.get("stake_label")
    buy_in = payload.get("buy_in")
    opponent_count = int(payload.get("opponents", 5))

    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": "Invalid stake_label",
            "valid_stakes": list(STAKES_LADDER.keys()),
        }), 400
    if not isinstance(buy_in, int) or buy_in <= 0:
        return jsonify({"error": "buy_in must be a positive integer"}), 400

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
    if buy_in < min_buy_in or buy_in > max_buy_in:
        return jsonify({
            "error": (
                f"buy_in {buy_in} out of range for {stake_label} table "
                f"(min={min_buy_in}, max={max_buy_in})"
            ),
        }), 400

    # Block duplicate sessions: one cash game per owner at a time.
    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify({
            "error": "A cash session is already active. Leave first.",
            "game_id": existing,
        }), 409

    from flask_app.extensions import bankroll_repo

    # Player bankroll: load or seed; verify affordability.
    player_bankroll = _load_or_seed_player_bankroll(owner_id)
    if player_bankroll.chips < buy_in:
        return jsonify({
            "error": (
                f"Insufficient bankroll: {player_bankroll.chips} chips, "
                f"buy_in {buy_in}"
            ),
        }), 400

    # Build + register the game (AI selection, controllers, memory manager).
    game_id, err = _build_cash_game(
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        player_starting_stack=buy_in,
        welcome_message=f"*** Cash table {stake_label} — sit down at ${buy_in} ***",
        opponent_count=opponent_count,
    )
    if err is not None:
        return jsonify(err[0]), err[1]

    # Debit the player's bankroll. Loan fields stay zeroed — this is
    # the self-funded path.
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=player_bankroll.player_id,
        chips=player_bankroll.chips - buy_in,
        starting_bankroll=player_bankroll.starting_bankroll,
    ))

    _record_cash_session_start(
        game_id=game_id,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        initial_buy_in=buy_in,
    )

    return jsonify({"game_id": game_id})


@cash_bp.route("/api/cash/sit", methods=["POST"])
def sit_at_table():
    """POST /api/cash/sit  body: {table_id, seat_index, buy_in?}

    Lobby v1.5 sit-down — replaces `/api/cash/start`. The player taps
    an open seat in the lobby; the route validates that the seat is
    open on the persisted table, that they can afford it, and that
    they have no active session. On success, the persisted table is
    mutated to mark the seat `"human"` (so concurrent reads see the
    sit), then a cash game is built using the table's CURRENT AI
    roster + persisted chip counts (no fresh sample).

    `buy_in` is optional; when omitted, defaults to the table's
    `min_buy_in`. Must lie in `[min_buy_in, max_buy_in]` if provided.

    Returns:
      - 200 `{game_id, table_id, seat_index}` on success.
      - 402 `{requires_sponsor: True, ...}` when bankroll <
        min_buy_in but sponsor-eligible at this stake. Frontend opens
        SponsorModal.
      - 400 on invalid input / unaffordable / not sponsor-eligible.
      - 404 if `table_id` doesn't exist.
      - 409 if the seat is taken or the player has an active session.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    payload = request.get_json(silent=True) or {}
    table_id = payload.get("table_id")
    seat_index = payload.get("seat_index")
    buy_in = payload.get("buy_in")

    if not isinstance(table_id, str) or not table_id:
        return jsonify({"error": "table_id is required"}), 400
    if not isinstance(seat_index, int) or seat_index < 0:
        return jsonify({"error": "seat_index must be a non-negative integer"}), 400

    from flask_app.extensions import bankroll_repo, cash_table_repo

    table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
    if table is None:
        return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404

    if seat_index >= len(table.seats):
        return jsonify({"error": "seat_index out of range"}), 400
    target_slot = table.seats[seat_index]
    if target_slot["kind"] != "open":
        return jsonify({
            "error": "Seat is not open",
            "seat_kind": target_slot["kind"],
        }), 409

    stake_label = table.stake_label
    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": f"Table has invalid stake_label {stake_label!r}",
        }), 500
    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    if buy_in is None:
        buy_in = min_buy_in
    if not isinstance(buy_in, int) or buy_in <= 0:
        return jsonify({"error": "buy_in must be a positive integer"}), 400
    if buy_in < min_buy_in or buy_in > max_buy_in:
        return jsonify({
            "error": (
                f"buy_in {buy_in} out of range for {stake_label} table "
                f"(min={min_buy_in}, max={max_buy_in})"
            ),
        }), 400

    # Block duplicate sessions: one cash game per owner at a time.
    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify({
            "error": "A cash session is already active. Leave first.",
            "game_id": existing,
        }), 409

    # Belt-and-suspenders against orphaned seats: the duplicate-session
    # check above guards against duplicate game rows, but a stale human
    # slot on `cash_tables` (left over after a leave path skipped its
    # seat revert, or a purge that cleaned the game row without freeing
    # the seat) won't show up there. Sweep any seats this owner is
    # still occupying before claiming the new one — otherwise the
    # `with_seat` below succeeds and the user double-seats.
    _free_ghost_human_seats(owner_id, sandbox_id=sandbox_id)

    # Affordability + sponsor-eligibility branching.
    player_bankroll = _load_or_seed_player_bankroll(owner_id)
    if player_bankroll.chips < buy_in:
        if is_sponsor_eligible(player_bankroll.chips, stake_label):
            return jsonify({
                "requires_sponsor": True,
                "stake_label": stake_label,
                "bankroll": player_bankroll.chips,
                "min_buy_in": min_buy_in,
                "max_buy_in": max_buy_in,
            }), 402
        return jsonify({
            "error": (
                f"Insufficient bankroll: {player_bankroll.chips} chips, "
                f"buy_in {buy_in}"
            ),
            "bankroll": player_bankroll.chips,
        }), 400

    # Persist the seat claim immediately so a second device can't
    # double-sit. The roster-based _build_cash_game below reads this
    # updated table.
    #
    # Re-load the table here: `table` was the snapshot taken at the
    # top of the route (line ~1005), but `_free_ghost_human_seats`
    # above may have just rewritten the row to clear an orphan human
    # slot. Using the stale snapshot would re-introduce the orphan
    # when we `.with_seat()` + save below — last-write-wins on the
    # `cash_tables` row. That's the regression that recreated a
    # "ghost me" seat on session resume, which then survived the
    # next leave and blocked AI live-fill on the abandoned chair.
    from cash_mode.tables import human_slot
    table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
    if table is None:
        return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
    if table.seats[seat_index]["kind"] != "open":
        return jsonify({
            "error": "Seat is not open",
            "seat_kind": table.seats[seat_index]["kind"],
        }), 409
    claimed_table = table.with_seat(seat_index, human_slot(owner_id, buy_in))
    cash_table_repo.save_table(claimed_table, sandbox_id=sandbox_id)

    # Build the cash game using the table's CURRENT AI roster + chip
    # counts, sourced via the shared preselected-builder.
    from flask_app.extensions import personality_repo
    preselected_ai, preselected_chips, dealer_player_idx = (
        _build_preselected_from_table(
            claimed_table=claimed_table,
            seat_index=seat_index,
            personality_repo=personality_repo,
        )
    )

    game_id, err = _build_cash_game(
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        player_starting_stack=buy_in,
        welcome_message=(
            f"*** Cash table {stake_label} — sit down at ${buy_in} ***"
        ),
        preselected_ai=preselected_ai,
        preselected_ai_chips=preselected_chips,
        dealer_player_idx=dealer_player_idx,
    )
    if err is not None:
        # Roll back the seat claim so the player can retry.
        cash_table_repo.save_table(table, sandbox_id=sandbox_id)
        return jsonify(err[0]), err[1]

    # Debit the player's bankroll. Loan fields stay zeroed — this is
    # the self-funded path.
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=player_bankroll.player_id,
        chips=player_bankroll.chips - buy_in,
        starting_bankroll=player_bankroll.starting_bankroll,
    ))

    # Stash the table_id + seat_index on the game_data so /api/cash/leave
    # can free the seat back to "open" at session end.
    from flask_app.services import game_state_service
    game_data = game_state_service.get_game(game_id)
    if game_data is not None:
        game_data["cash_table_id"] = table_id
        game_data["cash_seat_index"] = seat_index
        game_state_service.set_game(game_id, game_data)

    _record_cash_session_start(
        game_id=game_id,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        initial_buy_in=buy_in,
        cash_table_id=table_id,
        cash_seat_index=seat_index,
    )

    return jsonify({
        "game_id": game_id,
        "table_id": table_id,
        "seat_index": seat_index,
    })


@cash_bp.route("/api/cash/sponsor-offers", methods=["GET"])
def sponsor_offers_for_stake():
    """GET /api/cash/sponsor-offers?stake_label=$10&table_id=...

    Returns up to 3 sponsor offers for the requested stake — Path B
    mixes named AI personalities with anonymous "house" archetypes.

    Personality offers come first (sorted by lender capacity desc),
    filtered through `compute_personality_offers`' four eligibility
    gates (willing / capacity / respect_floor / heat_ceiling).

    **Lobby v1.5 narrowing** (commit 7): when `table_id` is supplied
    and resolves to a persisted cash table, the candidate pool is
    narrowed to the AIs currently SEATED at that table. The model: a
    personality only lends if they're going to be at the table
    watching you play. If zero of the table's seated AIs qualify,
    fall back to the broad eligible pool — and from there, fall back
    to anonymous house archetypes as before.

    Validates that the player is sponsor-eligible at this tier; if
    not, returns a structured rejection the frontend can render
    ("locked tier — earn $X to unlock").

    Side-effect-free: this is just a query. The player picks an
    offer and POSTs to /api/cash/sponsor-and-sit to actually create
    the loan + game.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    stake_label = request.args.get("stake_label")
    table_id = request.args.get("table_id")
    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": "Invalid stake_label",
            "valid_stakes": list(STAKES_LADDER.keys()),
        }), 400

    bankroll = _load_or_seed_player_bankroll(owner_id)
    if not is_sponsor_eligible(bankroll.chips, stake_label):
        _, this_min, _ = table_buy_in_window(stake_label)
        return jsonify({
            "eligible": False,
            "reason": "tier_locked",
            "bankroll": bankroll.chips,
            "this_min_buy_in": this_min,
        }), 200

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    # Path B + Lobby v1.5: assemble personality offers, narrowed to the
    # current table's seated AIs when `table_id` is provided.
    from flask_app.extensions import (
        bankroll_repo, cash_table_repo, personality_repo, relationship_repo,
        stake_repo,
    )
    from cash_mode.staking_tier import resolve_tier

    broad_candidates = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)

    # Vice spending: drop any candidates currently off-grid on a vice.
    # Best-effort — if the lookup fails, fall through with the full
    # list rather than failing the route.
    try:
        from flask_app.extensions import vice_state_repo
        if vice_state_repo is not None:
            on_vice_pids = vice_state_repo.active_pids(
                sandbox_id=sandbox_id, now=datetime.utcnow(),
            )
            if on_vice_pids:
                broad_candidates = [
                    c for c in broad_candidates
                    if c.get("personality_id") not in on_vice_pids
                ]
    except Exception as exc:
        logger.warning(
            "[CASH][SPONSOR_OFFERS] vice filter failed: %s", exc,
        )

    candidates = broad_candidates

    if table_id:
        table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
        if table is not None and table.stake_label == stake_label:
            seated_pids = set(table.seated_personality_ids())
            narrowed = [
                c for c in broad_candidates
                if c.get("personality_id") in seated_pids
            ]
            if narrowed:
                candidates = narrowed

    # Phase 2 (Commit 3): rejections list is populated as candidates
    # fail eligibility / tier gates so the modal can render a "they
    # won't back you" section. Resolved once here so the same list is
    # surfaced regardless of which candidate pool produced the offers.
    rejections: List[LenderRejection] = []
    personality_offers = compute_personality_offers(
        player_owner_id=owner_id,
        sandbox_id=sandbox_id,
        min_buy_in=min_buy_in,
        max_buy_in=max_buy_in,
        candidate_personalities=candidates,
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        stake_repo=stake_repo,
        stake_label=stake_label,
        count=3,
        rejections_out=rejections,
    )

    # Lobby v1.5 fallback: if the narrowed-to-table pool produced zero
    # qualifying offers, retry with the broader pool. House archetypes
    # are still the final fallback when even that returns nothing.
    if table_id and not personality_offers and candidates is not broad_candidates:
        rejections = []  # reset — broader pool will produce its own
        personality_offers = compute_personality_offers(
            player_owner_id=owner_id,
            sandbox_id=sandbox_id,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
            candidate_personalities=broad_candidates,
            bankroll_repo=bankroll_repo,
            relationship_repo=relationship_repo,
            stake_repo=stake_repo,
            stake_label=stake_label,
            count=3,
            rejections_out=rejections,
        )

    # Resolve the borrower's tier so the response (Commit 3 frontend)
    # can render a tier indicator alongside the offers. Tolerates a
    # missing stake_repo by defaulting to 'premium' for back-compat
    # in tests that don't wire one through.
    tier = resolve_tier(
        borrower_id=owner_id,
        current_stake_label=stake_label,
        stake_repo=stake_repo,
    ) if stake_repo is not None else 'premium'

    # House fallback: fill the remainder up to 3 with anonymous archetypes.
    house_slots = max(0, 3 - len(personality_offers))
    house_offers = (
        compute_offers_for_table(min_buy_in, max_buy_in, count=house_slots)
        if house_slots > 0 else []
    )

    response_offers = []
    for po in personality_offers:
        response_offers.append({
            "kind": "personality",
            "lender_id": po.lender_id,
            "name": po.lender_name,
            "amount": po.amount,
            "floor": po.floor,
            "rate": po.rate,
            "flavor": po.flavor,
            "relationship_hint": po.relationship_hint,
        })
    for ho in house_offers:
        response_offers.append({
            "kind": "house",
            "archetype_id": ho.archetype_id,
            "name": ho.name,
            "amount": ho.amount,
            "floor": ho.floor,
            "rate": ho.rate,
            "flavor": ho.flavor,
        })

    return jsonify({
        "eligible": True,
        "stake_label": stake_label,
        "offers": response_offers,
        "tier": tier,
        "rejections": [
            {
                "lender_id": r.lender_id,
                "name": r.lender_name,
                "reason": r.reason,
                "detail": r.detail,
            }
            for r in rejections
        ],
    })


def _record_relationship_event(
    *,
    actor_id: str,
    target_id: str,
    event: RelationshipEvent,
) -> None:
    """Fire a relationship event from outside hand flow.

    Cash mode emits STAKE_OFFERED at sit-down and STAKE_REPAID /
    STAKE_DEFAULTED at leave. None of those happen inside hand flow
    where a `memory_manager` is already wired into the game; the
    route constructs a transient `OpponentModelManager` around the
    live `relationship_repo` so the projection-on-read / clamp /
    persist guarantees inside `record_event` still apply.

    Failures (missing repo, repo write error) log a warning and
    return silently — the stake settlement is the load-bearing
    surface; relationship-state drift is a recoverable degradation,
    not a reason to fail the leave route.
    """
    try:
        from flask_app.extensions import relationship_repo
        from poker.memory import OpponentModelManager
        mgr = OpponentModelManager(relationship_repo=relationship_repo)
        mgr.record_event(actor_id=actor_id, target_id=target_id, event=event)
    except Exception as e:
        logger.warning(
            "[CASH] record_relationship_event(%s) actor=%r target=%r failed: %s",
            event.value, actor_id, target_id, e,
        )


def _materialize_personality_offer(
    *,
    lender_id: str,
    player_owner_id: str,
    sandbox_id: str,
    min_buy_in: int,
    max_buy_in: int,
    bankroll_repo,
    personality_repo,
    relationship_repo,
    stake_repo=None,
    stake_label: Optional[str] = None,
) -> Optional[PersonalitySponsorOffer]:
    """Server-side: re-derive a personality offer fresh for sponsor-and-sit.

    Mirrors `offer_for_archetype` — the client only sends an id, and
    the server recomputes the concrete terms from authoritative state
    (lender's projected bankroll, relationship axes, AND Phase 2 tier
    + per-staker garnishment). A tampered client can't grift better
    terms than the lender's profile + tier + relationship permits.

    `stake_repo` and `stake_label` are optional for back-compat; when
    omitted, tier/garnishment terms aren't applied. Production callers
    pass them so the re-materialized offer matches what the sponsor-
    offers route surfaced (else the client and server would compute
    different rates for the same lender).

    Returns None if the named lender doesn't qualify (unwilling, broke,
    respect floor / heat ceiling violations, tier-floor violations,
    missing personality). The caller treats None as a tampering or
    stale-offer condition.
    """
    # Locate the candidate in the eligible pool — same pool the
    # sponsor-offers route surfaces, so we can't sit with a lender
    # who wasn't actually offered.
    candidates = personality_repo.list_eligible_for_cash_mode(user_id=player_owner_id)
    match = next((c for c in candidates if c.get("personality_id") == lender_id), None)
    if match is None:
        return None

    offers = compute_personality_offers(
        player_owner_id=player_owner_id,
        sandbox_id=sandbox_id,
        min_buy_in=min_buy_in,
        max_buy_in=max_buy_in,
        candidate_personalities=[match],
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        stake_repo=stake_repo,
        stake_label=stake_label,
        count=1,
    )
    return offers[0] if offers else None


@cash_bp.route("/api/cash/sponsor-and-sit", methods=["POST"])
def sponsor_and_sit():
    """POST /api/cash/sponsor-and-sit
       body: {stake_label, archetype_id | lender_id, opponents?}

    Atomic: validate sponsor eligibility, look up archetype OR
    personality lender, build the cash game with the stake's
    `principal` as the player's starting stack, persist the stake
    row. The stake principal never lands in bankroll — it goes
    directly to the table stack, closing the "pocket the spare
    loan" exploit by construction.

    Two paths:
      - `archetype_id` (string) → anonymous house stake (v1 sponsorship
        archetypes). Stake row has `staker_id=NULL`, `staker_kind='house'`,
        and the bank-side ledger fires `house_stake_issue`.
      - `lender_id` (string) → personality stake. The offer is
        re-materialized server-side from the lender's projected
        bankroll + the relationship axes — clients can't tamper.
        Stake row has `staker_id=lender_id`, `staker_kind='personality'`,
        and leave-time settlement routes `staker_total` back to the
        AI lender's bankroll.

    Either field can be present; exactly one is required. Sending
    both is rejected to make the source-of-truth unambiguous.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    payload = request.get_json(silent=True) or {}
    stake_label = payload.get("stake_label")
    archetype_id = payload.get("archetype_id")
    lender_id = payload.get("lender_id")
    opponent_count = int(payload.get("opponents", 5))
    # Lobby v1.5: when the sponsor flow originated from a specific
    # seat tap, the frontend passes the table_id + seat_index so the
    # game is built against the AIs the lobby actually showed. Both
    # required together (one without the other is ambiguous).
    table_id = payload.get("table_id")
    seat_index = payload.get("seat_index")

    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": "Invalid stake_label",
            "valid_stakes": list(STAKES_LADDER.keys()),
        }), 400
    if archetype_id and lender_id:
        return jsonify({
            "error": "Send either archetype_id (house) or lender_id (personality), not both",
        }), 400
    if not archetype_id and not lender_id:
        return jsonify({
            "error": "archetype_id or lender_id is required",
        }), 400
    if archetype_id is not None and not isinstance(archetype_id, str):
        return jsonify({"error": "archetype_id must be a string"}), 400
    if lender_id is not None and not isinstance(lender_id, str):
        return jsonify({"error": "lender_id must be a string"}), 400
    if (table_id is None) != (seat_index is None):
        return jsonify({
            "error": "table_id and seat_index must be sent together",
        }), 400

    # Vice spending: refuse staking an AI who's currently off-grid.
    # The AI's bankroll is part of the stake's economic shape (offers
    # are re-materialized server-side from their projected bankroll),
    # and vicing AIs are not in the lobby anyway. Surface a clear
    # message so the frontend can advise "back in X min" rather than
    # a generic failure.
    if lender_id is not None:
        try:
            from flask_app.extensions import vice_state_repo
            if vice_state_repo is not None:
                vstate = vice_state_repo.load(
                    lender_id, sandbox_id=sandbox_id,
                )
                if vstate is not None and vstate.ends_at > datetime.utcnow():
                    return jsonify({
                        "error": "lender is currently away",
                        "lender_id": lender_id,
                        "vice_ends_at": vstate.ends_at.isoformat(),
                        "vice_narration": vstate.narration,
                    }), 409
        except Exception as exc:
            # Don't fail the route on a vice-check error — log and proceed.
            logger.warning(
                "[CASH][SPONSOR] vice check failed lender=%r: %s",
                lender_id, exc,
            )
    if table_id is not None and not isinstance(table_id, str):
        return jsonify({"error": "table_id must be a string"}), 400
    if seat_index is not None and (
        not isinstance(seat_index, int) or seat_index < 0
    ):
        return jsonify({"error": "seat_index must be a non-negative integer"}), 400

    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify({
            "error": "A cash session is already active. Leave first.",
            "game_id": existing,
        }), 409

    from flask_app.extensions import (
        bankroll_repo, personality_repo, relationship_repo, stake_repo,
    )
    bankroll = _load_or_seed_player_bankroll(owner_id)

    if not is_sponsor_eligible(bankroll.chips, stake_label):
        return jsonify({
            "error": "Not sponsor-eligible at this stake",
            "bankroll": bankroll.chips,
        }), 400

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    # Resolve to a concrete offer — server-side, fresh from authoritative
    # state, no client trust. Pass stake_repo + stake_label so the
    # re-materialized terms include the Phase 2 tier bump + per-staker
    # garnishment that the sponsor-offers route applied.
    if lender_id:
        personality_offer = _materialize_personality_offer(
            lender_id=lender_id,
            player_owner_id=owner_id,
            sandbox_id=sandbox_id,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
            bankroll_repo=bankroll_repo,
            personality_repo=personality_repo,
            relationship_repo=relationship_repo,
            stake_repo=stake_repo,
            stake_label=stake_label,
        )
        if personality_offer is None:
            return jsonify({
                "error": (
                    f"Lender {lender_id!r} doesn't qualify for a loan right now"
                ),
            }), 400
        offer_amount = personality_offer.amount
        offer_floor = personality_offer.floor
        offer_rate = personality_offer.rate
        welcome_lender_label = personality_offer.lender_name
        offer_lender_id = lender_id
    else:
        house_offer = offer_for_archetype(archetype_id, min_buy_in, max_buy_in)
        if house_offer is None:
            return jsonify({
                "error": f"Unknown sponsor archetype {archetype_id!r}",
            }), 400
        offer_amount = house_offer.amount
        offer_floor = house_offer.floor
        offer_rate = house_offer.rate
        welcome_lender_label = house_offer.name
        offer_lender_id = None

    # Lobby-v1.5 table-aware path: when the sponsor flow originated
    # from a specific seat tap, claim that seat on `cash_tables` and
    # build the game with the table's persisted AI roster. Without
    # this branch the legacy fresh-sample path inside `_build_cash_game`
    # silently swaps the lineup — the user clicks a table showing
    # AIs X/Y/Z but gets seated against A/B/C, which they correctly
    # flagged as a recurring bug.
    from flask_app.extensions import cash_table_repo
    from cash_mode.tables import human_slot
    claimed_table = None
    pre_claim_table = None  # snapshot for rollback on build failure
    preselected_ai = None
    preselected_chips = None
    dealer_player_idx = 0
    if table_id is not None:
        table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
        if table is None:
            return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
        if table.stake_label != stake_label:
            return jsonify({
                "error": (
                    f"Table {table_id!r} stake {table.stake_label!r} doesn't "
                    f"match request stake {stake_label!r}"
                ),
            }), 400
        if seat_index >= len(table.seats):
            return jsonify({"error": "seat_index out of range"}), 400
        if table.seats[seat_index]["kind"] != "open":
            return jsonify({
                "error": "Seat is not open",
                "seat_kind": table.seats[seat_index]["kind"],
            }), 409
        # Sweep any orphan human seats for this owner BEFORE claiming
        # the new one — same defense sit_at_table uses. Reload after
        # the sweep because the sweep may have rewritten this table's
        # row; building `with_seat` from a stale snapshot would
        # resurrect the orphan (the regression we just fixed in sit).
        _free_ghost_human_seats(owner_id, sandbox_id=sandbox_id)
        table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
        if table is None:
            return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
        if table.seats[seat_index]["kind"] != "open":
            return jsonify({
                "error": "Seat is not open",
                "seat_kind": table.seats[seat_index]["kind"],
            }), 409
        pre_claim_table = table
        claimed_table = table.with_seat(
            seat_index, human_slot(owner_id, offer_amount),
        )
        cash_table_repo.save_table(claimed_table, sandbox_id=sandbox_id)
        preselected_ai, preselected_chips, dealer_player_idx = (
            _build_preselected_from_table(
                claimed_table=claimed_table,
                seat_index=seat_index,
                personality_repo=personality_repo,
            )
        )

    # Build + register the game with loan.amount as the starting stack.
    game_id, err = _build_cash_game(
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        player_starting_stack=offer_amount,
        welcome_message=(
            f"*** Cash table {stake_label} — sponsored sit-down "
            f"({welcome_lender_label}: ${offer_amount}) ***"
        ),
        opponent_count=opponent_count,
        preselected_ai=preselected_ai,
        preselected_ai_chips=preselected_chips,
        dealer_player_idx=dealer_player_idx,
    )
    if err is not None:
        # Roll back the seat claim so the player can retry.
        if pre_claim_table is not None:
            cash_table_repo.save_table(pre_claim_table, sandbox_id=sandbox_id)
        return jsonify(err[0]), err[1]

    # Stamp table_id + seat_index on game_data so leave_table can free
    # the seat back to "open" at session end (mirror sit_at_table).
    if table_id is not None:
        from flask_app.services import game_state_service
        game_data = game_state_service.get_game(game_id)
        if game_data is not None:
            game_data["cash_table_id"] = table_id
            game_data["cash_seat_index"] = seat_index
            game_state_service.set_game(game_id, game_data)

    # Persist the stake row that leave_table will settle. `stake_id`
    # is deterministic on `game_id` so a retry of sponsor_and_sit
    # (shouldn't happen — game_id is unique per call) hits a PK
    # conflict rather than silently double-booking. The bankroll's
    # chip count doesn't change here (the principal went straight to
    # the table stack, never landed in bankroll), so there's nothing
    # to save on player_bankroll_state — the stake row IS the record.
    #
    # `cut` maps from the legacy `offer_rate`; the legacy `floor` knob
    # has no equivalent in the stake model and is intentionally
    # dropped (the model collapses both into a single share-of-net-
    # winnings number). This shifts settlement math relative to the
    # pre-cutover behavior — pre-launch, that's the design intent.
    stake_kind = STAKER_KIND_HOUSE if offer_lender_id is None else STAKER_KIND_PERSONALITY
    stake_format = STAKE_FORMAT_HOUSE if offer_lender_id is None else STAKE_FORMAT_PURE
    stake_id = f"sponsor_{game_id}"
    stake_repo.create_stake(Stake(
        stake_id=stake_id,
        session_id=game_id,
        staker_id=offer_lender_id,
        staker_kind=stake_kind,
        borrower_id=owner_id,
        borrower_kind=BORROWER_KIND_HUMAN,
        format=stake_format,
        principal=offer_amount,
        match_amount=0,
        origination_fee=0,
        cut=offer_rate,
        status=STAKE_STATUS_ACTIVE,
        carry_amount=0,
        stake_tier=stake_label,
        created_at=datetime.utcnow(),
        # v111: pin the stake to the specific lobby table the player
        # sat at, so per-table analytics ("which $50 table has the
        # highest carry rate?") become possible. `table_id` is set on
        # the seat-targeted sponsor-and-sit path; the auto-sit fallback
        # (no table_id in payload) leaves it NULL.
        table_id=table_id,
    ))

    # The player put up no own chips — `sponsor_principal` carries the
    # full table stack, `initial_buy_in` stays 0. The leave-time summary
    # uses this split to label the modal correctly ("Sponsor put up $X"
    # instead of a misleading "Buy-in $X").
    _record_cash_session_start(
        game_id=game_id,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        initial_buy_in=0,
        sponsor_principal=offer_amount,
        is_staked=True,
        stake_id=stake_id,
    )

    # House-archetype loans create chips out of central_bank. Personality
    # loans are pure transfers (AI lender's bankroll → player's table
    # stack via the AI debit step in _build_cash_game) and aren't routed
    # through here.
    if offer_lender_id is None:
        from flask_app.extensions import chip_ledger_repo
        chip_ledger.record_house_stake_issue(
            chip_ledger_repo,
            owner_id=owner_id,
            amount=offer_amount,
            context={
                'game_id': game_id,
                'stake_label': stake_label,
                'archetype_id': archetype_id,
                'offer_floor': offer_floor,
                'offer_rate': offer_rate,
                'sandbox_id': sandbox_id,
            },
            sandbox_id=sandbox_id,
        )

    if lender_id:
        logger.info(
            "[CASH] Sponsored sit %r owner=%r stake=%r lender=%r "
            "amount=%d floor=%.2f rate=%.2f",
            game_id, owner_id, stake_label, lender_id,
            offer_amount, offer_floor, offer_rate,
        )
        # Path B relationship event: the AI lender just extended trust.
        # Anonymous house loans don't fire this — no `actor` to credit
        # the gesture to. Actor = lender (AI), target = player.
        _record_relationship_event(
            actor_id=lender_id,
            target_id=owner_id,
            event=RelationshipEvent.STAKE_OFFERED,
        )
    else:
        logger.info(
            "[CASH] Sponsored sit %r owner=%r stake=%r archetype=%r "
            "amount=%d floor=%.2f rate=%.2f",
            game_id, owner_id, stake_label, archetype_id,
            offer_amount, offer_floor, offer_rate,
        )

    response_offer = {
        "name": welcome_lender_label,
        "amount": offer_amount,
        "floor": offer_floor,
        "rate": offer_rate,
    }
    if lender_id:
        response_offer["kind"] = "personality"
        response_offer["lender_id"] = lender_id
        response_offer["flavor"] = personality_offer.flavor
        response_offer["relationship_hint"] = personality_offer.relationship_hint
    else:
        response_offer["kind"] = "house"
        response_offer["archetype_id"] = archetype_id
        response_offer["flavor"] = house_offer.flavor

    return jsonify({
        "game_id": game_id,
        "offer": response_offer,
    })


@cash_bp.route("/api/cash/rebuy", methods=["POST"])
def rebuy():
    """POST /api/cash/rebuy body: {amount: int}

    In-table rebuy after the player busts (stack == 0) with bankroll
    still > 0. Distinct from /api/cash/topup: top-up adds to a
    non-zero stack mid-session; rebuy refills from zero. The amount
    must satisfy the table's `[min_buy_in, max_buy_in]` window.

    Blocked while a sponsor loan is active — loans must settle on
    /api/cash/leave before more chips enter the table. This avoids
    mingling loan-funded chips with bankroll-funded chips in the
    leave-time math.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({"error": "No active cash session"}), 404

    payload = request.get_json(silent=True) or {}
    amount = payload.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        return jsonify({"error": "amount must be a positive integer"}), 400

    from flask_app.extensions import bankroll_repo
    from flask_app.services import game_state_service
    from poker.poker_state_machine import PokerPhase

    game_data = game_state_service.get_game(game_id)
    state_machine = game_data["state_machine"]
    stake_label = game_data.get("cash_stake_label")
    if stake_label not in STAKES_LADDER:
        return jsonify({"error": "Game has no valid cash_stake_label"}), 500
    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    # Between-hands gate (same set as top-up).
    if state_machine.current_phase not in (
        PokerPhase.INITIALIZING_GAME,
        PokerPhase.INITIALIZING_HAND,
        PokerPhase.HAND_OVER,
    ):
        return jsonify({"error": "Rebuy is only allowed between hands"}), 400

    human_idx = next(
        (i for i, p in enumerate(state_machine.game_state.players) if p.is_human),
        None,
    )
    if human_idx is None:
        return jsonify({"error": "Player not seated"}), 400
    human_player = state_machine.game_state.players[human_idx]
    if human_player.stack != 0:
        return jsonify({
            "error": "Rebuy is only allowed when stack is 0 (use top-up otherwise)",
            "stack": human_player.stack,
        }), 400

    if amount < min_buy_in or amount > max_buy_in:
        return jsonify({
            "error": (
                f"amount {amount} out of range for {stake_label} table "
                f"(min={min_buy_in}, max={max_buy_in})"
            ),
        }), 400

    bankroll = _load_or_seed_player_bankroll(owner_id)

    # Block rebuy while an active stake is live for this session.
    # Mingling stake-funded chips with fresh bankroll chips would corrupt
    # the leave-time settlement math (the new buy-in would be subject to
    # the staker's cut on the upside). Force a /leave to settle first.
    from flask_app.extensions import stake_repo
    if stake_repo is not None and stake_repo.load_active_for_session(game_id) is not None:
        return jsonify({
            "error": "Rebuy disabled while a stake is active. Leave the table to settle.",
        }), 400
    if bankroll.chips < amount:
        return jsonify({"error": "Insufficient bankroll"}), 400

    state_machine.game_state = state_machine.game_state.update_player(
        human_idx, stack=amount,
    )
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips - amount,
        starting_bankroll=bankroll.starting_bankroll,
    ))

    # Rebuy is another bankroll → table transfer, just like top-up;
    # leave-time P&L needs it counted as money put in (not won).
    _increment_cash_session_buy_in(game_id, amount)

    from flask_app.handlers.game_handler import progress_game, update_and_emit_game_state
    update_and_emit_game_state(game_id)
    # Resume play: if the table was paused in HAND_OVER because the
    # human's bust dropped chip-holders below 2, refilling our stack
    # restores quorum. Kick progress_game so the next hand actually
    # deals instead of waiting for some other event.
    progress_game(game_id)

    return jsonify({
        "stack": amount,
        "bankroll": bankroll.chips - amount,
    })


@cash_bp.route("/api/cash/stakes/<stake_id>/default", methods=["POST"])
def default_stake(stake_id: str):
    """POST /api/cash/stakes/<stake_id>/default

    Explicit borrower default on a sitting carry. Phase 2 Commit 2 of
    the staking-system handoff. The borrower trades the carry's
    ongoing tier-degradation pressure for a one-shot reputation hit on
    the specific lender:
      - status flips to 'defaulted', carry_amount zeroes.
      - STAKE_DEFAULTED relationship event fires (actor=staker,
        target=borrower), which drives the dispatch table's sharpest
        negative axis shift — heat up, respect down, likability down.
      - **No bankroll movement.** The reputation hit IS the cost.
        Locked decision #12: defaulting is always allowed regardless
        of whether the borrower could afford to settle voluntarily.

    Rejections:
      - 404 if the stake_id doesn't exist or belongs to a different
        borrower (the auth check leaks no info: both yield the same
        404 so a probing client can't enumerate other players' carries).
      - 400 if the stake isn't in 'carry' status — active / settled /
        already-defaulted rows can't be defaulted from here.
      - 400 if the staker is the house — house stakes never carry, so
        there's nothing to default and no actor to take the reputation
        hit anyway.

    Phase 4 wires AI borrowers into the same path; the route checks
    `borrower_kind == 'human'` so the player endpoint only operates on
    the player's own rows. AI-initiated defaults will land in a
    separate path via the movement decision tree.

    The leverage exploit (take cheap stake, win big, default cheap,
    pocket profit) is tolerated for v1 per the design lock — the
    reputation cost on the specific lender is the trade. Follow-up
    work could scale the reputation magnitude by carry size or by
    bankroll-vs-carry ratio at default time if playtest shows
    systematic abuse.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    stake, err = _load_human_stake_or_404(stake_id, owner_id)
    if err is not None:
        return err

    if stake.status != STAKE_STATUS_CARRY:
        return jsonify({
            "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
        }), 400
    if stake.staker_id is None:
        # House stakes never carry. This branch only fires if a row
        # somehow got into 'carry' status with NULL staker_id, which
        # shouldn't happen — Phase 1's settle_stake_on_leave overrides
        # house carries to 'settled' before persisting.
        return jsonify({
            "error": "House stakes cannot be defaulted (they don't carry)",
        }), 400

    from flask_app.extensions import stake_repo
    former_carry = stake.carry_amount
    former_staker = stake.staker_id

    stake_repo.update_carry_amount(stake_id, 0)
    stake_repo.update_status(
        stake_id, STAKE_STATUS_DEFAULTED, settled_at=datetime.utcnow(),
    )

    # Fire the reputation hit. The dispatch table's STAKE_DEFAULTED
    # entry is the sharpest negative axis shift in the calibration —
    # the spec calls this "the worst thing a borrower can do to a
    # staker" deliberately. Phase 1 Commit 1 calibrated both the
    # actor and mirror entries; we just trigger the event here.
    _record_relationship_event(
        actor_id=former_staker,
        target_id=owner_id,
        event=RelationshipEvent.STAKE_DEFAULTED,
    )

    logger.info(
        "[STAKE] Explicit default stake_id=%r owner=%r staker=%r "
        "former_carry=%d",
        stake_id, owner_id, former_staker, former_carry,
    )

    return jsonify({
        "stake_id": stake_id,
        "status": STAKE_STATUS_DEFAULTED,
        "former_carry_amount": former_carry,
        "staker_id": former_staker,
    })


@cash_bp.route("/api/cash/stakes/<stake_id>/payoff", methods=["POST"])
def payoff_stake(stake_id: str):
    """POST /api/cash/stakes/<stake_id>/payoff — voluntary carry clearance.

    Phase 3 Commit 1 of the backing system handoff. The player chooses
    to clear an outstanding carry from their bankroll. Chips transfer
    bankroll → staker bankroll directly (no seat involved — the session
    is already over). The stake transitions to 'settled' and fires
    STAKE_REPAID so the staker's relationship axes warm up.

    Rejections:
      - 404 if the stake doesn't exist or belongs to another player
        (single error string for both; the leak-avoidance pattern
        mirrors /default).
      - 400 if the stake isn't in 'carry' status.
      - 400 if the bankroll can't cover `carry_amount`. The UI greys
        the action out when funds are short, but this defends against
        a race / tampered client.
      - 400 if `staker_kind == 'house'` (house carries shouldn't exist
        — settle_stake_on_leave forgives them — but defensive).
      - 501 if `staker_kind == 'human'`. Phase 5 lands the human-as-
        staker bankroll credit path; refusing explicitly avoids silently
        routing the credit into an AI bankroll write.

    Unlike `/default`: this route DOES move chips. The carry leaves the
    "open obligation" pool and the staker is made whole.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    stake, err = _load_human_stake_or_404(stake_id, owner_id)
    if err is not None:
        return err

    if stake.status != STAKE_STATUS_CARRY:
        return jsonify({
            "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
        }), 400
    if stake.staker_id is None:
        return jsonify({
            "error": "House stakes cannot be paid off (they don't carry)",
        }), 400
    if stake.staker_kind == STAKER_KIND_HUMAN:
        return jsonify({
            "error": "Human-staker payoff not yet supported",
        }), 501

    from flask_app.extensions import bankroll_repo, chip_ledger_repo, stake_repo

    bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
    carry_amount = int(stake.carry_amount)
    if bankroll.chips < carry_amount:
        return jsonify({
            "error": "Insufficient bankroll to cover carry",
            "bankroll": bankroll.chips,
            "carry_amount": carry_amount,
        }), 400

    # Pre-flight: confirm the staker's bankroll row exists. Without
    # this, `credit_ai_cash_out` silently returns None on a missing
    # row (its documented contract) — and we'd debit the player while
    # the credit evaporates, plus flip the stake to settled so the
    # player can't retry. Fail fast before any state mutation.
    if bankroll_repo.load_ai_bankroll(
        stake.staker_id, sandbox_id=sandbox_id,
    ) is None:
        return jsonify({
            "error": "Staker bankroll unavailable for this carry",
        }), 503

    # Transfer: player bankroll → staker bankroll. credit_ai_cash_out
    # mirrors the leave-time settlement path so the staker's bankroll
    # accounting (projection-with-regen + cap clamp + ledger
    # instrumentation) stays consistent across "session-end settle" and
    # "voluntary payoff" — the staker doesn't experience a different
    # accounting shape depending on which event produced the credit.
    now = datetime.utcnow()
    new_player_chips = bankroll.chips - carry_amount
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=new_player_chips,
        starting_bankroll=bankroll.starting_bankroll,
    ))
    credit_ai_cash_out(
        bankroll_repo, stake.staker_id, carry_amount,
        sandbox_id=sandbox_id,
        now=now,
        chip_ledger_repo=chip_ledger_repo,
        ledger_context={
            'stake_id': stake_id,
            'site': 'voluntary_payoff',
        },
    )

    stake_repo.update_carry_amount(stake_id, 0)
    stake_repo.update_status(stake_id, STAKE_STATUS_SETTLED, settled_at=now)

    # v106 payout accounting on voluntary payoff:
    #   staker_payout  += carry_amount  (staker received the deferred chips)
    #   borrower_payout −= carry_amount (borrower paid them out of bankroll)
    # Together these keep the Net Worth history's per-stake net P&L
    # accurate. Without these updates, a stake that went bust → carry →
    # later-paid-off would still show -principal in history even though
    # the player made the staker whole.
    prior_staker_payout = stake.staker_payout or 0
    prior_borrower_payout = stake.borrower_payout or 0
    stake_repo.update_payouts(
        stake_id,
        staker_payout=prior_staker_payout + carry_amount,
        borrower_payout=prior_borrower_payout - carry_amount,
    )

    # Voluntary payoff reads as a STAKE_REPAID event — the borrower
    # made the staker whole, same axis shifts as natural session-end
    # repayment. The "remorseful payoff vs winning payoff" distinction
    # could grow its own event later; for v1 they share the calibration.
    _record_relationship_event(
        actor_id=stake.staker_id,
        target_id=owner_id,
        event=RelationshipEvent.STAKE_REPAID,
    )

    logger.info(
        "[STAKE] Voluntary payoff stake_id=%r owner=%r staker=%r amount=%d",
        stake_id, owner_id, stake.staker_id, carry_amount,
    )

    return jsonify({
        "stake_id": stake_id,
        "status": STAKE_STATUS_SETTLED,
        "paid": carry_amount,
        "bankroll": new_player_chips,
        "staker_id": stake.staker_id,
    })


# Phase 3 Commit 3 — forgiveness-request constants
#
# Threshold formula: weighted sum of the staker's view of the borrower
# along the relationship axes. Likability and respect both work in
# the borrower's favor; heat works against. The 0.55 default is
# meaningfully above the no-history baseline of 0.45 — a player has
# to have actually built goodwill rather than relying on default
# neutrality. Tunable from play data.
FORGIVENESS_LIKABILITY_WEIGHT = 0.5
FORGIVENESS_RESPECT_WEIGHT = 0.4
FORGIVENESS_HEAT_WEIGHT = 0.3
FORGIVENESS_THRESHOLD = 0.55

# Rate-limit: at most one ask per stake per 24 hours. Without this
# the player could spam-request until the staker's axes coincidentally
# drift across the threshold. Locked at 24h per the spec; the column
# stamping makes it survive backend restarts.
FORGIVENESS_RATE_LIMIT_SECONDS = 24 * 60 * 60


def _forgiveness_score(*, likability: float, respect: float, heat: float) -> float:
    """Weighted score driving the grant decision.

    Pure function for testability — the route reads relationship state
    and feeds these three numbers in.
    """
    return (
        likability * FORGIVENESS_LIKABILITY_WEIGHT
        + respect * FORGIVENESS_RESPECT_WEIGHT
        - heat * FORGIVENESS_HEAT_WEIGHT
    )


@cash_bp.route(
    "/api/cash/stakes/<stake_id>/request-forgiveness", methods=["POST"],
)
def request_forgiveness(stake_id: str):
    """POST /api/cash/stakes/<stake_id>/request-forgiveness

    Phase 3 Commit 3. The borrower asks the staker to write off the
    carry as a goodwill gesture. The staker decides via the weighted
    relationship-axes score (`likability`, `respect`, `heat`) against
    `FORGIVENESS_THRESHOLD`. Rate-limited at one ask per stake per
    24 hours so spam clicks can't accidentally cross the threshold.

    Decision paths:
      - **Granted**: stake's `carry_amount` zeros, `status` flips to
        'settled', STAKE_FORGIVEN fires (positive axis shifts both
        sides — borrower grateful, staker generous).
      - **Refused**: stake stays as-is, STAKE_FORGIVENESS_REFUSED
        fires (small actor-side likability hit; mild mirror cool-down).

    Both paths stamp `forgiveness_last_asked` so the rate-limit holds.

    Rejections:
      - 404 if stake missing or borrower isn't the requesting player
        (same leak-avoidance pattern as /default and /payoff).
      - 400 if stake status != 'carry'.
      - 400 if staker_kind == 'house' (house carries don't exist; no
        staker to forgive).
      - 429 if a prior ask was within the rate-limit window — error
        body includes `retry_after_seconds` so the UI can countdown.

    Response (granted/refused):
      {
        "stake_id": str,
        "granted": bool,
        "status": 'settled' | 'carry',
        "staker_id": str,
        "staker_display_name": str,
        "score": float,           # the weighted score the decision used
        "threshold": float,       # FORGIVENESS_THRESHOLD
      }
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    stake, err = _load_human_stake_or_404(stake_id, owner_id)
    if err is not None:
        return err

    from flask_app.extensions import (
        personality_repo, relationship_repo, stake_repo,
    )

    if stake.status != STAKE_STATUS_CARRY:
        return jsonify({
            "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
        }), 400
    if stake.staker_id is None:
        return jsonify({
            "error": "House stakes cannot be forgiven (they don't carry)",
        }), 400

    now = datetime.utcnow()
    if stake.forgiveness_last_asked is not None:
        elapsed = (now - stake.forgiveness_last_asked).total_seconds()
        if elapsed < FORGIVENESS_RATE_LIMIT_SECONDS:
            retry_after = int(FORGIVENESS_RATE_LIMIT_SECONDS - elapsed)
            return jsonify({
                "error": "Forgiveness already requested recently",
                "retry_after_seconds": retry_after,
            }), 429

    # Read staker's view of borrower. `load_relationship_state` returns
    # None for never-interacted pairs — treat as the neutral default
    # (0.5/0.5/0.0). Heat is already projected through decay on read.
    rel = relationship_repo.load_relationship_state(
        observer_id=stake.staker_id, opponent_id=owner_id, now=now,
    )
    likability = rel.likability if rel is not None else 0.5
    respect = rel.respect if rel is not None else 0.5
    heat = rel.heat if rel is not None else 0.0

    score = _forgiveness_score(
        likability=likability, respect=respect, heat=heat,
    )
    granted = score > FORGIVENESS_THRESHOLD

    # Stamp the rate-limit on BOTH paths so spam clicks can't sneak
    # across the threshold via lucky axis drift between attempts.
    stake_repo.mark_forgiveness_asked(stake_id, now)

    if granted:
        stake_repo.update_carry_amount(stake_id, 0)
        stake_repo.update_status(stake_id, STAKE_STATUS_SETTLED, settled_at=now)
        _record_relationship_event(
            actor_id=stake.staker_id,
            target_id=owner_id,
            event=RelationshipEvent.STAKE_FORGIVEN,
        )
    else:
        _record_relationship_event(
            actor_id=stake.staker_id,
            target_id=owner_id,
            event=RelationshipEvent.STAKE_FORGIVENESS_REFUSED,
        )

    # Display-name resolution mirrors /net-worth — best-effort.
    display_name = stake.staker_id
    if stake.staker_kind == STAKER_KIND_PERSONALITY:
        try:
            personality = personality_repo.load_personality_by_id(stake.staker_id)
            if personality and personality.get("name"):
                display_name = personality["name"]
        except Exception:
            pass

    logger.info(
        "[STAKE] Forgiveness request stake_id=%r owner=%r staker=%r "
        "score=%.3f threshold=%.3f granted=%s",
        stake_id, owner_id, stake.staker_id, score,
        FORGIVENESS_THRESHOLD, granted,
    )

    return jsonify({
        "stake_id": stake_id,
        "granted": granted,
        "status": STAKE_STATUS_SETTLED if granted else STAKE_STATUS_CARRY,
        "staker_id": stake.staker_id,
        "staker_display_name": display_name,
        "score": round(score, 3),
        "threshold": FORGIVENESS_THRESHOLD,
    })


# v110 — AI-initiated forgiveness asks against a human staker. The
# inverse direction of /request-forgiveness above: there the human
# borrower asks an AI staker; here an AI borrower asks the human
# staker. The decision flows through the player (not the auto-grant
# axes-score) because the human's chips are at stake — silently voiding
# them on a generous likability would erase the player's bankroll
# without their say.


@cash_bp.route("/api/cash/forgiveness-requests", methods=["GET"])
def list_forgiveness_requests():
    """GET /api/cash/forgiveness-requests — pending asks for this owner.

    Returns rows the player needs to decide on (grant/refuse). Each
    row carries the AI borrower's display name, the carry amount,
    the stake tier, and `pending_since` (ISO timestamp) so the UI
    can show "Napoleon, asking 2 days ago — $400 at $10 stakes".
    Oldest pending first so longstanding asks float to the top.

    Response: `{"requests": [{stake_id, borrower_id, borrower_display_name,
    carry_amount, stake_tier, pending_since, created_at}]}`.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    from flask_app.extensions import personality_repo, stake_repo

    try:
        pending = stake_repo.list_pending_forgiveness_for_staker(owner_id)
    except Exception as exc:
        logger.warning(
            "[CASH] list pending forgiveness failed for %r: %s",
            owner_id, exc,
        )
        return jsonify({"requests": []})

    def _borrower_name(pid: str) -> str:
        try:
            p = personality_repo.load_personality_by_id(pid)
            if p and p.get("name"):
                return p["name"]
        except Exception:
            pass
        return pid

    requests = [
        {
            "stake_id": s.stake_id,
            "borrower_id": s.borrower_id,
            "borrower_display_name": _borrower_name(s.borrower_id),
            "carry_amount": int(s.carry_amount),
            "stake_tier": s.stake_tier,
            "pending_since": (
                s.pending_forgiveness_ask.isoformat()
                if s.pending_forgiveness_ask else None
            ),
            "created_at": (
                s.created_at.isoformat() if s.created_at else None
            ),
        }
        for s in pending
    ]
    return jsonify({"requests": requests})


@cash_bp.route(
    "/api/cash/stakes/<stake_id>/staker-forgive", methods=["POST"],
)
def staker_forgive(stake_id: str):
    """POST /api/cash/stakes/<id>/staker-forgive — grant or refuse.

    Body: `{"grant": bool}`. The current owner must be the stake's
    `staker_id` (404 leak-avoidance pattern from /payoff). The stake
    must be a `carry` row with `staker_kind='human'` AND have a
    pending ask (400 otherwise — keeps the route from being used to
    side-step normal carry resolution).

    On grant: clear carry_amount, status → settled, clear pending_ask,
    fire `STAKE_FORGIVEN` (actor = player, target = AI borrower) so
    the AI's relationship axes register the gratitude. On refuse:
    clear pending_ask, fire `STAKE_FORGIVENESS_REFUSED` (actor = player,
    target = AI). Either way the badge clears for this stake.

    Response: `{stake_id, granted, status, borrower_id, borrower_display_name}`.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    body = request.get_json(silent=True) or {}
    grant = bool(body.get("grant"))

    from flask_app.extensions import personality_repo, stake_repo

    stake = stake_repo.load_stake(stake_id)
    if stake is None or stake.staker_id != owner_id:
        # Leak-avoidance: don't reveal that the stake exists if the
        # caller isn't the staker. Mirrors the /payoff pattern.
        return jsonify({"error": "Stake not found"}), 404
    if stake.staker_kind != STAKER_KIND_HUMAN:
        return jsonify({
            "error": "Only human-staker carries route through staker-forgive",
        }), 400
    if stake.status != STAKE_STATUS_CARRY:
        return jsonify({
            "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
        }), 400
    if stake.pending_forgiveness_ask is None:
        return jsonify({
            "error": "No pending forgiveness ask on this stake",
        }), 400

    now = datetime.utcnow()
    if grant:
        stake_repo.update_carry_amount(stake_id, 0)
        stake_repo.update_status(stake_id, STAKE_STATUS_SETTLED, settled_at=now)
        stake_repo.update_pending_forgiveness_ask(stake_id, None)
        _record_relationship_event(
            actor_id=owner_id,
            target_id=stake.borrower_id,
            event=RelationshipEvent.STAKE_FORGIVEN,
        )
    else:
        stake_repo.update_pending_forgiveness_ask(stake_id, None)
        _record_relationship_event(
            actor_id=owner_id,
            target_id=stake.borrower_id,
            event=RelationshipEvent.STAKE_FORGIVENESS_REFUSED,
        )

    borrower_display_name = stake.borrower_id
    try:
        p = personality_repo.load_personality_by_id(stake.borrower_id)
        if p and p.get("name"):
            borrower_display_name = p["name"]
    except Exception:
        pass

    logger.info(
        "[STAKE] staker-forgive stake_id=%r owner=%r borrower=%r granted=%s",
        stake_id, owner_id, stake.borrower_id, grant,
    )

    return jsonify({
        "stake_id": stake_id,
        "granted": grant,
        "status": STAKE_STATUS_SETTLED if grant else STAKE_STATUS_CARRY,
        "borrower_id": stake.borrower_id,
        "borrower_display_name": borrower_display_name,
    })


# Phase 5 — humans as stakers. Player offers a stake to a specific AI;
# the AI evaluates and accepts/refuses. On accept, the AI is seated at
# the target lobby table with `principal` chips funded from the player's
# bankroll, and a stake row with `staker_kind='human'` is persisted.
# The AI plays in lobby sim hands until they leave; the existing leave-
# time settlement path (`refresh_unseated_tables`) credits the player
# bankroll via the Phase 5 Commit 3 human-staker branch.

# Player-stake constants live in `cash_mode.player_staking` so the
# list endpoint, the offer route, and any future analytics paths
# import from one home. Re-export the cooldown here for the
# `_load_recent_defaults` helper below, which is the only remaining
# route-internal reference.
from cash_mode.player_staking import (
    PLAYER_STAKE_DEFAULT_COOLDOWN_SECONDS,
)


@cash_bp.route("/api/cash/stakable-ai", methods=["GET"])
def stakable_ai():
    """GET /api/cash/stakable-ai — curated per-tier list of AIs the
    player can offer a stake to right now.

    Phase 5 refinement (2026-05-21). The lobby surfaces this in a
    dedicated "Idle players" panel so the player has a focused
    decision (3 candidates per tier) rather than scanning every
    portrait for the stake affordance. Filters fire all the same
    gates the offer route enforces, so the player can't pick a
    candidate the server would reject — what they see is what they
    can act on.

    Empty result: returns `{by_tier: []}` (no error) when no AI
    clears every gate. The frontend renders an "ack" message
    ("no one's ready for a stake right now — keep playing")
    instead of treating it as an error.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    from flask_app.extensions import (
        bankroll_repo, cash_table_repo, personality_repo,
        relationship_repo, stake_repo,
    )
    from cash_mode.player_staking import list_stakeable_ai

    bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
    candidates = list_stakeable_ai(
        owner_id=owner_id,
        player_bankroll=bankroll.chips,
        sandbox_id=sandbox_id,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        stake_repo=stake_repo,
        cash_table_repo=cash_table_repo,
    )

    # Group by target tier for the per-section rendering pattern. Tier
    # order matches STAKES_ORDER so the frontend can iterate in lobby
    # order without re-sorting.
    by_tier: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        bucket = by_tier.setdefault(c.target_stake_label, {
            "stake_label": c.target_stake_label,
            "min_buy_in": c.min_buy_in,
            "max_buy_in": c.max_buy_in,
            "candidates": [],
        })
        bucket["candidates"].append({
            "personality_id": c.personality_id,
            "name": c.name,
            "comfort_zone": c.comfort_zone,
            "suggested_principal": c.suggested_principal,
            "relationship_hint": c.relationship_hint,
            "likability": round(c.likability, 3),
            "respect": round(c.respect, 3),
            "heat": round(c.heat, 3),
            "desperation": round(c.desperation, 3),
            "ego": round(c.ego, 3),
        })

    return jsonify({
        "by_tier": list(by_tier.values()),
        "bankroll": bankroll.chips,
    })


@cash_bp.route("/api/cash/stakes/offer", methods=["POST"])
def offer_stake_to_ai():
    """POST /api/cash/stakes/offer — player proposes a stake to an AI.

    Phase 5 Commit 1 + 2026-05-21 refinement.

    Body: `{target_pid, stake_label, principal, cut, format?, match_amount?, origination_fee?}`.
    `format` is `'pure'` (default) or `'match_share'`. Origination fee
    is honored on pure stakes only (mirrors the schema's invariant).

    Validates (all gates also enforced in `list_stakeable_ai`):
      - Player bankroll ≥ 1.5 × min_buy_in @ stake_label.
      - `principal` in `[min_buy_in, max_buy_in]`.
      - For match_share: bankroll covers (principal); AI's match
        contribution comes from their seat funding.
      - Target AI cash-eligible, willing, met-before, relationship
        floors, no active stake, not seated, not at house_only tier,
        no 7-day default cooldown.
      - `stake_label` is exactly `+1` tier above the AI's
        `stake_comfort_zone` (help-them-work-up-the-ranks rule).

    AI evaluation runs the full willingness math from
    `evaluate_player_offer`: score vs effective_threshold where
    effective_threshold rises with cut overage and falls with
    desperation (ego × bankroll deficit).

    On accept: debit player bankroll, seat AI with `principal` chips,
    create stake row with `staker_kind='human'`. For match_share
    additionally debit the AI's bankroll for the match amount (chips
    go onto the seat alongside the player's principal).

    Rejection wire shape:
      - 400 — client-input rejections (bankroll gate, tier mismatch,
        invalid principal, etc.)
      - 409 — invariant conflicts (AI already in active stake / seated)
      - 200 `{accepted: false, reason: ..., evaluation: {...}}` —
        AI's evaluation refused the offer
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    payload = request.get_json(silent=True) or {}
    target_pid = payload.get("target_pid")
    stake_label = payload.get("stake_label")
    principal = payload.get("principal")
    cut = payload.get("cut")
    stake_format = payload.get("format") or STAKE_FORMAT_PURE
    match_amount = int(payload.get("match_amount") or 0)
    origination_fee = int(payload.get("origination_fee") or 0)

    if not isinstance(target_pid, str) or not target_pid:
        return jsonify({"error": "target_pid is required"}), 400
    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": "Invalid stake_label",
            "valid_stakes": list(STAKES_LADDER.keys()),
        }), 400
    if not isinstance(principal, int) or principal <= 0:
        return jsonify({"error": "principal must be a positive integer"}), 400
    if not isinstance(cut, (int, float)):
        return jsonify({"error": "cut must be a number"}), 400
    cut = float(cut)
    if cut < 0.0 or cut > 0.55:
        # Match the cap used by sponsor_offers garnishment so client
        # tampering can't produce a cut beyond the standard cap.
        return jsonify({"error": "cut must lie in [0.0, 0.55]"}), 400
    if stake_format not in (STAKE_FORMAT_PURE, STAKE_FORMAT_MATCH_SHARE):
        return jsonify({
            "error": (
                "format must be 'pure' or 'match_share'"
            ),
        }), 400
    if stake_format == STAKE_FORMAT_PURE:
        if match_amount != 0:
            return jsonify({
                "error": "match_amount is only valid with format='match_share'",
            }), 400
    else:  # match_share
        if origination_fee != 0:
            return jsonify({
                "error": (
                    "origination_fee is only valid with format='pure' — "
                    "match_share shares both up- and downside instead"
                ),
            }), 400
        if match_amount <= 0:
            return jsonify({
                "error": "match_amount must be a positive integer for match_share",
            }), 400
    if origination_fee < 0:
        return jsonify({"error": "origination_fee must be non-negative"}), 400

    from flask_app.extensions import (
        bankroll_repo, cash_table_repo, chip_ledger_repo,
        personality_repo, relationship_repo, stake_repo,
    )
    from cash_mode.player_staking import (
        PLAYER_STAKER_BANKROLL_FLOOR_MULT,
        evaluate_player_offer,
    )

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
    if principal < min_buy_in or principal > max_buy_in:
        return jsonify({
            "error": (
                f"principal {principal} out of range for {stake_label} table "
                f"(min={min_buy_in}, max={max_buy_in})"
            ),
        }), 400
    if stake_format == STAKE_FORMAT_MATCH_SHARE:
        # Match-share's combined principal+match must fit the buy-in
        # window — both contributions land on the seat together. AI's
        # share comes from their bankroll, so we need to check their
        # capacity too (further down once we have the AI loaded).
        if principal + match_amount > max_buy_in:
            return jsonify({
                "error": (
                    f"principal+match_amount {principal + match_amount} exceeds "
                    f"max buy-in {max_buy_in} for {stake_label}"
                ),
            }), 400

    bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
    bankroll_floor = int(PLAYER_STAKER_BANKROLL_FLOOR_MULT * min_buy_in)
    if bankroll.chips < bankroll_floor:
        return jsonify({
            "error": (
                f"Bankroll ${bankroll.chips} below stake-offer floor "
                f"${bankroll_floor} for {stake_label}"
            ),
            "bankroll": bankroll.chips,
            "required": bankroll_floor,
        }), 400
    if bankroll.chips < principal:
        return jsonify({
            "error": "Insufficient bankroll to cover principal",
            "bankroll": bankroll.chips,
        }), 400
    # Origination fee comes from the player too (paid to the AI's
    # bankroll at deal time on pure stakes). Validate together with
    # the principal so we don't half-commit.
    total_player_outlay = principal + origination_fee
    if bankroll.chips < total_player_outlay:
        return jsonify({
            "error": "Insufficient bankroll to cover principal + origination_fee",
            "bankroll": bankroll.chips,
            "required": total_player_outlay,
        }), 400

    # Target AI must be a real cash-eligible personality.
    personality = personality_repo.load_personality_by_id(target_pid)
    if not personality:
        return jsonify({"error": f"Unknown personality {target_pid!r}"}), 400
    target_display_name = personality.get("name") or target_pid

    # +1 tier rule: target stake_label must be exactly one tier above
    # the AI's stake_comfort_zone. Help-them-work-up-the-ranks model.
    knobs = bankroll_repo.load_personality_knobs(target_pid)
    try:
        comfort_idx = STAKES_ORDER.index(knobs.stake_comfort_zone)
        target_idx = STAKES_ORDER.index(stake_label)
    except ValueError:
        comfort_idx = -1
        target_idx = -1
    if comfort_idx == -1 or target_idx == -1 or target_idx != comfort_idx + 1:
        return jsonify({
            "error": (
                f"Can only stake {target_display_name} at the tier directly "
                f"above their comfort zone ({knobs.stake_comfort_zone})."
            ),
            "ai_comfort_zone": knobs.stake_comfort_zone,
        }), 400

    # AI can't already have an active stake as borrower (one-active-
    # stake invariant).
    existing_active = stake_repo.load_active_for_borrower(
        target_pid, BORROWER_KIND_PERSONALITY,
    )
    if existing_active is not None:
        return jsonify({
            "error": f"{target_display_name} is already in an active stake",
        }), 409

    # AI's borrower_profile must allow stakes at all.
    profile = bankroll_repo.load_borrower_profile(target_pid)
    if not profile.willing:
        return jsonify({
            "accepted": False,
            "reason": "unwilling",
            "target_pid": target_pid,
            "target_display_name": target_display_name,
            "detail": f"{target_display_name} doesn't accept stakes from anyone.",
        }), 200

    # Met-before gate: AI must have a relationship row toward this
    # player (created on first interaction). Without history, the
    # offer is a stranger's gesture — refuse with a "build history"
    # nudge rather than an error.
    if relationship_repo.load_relationship_state(
        observer_id=target_pid, opponent_id=owner_id,
    ) is None:
        return jsonify({
            "accepted": False,
            "reason": "no_history",
            "target_pid": target_pid,
            "target_display_name": target_display_name,
            "detail": (
                f"{target_display_name} hasn't played with you yet — "
                "share a few hands together first."
            ),
        }), 200

    # Relationship status floor — separate from the willingness math
    # because crossing it means "AI won't even consider the offer."
    now = datetime.utcnow()
    rel_check = relationship_repo.load_relationship_state(
        observer_id=target_pid, opponent_id=owner_id, now=now,
    )
    if rel_check is not None:
        if rel_check.heat >= 0.5:
            return jsonify({
                "accepted": False,
                "reason": "heat",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": f"{target_display_name} is still upset with you.",
            }), 200
        if rel_check.likability < 0.2:
            return jsonify({
                "accepted": False,
                "reason": "dislike",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": f"{target_display_name} doesn't like you enough.",
            }), 200

    # Tier gate — if the AI is over-leveraged at this stake, they're
    # house-only and can't take a new peer stake.
    from cash_mode.staking_tier import resolve_tier, TIER_HOUSE_ONLY
    target_tier = resolve_tier(
        borrower_id=target_pid,
        borrower_kind=BORROWER_KIND_PERSONALITY,
        current_stake_label=stake_label,
        stake_repo=stake_repo,
    )
    if target_tier == TIER_HOUSE_ONLY:
        return jsonify({
            "accepted": False,
            "reason": "tier_blocked",
            "target_pid": target_pid,
            "target_display_name": target_display_name,
            "detail": (
                f"{target_display_name} has too much outstanding debt "
                "to take a new stake at this level."
            ),
        }), 200

    # 7-day default cooldown.
    cooldown_threshold = now - _timedelta_seconds(
        PLAYER_STAKE_DEFAULT_COOLDOWN_SECONDS,
    )
    prior_defaults = _load_recent_defaults(
        stake_repo=stake_repo,
        staker_id=owner_id,
        borrower_id=target_pid,
        since=cooldown_threshold,
    )
    if prior_defaults:
        return jsonify({
            "accepted": False,
            "reason": "cooldown",
            "target_pid": target_pid,
            "target_display_name": target_display_name,
            "detail": (
                f"{target_display_name} won't take a stake from you yet — "
                "they defaulted on a recent stake from you."
            ),
        }), 200

    # For match-share: the AI must be able to fund their match from
    # bankroll. Refuse if their capacity is too low — they'd otherwise
    # accept and the seat would under-fund.
    if stake_format == STAKE_FORMAT_MATCH_SHARE:
        ai_chips = bankroll_repo.load_ai_bankroll_current(
            target_pid, sandbox_id=sandbox_id, now=now,
        ) or 0
        if int(ai_chips) < match_amount:
            return jsonify({
                "accepted": False,
                "reason": "ai_underfunded",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": (
                    f"{target_display_name} can't cover the ${match_amount:,} "
                    "match — pick pure stake or lower the match amount."
                ),
            }), 200

    # AI evaluation — desperation + cut-penalty layered on the base
    # willingness threshold. The cut_penalty rises with overage past
    # `FAIR_CUT_REFERENCE` (0.30); desperation × ego lowers the bar.
    evaluation = evaluate_player_offer(
        target_pid=target_pid,
        owner_id=owner_id,
        principal=principal,
        cut=cut,
        sandbox_id=sandbox_id,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        now=now,
    )
    if not evaluation.accepted:
        if evaluation.reason == 'cut_too_steep':
            detail = (
                f"{target_display_name} thinks the cut is too steep — "
                "try lowering it or building more goodwill first."
            )
        else:
            detail = (
                f"{target_display_name} doesn't trust you enough yet — "
                "build more goodwill first."
            )
        return jsonify({
            "accepted": False,
            "reason": evaluation.reason,
            "target_pid": target_pid,
            "target_display_name": target_display_name,
            "score": evaluation.score,
            "threshold": evaluation.effective_threshold,
            "evaluation": {
                "score": evaluation.score,
                "base_threshold": evaluation.base_threshold,
                "cut_penalty": evaluation.cut_penalty,
                "desperation": evaluation.desperation,
                "desperation_relief": evaluation.desperation_relief,
                "effective_threshold": evaluation.effective_threshold,
            },
            "detail": detail,
        }), 200

    # Find an open seat at any lobby table for this stake. v111+ runs
    # N tables per tier; pick randomly among tables with an open seat
    # so offers spread across the tier rather than always landing at
    # the canonical -001 table (which may be full). Also verifies the
    # AI isn't already seated elsewhere (global double-seat invariant).
    #
    # ensure_lobby_seeded backfills any tables added to LOBBY_TABLES
    # since this sandbox's last lobby visit — without it, a player
    # whose sandbox predates a multi-table expansion would hit a 503
    # here even when sibling tables should exist.
    import random as _random
    from cash_mode.lobby import ensure_lobby_seeded
    from cash_mode.tables import ai_slot
    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        user_id=owner_id,
        sandbox_id=sandbox_id,
    )
    all_tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)

    for t in all_tables:
        for slot in t.seats:
            if slot.get("kind") == "ai" and slot.get("personality_id") == target_pid:
                return jsonify({
                    "error": (
                        f"{target_display_name} is already seated at "
                        f"{t.stake_label} — can't double-seat"
                    ),
                }), 409

    seatable = []
    for t in all_tables:
        if t.stake_label != stake_label:
            continue
        for idx, slot in enumerate(t.seats):
            if slot.get("kind") == "open":
                seatable.append((t, idx))
                break

    if not seatable:
        return jsonify({
            "error": f"No open seat at any {stake_label} table right now",
        }), 503

    table, open_seat_index = _random.choice(seatable)
    target_table_id = table.table_id

    # Atomic-ish commit: debit player bankroll (+ AI bankroll for the
    # match in match-share), persist seat, persist stake row. If any
    # step fails after the player debit, the chip-ledger audit will
    # surface the drift — the alternative (full transactional wrap)
    # would require pulling all four repos into one connection, which
    # the BaseRepository API doesn't expose. Order matters: charge the
    # player BEFORE seating so an exception leaves them under-charged
    # (recoverable via account credit) not over-charged.
    seat_chips = principal + (
        match_amount if stake_format == STAKE_FORMAT_MATCH_SHARE else 0
    )

    new_player_chips = bankroll.chips - total_player_outlay
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=new_player_chips,
        starting_bankroll=bankroll.starting_bankroll,
    ))

    # Pure-stake origination fee: chips move player bankroll → AI
    # bankroll at deal time. The total_player_outlay above already
    # deducted from the player; credit the AI side here. Use
    # credit_ai_cash_out so the regen + ledger semantics stay
    # consistent with the rest of the AI-credit surface.
    if stake_format == STAKE_FORMAT_PURE and origination_fee > 0:
        credit_ai_cash_out(
            bankroll_repo, target_pid, origination_fee,
            sandbox_id=sandbox_id,
            now=now,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={
                'site': 'player_stake_origination_fee',
                'stake_label': stake_label,
            },
        )

    # Match-share: debit the AI's match contribution from their bankroll
    # before the seat write. Pure non-bank transfer (chips go onto the
    # seat alongside the player's principal) so no ledger entry.
    if stake_format == STAKE_FORMAT_MATCH_SHARE:
        from cash_mode.bankroll import debit_bankroll_for_seat
        debit_bankroll_for_seat(
            bankroll_repo, target_pid, match_amount,
            sandbox_id=sandbox_id,
        )

    updated_table = table.with_seat(
        open_seat_index, ai_slot(target_pid, seat_chips),
    )
    cash_table_repo.save_table(updated_table, sandbox_id=sandbox_id, now=now)

    import uuid as _uuid
    stake_id = f"player_stake_{_uuid.uuid4().hex[:12]}"
    session_id = f"player_session_{target_pid}_{int(now.timestamp())}"
    stake_repo.create_stake(Stake(
        stake_id=stake_id,
        session_id=session_id,
        staker_id=owner_id,
        staker_kind=STAKER_KIND_HUMAN,
        borrower_id=target_pid,
        borrower_kind=BORROWER_KIND_PERSONALITY,
        format=stake_format,
        principal=principal,
        match_amount=match_amount,
        origination_fee=origination_fee,
        cut=cut,
        status=STAKE_STATUS_ACTIVE,
        carry_amount=0,
        stake_tier=stake_label,
        created_at=now,
        table_id=target_table_id,
    ))

    # STAKE_OFFERED event: actor=player, target=AI. Mirrors the
    # personality-staker path's event firing. Player extends trust;
    # the AI's relationship axes warm slightly toward the player.
    _record_relationship_event(
        actor_id=owner_id,
        target_id=target_pid,
        event=RelationshipEvent.STAKE_OFFERED,
    )

    logger.info(
        "[STAKE][PLAYER_OFFER] %r staked %r principal=%d match=%d cut=%.2f "
        "fee=%d format=%s stake=%r",
        owner_id, target_pid, principal, match_amount, cut,
        origination_fee, stake_format, stake_label,
    )

    return jsonify({
        "accepted": True,
        "stake_id": stake_id,
        "target_pid": target_pid,
        "target_display_name": target_display_name,
        "principal": principal,
        "match_amount": match_amount,
        "origination_fee": origination_fee,
        "format": stake_format,
        "cut": cut,
        "stake_label": stake_label,
        "table_id": target_table_id,
        "seat_index": open_seat_index,
        "evaluation": {
            "score": evaluation.score,
            "base_threshold": evaluation.base_threshold,
            "cut_penalty": evaluation.cut_penalty,
            "desperation": evaluation.desperation,
            "desperation_relief": evaluation.desperation_relief,
            "effective_threshold": evaluation.effective_threshold,
        },
        "bankroll": new_player_chips,
    })


def _timedelta_seconds(seconds: int):
    """Lazy import wrapper so the route doesn't pollute the import
    surface with timedelta at module scope."""
    from datetime import timedelta
    return timedelta(seconds=seconds)


def _load_recent_defaults(
    *,
    stake_repo,
    staker_id: str,
    borrower_id: str,
    since: datetime,
) -> List[Stake]:
    """Return defaulted stakes from `staker_id` against `borrower_id`
    settled within the cooldown window.

    Phase 5 Commit 1 — used to enforce the 7-day re-offer cooldown
    after a default. Direct SQL because the existing repo methods
    don't filter on `(staker_id, borrower_id, status, settled_at)`.
    """
    with stake_repo._get_connection() as conn:
        rows = conn.execute(
            """
            SELECT stake_id, session_id, staker_id, staker_kind,
                   borrower_id, borrower_kind, format,
                   principal, match_amount, origination_fee, cut,
                   status, carry_amount, stake_tier,
                   created_at, settled_at, forgiveness_last_asked,
                   staker_payout, borrower_payout,
                   pending_forgiveness_ask, table_id
            FROM stakes
            WHERE staker_id = ?
              AND borrower_id = ?
              AND status = 'defaulted'
              AND settled_at IS NOT NULL
              AND settled_at >= ?
            ORDER BY settled_at DESC
            """,
            (staker_id, borrower_id, since.isoformat()),
        ).fetchall()
    from poker.repositories.stake_repository import _row_to_stake
    return [_row_to_stake(r) for r in rows]


@cash_bp.route("/api/cash/leave", methods=["POST"])
def leave_table():
    """POST /api/cash/leave — player stands up; any active stake settles.

    Pulls the human's current `Player.stack` and applies the leave-time
    stake math via `settle_stake_on_leave` (stakes table is the
    source-of-truth post-cutover):
      - With an active stake: chips_at_table is split between staker and
        borrower per `cut`; under-water bust creates a carry (or fires
        `forgive_balance` on house stakes). See `cash_mode/stake_settlement.py`.
      - Without an active stake: chips_at_table returns to bankroll verbatim.

    The old "auto-$5k fresh grant on full bust" branch is gone —
    a fully busted player walks away with $0 and picks a sponsor
    at /cash entry to keep playing.

    Tears down the game from `game_state_service`.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({"error": "No active cash session"}), 404

    from flask_app.services import game_state_service

    # Cooperative cancellation: signal an in-flight `progress_game` to
    # bail out of its while loop on the next iteration instead of running
    # the full orbit (potentially multiple AI LLM calls + animation
    # sleeps) before releasing the per-game lock. Without this flag the
    # human sees "Leaving…" while AI play continues, sometimes for tens
    # of seconds, until the loop happens to land on a human-turn break.
    # The dict mutation is GIL-atomic and visible to the lock holder
    # because `get_game` returns the same dict object stored in
    # `game_state_service.games`. Safe no-op when no game_data exists
    # (the memory-miss path below handles cold-leave cleanup).
    pending = game_state_service.get_game(game_id)
    if pending is not None:
        pending['leave_requested'] = True

    # Hold the per-game lock for the whole settlement + teardown so an
    # in-flight `progress_game` can't resurrect the row right after we
    # delete it. Without this, progress_game's `set_game` / `save_game`
    # at the end of its iteration writes the (now-stale) state machine
    # back to memory + DB, and the next `/api/cash/state` redirects the
    # player straight back into the table they thought they'd left —
    # which also lets a second leave return the full stack with no
    # sponsor cut (free-money exploit on loan leaves).
    lock = game_state_service.get_game_lock(game_id)
    with lock:
        return _leave_table_locked(owner_id, game_id)


def _build_session_summary(
    *,
    game_id: str,
    human_name: str,
    cash_out: int,
    state_machine=None,
    cash_session=None,
    sponsor_repaid: int = 0,
    player_take_home: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compute the post-session summary payload for the leave response.

    Reads buy-in / start-time / staking flags from the durable
    `cash_sessions` row (loaded lazily if not passed in). Pulls
    hand_history rows for this game and delegates to the pure
    `summarize_cash_session` helper.

    `cash_session` is optional so callers that already loaded it can
    skip the second read. The function tolerates `None` — leftover
    sessions from before the cash_sessions table existed fall back to
    a zeroed buy-in summary rather than raising.
    """
    from cash_mode.session_summary import summarize_cash_session
    from flask_app.extensions import cash_session_repo, hand_history_repo

    if now is None:
        now = datetime.utcnow()

    if cash_session is None and cash_session_repo is not None:
        try:
            cash_session = cash_session_repo.load(game_id)
        except Exception as e:
            logger.warning(
                "[CASH] cash_sessions load failed for summary %r: %s",
                game_id, e,
            )
            cash_session = None

    if cash_session is not None:
        buy_in = cash_session.total_buy_in
        started_at = cash_session.started_at
        is_staked = cash_session.is_staked
        sponsor_principal = cash_session.sponsor_principal
    else:
        # No durable row — this is a legacy session predating v108 or
        # one where the row create failed at sit-down. Fall back to
        # zeros so the summary at least renders something coherent.
        buy_in = 0
        started_at = None
        is_staked = False
        sponsor_principal = 0

    hands: List[Dict[str, Any]] = []
    if hand_history_repo is not None:
        try:
            hands = hand_history_repo.load_hand_history(game_id) or []
        except Exception as e:
            logger.warning(
                "[CASH] load_hand_history failed for summary %r: %s",
                game_id, e,
            )

    fallback_hand_count = 0
    if state_machine is not None:
        try:
            fallback_hand_count = int(
                getattr(state_machine, "_state", None).stats.hand_count
            )
        except Exception:
            fallback_hand_count = 0

    return summarize_cash_session(
        hands=hands,
        human_name=human_name,
        buy_in=buy_in,
        cash_out=cash_out,
        started_at=started_at,
        now=now,
        fallback_hand_count=fallback_hand_count,
        is_staked=is_staked,
        sponsor_principal=sponsor_principal,
        sponsor_repaid=sponsor_repaid,
        player_take_home=player_take_home,
    )


def _finalise_cash_session(**kwargs) -> None:
    """Thin wrapper — see `cash_mode.cash_session_persistence.finalise_cash_session`."""
    from cash_mode.cash_session_persistence import finalise_cash_session
    from flask_app.extensions import cash_session_repo
    finalise_cash_session(cash_session_repo=cash_session_repo, **kwargs)


def _leave_table_locked(owner_id: str, game_id: str):
    """Body of `leave_table`, run under the per-game lock.

    Split out so the `with lock:` scope covers the entire teardown
    without indenting the existing block — see the `with lock:` in
    `leave_table` for the rationale.
    """
    from flask_app.extensions import bankroll_repo, cash_session_repo, cash_table_repo, game_repo, personality_repo
    from flask_app.services import game_state_service

    game_data = game_state_service.get_game(game_id)
    # Resolve sandbox_id: prefer the value stamped at session-creation
    # time (sponsor_and_sit / sit_at_table both set it on game_data) so
    # cold-loaded sessions don't end up re-resolving against a different
    # sandbox. Fall back to the owner's default sandbox when game_data
    # is missing the field (e.g. memory-miss or a session that pre-
    # dated the stamping).
    sandbox_id = (game_data or {}).get("sandbox_id") if game_data else None
    # The durable cash_sessions row carries sandbox_id even when the
    # in-memory game_data is gone — use it as a second-best source so
    # the memory-miss path doesn't fall back to a re-resolved default
    # sandbox that may differ from the one the session was created on.
    persisted_cash_session = None
    if cash_session_repo is not None:
        try:
            persisted_cash_session = cash_session_repo.load(game_id)
        except Exception as e:
            logger.warning(
                "[CASH] cash_sessions load failed for leave %r: %s",
                game_id, e,
            )
    if not sandbox_id and persisted_cash_session is not None:
        sandbox_id = persisted_cash_session.sandbox_id
    if not sandbox_id:
        sandbox_id = _resolve_sandbox_id(owner_id)

    now = datetime.utcnow()

    if game_data is None:
        # Memory-only miss is fine when the game is still in the DB
        # (e.g. server restarted mid-session). Best-effort cleanup of
        # any persisted row(s) for this owner so we don't strand them
        # in the no-active-session state with a stale `/api/cash/state`
        # redirect target. No chips to settle when there's no state
        # machine to read a stack from.
        try:
            game_repo.delete_game(game_id)
        except Exception as e:
            logger.warning("[CASH] delete_game failed for %r: %s", game_id, e)
        _purge_other_cash_rows(owner_id, except_game_id=None)
        # Free any cash_tables human seat owned by this user — without
        # this the lobby keeps rendering them as seated at a ghost table
        # (the cash row is gone but the persisted seat survives). Chips
        # on the seat are notional only (last hand-boundary sync); the
        # bankroll already reflects the actual loss from buy-in, so we
        # don't refund here.
        _free_ghost_human_seats(owner_id, sandbox_id=sandbox_id)
        # Build a real summary from the durable cash_sessions row even
        # though we have no live state machine. The user spent time at
        # the table (buy-in, hands played) — surface what we know
        # rather than returning `null` and stranding them on the lobby
        # without acknowledgement of the session that vanished.
        bankroll_now = _load_or_seed_player_bankroll(owner_id).chips
        ghost_summary = None
        if persisted_cash_session is not None:
            ghost_summary = _build_session_summary(
                game_id=game_id,
                human_name="",
                cash_out=0,  # chips lost when game_data evaporated
                cash_session=persisted_cash_session,
                sponsor_repaid=0,
                player_take_home=0,
                now=now,
            )
            _finalise_cash_session(
                game_id=game_id,
                now=now,
                final_chips_at_table=0,
                sponsor_repaid=0,
                player_take_home=0,
                summary=ghost_summary,
                closed_status="ghost_cleanup",
            )
        logger.info(
            "[CASH] Left game_id=%r owner=%r (memory-miss path, "
            "ghost-seat cleanup ran, summary=%s)",
            game_id, owner_id, "from-db" if ghost_summary else "null",
        )
        return jsonify({
            "session_ended": True,
            "chips_at_table": 0,
            "had_active_loan": False,
            "sponsor_repaid": 0,
            "returned_chips": 0,
            "bankroll": bankroll_now,
            "session_summary": ghost_summary,
        })
    state_machine = game_data["state_machine"]
    human_player = next(
        (p for p in state_machine.game_state.players if p.is_human), None,
    )
    chips_at_table = human_player.stack if human_player else 0

    bankroll = _load_or_seed_player_bankroll(owner_id)
    from flask_app.extensions import chip_ledger_repo, stake_repo

    # Stake-table settlement. Sessions with an active stake row settle
    # via the stake_chip_flow plumbing; sessions without (no stake was
    # ever struck — player walked in with their own bankroll) get their
    # chips returned verbatim.
    active_stake = (
        stake_repo.load_active_for_session(game_id)
        if stake_repo is not None else None
    )

    # Response payload values — populated by whichever settlement path
    # runs. Defaults cover the "no stake" leave (player walks away with
    # their chips).
    sponsor_repaid = 0
    returned_chips = chips_at_table
    new_bankroll_chips = bankroll.chips + chips_at_table
    had_loan = False

    if active_stake is not None:
        from cash_mode.stake_settlement import settle_stake_on_leave
        from cash_mode.stake_chip_flow import (
            DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL,
            DIRECTION_BORROWER_SEAT_TO_HOUSE,
            DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL,
            build_stake_settlement_flows,
        )

        stake_settlement = settle_stake_on_leave(
            active_stake.stake_id, chips_at_table,
            stake_repo=stake_repo,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={'game_id': game_id, 'site': 'leave_table'},
            sandbox_id=sandbox_id,
            now=now,
        )
        flows = build_stake_settlement_flows(stake_settlement)
        borrower_credit = 0
        for flow in flows:
            if flow.direction == DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL:
                # Personality (or Phase-5 human) staker — credit their bankroll.
                credit_ai_cash_out(
                    bankroll_repo, flow.staker_id, flow.amount,
                    sandbox_id=sandbox_id,
                    now=now,
                    chip_ledger_repo=chip_ledger_repo,
                    ledger_context={
                        'game_id': game_id,
                        'stake_id': active_stake.stake_id,
                        'site': 'stake_settle',
                    },
                )
            elif flow.direction == DIRECTION_BORROWER_SEAT_TO_HOUSE:
                # House staker — chips return to the bank. Ledger entry
                # closes the loop for the audit's house-stake reconciliation
                # (forgive_balance for unrecovered portion already fired
                # inside settle_stake_on_leave above).
                chip_ledger.record_house_stake_settle(
                    chip_ledger_repo,
                    owner_id=stake_settlement.borrower_id,
                    amount=flow.amount,
                    context={
                        'game_id': game_id,
                        'stake_id': active_stake.stake_id,
                        'site': 'leave_table',
                        'sandbox_id': sandbox_id,
                    },
                    sandbox_id=sandbox_id,
                )
            elif flow.direction == DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL:
                borrower_credit = flow.amount

        new_bankroll_chips = bankroll.chips + borrower_credit
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=bankroll.player_id,
            chips=new_bankroll_chips,
            starting_bankroll=bankroll.starting_bankroll,
        ))

        # Fire STAKE_REPAID only when a personality staker was made whole.
        # Natural carries roll forward silently (no event per the spec);
        # house settlements have no actor; explicit defaults go through
        # the dedicated POST /api/cash/stakes/<id>/default route.
        if (
            stake_settlement.staker_id
            and stake_settlement.new_status == STAKE_STATUS_SETTLED
            and stake_settlement.forgiven_amount == 0
        ):
            _record_relationship_event(
                actor_id=stake_settlement.staker_id,
                target_id=owner_id,
                event=RelationshipEvent.STAKE_REPAID,
            )

        sponsor_repaid = stake_settlement.staker_total
        returned_chips = borrower_credit
        had_loan = True
    else:
        # No active stake — chips return to bankroll verbatim. The
        # pre-cutover `active_loan_*` legacy branch is gone (Cleanup A):
        # post-cutover sessions all create stake rows, and Phase 1's
        # one-shot migration converted historical loans into stake rows.
        new_bankroll_chips = bankroll.chips + chips_at_table
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id=bankroll.player_id,
            chips=new_bankroll_chips,
            starting_bankroll=bankroll.starting_bankroll,
        ))

    # Now that settlement is done we know `sponsor_repaid` (chips the
    # staker pulled off the top) and `returned_chips` (what actually
    # credited the player's bankroll). Both are passed into the summary
    # so the staked-session headline P&L reflects player take-home,
    # not gross table P&L. Persisted on the cash_sessions row so a
    # history view can render the same numbers later.
    session_summary = _build_session_summary(
        game_id=game_id,
        human_name=human_player.name if human_player else "",
        cash_out=chips_at_table,
        state_machine=state_machine,
        cash_session=persisted_cash_session,
        sponsor_repaid=sponsor_repaid,
        player_take_home=returned_chips,
        now=now,
    )
    _finalise_cash_session(
        game_id=game_id,
        now=now,
        final_chips_at_table=chips_at_table,
        sponsor_repaid=sponsor_repaid,
        player_take_home=returned_chips,
        summary=session_summary,
        closed_status="left",
    )

    # Credit every seated AI's current Player.stack back to their
    # persistent bankroll. Without this loop, AI table winnings
    # evaporate at session end and AI bankrolls drift monotonically
    # downward — sit-down debits never get matched by cash-out
    # credits. Path B (AI sponsorship) needs this to be honest, since
    # lender-eligibility reads `load_ai_bankroll_current`.
    # (`now` was already pinned above for the settlement timestamp.)
    cash_personality_ids: Dict[str, str] = game_data.get(
        "cash_personality_ids", {}
    ) or {}
    for player in state_machine.game_state.players:
        if player.is_human:
            continue
        pid = cash_personality_ids.get(player.name)
        if not pid:
            logger.warning(
                "[CASH] AI cash-out skipped — no personality_id mapping for %r",
                player.name,
            )
            continue
        from flask_app.extensions import chip_ledger_repo
        credit_ai_cash_out(
            bankroll_repo,
            pid,
            player.stack,
            sandbox_id=sandbox_id,
            now=now,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={'game_id': game_id, 'site': 'cash_leave_cashout'},
        )

    # Lobby v1.5: persist end-of-session chip counts back to the
    # `cash_tables` row, free the human seat, and run a final
    # refresh_table_roster so AI movement can act on the post-session
    # state. The seat free happens BEFORE the refresh so live-fill can
    # claim the now-open intent slot.
    cash_table_id = game_data.get("cash_table_id")
    cash_seat_index = game_data.get("cash_seat_index")
    # Cold-loaded sessions don't have these on game_data (cold-load
    # only restores cash_mode / cash_stake_label / cash_personality_ids).
    # Fall back to the durable cash_sessions row so cold-load leaves
    # still free the lobby seat instead of stranding a ghost.
    if cash_table_id is None and persisted_cash_session is not None:
        cash_table_id = persisted_cash_session.cash_table_id
        if cash_seat_index is None:
            cash_seat_index = persisted_cash_session.cash_seat_index
    if cash_table_id is not None:
        from cash_mode.tables import ai_slot, open_slot
        from flask_app.extensions import cash_table_repo
        table = cash_table_repo.load_table(cash_table_id, sandbox_id=sandbox_id)
        if table is not None:
            # Build chip map: AI's name → personality_id (from session)
            # → final stack.
            pid_chips: Dict[str, int] = {}
            name_to_pid = cash_personality_ids
            for player in state_machine.game_state.players:
                if player.is_human:
                    continue
                pid = name_to_pid.get(player.name)
                if pid:
                    pid_chips[pid] = int(player.stack)

            new_seats = []
            for idx, slot in enumerate(table.seats):
                # Free every human seat owned by this user — the current
                # session's seat AND any orphan human seat surviving from
                # an earlier session that didn't close cleanly. The
                # narrow `idx == cash_seat_index` check used to leave such
                # orphans behind: a player who left this table cleanly
                # would still render as seated at the stale index.
                if slot["kind"] == "human" and slot.get("personality_id") == owner_id:
                    new_seats.append(open_slot())
                elif slot["kind"] == "ai":
                    pid = slot["personality_id"]
                    if pid in pid_chips and pid_chips[pid] > 0:
                        new_seats.append(ai_slot(pid, pid_chips[pid]))
                    elif pid in pid_chips and pid_chips[pid] <= 0:
                        # Busted on table; free their seat too.
                        new_seats.append(open_slot())
                    else:
                        # AI was added mid-session (live fill) — preserve.
                        new_seats.append(dict(slot))
                else:
                    new_seats.append(dict(slot))

            from cash_mode.tables import CashTableState
            updated_table = CashTableState(
                table_id=table.table_id,
                stake_label=table.stake_label,
                seats=new_seats,
                created_at=table.created_at,
                last_activity_at=table.last_activity_at,
                dealer_idx=table.dealer_idx,
                # v111: preserve identity so the leave-table write
                # doesn't blank the name/type.
                name=table.name,
                table_type=table.table_type,
                # v113: preserve casino closing state across the leave-
                # table write.
                closing_hand_countdown=table.closing_hand_countdown,
            )
            cash_table_repo.save_table(updated_table, sandbox_id=sandbox_id, now=now)
            logger.info(
                "[CASH][LOBBY] freed seat %r:%s and persisted final chip counts",
                cash_table_id, cash_seat_index,
            )

            # Cross-table sweep: catch human seats owned by this user
            # that survived on OTHER tables (e.g. an earlier session
            # ended without a clean leave — back-arrow, browser close,
            # crashed Flask). Without this the lobby keeps rendering the
            # player as seated at a ghost table even after a clean leave.
            # Same helper the memory-miss path already uses.
            _free_ghost_human_seats(owner_id, sandbox_id=sandbox_id)

            # Final refresh pass: lets AI movement act on the post-leave
            # state (e.g., an AI who won big can now stake_up).
            try:
                from cash_mode.lobby import refresh_unseated_tables
                from flask_app.extensions import (
                    chip_ledger_repo, relationship_repo, stake_repo,
                )
                refresh_unseated_tables(
                    cash_table_repo=cash_table_repo,
                    personality_repo=personality_repo,
                    bankroll_repo=bankroll_repo,
                    user_id=owner_id,
                    sandbox_id=sandbox_id,
                    now=now,
                    chip_ledger_repo=chip_ledger_repo,
                    relationship_repo=relationship_repo,
                    stake_repo=stake_repo,
                )
            except Exception as e:
                logger.warning(
                    "[CASH][LOBBY] leave-time final refresh failed: %s", e,
                )

    game_state_service.delete_game(game_id)
    # Best-effort: drop the persisted row too so the cash game doesn't
    # linger in game_repo. Cash games shouldn't even be persisted
    # (spec §"v1 architectural invariants" — CashTable in-memory
    # only), but progress_game's auto-save can write rows; this
    # cleans them up on leave. Missing row is fine.
    try:
        game_repo.delete_game(game_id)
    except Exception as e:
        logger.warning("[CASH] delete_game failed for %r: %s", game_id, e)

    # "Leave = clean slate" — purge every OTHER cash session this
    # owner has, both in memory and in the DB. Defends against:
    #   - Two cash games for one owner in memory (e.g., a prior
    #     leave that hit the wrong game via iteration order, so the
    #     intended game survived and is now an orphan in memory).
    #   - Auto-saved rows from prior sessions that didn't make it
    #     through `_build_cash_game`'s `_purge_other_cash_rows`.
    # Without this, the next `/api/cash/state` would surface the
    # orphan as an "active session" and redirect the player back
    # into a table they thought they'd already left.
    for other_gid, other_gdata in list(game_state_service.games.items()):
        if other_gid == game_id:
            continue
        if other_gdata.get("cash_mode") and other_gdata.get("owner_id") == owner_id:
            game_state_service.delete_game(other_gid)
            logger.info(
                "[CASH] Leave purged orphan in-memory cash game_id=%r owner=%r",
                other_gid, owner_id,
            )
    _purge_other_cash_rows(owner_id, except_game_id=None)

    logger.info(
        "[CASH] Left game_id=%r owner=%r chips_at_table=%d had_loan=%s "
        "sponsor_repaid=%d returned=%d bankroll_now=%d",
        game_id, owner_id, chips_at_table, had_loan,
        sponsor_repaid, returned_chips, new_bankroll_chips,
    )

    return jsonify({
        "session_ended": True,
        "chips_at_table": chips_at_table,
        "had_active_loan": had_loan,
        "sponsor_repaid": sponsor_repaid,
        "returned_chips": returned_chips,
        "bankroll": new_bankroll_chips,
        "session_summary": session_summary,
    })


@cash_bp.route("/api/cash/topup", methods=["POST"])
def top_up():
    """POST /api/cash/topup body: {amount: int}

    Top up the human player's stack from bankroll. Allowed between
    hands, OR mid-hand once the human has folded — a folded player
    is no longer acting in the current hand, so adding chips to
    their stack can't influence in-flight betting.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({"error": "No active cash session"}), 404

    payload = request.get_json(silent=True) or {}
    amount = int(payload.get("amount", 0))
    if amount <= 0:
        return jsonify({"error": "amount must be a positive integer"}), 400

    from flask_app.extensions import bankroll_repo
    from flask_app.services import game_state_service
    from poker.poker_state_machine import PokerPhase

    game_data = game_state_service.get_game(game_id)
    state_machine = game_data["state_machine"]

    human_idx = next(
        (i for i, p in enumerate(state_machine.game_state.players) if p.is_human),
        None,
    )
    if human_idx is None:
        return jsonify({"error": "Player not seated"}), 400
    human_player = state_machine.game_state.players[human_idx]

    # Phase gate: between-hands phases are always safe. Mid-hand we
    # only allow it once the human has folded — they can't act this
    # hand, so the new chips just sit on the stack until the next
    # deal. A still-active player topping up mid-hand would shift
    # call/raise math underneath the AI opponents.
    between_hands = state_machine.current_phase in (
        PokerPhase.INITIALIZING_GAME,
        PokerPhase.INITIALIZING_HAND,
        PokerPhase.HAND_OVER,
    )
    if not between_hands and not human_player.is_folded:
        return jsonify({
            "error": "Top up is only allowed between hands or after folding",
        }), 400

    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if bankroll is None or bankroll.chips < amount:
        return jsonify({"error": "Insufficient bankroll"}), 400
    # Mingling bankroll chips with stake chips would corrupt the
    # leave-time math (your top-up money would be taxed by the
    # staker's cut). Force the player to settle first.
    from flask_app.extensions import stake_repo
    if stake_repo is not None and stake_repo.load_active_for_session(game_id) is not None:
        return jsonify({
            "error": "Top-up disabled while a stake is active. Leave the table to settle.",
        }), 400

    new_stack = state_machine.game_state.players[human_idx].stack + amount
    state_machine.game_state = state_machine.game_state.update_player(
        human_idx, stack=new_stack,
    )

    new_bankroll = PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips - amount,
        starting_bankroll=bankroll.starting_bankroll,
        # Loan fields are 0 by virtue of the guard above; no need to copy.
    )
    bankroll_repo.save_player_bankroll(new_bankroll)

    # Bump the durable session's total_buy_in so leave-time P&L counts
    # this top-up as money put in (not won). Skipped silently if the
    # session row doesn't exist (legacy game predating cash_sessions).
    _increment_cash_session_buy_in(game_id, amount)

    from flask_app.handlers.game_handler import update_and_emit_game_state
    update_and_emit_game_state(game_id)

    return jsonify({
        "stack": new_stack,
        "bankroll": new_bankroll.chips,
    })


@cash_bp.route("/api/cash/lobby", methods=["GET"])
def get_lobby():
    """GET /api/cash/lobby — multi-table lobby snapshot.

    Returns the player's bankroll + a list of all persistent tables
    with their seat rosters. Each AI seat carries a `relationship_hint`
    derived from the lender's POV of the player (same surface
    SponsorModal uses).

    Side-effect: runs `refresh_unseated_tables` on every table without
    a `"human"` slot before serializing. This is how the lobby stays
    cycling without a background daemon — movement + live-fill happen
    lazily on every read. Tables with a human seated are skipped here
    (the hand-boundary hook covers them in commit 7).

    Response shape:

      {
        "bankroll": int,
        "tables": [ {table_id, stake_label, big_blind,
                     min_buy_in, max_buy_in, affordability,
                     seats: [...]}, ... ],
        "events": [ {type, table_id, stake_label, personality_id,
                     name, reason, message, created_at}, ... ]
      }

    Seat shapes (in `tables[].seats`):
      {"kind": "open", "index": int}                            |
      {"kind": "ai",  "index", "personality_id", "name",
       "avatar_url" (nullable), "emotion", "chips",
       "relationship_hint"}                                     |
      {"kind": "human", "index", "personality_id", "chips"}

    Events are sourced from the in-memory ring buffer populated by
    `refresh_unseated_tables` — see `cash_mode/activity.py`.
    Newest-first, capped at 10. Empty list = nothing has happened
    since the last backend restart.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    from flask_app.extensions import (
        bankroll_repo, cash_table_repo, personality_repo,
        relationship_repo,
    )
    from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback
    from flask_app.services import game_state_service
    from cash_mode.lobby import (
        ensure_ai_bankrolls_seeded,
        ensure_lobby_seeded,
        get_dealer_index,
        refresh_unseated_tables,
    )

    bankroll = _load_or_seed_player_bankroll(owner_id)
    # Bankroll seed must run BEFORE lobby seed: the lobby seeder picks
    # AI candidates by `projected >= ai_threshold`, and a missing row
    # leans on `knobs.starting_bankroll` only via a defensive fallback —
    # writing real rows up-front keeps the live-fill path's
    # `load_ai_bankroll_current` from returning None for personalities
    # who have never sat.
    from flask_app.extensions import chip_ledger_repo as _chip_ledger_repo
    ensure_ai_bankrolls_seeded(
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        sandbox_id=sandbox_id,
        user_id=owner_id,
        chip_ledger_repo=_chip_ledger_repo,
    )
    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        user_id=owner_id,
        sandbox_id=sandbox_id,
    )

    # Read-side movement refresh on unseated tables. The handoff
    # documents this as intentional: lazy cadence vs. background ticker.
    try:
        from flask_app.extensions import (
            chip_ledger_repo, relationship_repo, stake_repo, vice_state_repo,
        )
        refresh_unseated_tables(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            user_id=owner_id,
            sandbox_id=sandbox_id,
            chip_ledger_repo=chip_ledger_repo,
            relationship_repo=relationship_repo,
            stake_repo=stake_repo,
            vice_repo=vice_state_repo,
        )
    except Exception as e:
        logger.warning("[CASH][LOBBY] refresh_unseated_tables failed: %s", e)

    # Build live-emotion map for AIs at the player's active cash table.
    # Other AIs (at tables without the player, or in the idle pool)
    # default to "confident" — a priority emotion that's always
    # generated, so the avatar URL lookup succeeds without a fallback
    # chain. Full Path C will source emotions from background-sim
    # state for unseated tables too.
    active_emotions: Dict[str, str] = {}
    active_game_id = _find_active_cash_game_id(owner_id)
    if active_game_id:
        active_game = game_state_service.get_game(active_game_id)
        if active_game:
            for name, controller in (active_game.get("ai_controllers") or {}).items():
                emotional_state = getattr(controller, "emotional_state", None)
                if emotional_state:
                    try:
                        active_emotions[name] = emotional_state.get_display_emotion()
                    except Exception:
                        active_emotions[name] = "confident"
                else:
                    active_emotions[name] = "confident"

    tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)

    # Resolve emotions for AIs at unseated tables from the persisted
    # emotional_state_json column (schema v97). Without this, every
    # unseated AI showed "confident" regardless of recent sim history
    # — tilted AIs surrender their signal to the player browsing the
    # lobby, breaking the "world feels alive" affordance. Batched
    # in one query to keep the lobby response cheap.
    unseated_pids: List[str] = []
    for table in tables:
        if table.human_seat_index() is not None:
            # Active session table: emotions come from live in-memory
            # controllers (active_emotions, populated above). No need
            # to read persisted state for these.
            continue
        for slot in table.seats:
            if slot.get("kind") == "ai":
                pid = slot.get("personality_id")
                if pid:
                    unseated_pids.append(pid)

    unseated_emotion_blobs = bankroll_repo.load_emotional_state_json_for_pids(
        unseated_pids,
        sandbox_id=sandbox_id,
    )
    unseated_emotions: Dict[str, str] = {}
    for pid, blob in unseated_emotion_blobs.items():
        if not blob:
            continue
        unseated_emotions[pid] = _resolve_emotion_from_blob(blob, pid)

    # Phase 2 Commit 3: resolve the player's tier at each table so the
    # frontend can render a per-card tier indicator alongside the
    # existing affordability. `stake_repo` is imported lazily here to
    # keep the existing module-level extension imports stable.
    from flask_app.extensions import stake_repo
    from cash_mode.staking_tier import (
        TIER_PREMIUM,
        resolve_tier,
    )

    # Phase 3 Commit 1: build a {staker_id: total_carry_amount} map so
    # AI seats the player has outstanding carries with can be annotated
    # in the response. A single owner can carry from the same lender
    # across multiple sessions, so values aggregate. Built once before
    # the table loop to keep serialization O(seats × 1).
    carries_by_staker: Dict[str, int] = {}
    if stake_repo is not None:
        try:
            for stake in stake_repo.list_carries_for_borrower(
                owner_id, BORROWER_KIND_HUMAN,
            ):
                if stake.staker_id is None:
                    continue  # house carries shouldn't exist; skip
                carries_by_staker[stake.staker_id] = (
                    carries_by_staker.get(stake.staker_id, 0)
                    + int(stake.carry_amount)
                )
        except Exception as e:
            logger.warning("[CASH][LOBBY] carry annotation load failed: %s", e)

    # Phase 4 UI extra: build the set of AIs currently in any active
    # stake (borrower or staker side). The lobby glyph on TableCard
    # surfaces this so the player can see who's in a live stake
    # position without opening every dossier. Bulk-fetch keeps the
    # cost a single query regardless of seat count.
    active_stake_pids: set = set()
    if stake_repo is not None:
        try:
            active_stake_pids = set(
                stake_repo.get_active_personality_participants()
            )
        except Exception as e:
            logger.warning(
                "[CASH][LOBBY] active stake participants load failed: %s", e,
            )

    response_tables = []
    for table in tables:
        big_blind, min_buy_in, max_buy_in = table_buy_in_window(table.stake_label)

        # Affordability tri-state mirrors CashModeEntry's `stakeAvailability`
        # client logic and `is_sponsor_eligible` server rule.
        if bankroll.chips >= min_buy_in:
            affordability = "affordable"
        elif is_sponsor_eligible(bankroll.chips, table.stake_label):
            affordability = "sponsor_eligible"
        else:
            affordability = "locked"

        # Tier at this table — bounded by carry load relative to this
        # stake's carry cap. Drops with carry growth; the player sees
        # tier degradation per-card so they can pick a table whose
        # tier matches the offer quality they want.
        try:
            table_tier = resolve_tier(
                borrower_id=owner_id,
                current_stake_label=table.stake_label,
                stake_repo=stake_repo,
            ) if stake_repo is not None else TIER_PREMIUM
        except Exception as e:
            logger.warning("[CASH][LOBBY] tier resolution failed for %r: %s",
                           table.stake_label, e)
            table_tier = TIER_PREMIUM

        serialized_seats = []
        for idx, slot in enumerate(table.seats):
            entry = {"index": idx, "kind": slot["kind"]}
            if slot["kind"] == "ai":
                pid = slot["personality_id"]
                personality = None
                try:
                    personality = personality_repo.load_personality_by_id(pid)
                except Exception:
                    personality = None
                # Orphan seat: the seat references a personality that no
                # longer exists in the DB (manual cleanup, migration, or
                # an old seat surviving a deletion). Render as `open` so
                # the next refresh_table_roster tick can fill it, and
                # critically — do NOT call get_avatar_url_with_fallback
                # with the personality_id as the name: that triggers
                # on-demand avatar generation, which calls
                # personality_generator.get_personality(name) which
                # auto-creates a new personality with the pid as the
                # display name. That's how "ai_12"-style zombies came
                # back after deletion.
                if personality is None:
                    entry = {"index": idx, "kind": "open"}
                    serialized_seats.append(entry)
                    continue
                entry["personality_id"] = pid
                ai_name = personality.get("name") or pid
                entry["name"] = ai_name
                entry["chips"] = int(slot.get("chips", 0))
                # Emotion resolution priority:
                #   1. active_emotions[name] — live in-memory state from
                #      the player's current cash table (always freshest).
                #   2. unseated_emotions[pid] — persisted state for AIs
                #      at tables the player isn't at (schema v97).
                #   3. "confident" default — fallback for AIs that have
                #      never been touched by sim.
                if ai_name in active_emotions:
                    emotion = active_emotions[ai_name]
                elif pid in unseated_emotions:
                    emotion = unseated_emotions[pid]
                else:
                    emotion = "confident"
                entry["emotion"] = emotion
                entry["avatar_url"] = get_avatar_url_with_fallback(
                    None, ai_name, emotion,
                )
                # Relationship hint: lender's POV of the player.
                hint = ""
                try:
                    rel = relationship_repo.load_relationship_state(
                        observer_id=pid, opponent_id=owner_id,
                    )
                    if rel is not None:
                        from cash_mode.sponsor_offers import _relationship_hint
                        hint = _relationship_hint(
                            likability=rel.likability,
                            heat=rel.heat,
                            respect=rel.respect,
                        )
                except Exception:
                    hint = ""
                entry["relationship_hint"] = hint
                # Phase 3 Commit 1: surface outstanding carries to this
                # AI so the lobby card can render a "you owe them"
                # corner pin. Aggregated across all carries to this
                # lender (separate sessions may have produced multiple).
                if pid in carries_by_staker:
                    entry["carry_amount"] = carries_by_staker[pid]
                # Phase 4 UI extra: mark AIs currently in any active
                # stake position. The frontend renders a small glyph
                # distinct from the carry-pin so the player can spot
                # active stake dynamics at a glance.
                if pid in active_stake_pids:
                    entry["in_active_stake"] = True
            elif slot["kind"] == "human":
                entry["personality_id"] = slot.get("personality_id")
                entry["chips"] = int(slot.get("chips", 0))
            serialized_seats.append(entry)

        response_tables.append({
            "table_id": table.table_id,
            "stake_label": table.stake_label,
            "big_blind": big_blind,
            "min_buy_in": min_buy_in,
            "max_buy_in": max_buy_in,
            "affordability": affordability,
            "seats": serialized_seats,
            "dealer_index": get_dealer_index(table),
            "tier": table_tier,
            # v111: surface name + table_type so the frontend can render
            # tier-grouped sections with friendly labels.
            "table_name": table.name,
            "table_type": table.table_type,
        })

    # Top-level tier reflects "what tier am I currently playing at?".
    # `_resolve_player_tier_stake_label` consolidates the active-session
    # → bankroll → cheapest fallback chain; the same helper drives
    # `/api/cash/net-worth` so the two surfaces can't disagree.
    current_tier_stake = _resolve_player_tier_stake_label(
        owner_id, bankroll.chips,
    )

    try:
        current_tier = resolve_tier(
            borrower_id=owner_id,
            current_stake_label=current_tier_stake,
            stake_repo=stake_repo,
        ) if stake_repo is not None else TIER_PREMIUM
    except Exception as e:
        logger.warning("[CASH][LOBBY] current tier resolution failed: %s", e)
        current_tier = TIER_PREMIUM

    # v110 — same pending forgiveness count the wallet badge reads.
    # Bundled into the lobby response so the badge can update every
    # poll tick without a second round trip; the actual request list
    # is fetched on demand when the player opens the Net Worth Drawer.
    pending_forgiveness_count = 0
    if stake_repo is not None:
        try:
            pending_forgiveness_count = len(
                stake_repo.list_pending_forgiveness_for_staker(owner_id)
            )
        except Exception as e:
            logger.warning(
                "[CASH][LOBBY] pending forgiveness count failed: %s", e,
            )

    from cash_mode.activity import recent_events, serialize_event

    # Vice spending: surface AIs currently on a vice so the frontend
    # can render an "Away" group + ETA badges. Best-effort — failures
    # here shouldn't break the lobby response.
    active_vices_payload = []
    try:
        from flask_app.extensions import vice_state_repo
        if vice_state_repo is not None and sandbox_id is not None:
            actives = vice_state_repo.list_active(
                sandbox_id=sandbox_id, now=datetime.utcnow(),
            )
            for v in actives:
                try:
                    p = personality_repo.load_personality_by_id(v.personality_id)
                except Exception:
                    p = None
                name = (p.get("name") if isinstance(p, dict) else None) or v.personality_id
                active_vices_payload.append({
                    "personality_id": v.personality_id,
                    "name": name,
                    "narration": v.narration,
                    "duration_bucket": v.duration_bucket,
                    "started_at": v.started_at.isoformat(),
                    "ends_at": v.ends_at.isoformat(),
                    "amount": v.amount,
                })
    except Exception as exc:
        logger.warning("[CASH][LOBBY] active_vices payload failed: %s", exc)

    return jsonify({
        "bankroll": bankroll.chips,
        "tier": current_tier,
        "tier_stake_label": current_tier_stake,
        "tables": response_tables,
        "events": [
            serialize_event(e)
            for e in recent_events(limit=5, sandbox_id=sandbox_id)
        ],
        "pending_forgiveness_count": pending_forgiveness_count,
        "active_vices": active_vices_payload,
    })


@cash_bp.route("/api/cash/net-worth", methods=["GET"])
def get_net_worth():
    """GET /api/cash/net-worth — bankroll, tier, carries, headroom.

    Phase 3 Commit 1 of the backing system handoff. Returns the
    player's full financial position so the Net Worth drawer can
    render bankroll + outstanding carries + tier status + headroom
    in one fetch.

    Response shape:
      {
        "bankroll": int,
        "tier_stake_label": str,        # e.g. "$50"
        "tier_status": str,             # 'premium' | 'standard' | 'restricted' | 'house_only'
        "carry_cap": int,               # 10 × min_buy_in @ tier_stake_label
        "payables": [
          {stake_id, staker_id, staker_kind, staker_display_name,
           carry_amount, principal, stake_tier, created_at}, ...
        ],
        "receivables": [],              # Phase 5 stub
        "net_worth": int,               # bankroll + Σreceivables − Σpayables
        "available": int,               # max(0, carry_cap − Σpayables)
      }

    Naming note: `tier_status` (carry-load gate) vs `tier_stake_label`
    (the stake the gate applies to) mirrors the same two keys returned
    by `/api/cash/lobby` so the frontend's tier rendering can share
    type definitions across both surfaces. The handoff doc uses `tier`
    for the stake label; that name clashes with the lobby route's
    pre-existing `tier` field (which means status), so we follow the
    lobby naming here and rename in the spec rather than confuse the
    wire format. No `game_id` required — the response is per-owner.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    from flask_app.extensions import bankroll_repo, personality_repo, stake_repo
    from cash_mode.staking_tier import (
        TIER_PREMIUM,
        max_carry_for_tier,
        resolve_tier,
    )

    bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
    tier_stake_label = _resolve_player_tier_stake_label(owner_id, bankroll.chips)

    try:
        tier_status = (
            resolve_tier(
                borrower_id=owner_id,
                current_stake_label=tier_stake_label,
                stake_repo=stake_repo,
            )
            if stake_repo is not None
            else TIER_PREMIUM
        )
    except Exception as e:
        logger.warning("[CASH][NET_WORTH] tier resolution failed: %s", e)
        tier_status = TIER_PREMIUM

    carry_cap = max_carry_for_tier(tier_stake_label)

    carries: List[Stake] = []
    if stake_repo is not None:
        try:
            carries = stake_repo.list_carries_for_borrower(
                owner_id, BORROWER_KIND_HUMAN,
            )
        except Exception as e:
            logger.warning("[CASH][NET_WORTH] list_carries failed: %s", e)

    payables: List[Dict[str, Any]] = []
    payables_sum = 0
    # Personality-name cache so repeated carries to the same lender
    # don't trigger N+1 reads. Most players will have ≤3 carries; the
    # cache is more for symmetry with the sponsor route's pattern.
    name_cache: Dict[str, str] = {}
    for stake in carries:
        if stake.staker_id is None:
            # Defensive: house carries shouldn't exist (Phase 1's
            # settle_stake_on_leave forgives them). Skip rather than
            # crash if one slipped through.
            continue
        display_name = name_cache.get(stake.staker_id, stake.staker_id)
        if (
            stake.staker_id not in name_cache
            and stake.staker_kind == STAKER_KIND_PERSONALITY
        ):
            try:
                personality = personality_repo.load_personality_by_id(
                    stake.staker_id,
                )
                if personality and personality.get("name"):
                    display_name = personality["name"]
            except Exception:
                pass  # fall back to id
            name_cache[stake.staker_id] = display_name
        payables.append({
            "stake_id": stake.stake_id,
            "staker_id": stake.staker_id,
            "staker_kind": stake.staker_kind,
            "staker_display_name": display_name,
            "carry_amount": int(stake.carry_amount),
            "principal": int(stake.principal),
            "stake_tier": stake.stake_tier,
            "created_at": (
                stake.created_at.isoformat() if stake.created_at else None
            ),
        })
        payables_sum += int(stake.carry_amount)

    # Phase 5 — populate receivables from BOTH active stakes the
    # player is funding AND carries owed to them. Active stakes
    # surface the in-flight position (`principal` chips currently on
    # the borrower's seat, settling at session end); carries surface
    # residual debt from busted sessions (`carry_amount` chips that
    # the borrower owes but hasn't paid back).
    #
    # Both share the receivables surface so the player has one place
    # to see "what's mine that isn't in my bankroll right now." A
    # `status` field on each row tells the UI which framing to use
    # ("in play" vs "owed").
    receivables: List[Dict[str, Any]] = []
    active_receivables_sum = 0
    carry_receivables_sum = 0

    def _borrower_display_for(stake) -> str:
        if stake.borrower_id in name_cache:
            return name_cache[stake.borrower_id]
        display = stake.borrower_id
        if stake.borrower_kind != BORROWER_KIND_HUMAN:
            try:
                personality = personality_repo.load_personality_by_id(
                    stake.borrower_id,
                )
                if personality and personality.get("name"):
                    display = personality["name"]
            except Exception:
                pass
        name_cache[stake.borrower_id] = display
        return display

    if stake_repo is not None:
        # Active stakes in flight — chips on the seat, not yet settled.
        try:
            active_for_staker = stake_repo.list_active_stakes_for_staker(owner_id)
        except Exception as e:
            logger.warning(
                "[CASH][NET_WORTH] list_active_stakes_for_staker failed: %s", e,
            )
            active_for_staker = []
        for stake in active_for_staker:
            amount = int(stake.principal) + int(stake.match_amount)
            receivables.append({
                "stake_id": stake.stake_id,
                "borrower_id": stake.borrower_id,
                "borrower_kind": stake.borrower_kind,
                "borrower_display_name": _borrower_display_for(stake),
                # `amount` is the unified field the UI renders; the
                # row's `status` tells the UI whether to call it
                # "in play" (active) or "owed" (carry).
                "amount": amount,
                "carry_amount": int(stake.carry_amount),
                "principal": int(stake.principal),
                "match_amount": int(stake.match_amount),
                "stake_tier": stake.stake_tier,
                "status": stake.status,  # 'active'
                "format": stake.format,
                "cut": float(stake.cut),
                "created_at": (
                    stake.created_at.isoformat() if stake.created_at else None
                ),
            })
            active_receivables_sum += amount

        # Carries owed to the player by AIs who busted under their stake.
        try:
            receivable_carries = stake_repo.list_carries_for_staker(owner_id)
        except Exception as e:
            logger.warning(
                "[CASH][NET_WORTH] list_carries_for_staker failed: %s", e,
            )
            receivable_carries = []
        for stake in receivable_carries:
            carry = int(stake.carry_amount)
            receivables.append({
                "stake_id": stake.stake_id,
                "borrower_id": stake.borrower_id,
                "borrower_kind": stake.borrower_kind,
                "borrower_display_name": _borrower_display_for(stake),
                "amount": carry,
                "carry_amount": carry,
                "principal": int(stake.principal),
                "match_amount": int(stake.match_amount),
                "stake_tier": stake.stake_tier,
                "status": stake.status,  # 'carry'
                "format": stake.format,
                "cut": float(stake.cut),
                "created_at": (
                    stake.created_at.isoformat() if stake.created_at else None
                ),
            })
            carry_receivables_sum += carry

    receivables_sum = active_receivables_sum + carry_receivables_sum

    # Phase 5 — recently closed stakes (settled / defaulted) where the
    # player was either staker or borrower. Gives the player a history
    # surface so cleanly-settled stakes don't just disappear into
    # bankroll, and explicit defaults leave a visible trail.
    history: List[Dict[str, Any]] = []
    if stake_repo is not None:
        try:
            closed = stake_repo.list_recent_closed_for_owner(owner_id, limit=20)
        except Exception as e:
            logger.warning(
                "[CASH][NET_WORTH] list_recent_closed_for_owner failed: %s", e,
            )
            closed = []
        for stake in closed:
            # Each row gets a `role` (am I the staker or the borrower
            # on this one?) so the UI can frame it from the player's
            # POV. `counterparty_*` is the other side regardless of role.
            if stake.staker_id == owner_id:
                role = "staker"
                counterparty_id = stake.borrower_id
                counterparty_kind = stake.borrower_kind
            else:
                role = "borrower"
                counterparty_id = stake.staker_id
                counterparty_kind = stake.staker_kind
            counterparty_display = counterparty_id or "House"
            if counterparty_id and counterparty_id not in name_cache:
                # Resolve display name; house stakes (counterparty=None)
                # skip the lookup and render as "House" above.
                if counterparty_kind == BORROWER_KIND_HUMAN:
                    name_cache[counterparty_id] = counterparty_id
                else:
                    try:
                        p = personality_repo.load_personality_by_id(
                            counterparty_id,
                        )
                        if p and p.get("name"):
                            counterparty_display = p["name"]
                            name_cache[counterparty_id] = counterparty_display
                    except Exception:
                        name_cache[counterparty_id] = counterparty_display
            elif counterparty_id:
                counterparty_display = name_cache[counterparty_id]
            # Compute per-role P&L when settlement chip flows were
            # captured (v106+). Legacy settled-pre-v106 rows have NULL
            # payouts → net is None and the UI hides the P&L line.
            #
            # Settled-cleanly P&L:
            #   staker:   payout − principal (+ origination_fee received on pure)
            #   borrower: payout − match_amount − origination_fee_paid
            # Settled-with-carry P&L (a transient status before this
            # row would be in History — only reaches here as 'defaulted'
            # post-explicit-default — but the math still holds):
            #   staker:   staker_payout − principal (negative, equals -carry)
            #   borrower: borrower_payout − match_amount (typically 0)
            # Defaulted P&L assumes the carry was already realized at
            # the original settle into 'carry'; defaulting just zeros
            # the IOU. Net for the staker who was owed = same as the
            # carry-creation moment (lost principal − staker_payout).
            net_for_player: Optional[int] = None
            payout = (
                stake.staker_payout if role == "staker"
                else stake.borrower_payout
            )
            if payout is not None:
                if role == "staker":
                    # Player put up principal; received payout (+ any
                    # origination fee at deal time, on pure stakes only).
                    cost = int(stake.principal)
                    proceeds = int(payout)
                    if stake.format == STAKE_FORMAT_PURE:
                        proceeds += int(stake.origination_fee)
                    net_for_player = proceeds - cost
                else:  # borrower
                    # Player put up match_amount + origination_fee;
                    # received payout. Principal was the staker's chips,
                    # not the borrower's — borrower's stack at the seat
                    # was already cash they got from the deal, not
                    # something they "spent."
                    cost = int(stake.match_amount)
                    if stake.format == STAKE_FORMAT_PURE:
                        cost += int(stake.origination_fee)
                    proceeds = int(payout)
                    net_for_player = proceeds - cost
            history.append({
                "stake_id": stake.stake_id,
                "role": role,
                "status": stake.status,  # 'settled' | 'defaulted'
                "counterparty_id": counterparty_id,
                "counterparty_kind": counterparty_kind,
                "counterparty_display_name": counterparty_display,
                "principal": int(stake.principal),
                "match_amount": int(stake.match_amount),
                "stake_tier": stake.stake_tier,
                "format": stake.format,
                "cut": float(stake.cut),
                # Chip flows captured at settle time. NULL on legacy
                # pre-v106 rows; the UI hides the P&L line then.
                "staker_payout": (
                    int(stake.staker_payout)
                    if stake.staker_payout is not None else None
                ),
                "borrower_payout": (
                    int(stake.borrower_payout)
                    if stake.borrower_payout is not None else None
                ),
                # Net for the player on this stake (positive = won
                # money, negative = lost money). null when chip flows
                # weren't captured (pre-v106 history).
                "net_for_player": net_for_player,
                "created_at": (
                    stake.created_at.isoformat() if stake.created_at else None
                ),
                "settled_at": (
                    stake.settled_at.isoformat() if stake.settled_at else None
                ),
            })

    net_worth = int(bankroll.chips) + receivables_sum - payables_sum
    available = max(0, int(carry_cap) - payables_sum)

    # v110 — pending forgiveness asks waiting on this player's
    # decision. Surfaced as a count here so the Lobby's wallet badge
    # can light up without an extra round-trip; the actual request
    # list is fetched on demand via GET /api/cash/forgiveness-requests
    # when the player opens the Net Worth Drawer.
    try:
        pending_forgiveness = stake_repo.list_pending_forgiveness_for_staker(
            owner_id,
        )
        pending_forgiveness_count = len(pending_forgiveness)
    except Exception as exc:
        logger.warning(
            "[CASH] pending forgiveness count failed for %r: %s",
            owner_id, exc,
        )
        pending_forgiveness_count = 0

    return jsonify({
        "bankroll": int(bankroll.chips),
        "tier_stake_label": tier_stake_label,
        "tier_status": tier_status,
        "carry_cap": int(carry_cap),
        "payables": payables,
        "receivables": receivables,
        "history": history,
        "net_worth": int(net_worth),
        "available": int(available),
        "pending_forgiveness_count": int(pending_forgiveness_count),
    })


@cash_bp.route("/api/cash/state", methods=["GET"])
def get_state():
    """GET /api/cash/state — entry-screen snapshot.

    Always returns `bankroll` at the top level (seeding a fresh row
    if needed) so the stake picker can show the player's bankroll
    and grey out tiers they can't afford. `state` is the active-
    session redirect target (or None when no session is live).

    Response shape: `{state: null | {game_id, stake_label}, bankroll: int}`.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    bankroll = _load_or_seed_player_bankroll(owner_id)
    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({
            "state": None,
            "bankroll": bankroll.chips,
        })

    from flask_app.services import game_state_service

    # The entry screen needs game_id to redirect; stake_label is a
    # nicety. Tolerate missing game_data (DB-only id after a restart;
    # the /game/:id cold-load will rehydrate it).
    game_data = game_state_service.get_game(game_id)
    return jsonify({
        "state": {
            "game_id": game_id,
            "stake_label": game_data.get("cash_stake_label") if game_data else None,
        },
        "bankroll": bankroll.chips,
    })
