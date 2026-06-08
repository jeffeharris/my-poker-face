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

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from cash_mode.bankroll import (
    AIBankrollState,
    PlayerBankrollState,
    credit_ai_cash_out,
    project_bankroll,
)
from cash_mode.sponsor_offers import (
    VILLAIN_REGARD_FLOOR,
    LenderRejection,
    PersonalitySponsorOffer,
    compute_offers_for_table,
    compute_personality_offers,
    offer_for_archetype,
)
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
from cash_mode.tables import personality_for_seat
from core.economy import ledger as chip_ledger
from flask_app import config

# `limiter` is a real Limiter constructed at import time in extensions.py
# (init_limiter() only attaches the storage backend via init_app()), so it is
# never None and never reassigned — a top-level import safely captures it
# (unlike the repo globals, which are reassigned by init_persistence() and must
# be imported lazily).
from flask_app.extensions import limiter
from flask_app.services.sandbox_resolver import resolve_default_sandbox_for
from poker.memory.opponent_model import REGARD_NEUTRAL
from poker.memory.relationship_events import RelationshipEvent

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

    Tier 3: a candidate row only counts as "active" if its
    `cash_sessions.session_state` is blocking (active/paused/abandoning).
    A `closed`/`broken` session whose `cash-*` games row lingers (failed
    delete, between sweeps, or a stray in-memory resurrection of a
    closed session) no longer wedges every new sit — the explicit state
    is the source of truth, not the mere existence of a row. Rows with
    no `cash_sessions` record at all (legacy / a create that failed
    before the session row landed) stay blocking as a fail-safe, so we
    never lose a real session to a missing-row read.
    """
    from flask_app.services import game_state_service

    for gid, gdata in list(game_state_service.games.items()):
        if gdata.get("cash_mode") and gdata.get("owner_id") == owner_id:
            if _cash_session_blocks(gid):
                return gid

    # Authoritative DB lookup (Codex review #4): query cash_sessions
    # directly for a blocking session. Unbounded + state-filtered in SQL,
    # so — unlike the old `list_games(limit=50)` + filter — it can't miss
    # a real session that happens to sort past the cap.
    from flask_app.extensions import cash_session_repo, game_repo

    if cash_session_repo is not None:
        try:
            sid = cash_session_repo.find_blocking_session_id_for_owner(owner_id)
            if sid is not None:
                return sid
        except Exception:
            # Fall through to the legacy net rather than fail-open here.
            pass

    # Legacy fail-safe net: a `cash-*` games row with NO cash_sessions
    # record (a sit that errored before the session row landed) still
    # blocks, via `_cash_session_blocks`'s missing-row fail-safe. The
    # direct query above can't see such a row. Bounded scan, limit bumped
    # past the old 50 so a busy owner's orphan isn't missed (Codex #4).
    try:
        rows = game_repo.list_games(owner_id=owner_id, limit=200, offset=0)
    except Exception:
        return None
    for row in rows:
        if row.game_id.startswith("cash-") and _cash_session_blocks(row.game_id):
            return row.game_id
    return None


def _cash_session_blocks(game_id: str) -> bool:
    """Whether a cash session should block a new sit / count as active.

    Reads the explicit `session_state` (Tier 3). A row in a terminal,
    non-blocking state (`closed`/`broken`) does not block. A missing
    `cash_sessions` record (legacy, or a sit that errored before the
    row landed) is treated as blocking — fail-safe, so a real frozen
    session is never lost to a read miss. Any lookup error also blocks
    (same fail-safe direction).
    """
    from cash_mode.cash_sessions import SESSION_STATES_BLOCKING
    from flask_app.extensions import cash_session_repo

    if cash_session_repo is None:
        return True
    try:
        session = cash_session_repo.load(game_id)
    except Exception:
        return True
    if session is None:
        return True
    return session.session_state in SESSION_STATES_BLOCKING


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
        logger.debug(f"[CASH][LOBBY] emotion resolution failed for {personality_id}: {exc}")
        return "confident"


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
                "[CASH] purge: delete_game(%r) failed: %s",
                row.game_id,
                e,
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
                    row.game_id,
                    e,
                )
    if purged:
        logger.info(
            "[CASH] purged %d prior cash row(s) for owner=%r: %s",
            len(purged),
            owner_id,
            purged,
        )
    return len(purged)


def _release_own_reserved_holds(owner_id: str, *, sandbox_id: str) -> int:
    """Open any `"reserved"` sponsorship seat-hold owned by `owner_id`.

    A real UX need NOT covered by the read-side projection: the sit/sponsor
    paths call this before claiming a new seat so a leftover hold from a
    previously-tapped seat (player tapped seat A, opened the SponsorModal,
    then tapped seat B instead) is freed rather than stranding seat A
    against live-fill.

    Only `"reserved"` slots owned by this player are touched. Stale `"human"`
    ghost seats are no longer swept here — the D1 read-side occupancy
    projection renders an unconfirmed cache slot `open` on read, and the
    deletion cascade frees the seat at the delete source; both self-heal on
    the next save_table.
    """
    from cash_mode.tables import open_slot
    from flask_app.extensions import cash_table_repo

    try:
        tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)
    except Exception as e:
        logger.warning(
            "[CASH] _release_own_reserved_holds: list_all_tables failed: %s",
            e,
        )
        return 0

    freed = 0
    for table in tables:
        for idx, slot in enumerate(table.seats):
            if slot.get("kind") != "reserved":
                continue
            if slot.get("personality_id") != owner_id:
                continue
            try:
                cash_table_repo.save_table(
                    table.with_seat(idx, open_slot()),
                    sandbox_id=sandbox_id,
                )
                logger.info(
                    "[CASH] _release_own_reserved_holds: freed reserved "
                    "table=%r seat=%d owner=%r",
                    table.table_id,
                    idx,
                    owner_id,
                )
                freed += 1
            except Exception as e:
                logger.warning(
                    "[CASH] _release_own_reserved_holds: save_table failed " "for %r:%d: %s",
                    table.table_id,
                    idx,
                    e,
                )
    return freed


def _first_open_seat_index(table) -> Optional[int]:
    """Return the lowest-index `"open"` seat on `table`, or None if full.

    The lobby's Sit/Sponsor buttons auto-pick `firstOpenIndex` from a
    poll snapshot that can be several seconds stale — by the time the tap
    lands, the world ticker's live-fill may have seated an AI in that
    exact seat. Rather than reject the whole tap with a 409 (which the
    UI surfaced as a silently-disabled button), the sit/sponsor paths
    fall back to whatever seat IS open on the same table via this helper.
    Only a genuinely full table 409s. Part of the cash-seat-conflict
    hardening — see `_release_own_reserved_holds` for the sibling helper.
    """
    for idx, slot in enumerate(table.seats):
        if slot.get("kind") == "open":
            return idx
    return None


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
        # `personality_for_seat` catches DB/decode failures and logs them;
        # programmer bugs propagate so they get fixed.
        personality = personality_for_seat(slot, personality_repo)
        # Tourists carry display_name on the seat; for regular AI seats
        # use the personality's name; fall back to pid.
        name = slot.get("display_name") or (personality or {}).get("name") or pid
        entry: Dict[str, Any] = {"personality_id": pid, "name": name}
        preselected_ai.append(entry)
        preselected_chips[pid] = int(slot.get("chips", 0))
        next_player_idx += 1
    return preselected_ai, preselected_chips, dealer_player_idx


def _load_or_seed_player_bankroll(
    owner_id: str,
    *,
    sandbox_id: Optional[str] = None,
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
                owner_id,
                sandbox_repo=sandbox_repo,
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
    logger.info(
        "[CASH] Seeded fresh bankroll for %r at %d chips (sandbox=%r)",
        owner_id,
        DEFAULT_PLAYER_STARTING_BANKROLL,
        sandbox_id,
    )
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
    """Thin wrapper — see `cash_mode.cash_session_persistence.increment_cash_session_buy_in`.

    Also emits the Cut-2 human chip statement: a rebuy / top-up is the
    same bankroll -> seat movement as the initial buy-in, so it gets a
    paired `player_buy_in` transfer row. owner_id + sandbox_id are read
    off the session (the only place they're reliably in scope from both
    the rebuy and top-up call sites). Conservation-neutral; best-effort.
    """
    from cash_mode.cash_session_persistence import increment_cash_session_buy_in
    from flask_app.extensions import cash_session_repo, chip_ledger_repo

    increment_cash_session_buy_in(cash_session_repo, game_id, amount)

    if chip_ledger_repo is None or amount <= 0 or cash_session_repo is None:
        return
    try:
        session = cash_session_repo.load(game_id)
    except Exception:
        session = None
    if session is None:
        return
    chip_ledger.record_player_buy_in(
        chip_ledger_repo,
        owner_id=session.owner_id,
        game_id=game_id,
        amount=amount,
        context={'site': 'cash_rebuy_or_topup'},
        sandbox_id=session.sandbox_id,
    )


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
    if stake is None or stake.borrower_id != owner_id or stake.borrower_kind != BORROWER_KIND_HUMAN:
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
    table_type: str = 'lobby',
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
        bankroll_repo,
        capture_label_repo,
        decision_analysis_repo,
        game_repo,
        hand_history_repo,
        persistence_db_path,
        personality_repo,
        relationship_repo,
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
                    stored,
                    knobs.starting_bankroll,
                    knobs.bankroll_rate,
                    now,
                )
            if projected < ai_threshold:
                continue
            selected_ai.append({"personality_id": pid, "name": name})
            ai_buy_ins[pid] = ai_buy_in
            ai_states[pid] = AIBankrollState(
                personality_id=pid,
                chips=projected,
                last_regen_tick=stored.last_regen_tick,
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
    from flask_app.game_adapter import StateMachineAdapter
    from flask_app.handlers.tiered_factory import build_controller
    from flask_app.routes.game_routes import generate_game_id, load_game_mode_preset
    from poker.cash_bot_assignment import assign_bot
    from poker.memory import AIMemoryManager
    from poker.poker_game import initialize_game_state
    from poker.poker_state_machine import PokerStateMachine
    from poker.pressure_detector import PressureEventDetector
    from poker.pressure_stats import PressureStatsTracker
    from poker.repositories.sqlite_repositories import PressureEventRepository
    from poker.table.seat import HumanSeat, PersonaSeat

    human_name = _resolve_player_name()
    ai_names = [a["name"] for a in selected_ai]

    game_state = initialize_game_state(
        player_names=ai_names,
        human_name=human_name,
        starting_stack=player_starting_stack,
        big_blind=big_blind,
        dealer_idx=dealer_player_idx,
    )
    # AI stacks may differ from the human's starting stack; adjust each. Also
    # stamp the canonical typed seat identity (T3-80) on every seat: the human's
    # HumanSeat keys on owner_id, each AI's PersonaSeat on its personality_id, so
    # seat_key(player) is the stable key the controller/memory bridges use.
    for idx, player in enumerate(game_state.players):
        if player.is_human:
            game_state = game_state.update_player(idx, seat_id=HumanSeat(owner_id))
            continue
        ai_entry = next((a for a in selected_ai if a["name"] == player.name), None)
        if ai_entry is None:
            continue
        pid = ai_entry["personality_id"]
        ai_buy_in = ai_buy_ins[pid]
        game_state = game_state.update_player(
            idx,
            stack=ai_buy_in,
            personality_id=pid,
            seat_id=PersonaSeat(pid),
        )

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
        # Fish are real curated personas now, so a plain DB lookup resolves
        # them (and their `rule_strategy: 'fish'` / `fish_leak`).
        if pid:
            personality_config = personality_repo.load_personality_by_id(pid)
        else:
            personality_config = None
        # Fish personalities route to
        # RuleBotController with `_strategy_fish`. assign_bot's poise-
        # bucket would otherwise put fish into 'chaos' (LLM-driven),
        # which both wastes tokens and ignores the personality's
        # designated leak. Detect via the personality's `rule_strategy`
        # field — set to 'fish' for both the JSON personalities and the
        # ephemeral tourists.
        rule_strategy_override = (
            (personality_config or {}).get("rule_strategy")
            if isinstance(personality_config, dict)
            else None
        )
        if rule_strategy_override == "fish":
            bot_types[player.name] = "fish"
            player_llm_configs[player.name] = {}
            # Pass the table's stake_label so build_fish_controller can force the
            # weak_fish loadout at the $2 bottom tier (rather than relying on its
            # big_blind reverse-lookup fallback). The fish's tell rides on persona
            # spot_tendencies, so the legacy `fish_leak` kwarg is no longer threaded.
            controller = build_controller(
                bot_type="fish",
                player_name=player.name,
                state_machine=state_machine,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=capture_label_repo,
                decision_analysis_repo=decision_analysis_repo,
                stake_label=stake_label,
            )
            ai_controllers[player.name] = controller
            continue
        assignment = assign_bot(personality_config)
        bot_types[player.name] = assignment.bot_type
        player_llm_configs[player.name] = assignment.llm_config

        controller = build_controller(
            bot_type=assignment.bot_type,
            player_name=player.name,
            state_machine=state_machine,
            llm_config=assignment.llm_config,
            prompt_config=default_prompt_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
            expression_enabled=True,
        )
        ai_controllers[player.name] = controller

    # T3-77 — seat each persona in the mood the cash world left it in. Hydrate
    # the freshly-built controller from the per-persona emotional_state_json
    # (schema v97); the leave/settle path flushes the evolved mood back, so a
    # cash table is two-way. No-op for a persona the world hasn't touched yet
    # (NULL column → baseline) and for the human (no persona blob). Fresh-seat
    # only — cold-load restores the per-game psychology_json instead, so it must
    # not run here (this is the build path, not the restore path).
    from cash_mode.psychology_persistence import hydrate_persona_psychology

    for ai in selected_ai:
        ctrl = ai_controllers.get(ai["name"])
        if ctrl is not None and ai.get("personality_id"):
            hydrate_persona_psychology(ctrl, ai["personality_id"], bankroll_repo, sandbox_id)

    # 4. Memory manager (cash_mode=True wires Phase 3 cash_pair_stats).
    pressure_event_repo = PressureEventRepository(persistence_db_path)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, pressure_event_repo)

    memory_manager = AIMemoryManager(game_id, persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(hand_history_repo)
    # Always wire the relationship repo — grinders and the human build
    # history with each other everywhere, including casino tables. Fish
    # are suppressed PER-PAIR via set_fish_ids (below): they're real but
    # transient chip-donors nobody should learn about (and they don't read
    # the dossier themselves), so any event/flow touching a fish is skipped
    # in the detector dispatch. Grinder↔grinder / grinder↔human pairs at
    # the same casino table still accrue normally.
    memory_manager.set_relationship_repo(
        relationship_repo,
        cash_mode=True,
        sandbox_id=sandbox_id,
        table_max_buy_in=max_buy_in,
    )
    memory_manager.set_fish_ids(
        {
            p['personality_id']
            for p in personality_repo.list_fish_for_cash_mode()
            if p.get('personality_id')
        }
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
        "messages": [
            {
                "id": "1",
                "sender": "Table",
                "content": welcome_message,
                "timestamp": datetime.now().isoformat(),
                "type": "table",
            }
        ],
        "hand_start_stacks": {p.name: p.stack for p in state_machine.game_state.players},
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
        game_id,
        state_machine._state_machine,
        owner_id,
        human_name,
        llm_configs={
            "player_llm_configs": player_llm_configs,
            "default_llm_config": default_llm_config,
            "bot_types": saved_bot_types,
        },
    )

    # New games adopt the owner's default coaching mode (sticky cross-device pref).
    from flask_app.handlers.game_handler import stamp_coach_default_mode

    stamp_coach_default_mode(game_id, owner_id)

    logger.info(
        "[CASH] Created game_id=%r owner=%r stake=%r player_stack=%d ai=%r bot_types=%r",
        game_id,
        owner_id,
        stake_label,
        player_starting_stack,
        [a["name"] for a in selected_ai],
        saved_bot_types,
    )
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
        return jsonify(
            {
                "error": "Invalid stake_label",
                "valid_stakes": list(STAKES_LADDER.keys()),
            }
        ), 400
    if not isinstance(buy_in, int) or buy_in <= 0:
        return jsonify({"error": "buy_in must be a positive integer"}), 400

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
    if buy_in < min_buy_in or buy_in > max_buy_in:
        return jsonify(
            {
                "error": (
                    f"buy_in {buy_in} out of range for {stake_label} table "
                    f"(min={min_buy_in}, max={max_buy_in})"
                ),
            }
        ), 400

    # Block duplicate sessions: one cash game per owner at a time.
    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify(
            {
                "error": "A cash session is already active. Leave first.",
                "game_id": existing,
            }
        ), 409

    from flask_app.extensions import bankroll_repo

    # Player bankroll: load or seed; verify affordability.
    player_bankroll = _load_or_seed_player_bankroll(owner_id)
    if player_bankroll.chips < buy_in:
        return jsonify(
            {
                "error": (
                    f"Insufficient bankroll: {player_bankroll.chips} chips, " f"buy_in {buy_in}"
                ),
            }
        ), 400

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
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(
            player_id=player_bankroll.player_id,
            chips=player_bankroll.chips - buy_in,
            starting_bankroll=player_bankroll.starting_bankroll,
        )
    )

    # Record the self-funded buy-in as a player -> seat ledger transfer, paired
    # with the leave-time `record_player_cash_out` (which fires unconditionally).
    # Without this the bankroll debit here is unledgered while the leave credits
    # it back, leaving an unpaired cash-out -> phantom chips in the derived
    # balance under chip custody. Mirrors the modern `/api/cash/sit` path.
    from flask_app.extensions import chip_ledger_repo as _chip_ledger_repo

    chip_ledger.record_player_buy_in(
        _chip_ledger_repo,
        owner_id=owner_id,
        game_id=game_id,
        amount=buy_in,
        context={'site': 'cash_start', 'stake_label': stake_label},
        sandbox_id=sandbox_id,
    )

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
        # The tapped seat filled in since the lobby snapshot. Fall back to
        # any other open seat on this table rather than rejecting the tap
        # (the stale-snapshot race that read as a dead button). Only a
        # genuinely full table 409s. The authoritative re-resolve happens
        # under the sandbox lock below; this is the pre-lock fast path so
        # the affordability/sponsor branch sees a real open seat.
        alt = _first_open_seat_index(table)
        if alt is None:
            return jsonify(
                {
                    "error": "Table is full",
                    "seat_kind": target_slot["kind"],
                }
            ), 409
        seat_index = alt
        target_slot = table.seats[seat_index]

    stake_label = table.stake_label
    if stake_label not in STAKES_LADDER:
        return jsonify(
            {
                "error": f"Table has invalid stake_label {stake_label!r}",
            }
        ), 500
    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    if buy_in is None:
        buy_in = min_buy_in
    if not isinstance(buy_in, int) or buy_in <= 0:
        return jsonify({"error": "buy_in must be a positive integer"}), 400
    if buy_in < min_buy_in or buy_in > max_buy_in:
        return jsonify(
            {
                "error": (
                    f"buy_in {buy_in} out of range for {stake_label} table "
                    f"(min={min_buy_in}, max={max_buy_in})"
                ),
            }
        ), 400

    # Block duplicate sessions: one cash game per owner at a time.
    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify(
            {
                "error": "A cash session is already active. Leave first.",
                "game_id": existing,
            }
        ), 409

    # Belt-and-suspenders against orphaned seats: the duplicate-session
    # check above guards against duplicate game rows. A stale human slot on
    # `cash_tables` no longer needs sweeping here — the read-side occupancy
    # projection renders an unconfirmed cache slot `open` and self-heals on
    # the next save_table. We DO release this player's own leftover
    # `"reserved"` sponsorship hold (abandoned SponsorModal on another seat)
    # so it can't strand a seat against live-fill before the new claim.
    _release_own_reserved_holds(owner_id, sandbox_id=sandbox_id)

    # Affordability + sponsor-eligibility branching.
    player_bankroll = _load_or_seed_player_bankroll(owner_id)
    if player_bankroll.chips < buy_in:
        if is_sponsor_eligible(player_bankroll.chips, stake_label):
            # Reserve the seat for the duration of the SponsorModal so the
            # world ticker's live-fill can't seat an AI in it while the
            # player picks a lender (the "cut by the AI" race). The
            # reservation is a `"reserved"` hold owned by this player; it
            # resolves to `"human"` on /sponsor-and-sit, back to `"open"`
            # on /release-seat (modal close) or TTL expiry (lobby sweep).
            #
            # Hold the per-sandbox seat lock for the read-check-reserve-save
            # — same race window as the self-funded claim below. The
            # `_release_own_reserved_holds` above already cleared any
            # prior reserved hold this player left on another seat.
            from cash_mode.tables import reserved_slot
            from flask_app.services import game_state_service

            with game_state_service.get_sandbox_lock(sandbox_id):
                fresh = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
                if fresh is None:
                    return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
                current_kind = fresh.seats[seat_index].get("kind")
                already_mine = (
                    current_kind == "reserved"
                    and fresh.seats[seat_index].get("personality_id") == owner_id
                )
                if current_kind != "open" and not already_mine:
                    # An AI (or another claim) took the seat between the
                    # tap and here. Fall back to any other open seat on
                    # this table so the SponsorModal opens against a seat
                    # the player can actually take; only a full table 409s.
                    alt = _first_open_seat_index(fresh)
                    if alt is None:
                        return jsonify(
                            {
                                "error": "Table is full",
                                "seat_kind": current_kind,
                            }
                        ), 409
                    seat_index = alt
                reserved_table = fresh.with_seat(
                    seat_index,
                    reserved_slot(owner_id, datetime.utcnow()),
                )
                cash_table_repo.save_table(reserved_table, sandbox_id=sandbox_id)

            return jsonify(
                {
                    "requires_sponsor": True,
                    "stake_label": stake_label,
                    "bankroll": player_bankroll.chips,
                    "min_buy_in": min_buy_in,
                    "max_buy_in": max_buy_in,
                    # Echo the held seat so the frontend can release it on
                    # modal-close and resend it on accept.
                    "table_id": table_id,
                    "seat_index": seat_index,
                }
            ), 402
        return jsonify(
            {
                "error": (
                    f"Insufficient bankroll: {player_bankroll.chips} chips, " f"buy_in {buy_in}"
                ),
                "bankroll": player_bankroll.chips,
            }
        ), 400

    # Persist the seat claim immediately so a second device can't
    # double-sit. The roster-based _build_cash_game below reads this
    # updated table.
    #
    # Re-load the table here: `table` was the snapshot taken at the
    # top of the route (line ~1005), but `_release_own_reserved_holds`
    # above may have just rewritten the row to clear a leftover reserved
    # hold. Using the stale snapshot would re-introduce the hold when we
    # `.with_seat()` + save below — last-write-wins on the `cash_tables`
    # row.
    from cash_mode.tables import human_slot
    from flask_app.services import game_state_service

    # Take the per-sandbox seat lock for the read-check-claim-save: the
    # world ticker's refresh_unseated_tables live-fills open seats on the
    # same `cash_tables` blob, so without this a ticker tick between our
    # load and save would be clobbered (its AI's buy-in stranded). The
    # window is small — once we save a human into the seat the ticker
    # skips this table — so we hold the lock only around the claim.
    with game_state_service.get_sandbox_lock(sandbox_id):
        table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
        if table is None:
            return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
        if table.seats[seat_index]["kind"] != "open":
            # Lost the seat to live-fill between the pre-lock resolve and
            # acquiring the lock. Re-resolve to any open seat on the table;
            # only a now-full table 409s.
            alt = _first_open_seat_index(table)
            if alt is None:
                return jsonify(
                    {
                        "error": "Table is full",
                        "seat_kind": table.seats[seat_index]["kind"],
                    }
                ), 409
            seat_index = alt
        # Presence-authoritative occupancy guard (read-side migration): when
        # entity_presence is the authority, trust IT for "is this seat free",
        # not just the cash_tables cache. Catches a cache↔authority disagreement
        # (a ghost in the cache that presence knows is occupied) at the point of
        # sit — preventing a double-book the cache alone would allow. Gated:
        # authority-off keeps the pure cash_tables check unchanged.
        from cash_mode import economy_flags as _ef

        if _ef.PRESENCE_AUTHORITY_ENABLED:
            from cash_mode.presence import player_entity_id as _peid
            from flask_app.extensions import entity_presence_repo as _epr

            if _epr is not None:
                _me = _peid(owner_id)

                def _presence_free(idx: int) -> bool:
                    occ = _epr.seat_occupant(sandbox_id, table_id, idx)
                    return occ is None or occ.entity_id == _me

                if not _presence_free(seat_index):
                    alt = next(
                        (
                            i
                            for i, s in enumerate(table.seats)
                            if s.get("kind") == "open" and _presence_free(i)
                        ),
                        None,
                    )
                    if alt is None:
                        return jsonify(
                            {"error": "Table is full", "seat_kind": "presence_occupied"}
                        ), 409
                    seat_index = alt
        claimed_table = table.with_seat(seat_index, human_slot(owner_id, buy_in))
        # save_table drives the human SIT into entity_presence authoritatively
        # inside its own transaction (the chokepoint), clearing any stale seat
        # occupant so the SIT can't collide in the partial-unique index. Inside
        # the sandbox lock per the §6.1 atomicity contract.
        cash_table_repo.save_table(claimed_table, sandbox_id=sandbox_id)

    # Build the cash game using the table's CURRENT AI roster + chip
    # counts, sourced via the shared preselected-builder.
    from flask_app.extensions import personality_repo

    preselected_ai, preselected_chips, dealer_player_idx = _build_preselected_from_table(
        claimed_table=claimed_table,
        seat_index=seat_index,
        personality_repo=personality_repo,
    )

    game_id, err = _build_cash_game(
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        stake_label=stake_label,
        player_starting_stack=buy_in,
        welcome_message=(f"*** Cash table {stake_label} — sit down at ${buy_in} ***"),
        preselected_ai=preselected_ai,
        preselected_ai_chips=preselected_chips,
        dealer_player_idx=dealer_player_idx,
        table_type=claimed_table.table_type,
    )
    if err is not None:
        # Roll back the seat claim so the player can retry. Re-read under
        # the sandbox lock and re-open ONLY our seat (rather than writing
        # back the stale pre-claim snapshot), so we don't clobber any
        # live-fill the ticker / a concurrent sit did to other seats.
        from cash_mode.tables import open_slot

        with game_state_service.get_sandbox_lock(sandbox_id):
            current = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
            if current is not None and current.seats[seat_index].get("kind") == "human":
                cash_table_repo.save_table(
                    current.with_seat(seat_index, open_slot()), sandbox_id=sandbox_id
                )
        return jsonify(err[0]), err[1]

    # Debit the player's bankroll. Loan fields stay zeroed — this is
    # the self-funded path.
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(
            player_id=player_bankroll.player_id,
            chips=player_bankroll.chips - buy_in,
            starting_bankroll=player_bankroll.starting_bankroll,
        )
    )

    # Human chip statement (Cut 2): record the initial self-funded buy-in
    # as a transfer player -> seat, paired with the leave-time cash-out.
    # Conservation-neutral; best-effort, never blocks the sit. (Rebuy /
    # top-up emit their own buy-in rows via _increment_cash_session_buy_in.
    # Staked sit-downs put up 0 of the player's own chips on a pure stake,
    # so they have no self-funded buy-in to record here.)
    from flask_app.extensions import chip_ledger_repo as _chip_ledger_repo

    chip_ledger.record_player_buy_in(
        _chip_ledger_repo,
        owner_id=owner_id,
        game_id=game_id,
        amount=buy_in,
        context={'site': 'cash_sit', 'stake_label': stake_label},
        sandbox_id=sandbox_id,
    )

    # Stash the table_id + seat_index on the game_data so /api/cash/leave
    # can free the seat back to "open" at session end.
    from flask_app.services import game_state_service

    game_data = game_state_service.get_game(game_id)
    if game_data is not None:
        game_data["cash_table_id"] = table_id
        game_data["cash_seat_index"] = seat_index
        # Friendly room name for the in-game header chip + arrival toast.
        # Rides along with table_id so build_cash_mode_payload stays a
        # pure reader (no per-frame DB lookup on the hot game-state path).
        game_data["cash_table_name"] = claimed_table.name
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

    return jsonify(
        {
            "game_id": game_id,
            "table_id": table_id,
            "seat_index": seat_index,
        }
    )


@cash_bp.route("/api/cash/release-seat", methods=["POST"])
def release_seat():
    """POST /api/cash/release-seat  body: {table_id, seat_index}

    Release a sponsorship seat-hold the player placed via the
    `/api/cash/sit` 402 path. Called by the frontend when the
    SponsorModal is dismissed without sitting, so the held seat returns
    to the live-fill pool immediately rather than waiting out the TTL.

    Only frees a `"reserved"` seat owned by the caller — never touches
    `"open"`, `"ai"`, or `"human"` seats. Idempotent: a hold that's
    already gone (expired, released, or claimed) returns 200 with
    `released: False` so a double-fire from the client is harmless.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    payload = request.get_json(silent=True) or {}
    table_id = payload.get("table_id")
    seat_index = payload.get("seat_index")

    if not isinstance(table_id, str) or not table_id:
        return jsonify({"error": "table_id is required"}), 400
    if not isinstance(seat_index, int) or seat_index < 0:
        return jsonify({"error": "seat_index must be a non-negative integer"}), 400

    from cash_mode.tables import open_slot
    from flask_app.extensions import cash_table_repo
    from flask_app.services import game_state_service

    with game_state_service.get_sandbox_lock(sandbox_id):
        table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
        if table is None:
            return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
        if seat_index >= len(table.seats):
            return jsonify({"error": "seat_index out of range"}), 400
        slot = table.seats[seat_index]
        # Only the caller's own hold is releasable here. Anything else
        # (an AI that already took it, the player's own claimed human
        # seat, a fresh open seat) is left untouched and reported as a
        # no-op so the client can move on without a hard error.
        if slot.get("kind") == "reserved" and slot.get("personality_id") == owner_id:
            cash_table_repo.save_table(
                table.with_seat(seat_index, open_slot()),
                sandbox_id=sandbox_id,
            )
            logger.info(
                "[CASH] release_seat: freed hold table=%r seat=%d owner=%r",
                table_id,
                seat_index,
                owner_id,
            )
            return jsonify({"released": True, "table_id": table_id, "seat_index": seat_index})

    return jsonify(
        {
            "released": False,
            "table_id": table_id,
            "seat_index": seat_index,
            "seat_kind": slot.get("kind"),
        }
    )


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
        return jsonify(
            {
                "error": "Invalid stake_label",
                "valid_stakes": list(STAKES_LADDER.keys()),
            }
        ), 400

    bankroll = _load_or_seed_player_bankroll(owner_id)
    if not is_sponsor_eligible(bankroll.chips, stake_label):
        _, this_min, _ = table_buy_in_window(stake_label)
        return jsonify(
            {
                "eligible": False,
                "reason": "tier_locked",
                "bankroll": bankroll.chips,
                "this_min_buy_in": this_min,
            }
        ), 200

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    # Path B + Lobby v1.5: assemble personality offers, narrowed to the
    # current table's seated AIs when `table_id` is provided.
    from cash_mode.staking_tier import resolve_tier
    from flask_app.extensions import (
        bankroll_repo,
        cash_table_repo,
        personality_repo,
        relationship_repo,
        stake_repo,
    )

    broad_candidates = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)

    # Vice spending: drop any candidates currently off-grid on a vice.
    # Best-effort — if the lookup fails, fall through with the full
    # list rather than failing the route.
    try:
        from flask_app.extensions import vice_state_repo

        if vice_state_repo is not None:
            on_vice_pids = vice_state_repo.active_pids(
                sandbox_id=sandbox_id,
                now=datetime.utcnow(),
            )
            if on_vice_pids:
                broad_candidates = [
                    c for c in broad_candidates if c.get("personality_id") not in on_vice_pids
                ]
    except Exception as exc:
        logger.warning(
            "[CASH][SPONSOR_OFFERS] vice filter failed: %s",
            exc,
        )

    candidates = broad_candidates

    if table_id:
        table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
        if table is not None and table.stake_label == stake_label:
            seated_pids = set(table.seated_personality_ids())
            narrowed = [c for c in broad_candidates if c.get("personality_id") in seated_pids]
            if narrowed:
                candidates = narrowed

    # Phase 2 (Commit 3): rejections list is populated as candidates
    # fail eligibility / tier gates so the modal can render a "they
    # won't back you" section. Resolved once here so the same list is
    # surfaced regardless of which candidate pool produced the offers.
    # Player-prestige hook 2: a reviled player loses the named-personality
    # backing pool ("nobody stakes a villain"); the house fallback below still
    # gives them a path (self-funded hard mode).
    human_regard = _resolve_human_regard(sandbox_id, owner_id)
    backing_restricted = human_regard is not None and human_regard <= VILLAIN_REGARD_FLOOR

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
        human_regard=human_regard,
    )

    # Lobby v1.5 fallback: if the narrowed-to-table pool produced zero
    # qualifying offers, retry with the broader pool. House archetypes
    # are still the final fallback when even that returns nothing. (Skipped
    # when backing is reputation-restricted — the broader pool is closed too,
    # so a retry would just be wasted work.)
    if (
        table_id
        and not personality_offers
        and not backing_restricted
        and candidates is not broad_candidates
    ):
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
            human_regard=human_regard,
        )

    # Resolve the borrower's tier so the response (Commit 3 frontend)
    # can render a tier indicator alongside the offers. Tolerates a
    # missing stake_repo by defaulting to 'premium' for back-compat
    # in tests that don't wire one through.
    tier = (
        resolve_tier(
            borrower_id=owner_id,
            current_stake_label=stake_label,
            stake_repo=stake_repo,
        )
        if stake_repo is not None
        else 'premium'
    )

    # House fallback: fill the remainder up to 3 with anonymous archetypes.
    house_slots = max(0, 3 - len(personality_offers))
    house_offers = (
        compute_offers_for_table(min_buy_in, max_buy_in, count=house_slots)
        if house_slots > 0
        else []
    )

    response_offers = []
    for po in personality_offers:
        response_offers.append(
            {
                "kind": "personality",
                "lender_id": po.lender_id,
                "name": po.lender_name,
                "amount": po.amount,
                "floor": po.floor,
                "rate": po.rate,
                "flavor": po.flavor,
                "relationship_hint": po.relationship_hint,
            }
        )
    for ho in house_offers:
        response_offers.append(
            {
                "kind": "house",
                "archetype_id": ho.archetype_id,
                "name": ho.name,
                "amount": ho.amount,
                "floor": ho.floor,
                "rate": ho.rate,
                "flavor": ho.flavor,
            }
        )

    return jsonify(
        {
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
            # Player-prestige hook 2: true when the player is too reviled for
            # named-AI backing — the modal can explain why only house offers
            # show ("your reputation precedes you").
            "backing_restricted": backing_restricted,
        }
    )


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
            event.value,
            actor_id,
            target_id,
            e,
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
    human_regard: Optional[float] = None,
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
        human_regard=human_regard,
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
        return jsonify(
            {
                "error": "Invalid stake_label",
                "valid_stakes": list(STAKES_LADDER.keys()),
            }
        ), 400
    if archetype_id and lender_id:
        return jsonify(
            {
                "error": "Send either archetype_id (house) or lender_id (personality), not both",
            }
        ), 400
    if not archetype_id and not lender_id:
        return jsonify(
            {
                "error": "archetype_id or lender_id is required",
            }
        ), 400
    if archetype_id is not None and not isinstance(archetype_id, str):
        return jsonify({"error": "archetype_id must be a string"}), 400
    if lender_id is not None and not isinstance(lender_id, str):
        return jsonify({"error": "lender_id must be a string"}), 400
    if (table_id is None) != (seat_index is None):
        return jsonify(
            {
                "error": "table_id and seat_index must be sent together",
            }
        ), 400

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
                    lender_id,
                    sandbox_id=sandbox_id,
                )
                if vstate is not None and vstate.ends_at > datetime.utcnow():
                    return jsonify(
                        {
                            "error": "lender is currently away",
                            "lender_id": lender_id,
                            "vice_ends_at": vstate.ends_at.isoformat(),
                            "vice_narration": vstate.narration,
                        }
                    ), 409
        except Exception as exc:
            # Don't fail the route on a vice-check error — log and proceed.
            logger.warning(
                "[CASH][SPONSOR] vice check failed lender=%r: %s",
                lender_id,
                exc,
            )
    if table_id is not None and not isinstance(table_id, str):
        return jsonify({"error": "table_id must be a string"}), 400
    if seat_index is not None and (not isinstance(seat_index, int) or seat_index < 0):
        return jsonify({"error": "seat_index must be a non-negative integer"}), 400

    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify(
            {
                "error": "A cash session is already active. Leave first.",
                "game_id": existing,
            }
        ), 409

    from flask_app.extensions import (
        bankroll_repo,
        personality_repo,
        relationship_repo,
        stake_repo,
    )

    bankroll = _load_or_seed_player_bankroll(owner_id)

    if not is_sponsor_eligible(bankroll.chips, stake_label):
        return jsonify(
            {
                "error": "Not sponsor-eligible at this stake",
                "bankroll": bankroll.chips,
            }
        ), 400

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
            # Player-prestige hook 2: enforce the same villain-closure here so a
            # reviled player can't sit with a personality lender the closed pool
            # would never have surfaced (anti-tamper parity with sponsor-offers).
            human_regard=_resolve_human_regard(sandbox_id, owner_id),
        )
        if personality_offer is None:
            return jsonify(
                {
                    "error": (f"Lender {lender_id!r} doesn't qualify for a loan right now"),
                }
            ), 400
        offer_amount = personality_offer.amount
        offer_floor = personality_offer.floor
        offer_rate = personality_offer.rate
        welcome_lender_label = personality_offer.lender_name
        offer_lender_id = lender_id
    else:
        house_offer = offer_for_archetype(archetype_id, min_buy_in, max_buy_in)
        if house_offer is None:
            return jsonify(
                {
                    "error": f"Unknown sponsor archetype {archetype_id!r}",
                }
            ), 400
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
    from cash_mode.tables import human_slot
    from flask_app.extensions import cash_table_repo

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
            return jsonify(
                {
                    "error": (
                        f"Table {table_id!r} stake {table.stake_label!r} doesn't "
                        f"match request stake {stake_label!r}"
                    ),
                }
            ), 400
        if seat_index >= len(table.seats):
            return jsonify({"error": "seat_index out of range"}), 400
        # Accept the player's own sponsorship hold as claimable: the
        # /api/cash/sit 402 path reserved this seat for them while the
        # SponsorModal was open, so it'll read `"reserved"` (theirs)
        # rather than `"open"` here. Any other non-open kind is a real
        # conflict. The `_release_own_reserved_holds` call inside the lock
        # below converts that hold back to "open" before we claim it.
        pre_kind = table.seats[seat_index]["kind"]
        held_by_me = (
            pre_kind == "reserved" and table.seats[seat_index].get("personality_id") == owner_id
        )
        if pre_kind != "open" and not held_by_me:
            # The reservation lapsed (TTL) and the seat filled, or the
            # client sent a seat that was never held. Fall back to any
            # open seat on this table rather than dead-ending the sponsor
            # flow; only a full table 409s.
            alt = _first_open_seat_index(table)
            if alt is None:
                return jsonify(
                    {
                        "error": "Table is full",
                        "seat_kind": pre_kind,
                    }
                ), 409
            seat_index = alt
        # Release this player's own leftover `"reserved"` hold BEFORE
        # claiming the new one — same defense sit_at_table uses, and it's
        # what converts this player's own hold back to "open" so the
        # claim below succeeds. Reload after the release because it may
        # have rewritten this table's row; building `with_seat` from a
        # stale snapshot would resurrect the hold. Hold the per-sandbox
        # seat lock around the whole claim so the world ticker's live-fill
        # can't clobber it (same race as sit).
        from flask_app.services import game_state_service

        with game_state_service.get_sandbox_lock(sandbox_id):
            _release_own_reserved_holds(owner_id, sandbox_id=sandbox_id)
            table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
            if table is None:
                return jsonify({"error": f"Unknown table_id {table_id!r}"}), 404
            if table.seats[seat_index]["kind"] != "open":
                # Lost it to live-fill after the sweep re-opened our hold.
                # Re-resolve to any open seat; only a full table 409s.
                alt = _first_open_seat_index(table)
                if alt is None:
                    return jsonify(
                        {
                            "error": "Table is full",
                            "seat_kind": table.seats[seat_index]["kind"],
                        }
                    ), 409
                seat_index = alt
            pre_claim_table = table
            claimed_table = table.with_seat(
                seat_index,
                human_slot(owner_id, offer_amount),
            )
            # save_table drives the sponsored human SIT into entity_presence
            # authoritatively at the chokepoint (same rationale as the
            # self-funded sit path).
            cash_table_repo.save_table(claimed_table, sandbox_id=sandbox_id)
        preselected_ai, preselected_chips, dealer_player_idx = _build_preselected_from_table(
            claimed_table=claimed_table,
            seat_index=seat_index,
            personality_repo=personality_repo,
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
        table_type=claimed_table.table_type if claimed_table is not None else 'lobby',
    )
    if err is not None:
        # Roll back the seat claim so the player can retry. Re-read under
        # the sandbox lock and re-open only our seat, so we don't clobber a
        # live-fill the ticker / a concurrent sit did to other seats.
        if pre_claim_table is not None and table_id is not None:
            from cash_mode.tables import open_slot
            from flask_app.services import game_state_service

            with game_state_service.get_sandbox_lock(sandbox_id):
                current = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id)
                if current is not None and current.seats[seat_index].get("kind") == "human":
                    cash_table_repo.save_table(
                        current.with_seat(seat_index, open_slot()), sandbox_id=sandbox_id
                    )
        return jsonify(err[0]), err[1]

    # Stamp table_id + seat_index on game_data so leave_table can free
    # the seat back to "open" at session end (mirror sit_at_table).
    if table_id is not None:
        from flask_app.services import game_state_service

        game_data = game_state_service.get_game(game_id)
        if game_data is not None:
            game_data["cash_table_id"] = table_id
            game_data["cash_seat_index"] = seat_index
            # Friendly room name for the header chip / arrival toast.
            # claimed_table may be None on this sponsor path; degrade to
            # None so the frontend simply omits the chip.
            game_data["cash_table_name"] = claimed_table.name if claimed_table is not None else None
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
    stake_repo.create_stake(
        Stake(
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
        )
    )

    # Obligation dimension: the human borrower owes the lender (house or AI
    # sponsor) the principal, regardless of which chip path funded the seat
    # (central_bank house-issue vs AI debit). Emit the originate once here; the
    # matching extinguish/forgive fires for HUMAN borrowers on the leave_table
    # path (_leave_table_locked) and via the carry-resolution / staker-forgive
    # routes. See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
    from cash_mode import economy_flags as _eflags_oblig
    from flask_app.extensions import chip_ledger_repo as _clr_oblig

    if _clr_oblig is not None and sandbox_id is not None and _eflags_oblig.CHIP_CUSTODY_ENABLED:
        from cash_mode.stake_obligations import apply_obligation_flows, flows_on_originate

        apply_obligation_flows(
            flows_on_originate(stake_id, offer_amount),
            _clr_oblig,
            sandbox_id=sandbox_id,
            context={'site': 'sponsor_sit_principal', 'stake_id': stake_id},
        )

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
        # Pin the session to the seat the player sat at (when the
        # sponsor flow originated from a seat tap). Without this,
        # sponsor sessions wrote cash_table_id=NULL, which left the
        # leave-time ghost-seat sweep unable to locate the seat and
        # stranded it on the lobby table. Both fields are NULL on the
        # auto-sit fallback (no table_id in the payload), which the
        # cross-table sweep in _leave_table_locked now handles.
        cash_table_id=table_id,
        cash_seat_index=seat_index,
    )

    # House-archetype loans create chips out of central_bank. Personality
    # loans are pure transfers (AI lender's bankroll → player's table
    # stack via the AI debit step in _build_cash_game) and aren't routed
    # through here.
    if offer_lender_id is None:
        from flask_app.extensions import chip_ledger_repo

        chip_ledger.record_house_stake_issue(
            chip_ledger_repo,
            game_id=game_id,
            amount=offer_amount,
            context={
                'game_id': game_id,
                'owner_id': owner_id,
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
            "[CASH] Sponsored sit %r owner=%r stake=%r lender=%r " "amount=%d floor=%.2f rate=%.2f",
            game_id,
            owner_id,
            stake_label,
            lender_id,
            offer_amount,
            offer_floor,
            offer_rate,
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
            game_id,
            owner_id,
            stake_label,
            archetype_id,
            offer_amount,
            offer_floor,
            offer_rate,
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

    return jsonify(
        {
            "game_id": game_id,
            "offer": response_offer,
        }
    )


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

    from flask_app.extensions import bankroll_repo, stake_repo
    from flask_app.services import game_state_service
    from poker.poker_state_machine import PokerPhase

    # Take the per-game lock and re-read state inside it (see top_up):
    # progress_game mutates the same game_state under this lock, so a
    # lock-free read-modify-write here could clobber hand state or lose
    # the chip add if it interleaved with hand progression.
    lock = game_state_service.get_game_lock(game_id)
    with lock:
        game_data = game_state_service.get_game(game_id)
        if game_data is None:
            return jsonify({"error": "No active cash session"}), 404
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
            return jsonify(
                {
                    "error": "Rebuy is only allowed when stack is 0 (use top-up otherwise)",
                    "stack": human_player.stack,
                }
            ), 400

        if amount < min_buy_in or amount > max_buy_in:
            return jsonify(
                {
                    "error": (
                        f"amount {amount} out of range for {stake_label} table "
                        f"(min={min_buy_in}, max={max_buy_in})"
                    ),
                }
            ), 400

        bankroll = _load_or_seed_player_bankroll(owner_id)

        # Block rebuy while an active stake is live for this session.
        # Mingling stake-funded chips with fresh bankroll chips would corrupt
        # the leave-time settlement math (the new buy-in would be subject to
        # the staker's cut on the upside). Force a /leave to settle first.
        if stake_repo is not None and stake_repo.load_active_for_session(game_id) is not None:
            return jsonify(
                {
                    "error": "Rebuy disabled while a stake is active. Leave the table to settle.",
                }
            ), 400
        if bankroll.chips < amount:
            return jsonify({"error": "Insufficient bankroll"}), 400

        state_machine.game_state = state_machine.game_state.update_player(
            human_idx,
            stack=amount,
        )
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=bankroll.chips - amount,
                starting_bankroll=bankroll.starting_bankroll,
            )
        )

        # Rebuy is another bankroll → table transfer, just like top-up;
        # leave-time P&L needs it counted as money put in (not won).
        _increment_cash_session_buy_in(game_id, amount)

    # Emit + resume play outside the lock: progress_game re-acquires this
    # same (non-reentrant) game lock, so calling it inside would deadlock.
    from flask_app.handlers.game_handler import progress_game, update_and_emit_game_state

    update_and_emit_game_state(game_id)
    # Resume play: the table pauses in HAND_OVER whenever the human is
    # busted (whether or not 2+ AIs still hold chips — see
    # handle_evaluating_hand_phase), so the rebuy has a between-hands
    # window to land in. Refilling our stack clears the bust; kick
    # progress_game so the next hand actually deals instead of waiting
    # for some other event.
    progress_game(game_id)

    return jsonify(
        {
            "stack": amount,
            "bankroll": bankroll.chips - amount,
        }
    )


