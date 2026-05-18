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
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from cash_mode.bankroll import (
    AIBankrollState,
    PlayerBankrollState,
    credit_ai_cash_out,
    project_bankroll,
)
from cash_mode.loan_settlement import classify_loan_outcome
from cash_mode.sponsor_offers import (
    PersonalitySponsorOffer,
    compute_offers_for_table,
    compute_personality_offers,
    offer_for_archetype,
)
from poker.memory.relationship_events import RelationshipEvent
from cash_mode.stakes import (
    MAX_BUY_IN_BB,
    MIN_BUY_IN_BB,
    STAKES_LADDER,
    STAKES_ORDER,
    is_sponsor_eligible,
    table_buy_in_window,
)
from cash_mode.table import PLAYER_SEAT_ID

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
    """
    from flask_app.extensions import game_repo
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
    if purged:
        logger.info(
            "[CASH] purged %d prior cash row(s) for owner=%r: %s",
            len(purged), owner_id, purged,
        )
    return len(purged)


def _load_or_seed_player_bankroll(owner_id: str) -> PlayerBankrollState:
    """Load the player's bankroll row or create a fresh seed on miss.

    Centralizes the "first-time entry" path so every cash route lands
    the same seed amount and writes the row immediately. Subsequent
    routes can assume `load_player_bankroll` returns non-None.
    """
    from flask_app.extensions import bankroll_repo
    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if bankroll is not None:
        return bankroll
    bankroll = PlayerBankrollState(
        player_id=owner_id,
        chips=DEFAULT_PLAYER_STARTING_BANKROLL,
        starting_bankroll=DEFAULT_PLAYER_STARTING_BANKROLL,
    )
    bankroll_repo.save_player_bankroll(bankroll)
    logger.info("[CASH] Seeded fresh bankroll for %r at %d chips",
                owner_id, DEFAULT_PLAYER_STARTING_BANKROLL)
    return bankroll


def _build_cash_game(
    *,
    owner_id: str,
    stake_label: str,
    player_starting_stack: int,
    welcome_message: str,
    opponent_count: int = 5,
    now: Optional[datetime] = None,
    preselected_ai: Optional[list] = None,
    preselected_ai_chips: Optional[Dict[str, int]] = None,
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
        bankroll_repo, hand_history_repo, personality_repo,
        persistence_db_path, relationship_repo,
        capture_label_repo, decision_analysis_repo,
    )

    selected_ai: list = []
    ai_buy_ins: Dict[str, int] = {}
    ai_states: Dict[str, AIBankrollState] = {}

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

            stored = bankroll_repo.load_ai_bankroll(pid)
            if stored is None:
                projected = knobs.bankroll_cap
                stored = AIBankrollState(personality_id=pid, chips=projected, last_regen_tick=None)
            else:
                projected = project_bankroll(
                    stored, knobs.bankroll_cap, knobs.bankroll_rate, now,
                )
            if projected < ai_threshold:
                continue
            selected_ai.append({"personality_id": pid, "name": name})
            ai_buy_ins[pid] = ai_buy_in
            ai_states[pid] = AIBankrollState(
                personality_id=pid, chips=projected, last_regen_tick=stored.last_regen_tick,
            )

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
    memory_manager.set_relationship_repo(relationship_repo, cash_mode=True)
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
            memory_manager.initialize_human_observer(player.name, personality_id=pid)

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
    for pid, state in ai_states.items():
        debited = AIBankrollState(
            personality_id=pid,
            chips=state.chips - ai_buy_ins[pid],
            last_regen_tick=now,
        )
        bankroll_repo.save_ai_bankroll(debited)

    # 7. Register with game_state_service.
    game_data = {
        "state_machine": state_machine,
        "ai_controllers": ai_controllers,
        "pressure_detector": pressure_detector,
        "pressure_stats": pressure_stats,
        "memory_manager": memory_manager,
        "owner_id": owner_id,
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
    }

    from flask_app.services import game_state_service
    game_state_service.set_game(game_id, game_data)
    logger.info("[CASH] Created game_id=%r owner=%r stake=%r player_stack=%d ai=%r",
                game_id, owner_id, stake_label, player_starting_stack,
                [a["name"] for a in selected_ai])
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

    payload = request.get_json(silent=True) or {}
    table_id = payload.get("table_id")
    seat_index = payload.get("seat_index")
    buy_in = payload.get("buy_in")

    if not isinstance(table_id, str) or not table_id:
        return jsonify({"error": "table_id is required"}), 400
    if not isinstance(seat_index, int) or seat_index < 0:
        return jsonify({"error": "seat_index must be a non-negative integer"}), 400

    from flask_app.extensions import bankroll_repo, cash_table_repo

    table = cash_table_repo.load_table(table_id)
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
    from cash_mode.tables import human_slot
    claimed_table = table.with_seat(seat_index, human_slot(owner_id, buy_in))
    cash_table_repo.save_table(claimed_table)

    # Build the cash game using the table's CURRENT AI roster + chip counts.
    preselected_ai = []
    preselected_chips = {}
    for slot in claimed_table.seats:
        if slot["kind"] != "ai":
            continue
        pid = slot["personality_id"]
        # Look up display name from the personality repo. Falls back to
        # personality_id if the row was deleted under us (shouldn't
        # happen for seated AIs).
        from flask_app.extensions import personality_repo
        personality = None
        try:
            personality = personality_repo.load_personality_by_id(pid)
        except Exception:
            personality = None
        name = (personality or {}).get("name") if personality else pid
        preselected_ai.append({"personality_id": pid, "name": name})
        preselected_chips[pid] = int(slot.get("chips", 0))

    game_id, err = _build_cash_game(
        owner_id=owner_id,
        stake_label=stake_label,
        player_starting_stack=buy_in,
        welcome_message=(
            f"*** Cash table {stake_label} — sit down at ${buy_in} ***"
        ),
        preselected_ai=preselected_ai,
        preselected_ai_chips=preselected_chips,
    )
    if err is not None:
        # Roll back the seat claim so the player can retry.
        cash_table_repo.save_table(table)
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
    )

    broad_candidates = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    candidates = broad_candidates

    if table_id:
        table = cash_table_repo.load_table(table_id)
        if table is not None and table.stake_label == stake_label:
            seated_pids = set(table.seated_personality_ids())
            narrowed = [
                c for c in broad_candidates
                if c.get("personality_id") in seated_pids
            ]
            if narrowed:
                candidates = narrowed

    personality_offers = compute_personality_offers(
        player_owner_id=owner_id,
        min_buy_in=min_buy_in,
        max_buy_in=max_buy_in,
        candidate_personalities=candidates,
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        count=3,
    )

    # Lobby v1.5 fallback: if the narrowed-to-table pool produced zero
    # qualifying offers, retry with the broader pool. House archetypes
    # are still the final fallback when even that returns nothing.
    if table_id and not personality_offers and candidates is not broad_candidates:
        personality_offers = compute_personality_offers(
            player_owner_id=owner_id,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
            candidate_personalities=broad_candidates,
            bankroll_repo=bankroll_repo,
            relationship_repo=relationship_repo,
            count=3,
        )

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
    })


def _record_relationship_event(
    *,
    actor_id: str,
    target_id: str,
    event: RelationshipEvent,
) -> None:
    """Fire a relationship event from outside hand flow.

    Path B emits SPONSORSHIP_OFFERED at sit-down and LOAN_REPAID /
    LOAN_DEFAULTED at leave. None of those happen inside hand flow
    where a `memory_manager` is already wired into the game; the
    route constructs a transient `OpponentModelManager` around the
    live `relationship_repo` so the projection-on-read / clamp /
    persist guarantees inside `record_event` still apply.

    Failures (missing repo, repo write error) log a warning and
    return silently — the loan settlement is the load-bearing
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
    min_buy_in: int,
    max_buy_in: int,
    bankroll_repo,
    personality_repo,
    relationship_repo,
) -> Optional[PersonalitySponsorOffer]:
    """Server-side: re-derive a personality offer fresh for sponsor-and-sit.

    Mirrors `offer_for_archetype` — the client only sends an id, and
    the server recomputes the concrete terms from authoritative state
    (lender's projected bankroll, relationship axes). A tampered
    client can't grift better terms than the lender's profile +
    relationship permits.

    Returns None if the named lender doesn't qualify (unwilling, broke,
    respect floor / heat ceiling violations, missing personality).
    The caller treats None as a tampering or stale-offer condition.
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
        min_buy_in=min_buy_in,
        max_buy_in=max_buy_in,
        candidate_personalities=[match],
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        count=1,
    )
    return offers[0] if offers else None


@cash_bp.route("/api/cash/sponsor-and-sit", methods=["POST"])
def sponsor_and_sit():
    """POST /api/cash/sponsor-and-sit
       body: {stake_label, archetype_id | lender_id, opponents?}

    Atomic: validate sponsor eligibility, look up archetype OR
    personality lender, build the cash game with `loan.amount` as the
    player's starting stack, record the loan terms on
    `player_bankroll_state`. The loan never lands in bankroll — it
    goes directly to the table stack, closing the "pocket the spare
    loan" exploit by construction.

    Two paths:
      - `archetype_id` (string) → anonymous house archetype (v1
        sponsorship). `active_loan_lender_id` stays NULL.
      - `lender_id` (string) → Path B personality sponsorship. The
        offer is re-materialized server-side from the lender's
        projected bankroll + the relationship axes — clients can't
        tamper. `active_loan_lender_id` is set to `lender_id`, so
        leave-time settlement routes sponsor_total back to the AI
        lender's bankroll (commit 5).

    Either field can be present; exactly one is required. Sending
    both is rejected to make the source-of-truth unambiguous.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    payload = request.get_json(silent=True) or {}
    stake_label = payload.get("stake_label")
    archetype_id = payload.get("archetype_id")
    lender_id = payload.get("lender_id")
    opponent_count = int(payload.get("opponents", 5))

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

    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify({
            "error": "A cash session is already active. Leave first.",
            "game_id": existing,
        }), 409

    from flask_app.extensions import (
        bankroll_repo, personality_repo, relationship_repo,
    )
    bankroll = _load_or_seed_player_bankroll(owner_id)

    if not is_sponsor_eligible(bankroll.chips, stake_label):
        return jsonify({
            "error": "Not sponsor-eligible at this stake",
            "bankroll": bankroll.chips,
        }), 400

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    # Resolve to a concrete offer — server-side, fresh from authoritative
    # state, no client trust.
    if lender_id:
        personality_offer = _materialize_personality_offer(
            lender_id=lender_id,
            player_owner_id=owner_id,
            min_buy_in=min_buy_in,
            max_buy_in=max_buy_in,
            bankroll_repo=bankroll_repo,
            personality_repo=personality_repo,
            relationship_repo=relationship_repo,
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

    # Build + register the game with loan.amount as the starting stack.
    game_id, err = _build_cash_game(
        owner_id=owner_id,
        stake_label=stake_label,
        player_starting_stack=offer_amount,
        welcome_message=(
            f"*** Cash table {stake_label} — sponsored sit-down "
            f"({welcome_lender_label}: ${offer_amount}) ***"
        ),
        opponent_count=opponent_count,
    )
    if err is not None:
        return jsonify(err[0]), err[1]

    # Record the loan terms; bankroll chips unchanged (loan went
    # straight to the table stack, never landed in bankroll).
    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips,
        starting_bankroll=bankroll.starting_bankroll,
        active_loan_amount=offer_amount,
        active_loan_floor=offer_floor,
        active_loan_rate=offer_rate,
        active_loan_lender_id=offer_lender_id,
    ))

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
            event=RelationshipEvent.SPONSORSHIP_OFFERED,
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
    if bankroll.active_loan_amount > 0:
        return jsonify({
            "error": "Rebuy disabled while a sponsor loan is active. Leave the table to settle.",
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
        active_loan_amount=bankroll.active_loan_amount,
        active_loan_floor=bankroll.active_loan_floor,
        active_loan_rate=bankroll.active_loan_rate,
    ))

    from flask_app.handlers.game_handler import update_and_emit_game_state
    update_and_emit_game_state(game_id)

    return jsonify({
        "stack": amount,
        "bankroll": bankroll.chips - amount,
    })


