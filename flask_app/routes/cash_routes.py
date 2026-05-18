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

from cash_mode.bankroll import AIBankrollState, project_bankroll
from cash_mode.seating import full_bankroll_bust
from cash_mode.table import PLAYER_SEAT_ID

logger = logging.getLogger(__name__)

cash_bp = Blueprint("cash", __name__)


# --- Stakes ladder (matches spec §"Stakes ladder") ---

STAKES_LADDER = {
    "$2":   {"big_blind": 2},
    "$10":  {"big_blind": 10},
    "$50":  {"big_blind": 50},
    "$200": {"big_blind": 200},
    "$1000": {"big_blind": 1000},
}

DEFAULT_PLAYER_STARTING_BANKROLL = 5_000
"""Fresh-grant amount for new players. Spec doesn't pin a value; 5k
chips is enough for a $10 table buy-in (400 chips min) plus headroom."""

MIN_BUY_IN_BB = 40
MAX_BUY_IN_BB = 100


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
    """Locate the owner's active cash game in game_state_service.

    Cash games are keyed on the standard `game_id` and tagged with
    `cash_mode=True`. v1 invariant: one active cash game per owner.
    """
    from flask_app.services import game_state_service
    for gid, gdata in list(game_state_service.games.items()):
        if gdata.get("cash_mode") and gdata.get("owner_id") == owner_id:
            return gid
    return None


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

    big_blind = STAKES_LADDER[stake_label]["big_blind"]
    min_buy_in = big_blind * MIN_BUY_IN_BB
    max_buy_in = big_blind * MAX_BUY_IN_BB
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

    from flask_app.extensions import (
        auth_manager, bankroll_repo, hand_history_repo, personality_repo,
        persistence_db_path, relationship_repo,
        capture_label_repo, decision_analysis_repo,
    )

    # 1. Player bankroll: load or seed.
    player_bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if player_bankroll is None:
        from cash_mode.bankroll import PlayerBankrollState
        player_bankroll = PlayerBankrollState(
            player_id=owner_id,
            chips=DEFAULT_PLAYER_STARTING_BANKROLL,
            starting_bankroll=DEFAULT_PLAYER_STARTING_BANKROLL,
        )
        bankroll_repo.save_player_bankroll(player_bankroll)
        logger.info("[CASH] Seeded fresh bankroll for %r at %d chips",
                    owner_id, DEFAULT_PLAYER_STARTING_BANKROLL)

    if player_bankroll.chips < buy_in:
        return jsonify({
            "error": (
                f"Insufficient bankroll: {player_bankroll.chips} chips, "
                f"buy_in {buy_in}"
            ),
        }), 400

    # 2. Pick AI personalities.
    now = datetime.utcnow()
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    selected_ai: list = []
    ai_buy_ins: Dict[str, int] = {}  # personality_id → buy_in
    ai_states: Dict[str, AIBankrollState] = {}  # personality_id → snapped state
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
        # Snap to projected for the upcoming debit.
        ai_states[pid] = AIBankrollState(
            personality_id=pid, chips=projected, last_regen_tick=stored.last_regen_tick,
        )

    if not selected_ai:
        return jsonify({
            "error": "No eligible AI opponents available for this stake",
        }), 503

    # 3. Build the game state. Mirrors the /api/new-game path.
    from datetime import datetime as _dt
    from poker.poker_game import initialize_game_state
    from poker.poker_state_machine import PokerStateMachine
    from poker.memory import AIMemoryManager
    from poker.pressure_detector import PressureEventDetector
    from poker.pressure_stats import PressureStatsTracker
    from poker.repositories.sqlite_repositories import PressureEventRepository
    from poker.hybrid_ai_controller import HybridAIController
    from poker.prompt_config import PromptConfig
    from flask_app.game_adapter import StateMachineAdapter
    from flask_app.routes.game_routes import generate_game_id, load_game_mode_preset
    from flask_app import config

    human_name = _resolve_player_name()
    ai_names = [a["name"] for a in selected_ai]

    # Set starting stack uniformly — players whose buy-ins differ from
    # the human's get adjusted via update_player after initialize.
    game_state = initialize_game_state(
        player_names=ai_names,
        human_name=human_name,
        starting_stack=buy_in,
        big_blind=big_blind,
    )
    # Adjust AI stacks to their per-personality buy-ins.
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
    game_id = generate_game_id()

    # 4. AI controllers — same shape as the tournament path.
    default_prompt_config = load_game_mode_preset("standard")
    default_llm_config: Dict[str, Any] = {}  # use system defaults
    ai_controllers = {}
    for player in state_machine.game_state.players:
        if player.is_human:
            continue
        ai_controllers[player.name] = HybridAIController(
            player.name,
            state_machine,
            llm_config=default_llm_config,
            prompt_config=default_prompt_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    # 5. Memory manager (cash_mode=True wires Phase 3 dispatch's
    # cash_pair_stats writes — the unique cash-mode payoff).
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

    # 6. Advance to first action so hole cards are dealt before recording.
    state_machine.run_until_player_action()
    memory_manager.on_hand_start(
        state_machine.game_state,
        hand_number=1,
        deck_seed=state_machine.current_hand_seed,
    )

    # 7. Debit bankrolls — atomic across all seats.
    player_bankroll = type(player_bankroll)(
        player_id=player_bankroll.player_id,
        chips=player_bankroll.chips - buy_in,
        starting_bankroll=player_bankroll.starting_bankroll,
    )
    bankroll_repo.save_player_bankroll(player_bankroll)
    for pid, state in ai_states.items():
        debited = AIBankrollState(
            personality_id=pid,
            chips=state.chips - ai_buy_ins[pid],
            last_regen_tick=now,
        )
        bankroll_repo.save_ai_bankroll(debited)

    # 8. Register with game_state_service. **No tournament_tracker** —
    # so handle_eliminations + check_tournament_complete no-op.
    # `cash_mode=True` is the flavor flag.
    game_data = {
        "state_machine": state_machine,
        "ai_controllers": ai_controllers,
        "pressure_detector": pressure_detector,
        "pressure_stats": pressure_stats,
        "memory_manager": memory_manager,
        "owner_id": owner_id,
        "owner_name": _resolve_player_name(),
        "llm_config": default_llm_config,
        "player_llm_configs": {},
        "player_prompt_configs": {},
        "default_game_mode": "standard",
        "last_announced_phase": None,
        "guest_tracking_id": None,
        "guest_messages_this_action": 0,
        "messages": [{
            "id": "1",
            "sender": "Table",
            "content": f"*** Cash table {stake_label} — sit down at ${buy_in} ***",
            "timestamp": datetime.now().isoformat(),
            "type": "table",
        }],
        "hand_start_stacks": {
            p.name: p.stack for p in state_machine.game_state.players
        },
        "short_stack_players": set(),
        # Cash-mode flavor flag — every cash-aware hook reads this.
        "cash_mode": True,
        "cash_stake_label": stake_label,
        "cash_personality_ids": {a["name"]: a["personality_id"] for a in selected_ai},
    }

    from flask_app.services import game_state_service
    game_state_service.set_game(game_id, game_data)
    logger.info("[CASH] Created game_id=%r owner=%r stake=%r buy_in=%d ai=%r",
                game_id, owner_id, stake_label, buy_in,
                [a["name"] for a in selected_ai])

    return jsonify({"game_id": game_id})


@cash_bp.route("/api/cash/leave", methods=["POST"])
def leave_table():
    """POST /api/cash/leave — player stands up.

    Between hands: player's stack returns to bankroll. Mid-hand:
    forfeit (spec §"Bust semantics"). v1 simplification: always
    return current stack to bankroll regardless — multi-table v2 can
    refine to the spec's mid-hand-quit forfeit. The tournament-mode
    flow doesn't have a forfeit concept either, so the simpler v1
    behavior aligns with what the engine supports out of the box.

    Player bankroll = 0 + table stack = 0 → fresh-grant fires.

    Tears down the game from `game_state_service`.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({"error": "No active cash session"}), 404

    from flask_app.extensions import bankroll_repo
    from flask_app.services import game_state_service

    game_data = game_state_service.get_game(game_id)
    state_machine = game_data["state_machine"]
    human_player = next(
        (p for p in state_machine.game_state.players if p.is_human), None,
    )
    returned_chips = human_player.stack if human_player else 0

    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if bankroll is None:
        from cash_mode.bankroll import PlayerBankrollState
        bankroll = PlayerBankrollState(
            player_id=owner_id,
            chips=DEFAULT_PLAYER_STARTING_BANKROLL,
            starting_bankroll=DEFAULT_PLAYER_STARTING_BANKROLL,
        )

    new_chips = bankroll.chips + returned_chips
    if new_chips == 0:
        bankroll = full_bankroll_bust(bankroll)
    else:
        bankroll = type(bankroll)(
            player_id=bankroll.player_id,
            chips=new_chips,
            starting_bankroll=bankroll.starting_bankroll,
        )
    bankroll_repo.save_player_bankroll(bankroll)

    game_state_service.delete_game(game_id)
    logger.info("[CASH] Left game_id=%r owner=%r returned=%d bankroll_now=%d",
                game_id, owner_id, returned_chips, bankroll.chips)

    return jsonify({
        "session_ended": True,
        "returned_chips": returned_chips,
        "bankroll": bankroll.chips,
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

    new_bankroll = type(bankroll)(
        player_id=bankroll.player_id,
        chips=bankroll.chips - amount,
        starting_bankroll=bankroll.starting_bankroll,
    )
    bankroll_repo.save_player_bankroll(new_bankroll)

    from flask_app.handlers.game_handler import update_and_emit_game_state
    update_and_emit_game_state(game_id)

    return jsonify({
        "stack": new_stack,
        "bankroll": new_bankroll.chips,
    })


@cash_bp.route("/api/cash/state", methods=["GET"])
def get_state():
    """GET /api/cash/state — minimal status snapshot.

    Returns `{state: null}` if no active session, else `{state:
    {game_id, bankroll, stake_label}}`. Used by the entry page to
    decide whether to redirect to /game/<id>.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    game_id = _find_active_cash_game_id(owner_id)
    if game_id is None:
        return jsonify({"state": None})

    from flask_app.extensions import bankroll_repo
    from flask_app.services import game_state_service

    game_data = game_state_service.get_game(game_id)
    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    return jsonify({
        "state": {
            "game_id": game_id,
            "bankroll": bankroll.chips if bankroll else 0,
            "stake_label": game_data.get("cash_stake_label"),
        },
    })