@cash_bp.route("/api/cash/reseat", methods=["POST"])
def reseat():
    """POST /api/cash/reseat  body: {personality_ids?: [str]}

    "Stay and play" from the everyone-left prompt: the table emptied of
    opponents (all left or busted without a refill) while the human still
    has chips, so the game paused in HAND_OVER. This seats up to two
    fresh AIs — preferring the ones the prompt named in
    `personality_ids` — debits their bankrolls for a buy-in, drops them
    into the running game, and kicks `progress_game` so the next hand
    deals.

    Distinct from /api/cash/rebuy (which refills the *human's* stack):
    reseat never touches the human's chips or any active stake, so there
    is no settlement-math interaction.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({"error": "No active cash session"}), 404

    from datetime import datetime

    from cash_mode.tables import CashTableState, ai_slot
    from core.economy import ledger as chip_ledger
    from flask_app.extensions import bankroll_repo, cash_table_repo, chip_ledger_repo
    from flask_app.handlers.game_handler import (
        _project_candidate_buy_in,
        _sandbox_id_for,
        _seat_freshly_filled_ais,
        progress_game,
        select_rejoin_candidates,
        update_and_emit_game_state,
    )
    from flask_app.services import game_state_service
    from poker.poker_state_machine import PokerPhase

    game_data = game_state_service.get_game(game_id)
    if game_data is None:
        return jsonify({"error": "No active cash session"}), 404
    state_machine = game_data["state_machine"]

    # Only meaningful from the paused solo state the prompt is shown for.
    if not game_data.get("cash_solo_paused"):
        return jsonify({"error": "Table is not waiting for players"}), 400
    if state_machine.current_phase not in (
        PokerPhase.INITIALIZING_GAME,
        PokerPhase.INITIALIZING_HAND,
        PokerPhase.HAND_OVER,
    ):
        return jsonify({"error": "Reseat is only allowed between hands"}), 400

    payload = request.get_json(silent=True) or {}
    prefer_pids = payload.get("personality_ids") or None
    if prefer_pids is not None and not isinstance(prefer_pids, list):
        return jsonify({"error": "personality_ids must be a list"}), 400

    candidates = select_rejoin_candidates(
        game_data, state_machine.game_state, limit=2, prefer_pids=prefer_pids
    )
    if not candidates:
        return jsonify({"error": "No players available to join right now"}), 409

    # Prune busted (stack 0) AI ghosts a pool-exhausted refill may have
    # left in the player tuple, so the resumed table is a clean
    # human + fresh AIs.
    gs = state_machine.game_state
    pruned = tuple(p for p in gs.players if p.is_human or p.stack > 0)
    if len(pruned) != len(gs.players):
        gs = gs.update(players=pruned)
        state_machine.game_state = gs

    big_blind = gs.current_ante
    min_buy_in = big_blind * 40
    max_buy_in = big_blind * 100
    sandbox_id = _sandbox_id_for(game_data)
    now = datetime.utcnow()

    table_id = game_data.get("cash_table_id")
    table = cash_table_repo.load_table(table_id, sandbox_id=sandbox_id) if table_id else None
    if table is None:
        # Legacy / table-less session: synthesize an in-memory table just
        # to carry the seated slots into _seat_freshly_filled_ais. Not
        # persisted (no table_id to save under).
        table = CashTableState(
            table_id=table_id or f"_session_{game_id}",
            stake_label=game_data.get("cash_stake_label") or "",
        )

    # Fillable seats: genuinely `open` slots (voluntary departures) AND
    # `ai` slots left at 0 chips (an AI busted and `_refill_cash_seats`
    # found no replacement — `_refresh_lobby_table_for_session` persists
    # those as `ai_slot(pid, 0)`, not `open`). Both are dead seats we can
    # reuse. The human seat (and any live AI) is never touched.
    fillable_indices = [
        i
        for i, s in enumerate(table.seats)
        if s.get("kind") == "open" or (s.get("kind") == "ai" and int(s.get("chips", 0)) == 0)
    ]
    seated_pids = []
    for cand in candidates:
        if not fillable_indices:
            break
        pid = cand["personality_id"]
        proj = _project_candidate_buy_in(
            pid, min_buy_in, max_buy_in, sandbox_id, now, bankroll_repo
        )
        if proj is None:
            continue
        buy_in, new_state, pre_regen_chips = proj
        table = table.with_seat(fillable_indices.pop(0), ai_slot(pid, buy_in))
        bankroll_repo.save_ai_bankroll(new_state, sandbox_id=sandbox_id)
        try:
            chip_ledger.record_ai_regen(
                chip_ledger_repo,
                personality_id=pid,
                stored_chips=pre_regen_chips,
                projected_chips=new_state.chips + buy_in,
                context={"game_id": game_id, "site": "cash_reseat", "sandbox_id": sandbox_id},
                sandbox_id=sandbox_id,
            )
        except Exception as e:
            logger.warning("[CASH] reseat ledger record failed for %r: %s", pid, e)
        seated_pids.append(pid)

    if not seated_pids:
        return jsonify({"error": "No players available to join right now"}), 409

    if table_id:
        # save_table drives the seated⇒not-idle invariant at the presence
        # chokepoint in the same transaction (a SIT clears the actor's IDLE
        # entity_presence row + metadata), so this is the single guard against
        # the seated_and_idle split-brain for the real path.
        cash_table_repo.save_table(table, sandbox_id=sandbox_id)
    else:
        # Legacy table-less session: there's no row to save through, so
        # clear each AI's IDLE presence directly to preserve the same invariant.
        for pid in seated_pids:
            try:
                cash_table_repo.delete_idle(pid, sandbox_id=sandbox_id)
            except Exception as e:
                logger.warning("[CASH] reseat idle-clear failed for %r: %s", pid, e)

    # Drop the new AIs into the running game, then resume play.
    _seat_freshly_filled_ais(game_id, game_data, state_machine, table, seated_pids)

    game_data.pop("cash_solo_paused", None)
    game_data.pop("cash_rejoin_candidates", None)
    game_data["state_machine"] = state_machine
    game_state_service.set_game(game_id, game_data)

    update_and_emit_game_state(game_id)
    # Kick the paused state machine so the next hand actually deals now
    # that quorum is restored (mirrors the /rebuy resume path).
    progress_game(game_id)

    logger.info(
        "[CASH] Reseated game_id=%r with %d AI(s): %s",
        game_id,
        len(seated_pids),
        seated_pids,
    )
    return jsonify({"seated": seated_pids})


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
        return jsonify(
            {
                "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
            }
        ), 400
    if stake.staker_id is None:
        # House stakes never carry. This branch only fires if a row
        # somehow got into 'carry' status with NULL staker_id, which
        # shouldn't happen — Phase 1's settle_stake_on_leave overrides
        # house carries to 'settled' before persisting.
        return jsonify(
            {
                "error": "House stakes cannot be defaulted (they don't carry)",
            }
        ), 400

    from flask_app.extensions import stake_repo

    former_carry = stake.carry_amount
    former_staker = stake.staker_id

    stake_repo.update_carry_amount(stake_id, 0)
    stake_repo.update_status(
        stake_id,
        STAKE_STATUS_DEFAULTED,
        settled_at=datetime.utcnow(),
    )

    # Obligation dimension: an explicit default writes off the carried principal
    # as bad debt so oblig:<id> closes. No chips move (the reputation hit is the
    # cost). Gated on origination. See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
    from cash_mode import economy_flags as _eflags_default
    from flask_app.extensions import chip_ledger_repo as _clr_default

    if _clr_default is not None and _eflags_default.CHIP_CUSTODY_ENABLED:
        from cash_mode.stake_lifecycle import assert_stake_obligation_closed
        from cash_mode.stake_obligations import apply_close_flows, flows_on_forgive

        _default_sandbox = _resolve_sandbox_id(owner_id)
        if apply_close_flows(
            flows_on_forgive(stake_id, int(former_carry)),
            _clr_default,
            stake_id,
            sandbox_id=_default_sandbox,
            context={'stake_id': stake_id, 'site': 'human_default'},
        ):
            assert_stake_obligation_closed(
                stake_id=stake_id,
                expected_residual=0,
                sandbox_id=_default_sandbox,
                chip_ledger_repo=_clr_default,
                already_originated=True,
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
        "[STAKE] Explicit default stake_id=%r owner=%r staker=%r " "former_carry=%d",
        stake_id,
        owner_id,
        former_staker,
        former_carry,
    )

    return jsonify(
        {
            "stake_id": stake_id,
            "status": STAKE_STATUS_DEFAULTED,
            "former_carry_amount": former_carry,
            "staker_id": former_staker,
        }
    )


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
        return jsonify(
            {
                "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
            }
        ), 400
    if stake.staker_id is None:
        return jsonify(
            {
                "error": "House stakes cannot be paid off (they don't carry)",
            }
        ), 400
    if stake.staker_kind == STAKER_KIND_HUMAN:
        return jsonify(
            {
                "error": "Human-staker payoff not yet supported",
            }
        ), 501

    from flask_app.extensions import bankroll_repo, chip_ledger_repo, stake_repo

    bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
    carry_amount = int(stake.carry_amount)
    if bankroll.chips < carry_amount:
        return jsonify(
            {
                "error": "Insufficient bankroll to cover carry",
                "bankroll": bankroll.chips,
                "carry_amount": carry_amount,
            }
        ), 400

    # Pre-flight: confirm the staker's bankroll row exists. Without
    # this, `credit_ai_cash_out` silently returns None on a missing
    # row (its documented contract) — and we'd debit the player while
    # the credit evaporates, plus flip the stake to settled so the
    # player can't retry. Fail fast before any state mutation.
    if (
        bankroll_repo.load_ai_bankroll(
            stake.staker_id,
            sandbox_id=sandbox_id,
        )
        is None
    ):
        return jsonify(
            {
                "error": "Staker bankroll unavailable for this carry",
            }
        ), 503

    now = datetime.utcnow()

    # Atomically claim the carry→settled transition BEFORE moving any
    # chips. Compare-and-swap: if a concurrent payoff already settled this
    # stake, the UPDATE matches 0 rows and we bail without double-debiting
    # the player / double-crediting the staker. (The status check above is
    # a fast-fail; this is the actual race guard.)
    if not stake_repo.update_status(
        stake_id,
        STAKE_STATUS_SETTLED,
        settled_at=now,
        expected_status=STAKE_STATUS_CARRY,
    ):
        return jsonify(
            {
                "error": "Stake is no longer in 'carry' status (already settled?)",
            }
        ), 409

    # We own the transition now — move the chips. Transfer: player bankroll
    # → staker bankroll. credit_ai_cash_out mirrors the leave-time
    # settlement path so the staker's bankroll accounting
    # (projection-with-regen + cap clamp + ledger instrumentation) stays
    # consistent across "session-end settle" and "voluntary payoff".
    new_player_chips = bankroll.chips - carry_amount
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(
            player_id=bankroll.player_id,
            chips=new_player_chips,
            starting_bankroll=bankroll.starting_bankroll,
        )
    )
    credit_ai_cash_out(
        bankroll_repo,
        stake.staker_id,
        carry_amount,
        sandbox_id=sandbox_id,
        now=now,
        chip_ledger_repo=chip_ledger_repo,
        ledger_context={
            'stake_id': stake_id,
            'site': 'voluntary_payoff',
        },
        from_seat=False,
    )
    # Chip-custody: the player→staker carry payoff is a bankroll transfer with
    # no seat. The player debit (save above) and the staker credit are each
    # unledgered; record ONE `stake_payoff` transfer so both stay derivable.
    # `from_seat=False` above suppresses the seat `ai_cash_out` double-count.
    from cash_mode import economy_flags as _economy_flags_payoff

    if chip_ledger_repo is not None and _economy_flags_payoff.CHIP_CUSTODY_ENABLED:
        from core.economy import ledger as _chip_ledger

        _chip_ledger.record_stake_payoff(
            chip_ledger_repo,
            source=_chip_ledger.player(owner_id),
            sink=_chip_ledger.ai(stake.staker_id),
            amount=carry_amount,
            context={'stake_id': stake_id, 'site': 'voluntary_payoff'},
            sandbox_id=sandbox_id,
        )

    stake_repo.update_carry_amount(stake_id, 0)

    # Obligation dimension: the carry is repaid in full → extinguish that much
    # principal so oblig:<id> closes to 0. Gated on origination (legacy stakes
    # skip). See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
    if chip_ledger_repo is not None and _economy_flags_payoff.CHIP_CUSTODY_ENABLED:
        from cash_mode.stake_lifecycle import assert_stake_obligation_closed
        from cash_mode.stake_obligations import apply_close_flows, flows_on_carry_payment

        if apply_close_flows(
            flows_on_carry_payment(stake_id, carry_amount),
            chip_ledger_repo,
            stake_id,
            sandbox_id=sandbox_id,
            context={'stake_id': stake_id, 'site': 'voluntary_payoff'},
        ):
            assert_stake_obligation_closed(
                stake_id=stake_id,
                expected_residual=0,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                already_originated=True,
            )

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
        stake_id,
        owner_id,
        stake.staker_id,
        carry_amount,
    )

    return jsonify(
        {
            "stake_id": stake_id,
            "status": STAKE_STATUS_SETTLED,
            "paid": carry_amount,
            "bankroll": new_player_chips,
            "staker_id": stake.staker_id,
        }
    )


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
    "/api/cash/stakes/<stake_id>/request-forgiveness",
    methods=["POST"],
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
        personality_repo,
        relationship_repo,
        stake_repo,
    )

    if stake.status != STAKE_STATUS_CARRY:
        return jsonify(
            {
                "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
            }
        ), 400
    if stake.staker_id is None:
        return jsonify(
            {
                "error": "House stakes cannot be forgiven (they don't carry)",
            }
        ), 400

    now = datetime.utcnow()
    if stake.forgiveness_last_asked is not None:
        elapsed = (now - stake.forgiveness_last_asked).total_seconds()
        if elapsed < FORGIVENESS_RATE_LIMIT_SECONDS:
            retry_after = int(FORGIVENESS_RATE_LIMIT_SECONDS - elapsed)
            return jsonify(
                {
                    "error": "Forgiveness already requested recently",
                    "retry_after_seconds": retry_after,
                }
            ), 429

    # Read staker's view of borrower. `load_relationship_state` returns
    # None for never-interacted pairs — treat as the neutral default
    # (REGARD_NEUTRAL/REGARD_NEUTRAL/0.0). Heat is already projected
    # through decay on read.
    rel = relationship_repo.load_relationship_state(
        observer_id=stake.staker_id,
        opponent_id=owner_id,
        now=now,
    )
    likability = rel.likability if rel is not None else REGARD_NEUTRAL
    respect = rel.respect if rel is not None else REGARD_NEUTRAL
    heat = rel.heat if rel is not None else 0.0

    score = _forgiveness_score(
        likability=likability,
        respect=respect,
        heat=heat,
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
        stake_id,
        owner_id,
        stake.staker_id,
        score,
        FORGIVENESS_THRESHOLD,
        granted,
    )

    return jsonify(
        {
            "stake_id": stake_id,
            "granted": granted,
            "status": STAKE_STATUS_SETTLED if granted else STAKE_STATUS_CARRY,
            "staker_id": stake.staker_id,
            "staker_display_name": display_name,
            "score": round(score, 3),
            "threshold": FORGIVENESS_THRESHOLD,
        }
    )


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
            owner_id,
            exc,
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
                s.pending_forgiveness_ask.isoformat() if s.pending_forgiveness_ask else None
            ),
            "created_at": (s.created_at.isoformat() if s.created_at else None),
        }
        for s in pending
    ]
    return jsonify({"requests": requests})


@cash_bp.route(
    "/api/cash/stakes/<stake_id>/staker-forgive",
    methods=["POST"],
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
        return jsonify(
            {
                "error": "Only human-staker carries route through staker-forgive",
            }
        ), 400
    if stake.status != STAKE_STATUS_CARRY:
        return jsonify(
            {
                "error": f"Stake is not in 'carry' status (current: {stake.status!r})",
            }
        ), 400
    if stake.pending_forgiveness_ask is None:
        return jsonify(
            {
                "error": "No pending forgiveness ask on this stake",
            }
        ), 400

    now = datetime.utcnow()
    if grant:
        stake_repo.update_carry_amount(stake_id, 0)
        stake_repo.update_status(stake_id, STAKE_STATUS_SETTLED, settled_at=now)
        stake_repo.update_pending_forgiveness_ask(stake_id, None)
        # Obligation dimension: write off the forgiven carry as bad debt so
        # oblig:<id> closes (the borrower no longer owes it). The forgiven amount
        # is the carry being cleared. See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
        from cash_mode import economy_flags as _eflags_forgive
        from flask_app.extensions import chip_ledger_repo as _clr_forgive

        if _clr_forgive is not None and _eflags_forgive.CHIP_CUSTODY_ENABLED:
            from cash_mode.stake_lifecycle import assert_stake_obligation_closed
            from cash_mode.stake_obligations import apply_close_flows, flows_on_forgive

            _forgive_sandbox = _resolve_sandbox_id(owner_id)
            if apply_close_flows(
                flows_on_forgive(stake_id, int(stake.carry_amount or 0)),
                _clr_forgive,
                stake_id,
                sandbox_id=_forgive_sandbox,
                context={'stake_id': stake_id, 'site': 'staker_forgive'},
            ):
                assert_stake_obligation_closed(
                    stake_id=stake_id,
                    expected_residual=0,
                    sandbox_id=_forgive_sandbox,
                    chip_ledger_repo=_clr_forgive,
                    already_originated=True,
                )
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
        stake_id,
        owner_id,
        stake.borrower_id,
        grant,
    )

    return jsonify(
        {
            "stake_id": stake_id,
            "granted": grant,
            "status": STAKE_STATUS_SETTLED if grant else STAKE_STATUS_CARRY,
            "borrower_id": stake.borrower_id,
            "borrower_display_name": borrower_display_name,
        }
    )


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
@limiter.limit(config.RATE_LIMIT_POLLING)
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

    from cash_mode.player_staking import list_stakeable_ai
    from flask_app.extensions import (
        bankroll_repo,
        cash_table_repo,
        personality_repo,
        relationship_repo,
        stake_repo,
    )

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

    # Resolve display emotions + avatar URLs for the candidate portraits.
    # Every candidate here is an unseated AI (the panel only surfaces
    # AIs not in a session), so emotion comes from the persisted
    # emotional_state_json column (schema v97) — same source the lobby
    # uses for its unseated seats. Falls back to "confident", a priority
    # emotion that's always generated, so the avatar lookup resolves
    # without kicking off on-demand image generation.
    from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback

    candidate_pids = [c.personality_id for c in candidates]
    emotion_blobs = bankroll_repo.load_emotional_state_json_for_pids(
        candidate_pids,
        sandbox_id=sandbox_id,
    )
    candidate_emotions: Dict[str, str] = {}
    for pid, blob in emotion_blobs.items():
        candidate_emotions[pid] = _resolve_emotion_from_blob(blob, pid) if blob else "confident"

    # Group by target tier for the per-section rendering pattern. Tier
    # order matches STAKES_ORDER so the frontend can iterate in lobby
    # order without re-sorting.
    by_tier: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        emotion = candidate_emotions.get(c.personality_id, "confident")
        bucket = by_tier.setdefault(
            c.target_stake_label,
            {
                "stake_label": c.target_stake_label,
                "min_buy_in": c.min_buy_in,
                "max_buy_in": c.max_buy_in,
                "candidates": [],
            },
        )
        bucket["candidates"].append(
            {
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
                "emotion": emotion,
                "avatar_url": get_avatar_url_with_fallback(None, c.name, emotion),
            }
        )

    return jsonify(
        {
            "by_tier": list(by_tier.values()),
            "bankroll": bankroll.chips,
        }
    )


@cash_bp.route("/api/cash/stakes/offer", methods=["POST"])
def offer_stake_to_ai():
    """POST /api/cash/stakes/offer — player proposes a stake to an AI.

    Phase 5 Commit 1 + 2026-05-21 refinement.

    Body: `{target_pid, stake_label, principal, cut, format?, match_amount?, origination_fee?}`.
    `format` is `'pure'` (default) or `'match_share'`. Origination fee
    is honored on pure stakes only (mirrors the schema's invariant).

    Validates (all gates also enforced in `list_stakeable_ai`):
      - Player bankroll ≥ 1.5 × min_buy_in @ stake_label.
      - Seat total in `[min_buy_in, max_buy_in]`: that's `principal`
        on pure stakes, or `principal + match_amount` on match_share
        (both halves land on the seat together).
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
        return jsonify(
            {
                "error": "Invalid stake_label",
                "valid_stakes": list(STAKES_LADDER.keys()),
            }
        ), 400
    if not isinstance(principal, int) or principal <= 0:
        return jsonify({"error": "principal must be a positive integer"}), 400
    if not isinstance(cut, int | float):
        return jsonify({"error": "cut must be a number"}), 400
    cut = float(cut)
    if cut < 0.0 or cut > 0.55:
        # Match the cap used by sponsor_offers garnishment so client
        # tampering can't produce a cut beyond the standard cap.
        return jsonify({"error": "cut must lie in [0.0, 0.55]"}), 400
    if stake_format not in (STAKE_FORMAT_PURE, STAKE_FORMAT_MATCH_SHARE):
        return jsonify(
            {
                "error": ("format must be 'pure' or 'match_share'"),
            }
        ), 400
    if stake_format == STAKE_FORMAT_PURE:
        if match_amount != 0:
            return jsonify(
                {
                    "error": "match_amount is only valid with format='match_share'",
                }
            ), 400
    else:  # match_share
        if origination_fee != 0:
            return jsonify(
                {
                    "error": (
                        "origination_fee is only valid with format='pure' — "
                        "match_share shares both up- and downside instead"
                    ),
                }
            ), 400
        if match_amount <= 0:
            return jsonify(
                {
                    "error": "match_amount must be a positive integer for match_share",
                }
            ), 400
    if origination_fee < 0:
        return jsonify({"error": "origination_fee must be non-negative"}), 400

    from cash_mode.player_staking import (
        PLAYER_STAKER_BANKROLL_FLOOR_MULT,
        evaluate_player_offer,
    )
    from flask_app.extensions import (
        bankroll_repo,
        cash_table_repo,
        chip_ledger_repo,
        personality_repo,
        relationship_repo,
        stake_repo,
    )

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
    # Match-share's combined principal+match lands on the seat together,
    # so the buy-in window check must compare against the combined sum;
    # pure stakes fund the seat entirely from `principal`. AI's match
    # capacity is checked further down once the AI is loaded.
    seat_total = principal + (match_amount if stake_format == STAKE_FORMAT_MATCH_SHARE else 0)
    if seat_total < min_buy_in or seat_total > max_buy_in:
        amount_label = (
            "principal+match_amount" if stake_format == STAKE_FORMAT_MATCH_SHARE else "principal"
        )
        return jsonify(
            {
                "error": (
                    f"{amount_label} {seat_total} out of range for {stake_label} table "
                    f"(min={min_buy_in}, max={max_buy_in})"
                ),
            }
        ), 400

    bankroll = _load_or_seed_player_bankroll(owner_id, sandbox_id=sandbox_id)
    bankroll_floor = int(PLAYER_STAKER_BANKROLL_FLOOR_MULT * min_buy_in)
    if bankroll.chips < bankroll_floor:
        return jsonify(
            {
                "error": (
                    f"Bankroll ${bankroll.chips} below stake-offer floor "
                    f"${bankroll_floor} for {stake_label}"
                ),
                "bankroll": bankroll.chips,
                "required": bankroll_floor,
            }
        ), 400
    if bankroll.chips < principal:
        return jsonify(
            {
                "error": "Insufficient bankroll to cover principal",
                "bankroll": bankroll.chips,
            }
        ), 400
    # Origination fee comes from the player too (paid to the AI's
    # bankroll at deal time on pure stakes). Validate together with
    # the principal so we don't half-commit.
    total_player_outlay = principal + origination_fee
    if bankroll.chips < total_player_outlay:
        return jsonify(
            {
                "error": "Insufficient bankroll to cover principal + origination_fee",
                "bankroll": bankroll.chips,
                "required": total_player_outlay,
            }
        ), 400

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
        return jsonify(
            {
                "error": (
                    f"Can only stake {target_display_name} at the tier directly "
                    f"above their comfort zone ({knobs.stake_comfort_zone})."
                ),
                "ai_comfort_zone": knobs.stake_comfort_zone,
            }
        ), 400

    # AI can't already have an active stake as borrower (one-active-
    # stake invariant).
    existing_active = stake_repo.load_active_for_borrower(
        target_pid,
        BORROWER_KIND_PERSONALITY,
    )
    if existing_active is not None:
        return jsonify(
            {
                "error": f"{target_display_name} is already in an active stake",
            }
        ), 409

    # AI's borrower_profile must allow stakes at all.
    profile = bankroll_repo.load_borrower_profile(target_pid)
    if not profile.willing:
        return jsonify(
            {
                "accepted": False,
                "reason": "unwilling",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": f"{target_display_name} doesn't accept stakes from anyone.",
            }
        ), 200

    # Met-before gate: AI must have a relationship row toward this
    # player (created on first interaction). Without history, the
    # offer is a stranger's gesture — refuse with a "build history"
    # nudge rather than an error.
    if (
        relationship_repo.load_relationship_state(
            observer_id=target_pid,
            opponent_id=owner_id,
        )
        is None
    ):
        return jsonify(
            {
                "accepted": False,
                "reason": "no_history",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": (
                    f"{target_display_name} hasn't played with you yet — "
                    "share a few hands together first."
                ),
            }
        ), 200

    # Relationship status floor — separate from the willingness math
    # because crossing it means "AI won't even consider the offer."
    now = datetime.utcnow()
    rel_check = relationship_repo.load_relationship_state(
        observer_id=target_pid,
        opponent_id=owner_id,
        now=now,
    )
    if rel_check is not None:
        if rel_check.heat >= 0.5:
            return jsonify(
                {
                    "accepted": False,
                    "reason": "heat",
                    "target_pid": target_pid,
                    "target_display_name": target_display_name,
                    "detail": f"{target_display_name} is still upset with you.",
                }
            ), 200
        if rel_check.likability < 0.2:
            return jsonify(
                {
                    "accepted": False,
                    "reason": "dislike",
                    "target_pid": target_pid,
                    "target_display_name": target_display_name,
                    "detail": f"{target_display_name} doesn't like you enough.",
                }
            ), 200

    # Tier gate — if the AI is over-leveraged at this stake, they're
    # house-only and can't take a new peer stake.
    from cash_mode.staking_tier import TIER_HOUSE_ONLY, resolve_tier

    target_tier = resolve_tier(
        borrower_id=target_pid,
        borrower_kind=BORROWER_KIND_PERSONALITY,
        current_stake_label=stake_label,
        stake_repo=stake_repo,
    )
    if target_tier == TIER_HOUSE_ONLY:
        return jsonify(
            {
                "accepted": False,
                "reason": "tier_blocked",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": (
                    f"{target_display_name} has too much outstanding debt "
                    "to take a new stake at this level."
                ),
            }
        ), 200

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
        return jsonify(
            {
                "accepted": False,
                "reason": "cooldown",
                "target_pid": target_pid,
                "target_display_name": target_display_name,
                "detail": (
                    f"{target_display_name} won't take a stake from you yet — "
                    "they defaulted on a recent stake from you."
                ),
            }
        ), 200

    # For match-share: the AI must be able to fund their match from
    # bankroll. Refuse if their capacity is too low — they'd otherwise
    # accept and the seat would under-fund.
    if stake_format == STAKE_FORMAT_MATCH_SHARE:
        ai_chips = (
            bankroll_repo.load_ai_bankroll_current(
                target_pid,
                sandbox_id=sandbox_id,
                now=now,
            )
            or 0
        )
        if int(ai_chips) < match_amount:
            return jsonify(
                {
                    "accepted": False,
                    "reason": "ai_underfunded",
                    "target_pid": target_pid,
                    "target_display_name": target_display_name,
                    "detail": (
                        f"{target_display_name} can't cover the ${match_amount:,} "
                        "match — pick pure stake or lower the match amount."
                    ),
                }
            ), 200

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
        return jsonify(
            {
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
            }
        ), 200

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
    from cash_mode.tables import ai_slot, open_slot
    from flask_app.extensions import side_hustle_state_repo, vice_state_repo

    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        user_id=owner_id,
        sandbox_id=sandbox_id,
        chip_ledger_repo=chip_ledger_repo,
        vice_repo=vice_state_repo,
        side_hustle_repo=side_hustle_state_repo,
    )
    all_tables = cash_table_repo.list_all_tables(sandbox_id=sandbox_id)

    for t in all_tables:
        for slot in t.seats:
            if slot.get("kind") == "ai" and slot.get("personality_id") == target_pid:
                return jsonify(
                    {
                        "error": (
                            f"{target_display_name} is already seated at "
                            f"{t.stake_label} — can't double-seat"
                        ),
                    }
                ), 409

    seatable = []
    for t in all_tables:
        if t.stake_label != stake_label:
            continue
        for idx, slot in enumerate(t.seats):
            if slot.get("kind") == "open":
                seatable.append((t, idx))
                break

    if not seatable:
        return jsonify(
            {
                "error": f"No open seat at any {stake_label} table right now",
            }
        ), 503

    table, open_seat_index = _random.choice(seatable)
    target_table_id = table.table_id

    # Phase 4 (CASH_SEAT_INVARIANT_HARDENING §1.2/§3 Window C): commit
    # all chip + seat + stake mutations INSIDE the per-sandbox lock, and
    # only AFTER re-verifying the chosen seat is still open. This closes
    # two partial-commit windows on this real-money human route:
    #   (race)   the seat is taken by a concurrent ticker live-fill
    #            between selection and the write → we now 409 with NO
    #            player debit (previously the player was debited above
    #            the lock and stranded).
    #   (orphan) `create_stake` raises after the seat write → we now
    #            roll back (un-seat + refund player + reverse AI
    #            fee/match) instead of leaving an AI seated with the
    #            player's principal and no backing stake row.
    # SQLite has no cross-repo transaction, so rollback is manual; if
    # rollback itself fails the chip-ledger audit is the backstop. The
    # SUCCESS end-state is byte-identical to the prior ordering (same
    # player debit, AI fee/match, seat write, stake row) — only the
    # commit *timing* (now under the lock) changed.
    seat_chips = principal + (match_amount if stake_format == STAKE_FORMAT_MATCH_SHARE else 0)
    new_player_chips = bankroll.chips - total_player_outlay

    import uuid as _uuid

    from cash_mode.bankroll import debit_bankroll_for_seat
    from flask_app.services import game_state_service

    stake_id = f"player_stake_{_uuid.uuid4().hex[:12]}"
    session_id = f"player_session_{target_pid}_{int(now.timestamp())}"

    with game_state_service.get_sandbox_lock(sandbox_id):
        # Re-read the table under the lock so we don't clobber a ticker
        # live-fill that took our chosen seat between selection and now.
        fresh = cash_table_repo.load_table(target_table_id, sandbox_id=sandbox_id)
        if fresh is None or fresh.seats[open_seat_index].get("kind") != "open":
            # Lost the seat in the race window. No chips have moved yet —
            # the player debit + AI fee/match now happen below, AFTER this
            # gate — so we return a clean 409 with NOTHING committed.
            return jsonify({"error": "That seat was just taken — please try again"}), 409

        # Debit the player (principal + origination_fee). Done inside the
        # lock now that the seat is confirmed ours.
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=new_player_chips,
                starting_bankroll=bankroll.starting_bankroll,
            )
        )

        # Chip-custody: record the player's stake funding so the player
        # bankroll debit is derivable from the ledger (it was unledgered
        # before — the staker side of the human-staking gap). The principal
        # funds the borrower's seat; the pure-stake fee also flows through
        # the seat (the credit_ai_cash_out below drains it seat -> ai), so
        # fund the seat with it too. The settlement `stake_payoff` later
        # drains the same seat back to the staker.
        from cash_mode import economy_flags as _economy_flags_fund
        from core.economy import ledger as _chip_ledger_fund

        if chip_ledger_repo is not None and _economy_flags_fund.CHIP_CUSTODY_ENABLED:
            _chip_ledger_fund.record_stake_fund(
                chip_ledger_repo,
                source=_chip_ledger_fund.player(owner_id),
                sink=_chip_ledger_fund.ai_seat(sandbox_id, target_pid),
                amount=principal,
                context={'site': 'player_stake_principal', 'stake_id': stake_id},
                sandbox_id=sandbox_id,
            )
            # Obligation dimension: the AI borrower owes the human staker the
            # principal. Settles via settle_departed_ai_stake (AI borrower),
            # which emits the matching extinguish/forgive. The origination fee
            # is NOT debt (settled at origination), so only principal originates.
            from cash_mode.stake_obligations import (
                apply_obligation_flows,
                flows_on_originate,
            )

            apply_obligation_flows(
                flows_on_originate(stake_id, principal),
                chip_ledger_repo,
                sandbox_id=sandbox_id,
                context={'site': 'player_stake_principal', 'stake_id': stake_id},
            )
            if stake_format == STAKE_FORMAT_PURE and origination_fee > 0:
                _chip_ledger_fund.record_stake_fund(
                    chip_ledger_repo,
                    source=_chip_ledger_fund.player(owner_id),
                    sink=_chip_ledger_fund.ai_seat(sandbox_id, target_pid),
                    amount=origination_fee,
                    context={'site': 'player_stake_origination_fee', 'stake_id': stake_id},
                    sandbox_id=sandbox_id,
                )

        # Pure-stake origination fee: chips move player bankroll → AI
        # bankroll at deal time. The total_player_outlay above already
        # deducted from the player; credit the AI side here. Use
        # credit_ai_cash_out so the regen + ledger semantics stay
        # consistent with the rest of the AI-credit surface.
        if stake_format == STAKE_FORMAT_PURE and origination_fee > 0:
            credit_ai_cash_out(
                bankroll_repo,
                target_pid,
                origination_fee,
                sandbox_id=sandbox_id,
                now=now,
                chip_ledger_repo=chip_ledger_repo,
                ledger_context={
                    'site': 'player_stake_origination_fee',
                    'stake_label': stake_label,
                },
            )

        # Match-share: debit the AI's match contribution from their
        # bankroll before the seat write. Atomic regen+debit
        # (chip_ledger_repo passed so any pending regen commits via
        # `ai_regen` instead of being ignored and silently clamped).
        if stake_format == STAKE_FORMAT_MATCH_SHARE:
            debit_bankroll_for_seat(
                bankroll_repo,
                target_pid,
                match_amount,
                sandbox_id=sandbox_id,
                chip_ledger_repo=chip_ledger_repo,
                now=now,
            )

        # save_table enforces the seated⇒not-idle invariant: if the staked
        # AI was resting in the idle pool, its row is cleared in the same
        # write (see CashTableRepository.save_table). Saving the FRESH
        # table preserves any other seat the ticker changed meanwhile.
        updated_table = fresh.with_seat(open_seat_index, ai_slot(target_pid, seat_chips))
        cash_table_repo.save_table(updated_table, sandbox_id=sandbox_id, now=now)

        # Write the backing stake row LAST, with manual rollback on
        # failure: a raise here would otherwise leave the AI seated with
        # the player's principal but no stake → settlement-time chip loss
        # for the player.
        try:
            stake_repo.create_stake(
                Stake(
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
                )
            )
        except Exception:
            logger.exception(
                "[STAKE][PLAYER_OFFER] create_stake FAILED after seat write — "
                "rolling back: owner=%r target=%r table=%r seat=%d principal=%d "
                "match=%d fee=%d",
                owner_id,
                target_pid,
                target_table_id,
                open_seat_index,
                principal,
                match_amount,
                origination_fee,
            )
            # 1) Un-seat the AI — revert exactly the seat we wrote.
            try:
                cash_table_repo.save_table(
                    updated_table.with_seat(open_seat_index, open_slot()),
                    sandbox_id=sandbox_id,
                    now=now,
                )
            except Exception:
                logger.exception(
                    "[STAKE][PLAYER_OFFER] ROLLBACK un-seat FAILED — orphaned "
                    "AI seat at %r[%d]; chip-ledger audit is the backstop",
                    target_table_id,
                    open_seat_index,
                )
            # 2) Refund the player to their pre-debit balance.
            try:
                bankroll_repo.save_player_bankroll(
                    PlayerBankrollState(
                        player_id=bankroll.player_id,
                        chips=bankroll.chips,
                        starting_bankroll=bankroll.starting_bankroll,
                    )
                )
            except Exception:
                logger.exception(
                    "[STAKE][PLAYER_OFFER] ROLLBACK player refund FAILED — "
                    "player %r short %d chips; chip-ledger audit is the backstop",
                    owner_id,
                    total_player_outlay,
                )
            # 3) Reverse any AI fee/match moves so the AI bankroll nets
            #    flat. Best-effort: regen-aware primitives may not restore
            #    byte-exact, but they keep conservation close and the audit
            #    surfaces residual drift.
            try:
                if stake_format == STAKE_FORMAT_PURE and origination_fee > 0:
                    debit_bankroll_for_seat(
                        bankroll_repo,
                        target_pid,
                        origination_fee,
                        sandbox_id=sandbox_id,
                        chip_ledger_repo=chip_ledger_repo,
                        now=now,
                    )
                elif stake_format == STAKE_FORMAT_MATCH_SHARE:
                    credit_ai_cash_out(
                        bankroll_repo,
                        target_pid,
                        match_amount,
                        sandbox_id=sandbox_id,
                        now=now,
                        chip_ledger_repo=chip_ledger_repo,
                        ledger_context={
                            'site': 'player_stake_offer_rollback',
                            'stake_label': stake_label,
                        },
                    )
            except Exception:
                logger.exception(
                    "[STAKE][PLAYER_OFFER] ROLLBACK AI fee/match reversal "
                    "FAILED for %r; chip-ledger audit is the backstop",
                    target_pid,
                )
            # 4) Reverse the obligation originate — the principal debt was born
            #    (player_stake_principal above) before this failed create_stake,
            #    so cancel it or oblig:<id> orphans at +principal for a stake
            #    that never existed. Mirrors unwind_climb_funding on the
            #    take_stake path. See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
            try:
                if chip_ledger_repo is not None and _economy_flags_fund.CHIP_CUSTODY_ENABLED:
                    from cash_mode.stake_obligations import (
                        apply_obligation_flows,
                        flows_on_cancel,
                    )

                    apply_obligation_flows(
                        flows_on_cancel(stake_id, principal),
                        chip_ledger_repo,
                        sandbox_id=sandbox_id,
                        context={'site': 'player_stake_offer_rollback', 'stake_id': stake_id},
                    )
            except Exception:
                logger.exception(
                    "[STAKE][PLAYER_OFFER] ROLLBACK obligation cancel FAILED for %r",
                    stake_id,
                )
            return jsonify({"error": "Failed to record the stake — your chips were refunded."}), 500

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
        owner_id,
        target_pid,
        principal,
        match_amount,
        cut,
        origination_fee,
        stake_format,
        stake_label,
    )

    return jsonify(
        {
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
        }
    )


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
        try:
            return _leave_table_locked(owner_id, game_id)
        except Exception:
            # Convergence (Tier 3): a teardown that raised left the
            # session in a partial state. Mark it `broken` so the sit
            # guard stops treating it as active — the player can sit
            # elsewhere immediately, and the boot sweep / watchdog reap
            # the residual rows. Best-effort; re-raise the original error.
            try:
                from cash_mode.cash_sessions import SESSION_STATE_BROKEN
                from flask_app.extensions import cash_session_repo

                if cash_session_repo is not None:
                    cash_session_repo.set_session_state(game_id, SESSION_STATE_BROKEN)
                # Alertable signal (PRH-28): `[CASH LIFECYCLE]` is in
                # alerting._PREFIXES, so a leave that couldn't tear down
                # cleanly pages the webhook rather than only showing up on
                # the admin counter.
                logger.warning(
                    "[CASH LIFECYCLE] cash session %r marked BROKEN — leave "
                    "teardown raised; sit guard will skip it (no player wedge), "
                    "but it needs operator attention",
                    game_id,
                )
                _emit_cash_session_event(game_id, "broken", owner_id=owner_id)
            except Exception:
                logger.exception("[CASH] failed to mark %r broken after leave failure", game_id)
            raise


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
                game_id,
                e,
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
                game_id,
                e,
            )

    fallback_hand_count = 0
    if state_machine is not None:
        try:
            fallback_hand_count = int(getattr(state_machine, "_state", None).stats.hand_count)
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


