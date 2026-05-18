"""Cash mode routes — entry, state, sit/leave/topup, action.

Five REST endpoints for v1:

  POST  /api/cash/start    — pick stake + buy_in, start a new session
                              and seat the player.
  POST  /api/cash/action   — player submits an action mid-hand
                              (fold/check/call/raise/all_in).
  POST  /api/cash/topup    — player tops up between hands.
  POST  /api/cash/leave    — player leaves the table; chips return
                              to bankroll, session ends.
  GET   /api/cash/state    — current session snapshot (table, stacks,
                              bankroll, awaiting status).

The Flask app maintains one active `CashSession` per user via
`cash_session_store` (in-memory singleton). v1 is single-table per
user; v2 will surface a lobby with multiple tables.

Hand progression: `/api/cash/start` runs hands until the player's
turn comes up (returning awaiting_human) or the session can't fill
seats. `/api/cash/action` applies the player's action and resumes;
when the hand completes it returns "continue" and the route runs
another hand. Repeat. This is a synchronous request model — v2 may
move to SocketIO push, but for v1 the polling shape keeps the
integration simple.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from cash_mode import PLAYER_SEAT_ID
from cash_mode.session import HandResult
from flask_app.services.cash_session_service import (
    cash_session_store,
    create_cash_session,
    register_cash_session_with_game_service,
    unregister_cash_session_from_game_service,
)

logger = logging.getLogger(__name__)

cash_bp = Blueprint("cash", __name__)


# --- Stake ladder (matches spec §"Stakes ladder") ---

STAKES_LADDER = {
    "$2":   {"big_blind": 2,    "min_buy_in_bb": 40, "max_buy_in_bb": 100},
    "$10":  {"big_blind": 10,   "min_buy_in_bb": 40, "max_buy_in_bb": 100},
    "$50":  {"big_blind": 50,   "min_buy_in_bb": 40, "max_buy_in_bb": 100},
    "$200": {"big_blind": 200,  "min_buy_in_bb": 40, "max_buy_in_bb": 100},
    "$1000": {"big_blind": 1000, "min_buy_in_bb": 40, "max_buy_in_bb": 100},
}


def _resolve_owner_id() -> str:
    """Return a stable identifier for the current request's user.

    Uses `auth_manager.get_current_user()['id']` — same path tournament
    routes use, so cash session owner_id matches the user id that
    `_authorize_game_access` checks against. Guest sessions go through
    the same channel (auth_manager creates a guest user record for
    them); the returned id is stable across requests for the same
    cookie.

    Raises ValueError if no current user is resolvable. Browser
    sessions should always produce one because the auth middleware
    sets up guest tracking before the route runs; raw curl without
    cookies will fail here, which is intentional.
    """
    from flask_app.extensions import auth_manager
    user = auth_manager.get_current_user() if auth_manager else None
    if user and user.get("id"):
        return user["id"]
    raise ValueError("No owner_id resolvable from request")


def _serialize_session(session) -> Dict[str, Any]:
    """Build the wire-format snapshot returned by /state, /start, /action."""
    table = session.table
    return {
        "game_id": session.game_id,
        "table": {
            "table_id": table.table_id,
            "stake_label": table.stake_label,
            "big_blind": table.big_blind,
            "min_buy_in": table.min_buy_in,
            "max_buy_in": table.max_buy_in,
            "seat_count": table.seat_count,
            "seats": list(table.seats),
            "stacks": dict(table.stacks),
            "hand_in_progress": table.hand_in_progress,
        },
        "player_bankroll": {
            "player_id": session.player_bankroll.player_id,
            "chips": session.player_bankroll.chips,
            "starting_bankroll": session.player_bankroll.starting_bankroll,
        },
        "hand_number": session.hand_number,
        "player_disconnected": session.is_player_disconnected(),
        "player_pending_quit": PLAYER_SEAT_ID in session._pending_quit,
    }


def _serialize_hand_result(result: HandResult) -> Dict[str, Any]:
    return {
        "status": result.status,
        "hand_number": result.hand_number,
        "bust_seats": result.bust_seats,
        "error": result.error,
        "awaiting_player_name": result.awaiting_player_name,
    }


def _build_controller_factory(*, game_id: str, owner_id: str):
    """Return the production AI controller factory used by cash sessions.

    Lazily imports the existing HybridAIController so the cash blueprint
    loads even if the LLM stack isn't ready (test paths use a smaller
    factory injected via the cash_session_service).

    `state_machine` is attached post-construction by `_build_state_machine`
    on each hand (cash mode rebuilds the state machine per hand because
    AI composition changes). Passing `state_machine=None` here is fine
    — the controller's `decide_action` won't fire before the session's
    next refresh attaches the live one.
    """
    from poker.hybrid_ai_controller import HybridAIController
    from core.llm import DEFAULT_PROVIDER, DEFAULT_MODEL
    from flask_app.extensions import capture_label_repo, decision_analysis_repo

    llm_config = {"provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL}

    def factory(personality_id: str, display_name: str, memory_manager):
        return HybridAIController(
            display_name,
            state_machine=None,  # attached per-hand by the session
            llm_config=llm_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    return factory


# --- Routes ---


@cash_bp.route("/api/cash/start", methods=["POST"])
def start_cash_session():
    """POST /api/cash/start

    Body: {stake_label: "$10", buy_in: 500}

    Creates a new session, seats the player, then runs hands until
    the player's turn comes up (or the session can't proceed).
    Returns the current state snapshot + last hand result.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    payload = request.get_json(silent=True) or {}
    stake_label = payload.get("stake_label")
    buy_in = payload.get("buy_in")
    seat_index = int(payload.get("seat_index", 0))

    if stake_label not in STAKES_LADDER:
        return jsonify({
            "error": "Invalid stake_label",
            "valid_stakes": list(STAKES_LADDER.keys()),
        }), 400
    if not isinstance(buy_in, int) or buy_in <= 0:
        return jsonify({"error": "buy_in must be a positive integer"}), 400

    if cash_session_store.has(owner_id):
        return jsonify({
            "error": "A cash session is already active. Leave first.",
        }), 409

    from flask_app.extensions import (
        bankroll_repo, relationship_repo, personality_repo,
        hand_history_repo, persistence_db_path,
    )

    stake = STAKES_LADDER[stake_label]
    try:
        session = create_cash_session(
            owner_id,
            stake_label=stake_label,
            big_blind=stake["big_blind"],
            bankroll_repo=bankroll_repo,
            relationship_repo=relationship_repo,
            personality_repo=personality_repo,
            hand_history_repo=hand_history_repo,
            db_path=persistence_db_path,
            controller_factory=_build_controller_factory(
                game_id=f"cash-{owner_id}",
                owner_id=owner_id,
            ),
            seat_count=6,
            user_id=owner_id,
        )
        session.sit_player(seat_index, buy_in)
    except Exception as e:
        logger.warning("cash/start: session setup failed: %s", e, exc_info=True)
        return jsonify({"error": f"Session setup failed: {e}"}), 500

    cash_session_store.put(owner_id, session)

    # Register with game_state_service NOW (after sit_player) so the
    # existing /game/:gameId page + SocketIO emitter pick it up. The
    # session has a state machine after registration (lazy-built if
    # needed). update_and_emit_game_state(game_id) fires from
    # CashSession's on_state_change callback after every action.
    register_cash_session_with_game_service(session)

    # Run the first hand. May yield with awaiting_human (player's turn).
    try:
        result = session.run_hand()
    except Exception as e:
        logger.warning("cash/start: first hand failed: %s", e, exc_info=True)
        return jsonify({
            "error": f"Hand failed: {e}",
            "state": _serialize_session(session),
            "game_id": session.game_id,
        }), 500

    return jsonify({
        "state": _serialize_session(session),
        "result": _serialize_hand_result(result),
        "game_id": session.game_id,
    })


