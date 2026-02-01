"""Test helper endpoints — only registered when ENABLE_TEST_ROUTES=true.

These endpoints allow Playwright E2E tests to manipulate game state
directly, bypassing the normal game flow for targeted scenario testing.
"""

import json
import logging

from flask import Blueprint, jsonify, request

from ..extensions import socketio, persistence
from ..services import game_state_service

logger = logging.getLogger(__name__)

test_bp = Blueprint('test', __name__)


@test_bp.route('/api/test/set-game-state', methods=['POST'])
def set_game_state():
    """Load a game state snapshot into memory.

    Expects JSON body with:
    - game_id: str
    - snapshot: dict (game state data from a previous /api/test/snapshot call)
    """
    data = request.json or {}
    game_id = data.get('game_id')
    snapshot = data.get('snapshot')

    if not game_id or not snapshot:
        return jsonify({'error': 'game_id and snapshot are required'}), 400

    try:
        from poker.poker_state_machine import PokerStateMachine, PokerPhase
        from poker.poker_game import PokerGameState, Player
        from ..game_adapter import StateMachineAdapter
        from core.card import Card

        # Reconstruct game state from snapshot
        state_data = snapshot.get('game_state', snapshot)

        # Build players
        players = []
        for p in state_data.get('players', []):
            hand = None
            if p.get('hand'):
                hand = tuple(Card(c['rank'], c['suit']) for c in p['hand'])
            players.append(Player(
                name=p['name'],
                stack=p.get('stack', 5000),
                bet=p.get('bet', 0),
                hand=hand,
                is_folded=p.get('is_folded', False),
                is_all_in=p.get('is_all_in', False),
                has_acted=p.get('has_acted', False),
                is_human=p.get('is_human', False),
            ))

        # Build community cards
        community_cards = tuple(
            Card(c['rank'], c['suit'])
            for c in state_data.get('community_cards', [])
        )

        pot = state_data.get('pot', {'total': 0})
        if isinstance(pot, (int, float)):
            pot = {'total': int(pot)}

        game_state = PokerGameState(
            players=tuple(players),
            community_cards=community_cards,
            deck=tuple(),
            pot=pot,
            current_player_idx=state_data.get('current_player_idx', 0),
            current_dealer_idx=state_data.get('current_dealer_idx', 0),
            current_ante=state_data.get('current_ante', state_data.get('big_blind', 100)),
            awaiting_action=state_data.get('awaiting_action', True),
            run_it_out=state_data.get('run_it_out', False),
            current_player_options=tuple(state_data.get('player_options', [])) or None,
        )

        # Build state machine
        phase_name = snapshot.get('phase', state_data.get('phase', 'PRE_FLOP'))
        phase = PokerPhase[phase_name] if isinstance(phase_name, str) else PokerPhase(phase_name)

        base_sm = PokerStateMachine(game_state=game_state)
        base_sm.current_phase = phase
        state_machine = StateMachineAdapter(base_sm)

        game_data = {
            'state_machine': state_machine,
            'ai_controllers': {},
            'messages': snapshot.get('messages', []),
            'owner_id': snapshot.get('owner_id', 'test-owner'),
            'owner_name': snapshot.get('owner_name', 'TestPlayer'),
            'last_announced_phase': None,
            'game_started': True,
        }
        game_state_service.set_game(game_id, game_data)

        return jsonify({'success': True, 'game_id': game_id})
    except Exception as e:
        logger.error(f"Failed to set game state: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@test_bp.route('/api/test/snapshot/<game_id>')
def snapshot_game(game_id):
    """Capture current game state as reusable JSON."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    state_machine = game_data['state_machine']
    game_state = state_machine.game_state

    snapshot = {
        'game_id': game_id,
        'phase': state_machine.current_phase.name,
        'game_state': game_state.to_dict(),
        'messages': game_data.get('messages', []),
        'owner_id': game_data.get('owner_id'),
        'owner_name': game_data.get('owner_name'),
    }

    return jsonify(snapshot)


@test_bp.route('/api/test/emit-event/<game_id>', methods=['POST'])
def emit_event(game_id):
    """Emit a Socket.IO event to a game room.

    Expects JSON body with:
    - event: str (event name)
    - data: dict (event payload)
    """
    data = request.json or {}
    event = data.get('event')
    payload = data.get('data', {})

    if not event:
        return jsonify({'error': 'event is required'}), 400

    socketio.emit(event, payload, to=game_id)
    return jsonify({'success': True})


@test_bp.route('/api/test/reset', methods=['POST'])
def reset_state():
    """Clear all in-memory game state."""
    game_state_service.games.clear()
    game_state_service.game_locks.clear()
    return jsonify({'success': True})