def _emit_cash_session_event(game_id: str, event: str, **detail) -> None:
    """Best-effort lifecycle telemetry (Tier 3) — see `cash_session_events`.

    Pulls the repo off extensions and records one event. Swallows
    everything: emitting telemetry must never break a leave. `owner_id`
    / `sandbox_id` are passed via kwargs when the caller has them.
    """
    from flask_app.extensions import cash_session_repo

    if cash_session_repo is None:
        return
    record = getattr(cash_session_repo, "record_event", None)
    if record is None:
        return
    owner_id = detail.pop("owner_id", None)
    sandbox_id = detail.pop("sandbox_id", None)
    try:
        record(
            game_id,
            event,
            owner_id=owner_id,
            sandbox_id=sandbox_id,
            detail=detail or None,
        )
    except Exception:
        logger.debug("[CASH] lifecycle event %r/%r emit failed", game_id, event)


def _warm_cash_game_for_leave(
    game_id: str,
    *,
    owner_id: str,
    persisted_cash_session=None,
) -> Optional[dict]:
    """Rehydrate just enough of a DB-only cash game to settle it.

    Used by the leave path when the in-memory copy is gone (server
    restart left the game as a `cash-*` row only). A full cold-load
    (`/api/game-state`'s path — controllers, opponent models, pressure
    stats, tournament tracker) is overkill for a leave: settlement only
    reads the human's final stack, each AI's stack, and the name→pid
    map for AI cash-out. We rebuild that minimal slice and register it
    so `_leave_table_locked` can fall through to its normal settlement
    branch instead of the chips-zeroing ghost-cleanup branch.

    Safe against the resurrection race that bit the out-of-process
    cleanup script: the caller holds the per-game lock for the whole
    teardown, and `/api/game-state`'s cold-load acquires the SAME lock,
    so a concurrent poll waits until we've deleted the row and only
    then sees the 404. (An out-of-process `create_app()` had a
    different lock object and lost that race.)

    Returns the registered game_data dict, or None when the row can't
    be loaded (the caller then ghost-cleans).
    """
    from flask_app.extensions import game_repo, personality_repo
    from flask_app.game_adapter import StateMachineAdapter
    from flask_app.services import game_state_service

    try:
        base_state_machine = game_repo.load_game(game_id)
    except Exception as e:
        logger.warning("[CASH] leave warm-load failed for %r: %s", game_id, e)
        return None
    if not base_state_machine:
        return None

    state_machine = StateMachineAdapter(base_state_machine)

    big_blind = state_machine.game_state.current_ante or 100
    stake_label = next(
        (label for label, cfg in STAKES_LADDER.items() if cfg["big_blind"] == big_blind),
        None,
    )

    cash_personality_ids: Dict[str, str] = {}
    for player in state_machine.game_state.players:
        if player.is_human:
            continue
        try:
            pid = personality_repo.resolve_name_to_personality_id(player.name)
        except Exception:
            pid = None
        if pid:
            cash_personality_ids[player.name] = pid
        else:
            logger.warning(
                "[CASH] leave warm-load: no personality_id for AI %r — "
                "its table stack won't be credited back on cash-out",
                player.name,
            )

    game_data = {
        "state_machine": state_machine,
        "owner_id": owner_id,
        "cash_mode": True,
        "cash_stake_label": stake_label,
        "cash_personality_ids": cash_personality_ids,
        "cash_table_id": persisted_cash_session.cash_table_id if persisted_cash_session else None,
        "cash_seat_index": persisted_cash_session.cash_seat_index
        if persisted_cash_session
        else None,
        "sandbox_id": persisted_cash_session.sandbox_id if persisted_cash_session else None,
        "messages": [],
        "ai_controllers": {},
    }
    game_state_service.set_game(game_id, game_data)
    logger.info(
        "[CASH] leave warm-load OK for %r — settling DB-only session " "(players=%d, stake=%s)",
        game_id,
        len(state_machine.game_state.players),
        stake_label,
    )
    return game_data


