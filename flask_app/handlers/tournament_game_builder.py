"""Builds the human's live single-table game for a multi-table tournament.

The human plays one table as an ordinary single-table game through the existing
machinery; this builder constructs that game from the tournament field's view of
the human's table (`human_table_seat_specs`). It mirrors the NON-cash tournament
game shape from `game_routes.api_new_game` — minus the single-table
`tournament_tracker` (the multi-table `TournamentSession` owns elimination /
completion) and minus all cash plumbing (bankroll / fish / relationship / rake).

AI seats are production tiered solver bots with the expression (LLM table-talk)
layer OFF — zero LLM, consistent with the headless field. Seats are named by
their field id (e.g. `P07`); the human's seat is named by the field human id and
flagged `is_human` (the frontend renders it as "You").
"""

from __future__ import annotations

from datetime import datetime

from tournament.session import TournamentSession

from .tournament_handler import human_table_seat_specs

# One game id per human-tournament; relocation reconciles in place rather than
# spawning a new game (the id is just a key).


def make_tournament_ai_controller(name: str, state_machine, *, game_id: str, owner_id: str):
    """A production-compatible, no-LLM tiered solver controller for one AI seat."""
    from flask_app.extensions import capture_label_repo, decision_analysis_repo
    from flask_app.handlers.tiered_factory import build_tiered_controller

    return build_tiered_controller(
        player_name=name,
        state_machine=state_machine,
        llm_config={},
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
        expression_enabled=False,  # no LLM table talk in v1
    )


def build_tournament_game(
    session: TournamentSession, *, tournament_id: str, owner_id: str, owner_name: str
) -> str:
    """Create + register the human's live game for their current table. Returns
    the game_id. Advances to the first human action (hole cards dealt)."""
    from flask_app import extensions
    from flask_app.game_adapter import StateMachineAdapter
    from flask_app.routes.game_routes import generate_game_id
    from flask_app.services import game_state_service
    from poker.memory import AIMemoryManager
    from poker.poker_game import Player, PokerGameState, create_deck
    from poker.poker_state_machine import PokerStateMachine
    from poker.pressure_detector import PressureEventDetector
    from poker.pressure_stats import PressureStatsTracker
    from poker.repositories.sqlite_repositories import PressureEventRepository

    specs = human_table_seat_specs(session)
    big_blind = session.current_level().big_blind
    players = tuple(
        Player(name=s.player_id, stack=s.stack, is_human=s.is_human) for s in specs
    )
    dealer_idx = next((i for i, s in enumerate(specs) if s.is_button), 0)
    deck_seed = session.config.seed * 100_003 + session.rounds

    game_state = PokerGameState(
        players=players,
        deck=create_deck(shuffled=True, random_seed=deck_seed),
        current_ante=big_blind,
        last_raise_amount=big_blind,
        current_dealer_idx=dealer_idx,
    )
    base_state_machine = PokerStateMachine(
        game_state=game_state,
        blind_config={"growth": 1.0, "hands_per_level": 999_999, "max_blind": big_blind},
    )
    state_machine = StateMachineAdapter(base_state_machine)
    game_id = f"tourney-{generate_game_id()}"

    persistence_db_path = extensions.persistence_db_path
    hand_history_repo = extensions.hand_history_repo

    ai_controllers: dict = {}
    for s in specs:
        if s.is_human:
            continue
        ai_controllers[s.player_id] = make_tournament_ai_controller(
            s.player_id, state_machine, game_id=game_id, owner_id=owner_id
        )

    memory_manager = AIMemoryManager(game_id, persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(hand_history_repo)
    for s in specs:
        if s.is_human:
            memory_manager.initialize_human_observer(s.player_id, personality_id=owner_id)
            continue
        memory_manager.initialize_for_player(s.player_id)
        controller = ai_controllers[s.player_id]
        controller.session_memory = memory_manager.get_session_memory(s.player_id)
        controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
        controller.memory_manager = memory_manager

    pressure_event_repo = PressureEventRepository(persistence_db_path)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, pressure_event_repo)

    state_machine.run_until_player_action()
    memory_manager.on_hand_start(
        state_machine.game_state, hand_number=1, deck_seed=state_machine.current_hand_seed
    )

    game_data = {
        "state_machine": state_machine,
        "ai_controllers": ai_controllers,
        "pressure_detector": pressure_detector,
        "pressure_stats": pressure_stats,
        "memory_manager": memory_manager,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "last_announced_phase": None,
        "guest_tracking_id": None,
        "messages": [
            {
                "id": "1",
                "sender": "Table",
                "content": "***   MAIN EVENT   ***",
                "timestamp": datetime.now().isoformat(),
                "type": "table",
            }
        ],
        "hand_start_stacks": {p.name: p.stack for p in state_machine.game_state.players},
        "short_stack_players": set(),
        # Tournament meta keys — the gate + bridge read these. NOTE: no
        # `tournament_tracker` (the multi-table session owns eliminations) and no
        # `cash_mode`, so handle_eliminations / check_tournament_complete /
        # the cash block all early-return for this game.
        "tournament_session": session,
        "tournament_id": tournament_id,
        "tournament_table_id": session.human_table.table_id,
        "tournament_human_id": session.human_id,
    }
    game_state_service.set_game(game_id, game_data)
    extensions.game_repo.save_game(game_id, state_machine._state_machine, owner_id, owner_name)
    return game_id


def tournament_hand_boundary(game_id: str, game_data: dict, state_machine) -> bool:
    """Effectful hand-boundary hook for the human's tournament game (called from
    game_handler). Advances the field, reconciles or stops, emits tournament
    events. Returns True if the human's game should pause (out / complete)."""
    from .tournament_handler import (
        COMPLETE,
        HUMAN_OUT,
        RELOCATED,
        advance_tournament_after_hand,
    )

    owner_id = game_data.get("owner_id")

    def _make(name, sm):
        return make_tournament_ai_controller(name, sm, game_id=game_id, owner_id=owner_id)

    outcome = advance_tournament_after_hand(game_data, state_machine, make_controller=_make)
    _emit_tournament(game_data, outcome, RELOCATED=RELOCATED, HUMAN_OUT=HUMAN_OUT, COMPLETE=COMPLETE)
    return outcome.kind in (HUMAN_OUT, COMPLETE)


def _emit_tournament(game_data, outcome, *, RELOCATED, HUMAN_OUT, COMPLETE) -> None:
    """Push tournament events to the owner's lobby room (already joined). Always
    a standings update; plus a relocation / elimination / complete beat."""
    try:
        from flask_app.extensions import socketio
        from flask_app.services import presence

        if socketio is None:
            return
        owner_id = game_data.get("owner_id")
        tournament_id = game_data.get("tournament_id")
        room = presence.lobby_room_name(owner_id)
        payload = {"tournament_id": tournament_id, "standings": outcome.standings}
        socketio.emit("tournament_update", payload, to=room)
        if outcome.kind == RELOCATED:
            socketio.emit(
                "tournament_relocated",
                {"tournament_id": tournament_id, "table_id": outcome.table_id},
                to=room,
            )
        elif outcome.kind == HUMAN_OUT:
            socketio.emit(
                "tournament_eliminated",
                {
                    "tournament_id": tournament_id,
                    "finishing_position": outcome.standings["human"]["rank"],
                },
                to=room,
            )
        elif outcome.kind == COMPLETE:
            socketio.emit(
                "tournament_complete",
                {"tournament_id": tournament_id, "standings": outcome.standings},
                to=room,
            )
    except Exception:  # noqa: BLE001 — emits are best-effort
        pass
