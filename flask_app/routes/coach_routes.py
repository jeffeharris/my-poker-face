"""Coach routes — REST endpoints for the poker coaching feature."""

import logging
from typing import Optional

from flask import Blueprint, jsonify, request

from ..extensions import limiter, persistence
from ..services import game_state_service
from ..services.coach_engine import compute_coaching_data
from ..services.coach_assistant import get_or_create_coach
from .stats_routes import build_hand_context_from_recorded_hand, format_hand_context_for_prompt

logger = logging.getLogger(__name__)

coach_bp = Blueprint('coach', __name__)


def _get_human_player_name(game_data: dict) -> Optional[str]:
    """Return the human player's name, or None."""
    game_state = game_data['state_machine'].game_state
    for player in game_state.players:
        if player.is_human:
            return player.name
    return None


@coach_bp.route('/api/coach/<game_id>/stats')
@limiter.limit("30/minute")
def coach_stats(game_id: str):
    """Return pre-computed coaching statistics for the human player."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    data = compute_coaching_data(game_id, player_name, game_data=game_data)
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
    request_type = body.get('type', '')
    question = body.get('question', '').strip()
    request_player_name = body.get('playerName', '')

    if request_type != 'proactive_tip' and not question:
        return jsonify({'error': 'No question provided'}), 400

    # Compute current stats
    stats = compute_coaching_data(game_id, player_name, game_data=game_data)

    coach = get_or_create_coach(game_data, game_id, player_name=request_player_name or player_name)

    try:
        if request_type == 'proactive_tip':
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


@coach_bp.route('/api/coach/<game_id>/config', methods=['GET'])
@limiter.limit("30/minute")
def coach_config_get(game_id: str):
    """Load coach mode preference for the game."""
    game_data = game_state_service.get_game(game_id)
    if game_data:
        config = game_data.get('coach_config', {})
        mode = config.get('mode')
        if mode:
            return jsonify({'mode': mode})

    mode = persistence.load_coach_mode(game_id)
    return jsonify({'mode': mode})


@coach_bp.route('/api/coach/<game_id>/config', methods=['POST'])
@limiter.limit("30/minute")
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
    persistence.save_coach_mode(game_id, mode)
    return jsonify({'status': 'ok', 'mode': mode})


@coach_bp.route('/api/coach/<game_id>/hand-review', methods=['POST'])
@limiter.limit("10/minute")
def coach_hand_review(game_id: str):
    """Generate a post-hand review of the most recently completed hand."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return jsonify({'error': 'No human player found'}), 400

    # Get the last completed hand from the memory manager
    memory_manager = game_data.get('memory_manager')
    completed_hands = (
        memory_manager.hand_recorder.completed_hands
        if memory_manager and hasattr(memory_manager, 'hand_recorder')
        else []
    )

    if not completed_hands:
        return jsonify({'error': 'No completed hands found'}), 404

    body = request.get_json(silent=True) or {}
    request_player_name = body.get('playerName', '')

    hand = completed_hands[-1]

    # Build context and format for LLM
    context = build_hand_context_from_recorded_hand(hand, player_name)
    hand_text = format_hand_context_for_prompt(context, player_name)

    coach = get_or_create_coach(game_data, game_id, player_name=request_player_name or player_name)

    try:
        review = coach.review_hand(hand_text)
    except Exception as e:
        logger.error(f"Coach hand review failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    return jsonify({
        'review': review,
        'hand_number': getattr(hand, 'hand_number', None),
    })
