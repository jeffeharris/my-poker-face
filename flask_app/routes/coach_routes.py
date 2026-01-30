"""Coach routes — REST endpoints for the poker coaching feature."""

import logging

from flask import Blueprint, jsonify, request

from ..extensions import limiter, auth_manager
from ..services import game_state_service
from ..services.coach_engine import compute_coaching_data
from ..services.coach_assistant import get_or_create_coach

logger = logging.getLogger(__name__)

coach_bp = Blueprint('coach', __name__)

PROACTIVE_TIP_MARKER = '__proactive_tip__'


def _get_human_player_name(game_data: dict) -> str | None:
    """Return the human player's name, or None."""
    game_state = game_data['state_machine'].game_state
    for player in game_state.players:
        if player.is_human:
            return player.name
    return None


@coach_bp.route('/api/coach/<game_id>/stats')
def coach_stats(game_id: str):
    """Return pre-computed coaching statistics for the human player."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    data = compute_coaching_data(game_id, player_name)
    if data is None:
        return jsonify({'error': 'Could not compute stats'}), 500

    return jsonify(data)


@coach_bp.route('/api/coach/<game_id>/ask', methods=['POST'])
@limiter.limit("10/minute")
def coach_ask(game_id: str):
    """Answer a coaching question (or generate a proactive tip)."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    body = request.get_json(silent=True) or {}
    question = body.get('question', '').strip()
    if not question:
        return jsonify({'error': 'No question provided'}), 400

    # Compute current stats
    stats = compute_coaching_data(game_id, player_name)

    coach = get_or_create_coach(game_data, game_id)

    try:
        if question == PROACTIVE_TIP_MARKER:
            answer = coach.get_proactive_tip(stats or {})
        else:
            answer = coach.ask(question, stats or {})
    except Exception as e:
        logger.error(f"Coach ask failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    return jsonify({
        'answer': answer,
        'stats': stats,
    })


@coach_bp.route('/api/coach/<game_id>/config', methods=['POST'])
def coach_config(game_id: str):
    """Store coach mode preference for the game."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    body = request.get_json(silent=True) or {}
    mode = body.get('mode')
    if mode not in ('proactive', 'reactive', 'off'):
        return jsonify({'error': 'Invalid mode'}), 400

    game_data['coach_config'] = {'mode': mode}
    return jsonify({'status': 'ok', 'mode': mode})
