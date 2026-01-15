"""Debug and diagnostic routes."""

import logging
from flask import Blueprint, jsonify, request, redirect

from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from .. import config

logger = logging.getLogger(__name__)

debug_bp = Blueprint('debug', __name__)


@debug_bp.route('/debug')
def debug_page_redirect():
    """Redirect legacy /debug to /admin/debug."""
    return redirect('/admin/debug', code=301)


@debug_bp.route('/api/game/<game_id>/diagnostic', methods=['GET'])
def api_game_diagnostic(game_id):
    """Get diagnostic information about a game's state."""
    current_game_data = game_state_service.get_game(game_id)

    lock = game_state_service.game_locks.get(game_id)
    lock_held = lock.locked() if lock else False

    diagnostic = {
        'game_id': game_id,
        'in_memory': current_game_data is not None,
        'lock_exists': lock is not None,
        'lock_held': lock_held,
    }

    if current_game_data:
        state_machine = current_game_data['state_machine']
        game_state = state_machine.game_state
        current_player = game_state.current_player

        diagnostic.update({
            'phase': str(state_machine.current_phase).split('.')[-1],
            'awaiting_action': game_state.awaiting_action,
            'current_player': current_player.name,
            'current_player_is_human': current_player.is_human,
            'current_player_is_folded': current_player.is_folded,
            'game_started_flag': current_game_data.get('game_started', False),
            'has_ai_controllers': 'ai_controllers' in current_game_data,
            'player_count': len(game_state.players),
            'pot': game_state.pot,
        })

        is_stuck = (
            game_state.awaiting_action and
            not current_player.is_human and
            not current_player.is_folded
        )
        diagnostic['appears_stuck'] = is_stuck
        if is_stuck:
            diagnostic['stuck_reason'] = 'AI turn pending but no progress'

    return jsonify(diagnostic)


@debug_bp.route('/api/game/<game_id>/elasticity', methods=['GET'])
def get_elasticity_data(game_id):
    """Get current elasticity data for all AI players."""
    game_data = game_state_service.get_game(game_id)
    if not game_data or 'elasticity_manager' not in game_data:
        return jsonify({'error': 'Game not found or elasticity not enabled'}), 404

    elasticity_manager = game_data['elasticity_manager']
    elasticity_data = format_elasticity_data(elasticity_manager)

    return jsonify(elasticity_data)


@debug_bp.route('/api/game/<game_id>/memory-debug', methods=['GET'])
def get_memory_debug(game_id):
    """Get current memory state for debugging."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    if 'memory_manager' not in game_data:
        return jsonify({'error': 'Memory manager not initialized', 'working': False}), 200

    memory_manager = game_data['memory_manager']

    debug_info = {
        'working': True,
        'game_id': memory_manager.game_id,
        'hand_count': memory_manager.hand_count,
        'initialized_players': list(memory_manager.initialized_players),
        'session_memories': {},
        'opponent_models': {},
        'current_hand': None,
        'completed_hands_count': len(memory_manager.hand_recorder.completed_hands)
    }

    for player_name, session in memory_manager.session_memories.items():
        debug_info['session_memories'][player_name] = {
            'hands_played': session.context.hands_played,
            'hands_won': session.context.hands_won,
            'current_streak': session.context.current_streak,
            'streak_count': session.context.streak_count,
            'total_winnings': session.context.total_winnings,
            'hand_memories_count': len(session.hand_memories),
            'context_preview': session.get_context_for_prompt(100)[:200] if session.hand_memories else 'No hands yet'
        }

    all_models = memory_manager.opponent_model_manager.models
    for observer, targets in all_models.items():
        debug_info['opponent_models'][observer] = {}
        for target, model in targets.items():
            debug_info['opponent_models'][observer][target] = {
                'hands_observed': model.tendencies.hands_observed,
                'vpip': round(model.tendencies.vpip, 2),
                'pfr': round(model.tendencies.pfr, 2),
                'aggression_factor': round(model.tendencies.aggression_factor, 2),
                'play_style': model.tendencies.get_play_style_label(),
                'summary': model.get_prompt_summary()
            }

    if memory_manager.hand_recorder.current_hand:
        current = memory_manager.hand_recorder.current_hand
        debug_info['current_hand'] = {
            'hand_number': current.hand_number,
            'actions_recorded': len(current.actions),
            'phase': current.actions[-1].phase if current.actions else 'PRE_FLOP'
        }

    return jsonify(debug_info)


@debug_bp.route('/api/game/<game_id>/tilt-debug', methods=['GET'])
def get_tilt_debug(game_id):
    """Get tilt state for all AI players."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    ai_controllers = game_data.get('ai_controllers', {})
    if not ai_controllers:
        return jsonify({'error': 'No AI controllers found'}), 200

    tilt_info = {}
    for player_name, controller in ai_controllers.items():
        if hasattr(controller, 'tilt_state'):
            state = controller.tilt_state
            tilt_info[player_name] = {
                'tilt_level': round(state.tilt_level, 2),
                'tilt_category': state.get_tilt_category(),
                'tilt_source': state.tilt_source or 'none',
                'nemesis': state.nemesis,
                'losing_streak': state.losing_streak,
                'recent_losses': state.recent_losses[-3:] if state.recent_losses else []
            }
        else:
            tilt_info[player_name] = {'error': 'No tilt state (old controller?)'}

    return jsonify({
        'game_id': game_id,
        'tilt_states': tilt_info
    })


@debug_bp.route('/api/game/<game_id>/tilt-debug/<player_name>', methods=['POST'])
def set_tilt_debug(game_id, player_name):
    """Set tilt state for an AI player - for testing."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    ai_controllers = game_data.get('ai_controllers', {})
    if player_name not in ai_controllers:
        return jsonify({
            'error': f'AI player "{player_name}" not found',
            'available_players': list(ai_controllers.keys())
        }), 404

    controller = ai_controllers[player_name]
    if not hasattr(controller, 'tilt_state'):
        return jsonify({'error': 'Controller has no tilt state'}), 500

    data = request.get_json() or {}
    state = controller.tilt_state

    if 'tilt_level' in data:
        state.tilt_level = max(0.0, min(1.0, float(data['tilt_level'])))
    if 'tilt_source' in data:
        state.tilt_source = data['tilt_source']
    if 'nemesis' in data:
        state.nemesis = data['nemesis']
    if 'losing_streak' in data:
        state.losing_streak = int(data['losing_streak'])

    return jsonify({
        'success': True,
        'player': player_name,
        'new_state': {
            'tilt_level': round(state.tilt_level, 2),
            'tilt_category': state.get_tilt_category(),
            'tilt_source': state.tilt_source,
            'nemesis': state.nemesis,
            'losing_streak': state.losing_streak
        }
    })


@debug_bp.route('/api/game/<game_id>/pressure-stats', methods=['GET'])
def get_pressure_stats(game_id):
    """Get pressure event statistics for the game."""
    game_data = game_state_service.get_game(game_id)
    if not game_data or 'pressure_stats' not in game_data:
        return jsonify({'error': 'Game not found or stats not available'}), 404

    pressure_stats = game_data['pressure_stats']
    return jsonify(pressure_stats.get_session_summary())