def _leave_table_locked(owner_id: str, game_id: str):
    """Body of `leave_table`, run under the per-game lock.

    Split out so the `with lock:` scope covers the entire teardown
    without indenting the existing block — see the `with lock:` in
    `leave_table` for the rationale.
    """
    from flask_app.extensions import (
        bankroll_repo,
        cash_session_repo,
        cash_table_repo,
        game_repo,
        personality_repo,
    )
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
                game_id,
                e,
            )
    if not sandbox_id and persisted_cash_session is not None:
        sandbox_id = persisted_cash_session.sandbox_id
    if not sandbox_id:
        sandbox_id = _resolve_sandbox_id(owner_id)

    now = datetime.utcnow()

    # Idempotency guard (T2.1). If the durable cash_sessions row is
    # already finalized (`ended_at` set), this is a *re-entry* on a
    # session that was settled once — a retry after a crash, or a leave
    # on a game that got resurrected into memory by a stray
    # `/api/game-state` poll. Re-running settlement here is the
    # double-settle bug: the stake is already non-active, so the
    # settlement falls into the "no stake" branch and refunds the full
    # table stack a SECOND time, injecting phantom chips. So when the
    # session is already closed we do CLEANUP ONLY — tear down the
    # residual game row + seats, never touch a bankroll — and return a
    # coherent already-ended response. This is the guard that would
    # have prevented the 2026-05-28 phantom-chip incident.
    if persisted_cash_session is not None and persisted_cash_session.ended_at is not None:
        logger.info(
            "[CASH] leave on already-finalized session %r (closed_status=%r) "
            "— cleanup only, no re-settlement",
            game_id,
            persisted_cash_session.closed_status,
        )
        # Drop any in-memory copy (a resurrection from a stray poll) so
        # the ticker can't keep re-saving the row after we delete it.
        game_state_service.delete_game(game_id)
        try:
            game_repo.delete_game(game_id)
        except Exception as e:
            logger.warning("[CASH] delete_game failed for %r: %s", game_id, e)
        # (No human-seat sweep needed: the read-side occupancy projection
        # renders a stale human slot `open` and it self-heals on the next
        # save_table.)
        _purge_other_cash_rows(owner_id, except_game_id=None)
        bankroll_now = _load_or_seed_player_bankroll(owner_id).chips
        already_summary = _build_session_summary(
            game_id=game_id,
            human_name="",
            cash_out=persisted_cash_session.final_chips_at_table or 0,
            cash_session=persisted_cash_session,
            sponsor_repaid=persisted_cash_session.sponsor_repaid or 0,
            player_take_home=persisted_cash_session.player_take_home or 0,
            now=now,
        )
        return jsonify(
            {
                "session_ended": True,
                "chips_at_table": 0,
                "had_active_loan": False,
                "sponsor_repaid": persisted_cash_session.sponsor_repaid or 0,
                "returned_chips": 0,
                "bankroll": bankroll_now,
                "session_summary": already_summary,
            }
        )

    if game_data is None:
        # Server restart left this session as a DB-only `cash-*` row.
        # Try to rehydrate just enough to settle it properly (real
        # stack → stake cut applied, AIs cashed out) before falling
        # back to the chips-zeroing ghost path below. This is the
        # lobby "End session" path too: the user is on the lobby (not
        # polling the game page), so there's no client to race, and we
        # hold the per-game lock regardless. Recommendation (a) in
        # docs/plans/CASH_MODE_SESSION_LIFECYCLE_HARDENING.md.
        game_data = _warm_cash_game_for_leave(
            game_id,
            owner_id=owner_id,
            persisted_cash_session=persisted_cash_session,
        )

    if game_data is None:
        # Memory-only miss AND the row couldn't be loaded (corrupt /
        # already-deleted). Best-effort cleanup of any persisted row(s)
        # for this owner so we don't strand them in the no-active-
        # session state with a stale `/api/cash/state` redirect target.
        # No chips to settle when there's no state machine to read a
        # stack from.
        try:
            game_repo.delete_game(game_id)
        except Exception as e:
            logger.warning("[CASH] delete_game failed for %r: %s", game_id, e)
        _purge_other_cash_rows(owner_id, except_game_id=None)
        # (No human-seat sweep needed: the cash row is gone, so the read-side
        # occupancy projection renders the orphaned persisted seat `open`,
        # and it self-heals on the next save_table. Chips on the seat are
        # notional only — the bankroll already reflects the buy-in loss.)
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
            game_id,
            owner_id,
            "from-db" if ghost_summary else "null",
        )
        _emit_cash_session_event(
            game_id,
            "left_ghost",
            owner_id=owner_id,
            sandbox_id=sandbox_id,
        )
        return jsonify(
            {
                "session_ended": True,
                "chips_at_table": 0,
                "had_active_loan": False,
                "sponsor_repaid": 0,
                "returned_chips": 0,
                "bankroll": bankroll_now,
                "session_summary": ghost_summary,
            }
        )
    state_machine = game_data["state_machine"]

    # T3-77 — flush each persona's evolved mood back to the cash world on leave.
    # A cash table is two-way: whatever emotional state the opponents built while
    # playing you carries back into emotional_state_json (schema v97) so the
    # lobby card + off-screen sim see it. Done here while controllers are still
    # live (before teardown). Best-effort; resolves the persona id the same way
    # the seat build did.
    try:
        from cash_mode.psychology_persistence import flush_persona_psychology

        for name, ctrl in (game_data.get("ai_controllers") or {}).items():
            try:
                flush_pid = personality_repo.resolve_name_to_personality_id(name)
            except Exception:
                flush_pid = None
            if flush_pid:
                flush_persona_psychology(ctrl, flush_pid, bankroll_repo, sandbox_id)
    except Exception as e:
        logger.warning("[CASH] psychology flush on leave failed for %r: %s", game_id, e)

    human_player = next(
        (p for p in state_machine.game_state.players if p.is_human),
        None,
    )
    chips_at_table = human_player.stack if human_player else 0

    bankroll = _load_or_seed_player_bankroll(owner_id)
    from flask_app.extensions import chip_ledger_repo, stake_repo

    # Stake-table settlement. Sessions with an active stake row settle
    # via the stake_chip_flow plumbing; sessions without (no stake was
    # ever struck — player walked in with their own bankroll) get their
    # chips returned verbatim.
    active_stake = stake_repo.load_active_for_session(game_id) if stake_repo is not None else None

    # Response payload values — populated by whichever settlement path
    # runs. Defaults cover the "no stake" leave (player walks away with
    # their chips).
    sponsor_repaid = 0
    returned_chips = chips_at_table
    new_bankroll_chips = bankroll.chips + chips_at_table
    had_loan = False

    if active_stake is not None:
        from cash_mode.stake_chip_flow import (
            DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL,
            DIRECTION_BORROWER_SEAT_TO_HOUSE,
            DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL,
            build_stake_settlement_flows,
        )
        from cash_mode.stake_settlement import settle_stake_on_leave

        stake_settlement = settle_stake_on_leave(
            active_stake.stake_id,
            chips_at_table,
            stake_repo=stake_repo,
            chip_ledger_repo=chip_ledger_repo,
            ledger_context={'game_id': game_id, 'site': 'leave_table'},
            sandbox_id=sandbox_id,
            now=now,
        )
        flows = build_stake_settlement_flows(stake_settlement)
        borrower_credit = 0
        from cash_mode import economy_flags as _economy_flags_leave

        # Obligation dimension: close the HUMAN borrower's debt — the pair to the
        # sponsor_sit_principal / player_stake_principal originate. Mirrors the
        # AI-borrower settle (settle_departed_ai_stake): extinguish the principal
        # RECOVERED (min(staker_total, principal)); on a non-carry terminal
        # (incl. house forgive, which never carries) write off the residual so
        # the debt fully closes. See CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
        if chip_ledger_repo is not None and _economy_flags_leave.CHIP_CUSTODY_ENABLED:
            from cash_mode.stake_lifecycle import assert_stake_obligation_closed
            from cash_mode.stake_obligations import apply_close_flows, flows_on_settle

            if apply_close_flows(
                flows_on_settle(
                    active_stake.stake_id,
                    principal=int(active_stake.principal),
                    staker_total=int(stake_settlement.staker_total),
                    is_carry=stake_settlement.new_status == STAKE_STATUS_CARRY,
                ),
                chip_ledger_repo,
                active_stake.stake_id,
                sandbox_id=sandbox_id,
                context={'game_id': game_id, 'site': 'leave_table'},
            ):
                # P2: the debt must now equal its residual (0 clean/forgiven,
                # carry_amount on a carry). Enforced in dev/sim; alarm in prod.
                assert_stake_obligation_closed(
                    stake_id=active_stake.stake_id,
                    expected_residual=int(stake_settlement.carry_amount),
                    sandbox_id=sandbox_id,
                    chip_ledger_repo=chip_ledger_repo,
                    already_originated=True,
                )

        for flow in flows:
            if flow.direction == DIRECTION_BORROWER_SEAT_TO_STAKER_BANKROLL:
                # Borrower (this leaving human) seat → staker bankroll. This is a
                # stake PAYOFF, not the staker cashing out their own seat — so the
                # credit must NOT record a seat:ai(staker)→ai(staker) transfer
                # (that drains the staker's own seat account instead of the
                # borrower's). Mirror the voluntary-payoff sibling: credit with
                # `from_seat=False`, then record ONE `stake_payoff` transfer from
                # the borrower's seat. Branch on staker_kind so a human staker is
                # credited to their player bankroll, not an `ai:` bankroll.
                if active_stake.staker_kind == STAKER_KIND_HUMAN:
                    staker_bankroll = _load_or_seed_player_bankroll(
                        flow.staker_id, sandbox_id=sandbox_id
                    )
                    bankroll_repo.save_player_bankroll(
                        PlayerBankrollState(
                            player_id=staker_bankroll.player_id,
                            chips=staker_bankroll.chips + flow.amount,
                            starting_bankroll=staker_bankroll.starting_bankroll,
                        )
                    )
                    stake_sink = chip_ledger.player(flow.staker_id)
                else:
                    credit_ai_cash_out(
                        bankroll_repo,
                        flow.staker_id,
                        flow.amount,
                        sandbox_id=sandbox_id,
                        now=now,
                        chip_ledger_repo=chip_ledger_repo,
                        ledger_context={
                            'game_id': game_id,
                            'stake_id': active_stake.stake_id,
                            'site': 'stake_settle',
                        },
                        from_seat=False,
                    )
                    stake_sink = chip_ledger.ai(flow.staker_id)
                if chip_ledger_repo is not None and _economy_flags_leave.CHIP_CUSTODY_ENABLED:
                    chip_ledger.record_stake_payoff(
                        chip_ledger_repo,
                        source=chip_ledger.seat(game_id),
                        sink=stake_sink,
                        amount=flow.amount,
                        context={
                            'game_id': game_id,
                            'stake_id': active_stake.stake_id,
                            'site': 'leave_table',
                        },
                        sandbox_id=sandbox_id,
                    )
            elif flow.direction == DIRECTION_BORROWER_SEAT_TO_HOUSE:
                # House staker — chips return to the bank. Ledger entry
                # closes the loop for the audit's house-stake reconciliation
                # (forgive_balance for unrecovered portion already fired
                # inside settle_stake_on_leave above).
                chip_ledger.record_house_stake_settle(
                    chip_ledger_repo,
                    game_id=game_id,
                    amount=flow.amount,
                    context={
                        'game_id': game_id,
                        'owner_id': stake_settlement.borrower_id,
                        'stake_id': active_stake.stake_id,
                        'site': 'leave_table',
                        'sandbox_id': sandbox_id,
                    },
                    sandbox_id=sandbox_id,
                )
            elif flow.direction == DIRECTION_BORROWER_SEAT_TO_BORROWER_BANKROLL:
                borrower_credit = flow.amount

        new_bankroll_chips = bankroll.chips + borrower_credit
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=new_bankroll_chips,
                starting_bankroll=bankroll.starting_bankroll,
            )
        )

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
        bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=new_bankroll_chips,
                starting_bankroll=bankroll.starting_bankroll,
            )
        )
        returned_chips = chips_at_table

    # Human chip statement (Cut 2): record the take-home as a transfer
    # seat -> player. `returned_chips` is the take-home in both branches
    # (borrower_credit when staked, chips_at_table when self-funded). A
    # 0-take-home bust writes no row — the buy_in with no matching
    # cash_out IS the record that the seat busted. Conservation-neutral;
    # best-effort, never blocks the leave.
    from flask_app.extensions import chip_ledger_repo as _chip_ledger_repo

    chip_ledger.record_player_cash_out(
        _chip_ledger_repo,
        owner_id=owner_id,
        game_id=game_id,
        amount=returned_chips,
        context={'site': 'cash_leave', 'sponsor_repaid': sponsor_repaid},
        sandbox_id=sandbox_id,
    )

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
    # INVARIANT (Codex review #6): finalise runs AFTER settlement above —
    # it stamps `ended_at`, which the idempotency guard keys on to skip
    # re-settlement. Keep this ordering: setting ended_at before the
    # stake/bankroll moves would let the guard skip a never-settled
    # session, stranding chips. (The boot sweep settles-then-finalises too.)
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
    cash_personality_ids: Dict[str, str] = game_data.get("cash_personality_ids", {}) or {}
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
            # Presence dual-write SHADOW (flag-gated no-op when off): mirror the
            # human cash-out as GO_OFFLINE — a human leaves the sandbox entirely
            # (design §5.1: IDLE is the AI idle-pool concept; a cashed-out human
            # is OFFLINE). The busted/vacated AI seats freed above are reconciled
            # by the refresh_unseated_tables pass below (its own shadow wiring),
            # so only the human needs an explicit transition here. GO_OFFLINE is
            # legal from SEATED/IDLE/POOL; from an absent (already-offline) row it
            # is illegal and swallowed — fine, the human is already gone.
            from cash_mode import presence_shadow
            from cash_mode.presence import PresenceEvent, player_entity_id

            presence_shadow.shadow_transition(
                entity_id=player_entity_id(owner_id),
                sandbox_id=sandbox_id,
                event=PresenceEvent.GO_OFFLINE,
            )
            logger.info(
                "[CASH][LOBBY] freed seat %r:%s and persisted final chip counts",
                cash_table_id,
                cash_seat_index,
            )

            # Final refresh pass: lets AI movement act on the post-leave
            # state (e.g., an AI who won big can now stake_up). Hold the
            # per-sandbox seat lock so it serializes with the world ticker's
            # refresh (which holds the same lock). This nests sandbox-inside-
            # game (the enclosing leave lock is the per-GAME lock); safe because
            # no sandbox-lock holder ever acquires a game lock (no inversion).
            try:
                from cash_mode import economy_flags as _economy_flags
                from cash_mode.lobby import refresh_unseated_tables
                from flask_app.extensions import (
                    chip_ledger_repo,
                    relationship_repo,
                    stake_repo,
                )
                from flask_app.handlers.game_handler import live_cash_seated_pids

                with game_state_service.get_sandbox_lock(sandbox_id):
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
                        live_seated_pids=live_cash_seated_pids(sandbox_id),
                        human_headroom=_economy_flags.LIVE_FILL_HUMAN_HEADROOM,
                    )
            except Exception as e:
                logger.warning(
                    "[CASH][LOBBY] leave-time final refresh failed: %s",
                    e,
                )

    # (No cross-table human-seat sweep needed here: the read-side occupancy
    # projection renders any human slot this session left behind — including
    # the cash_table_id=NULL sponsor-session case — as `open` on read, and it
    # self-heals on the next save_table.)

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
                other_gid,
                owner_id,
            )
    _purge_other_cash_rows(owner_id, except_game_id=None)

    logger.info(
        "[CASH] Left game_id=%r owner=%r chips_at_table=%d had_loan=%s "
        "sponsor_repaid=%d returned=%d bankroll_now=%d",
        game_id,
        owner_id,
        chips_at_table,
        had_loan,
        sponsor_repaid,
        returned_chips,
        new_bankroll_chips,
    )

    _emit_cash_session_event(
        game_id,
        "left_clean",
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        had_loan=had_loan,
        returned_chips=returned_chips,
        sponsor_repaid=sponsor_repaid,
    )

    return jsonify(
        {
            "session_ended": True,
            "chips_at_table": chips_at_table,
            "had_active_loan": had_loan,
            "sponsor_repaid": sponsor_repaid,
            "returned_chips": returned_chips,
            "bankroll": new_bankroll_chips,
            "session_summary": session_summary,
        }
    )


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

    from flask_app.extensions import bankroll_repo, stake_repo
    from flask_app.services import game_state_service
    from poker.poker_state_machine import PokerPhase

    # Take the per-game lock and re-read state inside it: progress_game
    # mutates the same game_state under this lock, so a lock-free
    # read-modify-write here would clobber hand state or lose the chip
    # add if it interleaved with hand progression (see leave_table).
    lock = game_state_service.get_game_lock(game_id)
    with lock:
        game_data = game_state_service.get_game(game_id)
        if game_data is None:
            return jsonify({"error": "No active cash session"}), 404
        state_machine = game_data["state_machine"]

        human_idx = next(
            (i for i, p in enumerate(state_machine.game_state.players) if p.is_human),
            None,
        )
        if human_idx is None:
            return jsonify({"error": "Player not seated"}), 400
        human_player = state_machine.game_state.players[human_idx]

        # Mingling bankroll chips with stake chips would corrupt the
        # leave-time math (your top-up money would be taxed by the
        # staker's cut). Force the player to settle first. Checked before
        # the phase branch so it applies to staged top-ups too — and it
        # guarantees a session carrying a pending_topup never also has an
        # active stake (the leave path relies on that).
        if stake_repo is not None and stake_repo.load_active_for_session(game_id) is not None:
            return jsonify(
                {
                    "error": "Top-up disabled while a stake is active. Leave the table to settle.",
                }
            ), 400

        bankroll = bankroll_repo.load_player_bankroll(owner_id)
        # Count chips already staged this hand so two quick clicks can't
        # commit more than the bankroll can cover.
        already_pending = int(game_data.get("pending_topup", 0) or 0)
        if bankroll is None or bankroll.chips < amount + already_pending:
            return jsonify({"error": "Insufficient bankroll"}), 400

        # Phase gate. Between hands we add the chips to the live stack
        # right now. Mid-hand — whether the player has folded or is still
        # in the pot — we must NOT touch the live stack: it would shift
        # the call/raise math under the opponents, and racing the
        # auto-dealt next hand is exactly what used to make this request
        # hang on the game lock and then 400. So we *stage* the chips:
        # park the amount and let the next-hand dealer flush it. The
        # bankroll is debited at flush time, not here, so a leave / bust /
        # session-drop before the next deal can't strand committed chips.
        between_hands = state_machine.current_phase in (
            PokerPhase.INITIALIZING_GAME,
            PokerPhase.INITIALIZING_HAND,
            PokerPhase.HAND_OVER,
        )

        if between_hands:
            new_stack = human_player.stack + amount
            state_machine.game_state = state_machine.game_state.update_player(
                human_idx,
                stack=new_stack,
            )
            new_bankroll = PlayerBankrollState(
                player_id=bankroll.player_id,
                chips=bankroll.chips - amount,
                starting_bankroll=bankroll.starting_bankroll,
                # Loan fields are 0 by virtue of the stake guard above.
            )
            bankroll_repo.save_player_bankroll(new_bankroll)
            # Bump the durable session's total_buy_in so leave-time P&L
            # counts this as money put in (not won), and emit the paired
            # buy-in ledger row. Skipped silently if the session row is
            # missing (legacy game predating cash_sessions).
            _increment_cash_session_buy_in(game_id, amount)
            staged_total = 0
            response_bankroll = new_bankroll.chips
        else:
            staged_total = already_pending + amount
            game_data["pending_topup"] = staged_total
            game_state_service.set_game(game_id, game_data)
            new_stack = human_player.stack
            response_bankroll = bankroll.chips

    # Emit outside the lock — update_and_emit_game_state only reads, and the
    # game lock is non-reentrant.
    from flask_app.handlers.game_handler import update_and_emit_game_state

    update_and_emit_game_state(game_id)

    return jsonify(
        {
            "stack": new_stack,
            "bankroll": response_bankroll,
            "staged": staged_total > 0,
            "pending_topup": staged_total,
        }
    )