@cash_bp.route("/api/cash/action", methods=["POST"])
def submit_action():
    """POST /api/cash/action

    Body: {action: "call", raise_to: 0}

    Applies the player's action to the in-progress hand and continues.
    Returns the next state + result (continue → another hand may have
    started; awaiting_human → another action expected).
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    session = cash_session_store.get(owner_id)
    if session is None:
        return jsonify({"error": "No active cash session"}), 404

    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    amount = int(payload.get("raise_to", 0) or 0)
    if action not in {"fold", "check", "call", "raise", "all_in"}:
        return jsonify({"error": f"Invalid action: {action!r}"}), 400

    try:
        result = session.apply_human_action(action, amount)
        # If the hand completed, automatically start the next one
        # until we yield again or can't proceed. Polling caller doesn't
        # have to micromanage hand boundaries.
        while result.status == "continue":
            result = session.run_hand()
    except Exception as e:
        logger.warning("cash/action: failed: %s", e, exc_info=True)
        return jsonify({
            "error": f"Action failed: {e}",
            "state": _serialize_session(session),
        }), 500

    return jsonify({
        "state": _serialize_session(session),
        "result": _serialize_hand_result(result),
    })


@cash_bp.route("/api/cash/topup", methods=["POST"])
def top_up():
    """POST /api/cash/topup

    Body: {amount: 200}

    Top up the player's table stack from bankroll. Between hands only.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    session = cash_session_store.get(owner_id)
    if session is None:
        return jsonify({"error": "No active cash session"}), 404

    payload = request.get_json(silent=True) or {}
    amount = int(payload.get("amount", 0))
    if amount <= 0:
        return jsonify({"error": "amount must be a positive integer"}), 400

    try:
        session.top_up_player(amount)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"state": _serialize_session(session)})


@cash_bp.route("/api/cash/leave", methods=["POST"])
def leave_table():
    """POST /api/cash/leave

    Player stands up from the table. If between hands, stack returns
    to bankroll (clean leave). If mid-hand, the request triggers a
    mid-hand quit (table stack forfeited). Session is ended either
    way.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    session = cash_session_store.get(owner_id)
    if session is None:
        return jsonify({"error": "No active cash session"}), 404

    try:
        if session.table.hand_in_progress:
            session.quit_player()
            # Continue the hand to settlement so the quit forfeit applies
            while True:
                result = session.run_hand()
                if result.status != "awaiting_human":
                    break
                # If somehow awaiting_human after quit (shouldn't happen
                # — quit auto-folds the player), bail to avoid an
                # infinite loop. Logged as a warning.
                logger.warning(
                    "cash/leave: unexpected awaiting_human after quit; bailing"
                )
                break
        else:
            session.leave_player()
    except Exception as e:
        logger.warning("cash/leave: failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500

    final_state = _serialize_session(session)
    unregister_cash_session_from_game_service(session.game_id)
    cash_session_store.end(owner_id)

    return jsonify({"state": final_state, "session_ended": True})


@cash_bp.route("/api/cash/state", methods=["GET"])
def get_state():
    """GET /api/cash/state — current session snapshot.

    Returns 404 if no active session for this user. The frontend can
    use this to recover state after a page refresh.
    """
    try:
        owner_id = _resolve_owner_id()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    session = cash_session_store.get(owner_id)
    if session is None:
        return jsonify({"error": "No active cash session"}), 404

    return jsonify({"state": _serialize_session(session)})