@cash_bp.route("/api/cash/leave", methods=["POST"])
def leave_table():
    """POST /api/cash/leave — player stands up; sponsor loan settles.

    Pulls the human's current `Player.stack` and applies the
    leave-time loan math via `settle_loan_on_leave`:
      - With an active loan: chips_at_table satisfies the floor
        first, then the sponsor takes their rate of what remains;
        whatever's left lands back in bankroll. Loan fields reset.
      - Without an active loan: chips_at_table returns to bankroll
        verbatim.

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

    from cash_mode.loan_settlement import settle_loan_on_leave
    from flask_app.extensions import bankroll_repo, game_repo, personality_repo
    from flask_app.services import game_state_service

    game_data = game_state_service.get_game(game_id)
    state_machine = game_data["state_machine"]
    human_player = next(
        (p for p in state_machine.game_state.players if p.is_human), None,
    )
    chips_at_table = human_player.stack if human_player else 0

    bankroll = _load_or_seed_player_bankroll(owner_id)

    # Snapshot the loan state before settle clears it — needed for the
    # post-settlement event classification (LOAN_REPAID vs LOAN_DEFAULTED).
    loan_outcome = classify_loan_outcome(bankroll, chips_at_table)
    lender_id_at_settle = bankroll.active_loan_lender_id

    # Settlement: pass the bankroll_repo so Path B can credit the AI
    # lender's persistent bankroll with sponsor_total (clamped to cap).
    # Anonymous loans (NULL lender_id) skip the AI-credit branch.
    settlement = settle_loan_on_leave(
        bankroll, chips_at_table, bankroll_repo=bankroll_repo,
    )
    bankroll_repo.save_player_bankroll(settlement.new_bankroll)

    # Path B relationship event: only when a named AI lender was on
    # the loan. Anonymous loans don't have an `actor` to update.
    if lender_id_at_settle:
        if loan_outcome == "repaid":
            _record_relationship_event(
                actor_id=lender_id_at_settle,
                target_id=owner_id,
                event=RelationshipEvent.LOAN_REPAID,
            )
        elif loan_outcome == "defaulted":
            _record_relationship_event(
                actor_id=lender_id_at_settle,
                target_id=owner_id,
                event=RelationshipEvent.LOAN_DEFAULTED,
            )
        # "no_chips_no_event" / "no_loan" branches deliberately silent —
        # the v1 rule forgives full busts and no-loan leaves don't have
        # a credit relationship to update.

    # Credit every seated AI's current Player.stack back to their
    # persistent bankroll. Without this loop, AI table winnings
    # evaporate at session end and AI bankrolls drift monotonically
    # downward — sit-down debits never get matched by cash-out
    # credits. Path B (AI sponsorship) needs this to be honest, since
    # lender-eligibility reads `load_ai_bankroll_current`.
    cash_personality_ids: Dict[str, str] = game_data.get(
        "cash_personality_ids", {}
    ) or {}
    now = datetime.utcnow()
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
        credit_ai_cash_out(
            bankroll_repo,
            pid,
            player.stack,
            now=now,
        )

    # Lobby v1.5: persist end-of-session chip counts back to the
    # `cash_tables` row, free the human seat, and run a final
    # refresh_table_roster so AI movement can act on the post-session
    # state. The seat free happens BEFORE the refresh so live-fill can
    # claim the now-open intent slot.
    cash_table_id = game_data.get("cash_table_id")
    cash_seat_index = game_data.get("cash_seat_index")
    if cash_table_id is not None:
        from cash_mode.tables import ai_slot, open_slot
        from flask_app.extensions import cash_table_repo
        table = cash_table_repo.load_table(cash_table_id)
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
                if cash_seat_index is not None and idx == cash_seat_index and slot["kind"] == "human":
                    # Free the human's seat back to "open".
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
            )
            cash_table_repo.save_table(updated_table, now=now)
            logger.info(
                "[CASH][LOBBY] freed seat %r:%s and persisted final chip counts",
                cash_table_id, cash_seat_index,
            )

            # Final refresh pass: lets AI movement act on the post-leave
            # state (e.g., an AI who won big can now stake_up).
            try:
                from cash_mode.lobby import refresh_unseated_tables
                refresh_unseated_tables(
                    cash_table_repo=cash_table_repo,
                    personality_repo=personality_repo,
                    bankroll_repo=bankroll_repo,
                    user_id=owner_id,
                    now=now,
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

    had_loan = bankroll.active_loan_amount > 0
    logger.info(
        "[CASH] Left game_id=%r owner=%r chips_at_table=%d had_loan=%s "
        "sponsor_repaid=%d returned=%d bankroll_now=%d",
        game_id, owner_id, chips_at_table, had_loan,
        settlement.sponsor_total, settlement.returned_chips,
        settlement.new_bankroll.chips,
    )

    return jsonify({
        "session_ended": True,
        "chips_at_table": chips_at_table,
        "had_active_loan": had_loan,
        "sponsor_repaid": settlement.sponsor_total,
        "returned_chips": settlement.returned_chips,
        "bankroll": settlement.new_bankroll.chips,
    })


@cash_bp.route("/api/cash/topup", methods=["POST"])
def top_up():
    """POST /api/cash/topup body: {amount: int}

    Top up the human player's stack from bankroll. v1 hard rule:
    between hands only — checks `state_machine.current_phase` is
    not in the middle of betting.
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

    # Hard rule: only between hands. INITIALIZING_HAND / HAND_OVER are
    # the safe phases; anything mid-hand we reject.
    if state_machine.current_phase not in (
        PokerPhase.INITIALIZING_GAME,
        PokerPhase.INITIALIZING_HAND,
        PokerPhase.HAND_OVER,
    ):
        return jsonify({
            "error": "Top up is only allowed between hands",
        }), 400

    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if bankroll is None or bankroll.chips < amount:
        return jsonify({"error": "Insufficient bankroll"}), 400
    if bankroll.active_loan_amount > 0:
        # Mingling bankroll chips with loan chips would corrupt the
        # leave-time math (your top-up money would be taxed by the
        # sponsor's cut). Force the player to settle first.
        return jsonify({
            "error": "Top-up disabled while a sponsor loan is active. Leave the table to settle.",
        }), 400

    human_idx = next(
        (i for i, p in enumerate(state_machine.game_state.players) if p.is_human),
        None,
    )
    if human_idx is None:
        return jsonify({"error": "Player not seated"}), 400

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
        "tables": [
          {
            "table_id": str,
            "stake_label": str,
            "big_blind": int,
            "min_buy_in": int,
            "max_buy_in": int,
            "affordability": "affordable" | "sponsor_eligible" | "locked",
            "seats": [
              {"kind": "open", "index": int}                        |
              {"kind": "ai", "index": int, "personality_id": str,
               "name": str, "avatar_url": str|null, "emotion": str,
               "chips": int, "relationship_hint": str}              |
              {"kind": "human", "index": int, "personality_id": str,
               "chips": int}
            ]
          },
          ...
        ]
      }
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    from flask_app.extensions import (
        bankroll_repo, cash_table_repo, personality_repo,
        relationship_repo,
    )
    from flask_app.handlers.avatar_handler import get_avatar_url_with_fallback
    from flask_app.services import game_state_service
    from cash_mode.lobby import refresh_unseated_tables

    bankroll = _load_or_seed_player_bankroll(owner_id)

    # Read-side movement refresh on unseated tables. The handoff
    # documents this as intentional: lazy cadence vs. background ticker.
    try:
        refresh_unseated_tables(
            cash_table_repo=cash_table_repo,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            user_id=owner_id,
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

    tables = cash_table_repo.list_all_tables()

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
                entry["personality_id"] = pid
                ai_name = (personality or {}).get("name") if personality else pid
                entry["name"] = ai_name
                entry["chips"] = int(slot.get("chips", 0))
                emotion = active_emotions.get(ai_name, "confident")
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
        })

    return jsonify({
        "bankroll": bankroll.chips,
        "tables": response_tables,
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