# A fish counts as a "whale" (prime target) for the lobby scouting tag
# when its persistent bankroll is deep relative to the stakes it's sitting
# at — enough to reload many times over. Relative (× max buy-in) so it
# scales if casinos ever run above $2. Tuning knob, not load-bearing.
WHALE_BANKROLL_MULTIPLE = 20


def _reputation_payload_from_snapshot(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a `prestige_snapshots` row into the lobby `reputation` payload.

    The v1 renames `captured_at` → `computed_at` and nests the renown_*/regard_*
    columns under `components` for the panel's explain affordance. When the row
    was written by the v2 formula (`formula_version == 'v2'`) it also carries the
    uncapped `renown_v2`, the field `high_cut`, the human's `victim_percentile`,
    the `field_size`, and the v2 driver breakdown under `renown_v2_components`;
    the panel branches on `formula_version` to render the uncapped gauge instead
    of the [0,1] bar. The v1 columns stay populated as a baseline either way.
    Named so the DB-column → wire-format mapping is in one testable place.
    """
    payload: Dict[str, Any] = {
        "renown": snap["renown"],
        "regard": snap["regard"],
        "quadrant": snap["quadrant"],
        "opponent_count": snap["opponent_count"],
        "computed_at": snap["captured_at"],
        "formula_version": snap.get("formula_version") or "v1",
        "components": {
            "breadth": snap["renown_breadth"],
            "tenure": snap["renown_tenure"],
            "stake_tier": snap["renown_stake_tier"],
            "beat_respected": snap["renown_beat_respected"],
            "high_stakes": snap["renown_high_stakes"],
            "likability": snap["regard_likability"],
            "respect": snap["regard_respect"],
            "heat": snap["regard_heat"],
        },
    }
    if (snap.get("formula_version") == "v2") and snap.get("renown_v2") is not None:
        v2_components: Dict[str, Any] = {}
        raw = snap.get("renown_v2_components")
        if raw:
            try:
                v2_components = json.loads(raw)
            except (ValueError, TypeError):
                v2_components = {}
        payload["renown_v2"] = snap["renown_v2"]
        payload["high_cut"] = snap.get("high_cut")
        payload["victim_percentile"] = snap.get("victim_percentile")
        payload["field_size"] = snap.get("field_size")
        payload["renown_v2_components"] = v2_components
    return payload


def _resolve_human_regard(sandbox_id: str, owner_id: str) -> Optional[float]:
    """Read the human's room-level regard from the prestige stat, or None.

    Player-prestige hook 2 (backing economy): the sponsor-offers and
    sponsor-and-sit paths read this and pass it to `compute_personality_offers`
    so a reviled player loses the named-personality backing pool. Best-effort:
    a missing repo / no capture yet / any error returns None (= no gate, the
    pre-hook behavior), so backing never breaks on a prestige read.
    """
    try:
        from flask_app.extensions import prestige_snapshots_repo

        if prestige_snapshots_repo is None:
            return None
        snap = prestige_snapshots_repo.load_latest(sandbox_id, owner_id)
        return snap["regard"] if snap is not None else None
    except Exception as exc:
        logger.warning("[CASH][SPONSOR] regard load failed: %s", exc)
        return None


@cash_bp.route("/api/cash/lobby", methods=["GET"])
@limiter.limit(config.RATE_LIMIT_POLLING)
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

    from cash_mode.lobby import (
        ensure_ai_bankrolls_seeded,
        ensure_lobby_seeded,
        get_dealer_index,
        refresh_unseated_tables,
    )
    from flask_app.extensions import (
        bankroll_repo,
        cash_table_repo,
        personality_repo,
        relationship_repo,
    )
    from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback
    from flask_app.services import game_state_service

    bankroll = _load_or_seed_player_bankroll(owner_id)
    # Bankroll seed must run BEFORE lobby seed: the lobby seeder picks
    # AI candidates by `projected >= ai_threshold`, and a missing row
    # leans on `knobs.starting_bankroll` only via a defensive fallback —
    # writing real rows up-front keeps the live-fill path's
    # `load_ai_bankroll_current` from returning None for personalities
    # who have never sat.
    from flask_app.extensions import (
        chip_ledger_repo as _chip_ledger_repo,
        side_hustle_state_repo as _side_hustle_state_repo,
        vice_state_repo as _vice_state_repo,
    )

    _seed_actions = ensure_ai_bankrolls_seeded(
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        sandbox_id=sandbox_id,
        user_id=owner_id,
        chip_ledger_repo=_chip_ledger_repo,
    )
    # Genesis reserve: seed the bank pool to a % of holdings ONCE at fresh-
    # sandbox birth so the world boots lived-in (flag-gated, default OFF). Must
    # run after bankrolls (it sizes off holdings) and only fires for a pristine
    # all-"created" seed pass — see ensure_genesis_reserve_seeded.
    from cash_mode.closed_economy import ensure_genesis_reserve_seeded

    ensure_genesis_reserve_seeded(
        chip_ledger_repo=_chip_ledger_repo,
        sandbox_id=sandbox_id,
        seed_actions=_seed_actions,
    )
    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        user_id=owner_id,
        sandbox_id=sandbox_id,
        chip_ledger_repo=_chip_ledger_repo,
        vice_repo=_vice_state_repo,
        side_hustle_repo=_side_hustle_state_repo,
    )

    # Mark this sandbox active so the realtime world ticker advances it.
    # `touch` also keeps the world alive for an HTTP-only client whose
    # websocket failed (it keeps polling → keeps touching).
    from flask_app.services import presence
    from flask_app.services.ticker_service import is_enabled as _ticker_enabled

    presence.touch(owner_id, sandbox_id)

    # World advancement. When the realtime ticker owns it, this read is
    # PURE — calling refresh here too would double-advance the world
    # (refresh_unseated_tables plays hands + rolls movement; it is not
    # idempotent). When the ticker is disabled, fall back to the legacy
    # read-driven refresh so the world still moves on read.
    if not _ticker_enabled():
        try:
            from cash_mode import economy_flags as _economy_flags_lobby
            from flask_app.extensions import (
                chip_ledger_repo,
                relationship_repo,
                side_hustle_state_repo,
                stake_repo,
                vice_state_repo,
            )
            from flask_app.handlers.game_handler import live_cash_seated_pids
            from flask_app.services import game_state_service

            # Same per-sandbox seat lock the route seat-claims take, so the
            # read-driven fallback serializes with concurrent sits instead of
            # last-write-wins clobbering a just-placed seat (ghost-seat class).
            with game_state_service.get_sandbox_lock(sandbox_id):
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
                    side_hustle_repo=side_hustle_state_repo,
                    live_seated_pids=live_cash_seated_pids(sandbox_id),
                    human_headroom=_economy_flags_lobby.LIVE_FILL_HUMAN_HEADROOM,
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
    # Which table is the player currently seated at? Drives the lobby
    # "you're seated here" pin (TableCard Resume state). None when the
    # player has no live session.
    #
    # `has_active_session` is the DB-aware truth (any cash-* game row for
    # this owner); `seated_table_id` is a display nicety. They can
    # legitimately diverge: a session abandoned mid-hand survives as a
    # DB-only `games` row that isn't in memory, so `get_game` returns
    # None. We must STILL surface the Resume bar in that case — otherwise
    # `_find_active_cash_game_id` keeps 409-ing every new sit while the
    # lobby shows no way back in or out (the wedge that stranded a cold
    # sponsored session). Fall back to the durable cash_sessions row for
    # the table id / stake label so the pin + label work for cold resumes
    # too.
    seated_table_id: Optional[str] = None
    seated_stake_label: Optional[str] = None
    # Tier 4.2: the active session's start time (ISO), so the lobby Resume
    # bar can show "Paused Xh ago". Sourced from the durable cash_sessions
    # row so it works for both hot and cold (DB-only) sessions.
    seated_since: Optional[str] = None
    active_game_id = _find_active_cash_game_id(owner_id)
    has_active_session = active_game_id is not None
    if active_game_id:
        # Load the durable cash_sessions row ONCE — it feeds both
        # `seated_since` (always) and the cold-path table_id/stake_label
        # fallback below, so we don't double-read.
        persisted_session = None
        try:
            from flask_app.extensions import cash_session_repo as _cs_repo

            if _cs_repo is not None:
                persisted_session = _cs_repo.load(active_game_id)
        except Exception as exc:
            logger.warning(
                "[CASH][LOBBY] cash_sessions lookup failed for %s: %s",
                active_game_id,
                exc,
            )
        if persisted_session is not None and getattr(persisted_session, "started_at", None):
            try:
                seated_since = persisted_session.started_at.isoformat()
            except Exception:
                seated_since = None

        active_game = game_state_service.get_game(active_game_id)
        if active_game:
            seated_table_id = active_game.get("cash_table_id")
            seated_stake_label = active_game.get("cash_stake_label")
            for name, controller in (active_game.get("ai_controllers") or {}).items():
                psych = getattr(controller, "psychology", None)
                if psych is not None:
                    try:
                        active_emotions[name] = psych.get_display_emotion()
                    except Exception:
                        active_emotions[name] = "confident"
                else:
                    active_emotions[name] = "confident"
        elif persisted_session is not None:
            # DB-only (cold) session — the game row exists but isn't
            # loaded into memory. Pull the table id / stake label from
            # the durable cash_sessions row so the Resume bar can route
            # the player back through the cold-load (which rehydrates it).
            seated_table_id = persisted_session.cash_table_id
            seated_stake_label = persisted_session.stake_label

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
    from cash_mode.staking_tier import (
        TIER_PREMIUM,
        resolve_tier,
    )
    from flask_app.extensions import stake_repo

    # Phase 3 Commit 1: build a {staker_id: total_carry_amount} map so
    # AI seats the player has outstanding carries with can be annotated
    # in the response. A single owner can carry from the same lender
    # across multiple sessions, so values aggregate. Built once before
    # the table loop to keep serialization O(seats × 1).
    carries_by_staker: Dict[str, int] = {}
    if stake_repo is not None:
        try:
            for stake in stake_repo.list_carries_for_borrower(
                owner_id,
                BORROWER_KIND_HUMAN,
            ):
                if stake.staker_id is None:
                    continue  # house carries shouldn't exist; skip
                carries_by_staker[stake.staker_id] = carries_by_staker.get(
                    stake.staker_id, 0
                ) + int(stake.carry_amount)
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
            active_stake_pids = set(stake_repo.get_active_personality_participants())
        except Exception as e:
            logger.warning(
                "[CASH][LOBBY] active stake participants load failed: %s",
                e,
            )

    # Fish/whale scouting tags. `load_fish_ids` is one bulk query for the
    # loose/passive donor archetype; the per-seat whale check (a fish deep
    # enough to be a prime target) only fires for those few pids, so the
    # hot path stays cheap. Fish are casino-only, so this mostly lights up
    # the Casino tab. Best-effort — an empty set just means no tags.
    fish_ids: set = set()
    try:
        from cash_mode.closed_economy import load_fish_ids

        fish_ids = load_fish_ids(bankroll_repo, sandbox_id=sandbox_id)
    except Exception as e:
        logger.warning("[CASH][LOBBY] fish-id load failed: %s", e)

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
            table_tier = (
                resolve_tier(
                    borrower_id=owner_id,
                    current_stake_label=table.stake_label,
                    stake_repo=stake_repo,
                )
                if stake_repo is not None
                else TIER_PREMIUM
            )
        except Exception as e:
            logger.warning("[CASH][LOBBY] tier resolution failed for %r: %s", table.stake_label, e)
            table_tier = TIER_PREMIUM

        serialized_seats = []
        for idx, slot in enumerate(table.seats):
            entry = {"index": idx, "kind": slot["kind"]}
            # A `"reserved"` hold is the player's own transient sponsorship
            # seat-lock (cash tables are per-sandbox, so the only viewer is
            # the player who placed it). Render it as the player's `"human"`
            # seat — they're parked there with the SponsorModal open — so
            # the frontend's open/ai/human seat union renders it as "you're
            # holding this seat" instead of failing to match an unknown kind.
            if slot["kind"] == "reserved":
                entry["kind"] = "human"
                entry["personality_id"] = slot.get("personality_id")
                entry["chips"] = 0
                serialized_seats.append(entry)
                continue
            if slot["kind"] == "ai":
                pid = slot["personality_id"]
                # `personality_for_seat` catches DB/decode failures and logs
                # them; programmer bugs propagate so they get fixed.
                personality = personality_for_seat(slot, personality_repo)
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
                # Tourists carry display_name on the seat; regular AIs
                # fall through to personality.name.
                ai_name = slot.get("display_name") or personality.get("name") or pid
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
                # Fish are real curated personas with their own avatar
                # rows, so the normal name-keyed fallback resolves them
                # (no synthetic-pid zombie risk that the old tourist path
                # had to guard against).
                entry["avatar_url"] = get_avatar_url_with_fallback(
                    None,
                    ai_name,
                    emotion,
                )
                # Relationship hint: lender's POV of the player.
                hint = ""
                try:
                    rel = relationship_repo.load_relationship_state(
                        observer_id=pid,
                        opponent_id=owner_id,
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
                # Fish/whale scouting tag. Fish = loose/passive donor
                # archetype (preloaded set); whale = a fish whose persistent
                # bankroll is deep relative to this table's stakes — a prime
                # target. Only fish trigger the bankroll lookup; non-fish get
                # no tag. Best-effort.
                if pid in fish_ids:
                    role = "fish"
                    try:
                        ai_bk = bankroll_repo.load_ai_bankroll(
                            pid,
                            sandbox_id=sandbox_id,
                        )
                        if (
                            ai_bk is not None
                            and ai_bk.chips >= WHALE_BANKROLL_MULTIPLE * max_buy_in
                        ):
                            role = "whale"
                    except Exception:
                        pass
                    entry["role"] = role
            elif slot["kind"] == "human":
                entry["personality_id"] = slot.get("personality_id")
                entry["chips"] = int(slot.get("chips", 0))
            serialized_seats.append(entry)

        response_tables.append(
            {
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
                # v113: casino tables run a closing countdown before teardown.
                # Surface it so the lobby can tag the table and flag the Casino
                # tab. None for active tables (the common case).
                "closing_hand_countdown": table.closing_hand_countdown,
            }
        )

    # Top-level tier reflects "what tier am I currently playing at?".
    # `_resolve_player_tier_stake_label` consolidates the active-session
    # → bankroll → cheapest fallback chain; the same helper drives
    # `/api/cash/net-worth` so the two surfaces can't disagree.
    current_tier_stake = _resolve_player_tier_stake_label(
        owner_id,
        bankroll.chips,
    )

    try:
        current_tier = (
            resolve_tier(
                borrower_id=owner_id,
                current_stake_label=current_tier_stake,
                stake_repo=stake_repo,
            )
            if stake_repo is not None
            else TIER_PREMIUM
        )
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
                "[CASH][LOBBY] pending forgiveness count failed: %s",
                e,
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
                sandbox_id=sandbox_id,
                now=datetime.utcnow(),
            )
            for v in actives:
                try:
                    p = personality_repo.load_personality_by_id(v.personality_id)
                except Exception:
                    p = None
                name = (p.get("name") if isinstance(p, dict) else None) or v.personality_id
                active_vices_payload.append(
                    {
                        "personality_id": v.personality_id,
                        "name": name,
                        "narration": v.narration,
                        "duration_bucket": v.duration_bucket,
                        "started_at": v.started_at.isoformat(),
                        "ends_at": v.ends_at.isoformat(),
                        "amount": v.amount,
                    }
                )
    except Exception as exc:
        logger.warning("[CASH][LOBBY] active_vices payload failed: %s", exc)

    # Current world pace so the lobby can render the pace selector in the
    # right state. Defaults gracefully if the prefs row is unset.
    try:
        from flask_app.extensions import user_prefs_repo

        world_pace = user_prefs_repo.get_world_pace(owner_id)
    except Exception:
        world_pace = "lively"

    # Send a deeper slice than the ~5 shown at once so a fresh load lands
    # with real scroll-back history; the client merges these into its
    # rolling feed (it accumulates further over the session). Bounded by
    # the activity ring buffer's own cap.
    events_payload = [serialize_event(e) for e in recent_events(limit=30, sandbox_id=sandbox_id)]

    # The player's own last-stand line — bankroll is $0 and they have a
    # stack in play, so their entire net worth is on a single table. The
    # AI version of this signal is emitted into the ring buffer during the
    # refresh, but the player's own table is never refreshed there (the
    # human-seated table is skipped), so we synthesize the line here at
    # response time. Recomputed each poll while the condition holds — a
    # standing self-warning rather than a one-shot beat.
    if bankroll.chips == 0 and active_game_id:
        active_game = game_state_service.get_game(active_game_id)
        if active_game:
            human_stack = 0
            try:
                sm = active_game.get("state_machine")
                for p in sm.game_state.players:
                    if p.is_human:
                        human_stack = int(p.stack)
                        break
            except Exception:
                human_stack = 0
            stake_label = active_game.get("cash_stake_label") or ""
            if human_stack > 0:
                from cash_mode.activity import (
                    EVENT_LAST_STAND,
                    format_player_last_stand_message,
                )

                events_payload.insert(
                    0,
                    {
                        "type": EVENT_LAST_STAND,
                        "table_id": active_game.get("cash_table_id", ""),
                        "stake_label": stake_label,
                        "personality_id": owner_id,
                        "name": "You",
                        "reason": "self",
                        "message": format_player_last_stand_message(
                            stake_label, active_game.get("cash_table_name")
                        ),
                        "created_at": datetime.utcnow().isoformat(),
                    },
                )

    # Net-worth trajectory + last-session delta for the career hero.
    #
    # The trajectory is the player's REAL net worth over time (chips +
    # stakes receivable − stakes outstanding), read from the
    # `holdings_snapshots` the world ticker records (~10 min per active
    # sandbox). Net worth (not liquid chips) so debt events read as dips
    # and a leveraged buy-in doesn't masquerade as a windfall. Returned as
    # `{t, value}` *change-points*: consecutive-equal idle samples (the
    # ticker writes one every ~10 min even when nothing moves) are
    # collapsed so the curve reads as the sequence of changes, and each
    # vertex carries the timestamp it was first reached so the sparkline
    # can show "net worth was $X at <time>" on hover. `last_session_delta`
    # stays a session figure — it labels the "last session" chip and tones
    # the curve. Best-effort: any failure drops both fields and the hero
    # renders without the chart/delta.
    bankroll_history: List[Dict[str, Any]] = []
    last_session_delta: Optional[int] = None
    try:
        from flask_app.extensions import cash_session_repo, holdings_snapshots_repo

        # Most recent finalised session's net result → the delta chip + tone.
        if cash_session_repo is not None:
            for s in cash_session_repo.list_for_owner(owner_id, limit=5):  # newest-first
                if s.ended_at is not None and s.player_take_home is not None:
                    last_session_delta = int(s.player_take_home) - int(s.total_buy_in)
                    break

        if holdings_snapshots_repo is not None:
            since_iso = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
            points = holdings_snapshots_repo.series_for_entity(
                sandbox_id=sandbox_id,
                entity_id=f"player:{owner_id}",
                since_iso=since_iso,
            )
            series: List[Dict[str, Any]] = []
            for p in points:
                v = int(p["net_worth"])
                if not series or series[-1]["value"] != v:
                    series.append({"t": p["captured_at"], "value": v})
            # Safety cap (preserve endpoints + shape) for a very busy career.
            cap = 60
            n = len(series)
            if n > cap:
                stride = (n - 1) / (cap - 1)
                idx = sorted({0, n - 1} | {round(i * stride) for i in range(1, cap - 1)})
                series = [series[i] for i in idx]
            bankroll_history = series
    except Exception as exc:
        logger.warning("[CASH][LOBBY] bankroll_history build failed: %s", exc)
        bankroll_history = []
        last_session_delta = None

    # Reputation scoreboard (v121). Read-only — the world ticker owns all
    # writes; the route never triggers a recompute. Best-effort: a missing
    # row (brand-new sandbox before the first tick) or any failure yields
    # `reputation: None`, and the frontend renders no panel.
    reputation_payload: Optional[Dict[str, Any]] = None
    try:
        from flask_app.extensions import prestige_snapshots_repo

        if prestige_snapshots_repo is not None:
            snap = prestige_snapshots_repo.load_latest(sandbox_id, owner_id)
            if snap is not None:
                reputation_payload = _reputation_payload_from_snapshot(snap)
    except Exception as exc:
        logger.warning("[CASH][LOBBY] reputation load failed: %s", exc)
        reputation_payload = None

    return jsonify(
        {
            "bankroll": bankroll.chips,
            "tier": current_tier,
            "tier_stake_label": current_tier_stake,
            "tables": response_tables,
            "seated_table_id": seated_table_id,
            "seated_stake_label": seated_stake_label,
            "seated_since": seated_since,
            "has_active_session": has_active_session,
            "events": events_payload,
            "pending_forgiveness_count": pending_forgiveness_count,
            "active_vices": active_vices_payload,
            "world_pace": world_pace,
            "bankroll_history": bankroll_history,
            "last_session_delta": last_session_delta,
            "reputation": reputation_payload,
        }
    )


@cash_bp.route("/api/cash/whereabouts", methods=["GET"])
@limiter.limit(config.RATE_LIMIT_POLLING)
def get_whereabouts():
    """GET /api/cash/whereabouts — where are the people I've met?

    Player-facing companion to the lobby. Lists every AI the player has
    actually tangled with in cash (a `cash_pair_stats` row with chip
    flow) and says where each one is right now: at another table,
    resting in the idle pool (recharging / waiting to stake up), off on
    a side hustle (earning back a buy-in), or indulging a vice. Scoped
    to "met" personas so it doesn't spoil opponents the player hasn't
    discovered yet, and so the list stays personal rather than a roster
    dump.

    The admin tripwire variant (`/api/admin/cash/whereabouts`) returns
    the unfiltered world + invariant `stuck` flags; this route strips
    `stuck` — a player shouldn't see "this persona is bugged."

    Response: `{"now", "people": [...], "counts": {...}}`. Each person
    carries name, status, location/timing, the player's PnL vs them,
    plus `avatar_url` + `emotion` for rendering.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    from cash_mode.whereabouts import (
        STUCK_UNKNOWN_PERSONALITY,
        build_whereabouts,
    )
    from flask_app.extensions import (
        bankroll_repo,
        cash_table_repo,
        personality_repo,
        relationship_repo,
        side_hustle_state_repo,
        tournament_invite_repo,
        tournament_session_repo,
        vice_state_repo,
    )
    from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback

    data = build_whereabouts(
        sandbox_id=sandbox_id,
        owner_id=owner_id,
        now=datetime.utcnow(),
        cash_table_repo=cash_table_repo,
        side_hustle_repo=side_hustle_state_repo,
        vice_repo=vice_state_repo,
        relationship_repo=relationship_repo,
        bankroll_repo=bankroll_repo,
        personality_repo=personality_repo,
        tournament_session_repo=tournament_session_repo,
        tournament_invite_repo=tournament_invite_repo,
    )

    met_people = [p for p in data["people"] if p.get("met")]

    # Bulk-resolve persisted emotion for the met pids (schema v97), same
    # source the lobby uses for unseated AIs. Drives both the emotion chip
    # and the avatar fallback's expression.
    emotions: Dict[str, str] = {}
    try:
        pids = [p["personality_id"] for p in met_people]
        blobs = bankroll_repo.load_emotional_state_json_for_pids(
            pids,
            sandbox_id=sandbox_id,
        )
        for pid, blob in (blobs or {}).items():
            if blob:
                emotions[pid] = _resolve_emotion_from_blob(blob, pid)
    except Exception as exc:
        logger.warning("[CASH][WHEREABOUTS] emotion resolution failed: %s", exc)

    enriched = []
    for person in met_people:
        emotion = emotions.get(person["personality_id"], "confident")
        # Never feed the personality_id as a name to the avatar fallback
        # (that path can auto-create a zombie persona); orphans are
        # filtered out of the met set anyway, but guard regardless.
        avatar_url = None
        if STUCK_UNKNOWN_PERSONALITY not in person.get("stuck", []):
            try:
                avatar_url = get_avatar_url_with_fallback(None, person["name"], emotion)
            except Exception:
                avatar_url = None
        # Strip the internal health flags from the player payload — a
        # player shouldn't see "this persona is bugged / overdue".
        clean = {k: v for k, v in person.items() if k not in ("stuck", "watch")}
        clean["emotion"] = emotion
        clean["avatar_url"] = avatar_url
        enriched.append(clean)

    # Fog of war: how many trackable AIs are around that the player
    # hasn't met yet. A count only — never their names/locations — so it
    # teases the wider world without spoiling undiscovered personas.
    unmet_count = len(data["people"]) - len(enriched)

    return jsonify(
        {
            "now": data["now"],
            "people": enriched,
            "unmet_count": unmet_count,
            "counts": {
                "total": len(enriched),
                "idle": sum(1 for p in enriched if p["status"] == "idle"),
                "side_hustle": sum(1 for p in enriched if p["status"] == "side_hustle"),
                "vice": sum(1 for p in enriched if p["status"] == "vice"),
                "seated": sum(1 for p in enriched if p["status"] == "seated"),
            },
        }
    )


@cash_bp.route("/api/cash/file-cabinet", methods=["GET"])
def get_file_cabinet():
    """GET /api/cash/file-cabinet — the dossier roster (Phase 4).

    Everyone the player has accumulated scouting on in their sandbox, with
    the headline stats the UI sorts by and the "People met / Dossiers
    unlocked" header counts. Sorting is client-side. Circuit-only (it reads
    the per-sandbox lifetime store); returns an empty roster for a player
    who hasn't played any Circuit hands yet.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    sandbox_id = _resolve_sandbox_id(owner_id)

    from datetime import datetime

    from flask_app.extensions import game_repo, personality_repo, relationship_repo
    from flask_app.services.file_cabinet import build_file_cabinet

    try:
        data = build_file_cabinet(
            sandbox_id=sandbox_id,
            observer_id=owner_id,
            game_repo=game_repo,
            relationship_repo=relationship_repo,
            personality_repo=personality_repo,
            now=datetime.utcnow(),
        )
    except Exception as e:
        logger.warning("[CASH][FILE_CABINET] build failed: %s", e)
        return jsonify({"people": [], "people_met": 0, "dossiers_unlocked": 0})

    return jsonify(data)


@cash_bp.route("/api/cash/world-pace", methods=["PUT"])
def set_world_pace():
    """PUT /api/cash/world-pace — set how fast the background world ticks.

    Body: `{"pace": "subtle" | "lively" | "bustling"}`. Persisted per
    user; the realtime ticker reads it on the next cycle. Returns the
    stored pace. 400 on an unknown value.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    data = request.get_json(silent=True) or {}
    pace = data.get("pace")
    from flask_app.extensions import user_prefs_repo

    try:
        user_prefs_repo.set_world_pace(owner_id, pace)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"world_pace": pace})


