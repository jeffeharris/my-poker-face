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

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState, project_bankroll
from cash_mode.sponsor_offers import (
    compute_offers_for_table,
    offer_for_archetype,
)
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
    auto-saves cash sessions, so a backend reload that wiped the
    in-memory copy still leaves the row behind under the `cash-`
    prefix. Returning the id lets `/api/game-state/<id>` cold-load
    the game back into memory with cash-mode flags restored. v1
    invariant: one active cash game per owner; if the DB has
    multiples (e.g., from prior unclean shutdowns) we pick the most
    recently updated one.
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
) -> tuple[Optional[str], Optional[tuple[dict, int]]]:
    """Create + register a cash game; return (game_id, None) or (None, (err, status)).

    Pure game-setup — AI selection, state machine, controllers, memory
    manager, registration. Does NOT touch the player bankroll: the
    caller decides whether to debit (start path) or write loan fields
    (sponsor path). AI bankrolls ARE debited here — they're symmetric
    across both paths.

    The error tuple is `(json_body, http_status)` so the caller can
    `return jsonify(err), status` directly.
    """
    if now is None:
        now = datetime.utcnow()

    big_blind, min_buy_in, max_buy_in = table_buy_in_window(stake_label)

    from flask_app.extensions import (
        bankroll_repo, hand_history_repo, personality_repo,
        persistence_db_path, relationship_repo,
        capture_label_repo, decision_analysis_repo,
    )

    # 1. Pick AI personalities.
    eligible = personality_repo.list_eligible_for_cash_mode(user_id=owner_id)
    selected_ai: list = []
    ai_buy_ins: Dict[str, int] = {}
    ai_states: Dict[str, AIBankrollState] = {}
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

    # 3. AI controllers — same shape as the tournament path.
    default_prompt_config = load_game_mode_preset("standard")
    default_llm_config: Dict[str, Any] = {}
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
        "player_llm_configs": {},
        "player_prompt_configs": {},
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


@cash_bp.route("/api/cash/sponsor-offers", methods=["GET"])
def sponsor_offers_for_stake():
    """GET /api/cash/sponsor-offers?stake_label=$10

    Returns up to 3 sampled sponsor offers for the requested stake.
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
    offers = compute_offers_for_table(min_buy_in, max_buy_in)
    return jsonify({
        "eligible": True,
        "stake_label": stake_label,
        "offers": [
            {
                "archetype_id": o.archetype_id,
                "name": o.name,
                "amount": o.amount,
                "floor": o.floor,
                "rate": o.rate,
                "flavor": o.flavor,
            }
            for o in offers
        ],
    })


@cash_bp.route("/api/cash/sponsor-and-sit", methods=["POST"])
def sponsor_and_sit():
    """POST /api/cash/sponsor-and-sit body: {stake_label, archetype_id, opponents?}

    Atomic: validate sponsor eligibility, look up archetype, build
    the cash game with `loan.amount` as the player's starting stack,
    record the loan terms on `player_bankroll_state`. The loan never
    lands in bankroll — it goes directly to the table stack, closing
    the "pocket the spare loan" exploit by construction.

    The client only sends `archetype_id`; the server recomputes the
    concrete amount/floor/rate from the archetype + table window,
    so a tampered client can't grift better terms than the archetype
    defines.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    payload = request.get_json(silent=True) or {}
    stake_label = payload.get("stake_label")
    archetype_id = payload.get("archetype_id")
    opponent_count = int(payload.get("opponents", 5))

    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": "Invalid stake_label",
            "valid_stakes": list(STAKES_LADDER.keys()),
        }), 400
    if not isinstance(archetype_id, str) or not archetype_id:
        return jsonify({"error": "archetype_id is required"}), 400

    existing = _find_active_cash_game_id(owner_id)
    if existing is not None:
        return jsonify({
            "error": "A cash session is already active. Leave first.",
            "game_id": existing,
        }), 409

    from flask_app.extensions import bankroll_repo
    bankroll = _load_or_seed_player_bankroll(owner_id)

    if not is_sponsor_eligible(bankroll.chips, stake_label):
        return jsonify({
            "error": "Not sponsor-eligible at this stake",
            "bankroll": bankroll.chips,
        }), 400

    _, min_buy_in, max_buy_in = table_buy_in_window(stake_label)
    offer = offer_for_archetype(archetype_id, min_buy_in, max_buy_in)
    if offer is None:
        return jsonify({"error": f"Unknown sponsor archetype {archetype_id!r}"}), 400

    # Build + register the game with loan.amount as the starting stack.
    game_id, err = _build_cash_game(
        owner_id=owner_id,
        stake_label=stake_label,
        player_starting_stack=offer.amount,
        welcome_message=(
            f"*** Cash table {stake_label} — sponsored sit-down "
            f"({offer.name}: ${offer.amount}) ***"
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
        active_loan_amount=offer.amount,
        active_loan_floor=offer.floor,
        active_loan_rate=offer.rate,
    ))

    logger.info(
        "[CASH] Sponsored sit %r owner=%r stake=%r archetype=%r "
        "amount=%d floor=%.2f rate=%.2f",
        game_id, owner_id, stake_label, archetype_id,
        offer.amount, offer.floor, offer.rate,
    )

    return jsonify({
        "game_id": game_id,
        "offer": {
            "archetype_id": offer.archetype_id,
            "name": offer.name,
            "amount": offer.amount,
            "floor": offer.floor,
            "rate": offer.rate,
            "flavor": offer.flavor,
        },
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
    from flask_app.extensions import bankroll_repo
    from flask_app.services import game_state_service

    # `_find_active_cash_game_id` may have returned a DB-only id
    # (in-memory cache lost to a backend restart). Fall back to the
    # persisted state machine so the player can still cash out the
    # last-saved stack without having to navigate to /game/:id
    # first to trigger a cold-load.
    from flask_app.extensions import game_repo
    game_data = game_state_service.get_game(game_id)
    if game_data is not None:
        state_machine = game_data["state_machine"]
        human_player = next(
            (p for p in state_machine.game_state.players if p.is_human), None,
        )
        chips_at_table = human_player.stack if human_player else 0
    else:
        base_state_machine = game_repo.load_game(game_id)
        if base_state_machine is None:
            return jsonify({"error": "No active cash session"}), 404
        human_player = next(
            (p for p in base_state_machine.game_state.players if p.is_human),
            None,
        )
        chips_at_table = human_player.stack if human_player else 0

    bankroll = _load_or_seed_player_bankroll(owner_id)
    settlement = settle_loan_on_leave(bankroll, chips_at_table)
    bankroll_repo.save_player_bankroll(settlement.new_bankroll)

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

    # `_find_active_cash_game_id` may return a DB-only id (after a
    # backend restart that wiped the in-memory cache). The /game/:id
    # cold-load path will rehydrate it on the next request — here we
    # just publish what the entry screen needs: a game_id to redirect
    # to and the current bankroll. `stake_label` is optional and
    # unused by the redirect, so we tolerate it being absent.
    game_data = game_state_service.get_game(game_id)
    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    return jsonify({
        "state": {
            "game_id": game_id,
            "bankroll": bankroll.chips if bankroll else 0,
            "stake_label": game_data.get("cash_stake_label") if game_data else None,
        },
    })
