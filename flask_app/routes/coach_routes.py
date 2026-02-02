"""Coach routes — REST endpoints for the poker coaching feature."""

import logging
from typing import Optional

from flask import Blueprint, jsonify, request

from ..extensions import limiter, game_repo
from ..services import game_state_service
from ..services.coach_engine import compute_coaching_data, compute_coaching_data_with_progression
from ..services.coach_assistant import get_or_create_coach, get_or_create_coach_with_mode
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


def _get_current_user_id() -> str:
    """Get the current authenticated user's ID, or empty string."""
    if not auth_manager:
        return ''
    user = auth_manager.get_current_user()
    if not user:
        return ''
    if isinstance(user, dict):
        return user.get('id', '')
    return getattr(user, 'id', '')


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

    user_id = _get_current_user_id()
    data = compute_coaching_data_with_progression(
        game_id, player_name, user_id=user_id,
        game_data=game_data, persistence=persistence,
    )
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

    # Compute current stats with progression context
    user_id = _get_current_user_id()
    stats = compute_coaching_data_with_progression(
        game_id, player_name, user_id=user_id,
        game_data=game_data, persistence=persistence,
    )

    # Use mode-aware coach if progression data is available
    progression = (stats or {}).get('progression', {})
    coaching_mode = progression.get('coaching_mode', '')
    coaching_prompt = progression.get('coaching_prompt', '')

    coach = get_or_create_coach_with_mode(
        game_data, game_id,
        player_name=request_player_name or player_name,
        mode=coaching_mode,
        skill_context=coaching_prompt,
    )

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

    mode = game_repo.load_coach_mode(game_id)
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
    game_repo.save_coach_mode(game_id, mode)
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


@coach_bp.route('/api/coach/<game_id>/progression')
@limiter.limit("30/minute")
def coach_progression(game_id: str):
    """Return the player's skill progression state."""
    user_id = _get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401

    try:
        from ..services.coach_progression import CoachProgressionService
        service = CoachProgressionService(persistence)
        state = service.get_or_initialize_player(user_id)

        return jsonify({
            'skill_states': {
                sid: {
                    'state': ss.state.value,
                    'total_opportunities': ss.total_opportunities,
                    'total_correct': ss.total_correct,
                    'window_accuracy': round(ss.window_accuracy, 2),
                    'streak_correct': ss.streak_correct,
                }
                for sid, ss in state['skill_states'].items()
            },
            'gate_progress': {
                str(gn): {
                    'unlocked': gp.unlocked,
                    'unlocked_at': gp.unlocked_at,
                }
                for gn, gp in state['gate_progress'].items()
            },
            'profile': state['profile'],
        })
    except Exception as e:
        logger.error(f"Coach progression failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load progression'}), 500


@coach_bp.route('/api/coach/<game_id>/onboarding', methods=['POST'])
@limiter.limit("5/minute")
def coach_onboarding(game_id: str):
    """Initialize or update the player's coaching profile."""
    user_id = _get_current_user_id()
    if not user_id:
        return jsonify({'error': 'Authentication required'}), 401

    body = request.get_json(silent=True) or {}
    level = body.get('level', 'beginner')
    if level not in ('beginner', 'intermediate', 'experienced'):
        return jsonify({'error': 'Invalid level'}), 400

    try:
        from ..services.coach_progression import CoachProgressionService
        service = CoachProgressionService(persistence)
        state = service.initialize_player(user_id, level=level)

        return jsonify({
            'status': 'ok',
            'profile': state['profile'],
        })
    except Exception as e:
        logger.error(f"Coach onboarding failed: {e}", exc_info=True)
        return jsonify({'error': 'Onboarding failed'}), 500