# Friendly labels for the player ledger (#4). Maps the chip-ledger `reason` to a
# human phrase; unknown reasons fall back to a title-cased reason.
_LEDGER_REASON_LABELS = {
    'player_seed': 'Starting chips',
    'player_buy_in': 'Sat down (buy-in)',
    'player_cash_out': 'Left table (take-home)',
    'tournament_buy_in': 'Tournament buy-in',
    'tournament_payout': 'Tournament prize',
    'stake_payoff': 'Backing payout',
    'house_stake_issue': 'Stake received',
    'house_stake_settle': 'Stake settled',
    'forgive_balance': 'Balance forgiven',
    'informant_unlock': 'Scouting fee',
    'table_rake': 'Table rake',
}


@cash_bp.route("/api/cash/ledger", methods=["GET"])
def get_ledger():
    """GET /api/cash/ledger — the human's chip transaction history (#4).

    The Net Worth drawer is a position snapshot (bankroll + stakes); it never
    queries the ledger, so cash and tournament winnings show no line item. This
    returns the itemized statement for the player's global `player:<owner_id>`
    account (across all sandboxes — the player bankroll is global), newest first,
    with a friendly label, signed amount, and a running balance per row.

    The player account already sees both sides of a cash session (`player_buy_in`
    out, `player_cash_out` in — the net is the session P&L) and tournament flows
    (`tournament_buy_in` / `tournament_payout`), so this is a complete history
    without needing the per-game `seat:<game_id>` sub-account rows.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    from core.economy.ledger import player as player_account
    from flask_app.extensions import chip_ledger_repo

    limit = min(int(request.args.get("limit", 200)), 500)
    account = player_account(owner_id)
    if chip_ledger_repo is None:
        return jsonify({"entries": [], "balance": 0})

    entries = chip_ledger_repo.entries_for_account(account, sandbox_id=None, limit=limit)
    balance = chip_ledger_repo.balance_of(account, sandbox_id=None)

    # Running balance: anchor on the true current balance (entries may be
    # truncated by `limit`), walk newest→oldest subtracting each newer signed
    # amount, so `balance_after` is correct for the FULL history at each row.
    running = int(balance)
    out = []
    for e in entries:
        signed = int(e['signed_amount'])
        ctx = e.get('context') or {}
        out.append(
            {
                'created_at': e['created_at'],
                'reason': e['reason'],
                'label': _LEDGER_REASON_LABELS.get(
                    e['reason'], e['reason'].replace('_', ' ').title()
                ),
                'signed_amount': signed,
                'balance_after': running,
                'finishing_position': ctx.get('finishing_position'),
            }
        )
        running -= signed

    return jsonify({"entries": out, "balance": int(balance)})


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

    from cash_mode.staking_tier import (
        TIER_PREMIUM,
        max_carry_for_tier,
        resolve_tier,
    )
    from flask_app.extensions import bankroll_repo, personality_repo, stake_repo

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
                owner_id,
                BORROWER_KIND_HUMAN,
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
        if stake.staker_id not in name_cache and stake.staker_kind == STAKER_KIND_PERSONALITY:
            try:
                personality = personality_repo.load_personality_by_id(
                    stake.staker_id,
                )
                if personality and personality.get("name"):
                    display_name = personality["name"]
            except Exception:
                pass  # fall back to id
            name_cache[stake.staker_id] = display_name
        payables.append(
            {
                "stake_id": stake.stake_id,
                "staker_id": stake.staker_id,
                "staker_kind": stake.staker_kind,
                "staker_display_name": display_name,
                "carry_amount": int(stake.carry_amount),
                "principal": int(stake.principal),
                "stake_tier": stake.stake_tier,
                "created_at": (stake.created_at.isoformat() if stake.created_at else None),
            }
        )
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
                "[CASH][NET_WORTH] list_active_stakes_for_staker failed: %s",
                e,
            )
            active_for_staker = []
        for stake in active_for_staker:
            amount = int(stake.principal) + int(stake.match_amount)
            receivables.append(
                {
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
                    "created_at": (stake.created_at.isoformat() if stake.created_at else None),
                }
            )
            active_receivables_sum += amount

        # Carries owed to the player by AIs who busted under their stake.
        try:
            receivable_carries = stake_repo.list_carries_for_staker(owner_id)
        except Exception as e:
            logger.warning(
                "[CASH][NET_WORTH] list_carries_for_staker failed: %s",
                e,
            )
            receivable_carries = []
        for stake in receivable_carries:
            carry = int(stake.carry_amount)
            receivables.append(
                {
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
                    "created_at": (stake.created_at.isoformat() if stake.created_at else None),
                }
            )
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
                "[CASH][NET_WORTH] list_recent_closed_for_owner failed: %s",
                e,
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
            payout = stake.staker_payout if role == "staker" else stake.borrower_payout
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
            history.append(
                {
                    "stake_id": stake.stake_id,
                    "role": role,
                    "status": stake.status,  # 'settled' | 'defaulted'
                    # v150 — display label for how it resolved when status
                    # alone isn't specific. 'bankruptcy' for valve-
                    # discharged carries; None for ordinary settle/default.
                    "resolution": stake.resolution,
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
                        int(stake.staker_payout) if stake.staker_payout is not None else None
                    ),
                    "borrower_payout": (
                        int(stake.borrower_payout) if stake.borrower_payout is not None else None
                    ),
                    # Net for the player on this stake (positive = won
                    # money, negative = lost money). null when chip flows
                    # weren't captured (pre-v106 history).
                    "net_for_player": net_for_player,
                    "created_at": (stake.created_at.isoformat() if stake.created_at else None),
                    "settled_at": (stake.settled_at.isoformat() if stake.settled_at else None),
                }
            )

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
            owner_id,
            exc,
        )
        pending_forgiveness_count = 0

    return jsonify(
        {
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
        }
    )


@cash_bp.route("/api/cash/state", methods=["GET"])
@limiter.limit(config.RATE_LIMIT_POLLING)
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
        return jsonify(
            {
                "state": None,
                "bankroll": bankroll.chips,
            }
        )

    from flask_app.services import game_state_service

    # The entry screen needs game_id to redirect; stake_label is a
    # nicety. Tolerate missing game_data (DB-only id after a restart;
    # the /game/:id cold-load will rehydrate it).
    game_data = game_state_service.get_game(game_id)
    return jsonify(
        {
            "state": {
                "game_id": game_id,
                "stake_label": game_data.get("cash_stake_label") if game_data else None,
            },
            "bankroll": bankroll.chips,
        }
    )
