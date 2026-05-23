"""Debug and diagnostic routes."""

import logging
from flask import Blueprint, jsonify, request, redirect, Response

from ..services import game_state_service
from ..services.elasticity_service import format_elasticity_data
from ..extensions import persistence_db_path
from .. import config
from ..route_utils import register_admin_guard

logger = logging.getLogger(__name__)

debug_bp = Blueprint('debug', __name__)
register_admin_guard(debug_bp)


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
            'phase': state_machine.current_phase.name,
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
    if not game_data or 'ai_controllers' not in game_data:
        return jsonify({'error': 'Game not found or no AI controllers'}), 404

    elasticity_data = format_elasticity_data(game_data['ai_controllers'])

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
        if controller.psychology is None:
            continue
        tilt = controller.psychology.tilt
        tilt_info[player_name] = {
            'tilt_level': round(tilt.tilt_level, 2),
            'tilt_category': tilt.get_tilt_category(),
            'tilt_source': tilt.tilt_source or 'none',
            'nemesis': tilt.nemesis,
            'losing_streak': tilt.losing_streak,
            'recent_losses': tilt.recent_losses[-3:] if tilt.recent_losses else []
        }

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
    if controller.psychology is None:
        return jsonify({'error': f'Player "{player_name}" is a RuleBot with no psychology'}), 400

    data = request.get_json() or {}
    tilt = controller.psychology.tilt

    if 'tilt_level' in data:
        tilt.tilt_level = max(0.0, min(1.0, float(data['tilt_level'])))
    if 'tilt_source' in data:
        tilt.tilt_source = data['tilt_source']
    if 'nemesis' in data:
        tilt.nemesis = data['nemesis']
    if 'losing_streak' in data:
        tilt.losing_streak = int(data['losing_streak'])

    return jsonify({
        'success': True,
        'player': player_name,
        'new_state': {
            'tilt_level': round(tilt.tilt_level, 2),
            'tilt_category': tilt.get_tilt_category(),
            'tilt_source': tilt.tilt_source,
            'nemesis': tilt.nemesis,
            'losing_streak': tilt.losing_streak
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


@debug_bp.route('/api/game/<game_id>/psychology', methods=['GET'])
def get_psychology_debug(game_id):
    """Get unified psychological state for all AI players.

    Returns combined view of:
    - Elastic personality (current traits, anchor values, pressure levels)
    - Emotional state (valence, arousal, control, focus, narrative)
    - Tilt state (level, source, nemesis, streaks)
    """
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'error': 'Game not found'}), 404

    ai_controllers = game_data.get('ai_controllers', {})
    if not ai_controllers:
        return jsonify({'error': 'No AI controllers found'}), 404

    psychology_data = {}

    for player_name, controller in ai_controllers.items():
        if not hasattr(controller, 'psychology') or not controller.psychology:
            continue

        psych = controller.psychology

        # Build comprehensive psychology view (v2.1 format)
        player_data = {
            'player_name': player_name,
            'hand_count': psych.hand_count,
            'last_updated': psych.last_updated,

            # v2.1: Quadrant-based emotional state
            'quadrant': psych.quadrant.value if hasattr(psych, 'quadrant') else None,
            'display_emotion': psych.get_display_emotion(),
            'mood': psych.mood,

            # v2.1: Dynamic axes
            'axes': {
                'confidence': round(psych.confidence, 2),
                'composure': round(psych.composure, 2),
                'energy': round(psych.energy, 2) if hasattr(psych, 'energy') else round(psych.table_talk, 2),
            },

            # v2.1: Derived values
            'derived': {
                'effective_aggression': round(psych.effective_aggression, 2) if hasattr(psych, 'effective_aggression') else round(psych.aggression, 2),
                'effective_looseness': round(psych.effective_looseness, 2) if hasattr(psych, 'effective_looseness') else round(1.0 - psych.tightness, 2),
            },

            # v2.1: Static anchors (if available)
            'anchors': psych.anchors.to_dict() if hasattr(psych, 'anchors') else None,

            # Backward compat: tilt indicators
            'tilt_level': round(psych.tilt_level, 2),
            'tilt_category': psych.tilt_category,
            'is_tilted': psych.is_tilted,

            # Backward compat: Current trait values
            'traits': {k: round(v, 2) for k, v in psych.traits.items()},

            # Emotional narrative (LLM-generated)
            'emotional': None,

            # Composure/tilt tracking
            'composure_state': None,
        }

        # Add emotional narrative details
        if psych.emotional:
            emo = psych.emotional
            player_data['emotional'] = {
                'narrative': emo.narrative,
                'inner_voice': emo.inner_voice,
                # Legacy 4D model (deprecated)
                'valence': round(emo.valence, 2),
                'arousal': round(emo.arousal, 2),
                'control': round(emo.control, 2),
                'focus': round(emo.focus, 2),
            }

        # Add composure state details
        if psych.composure_state:
            cs = psych.composure_state
            player_data['composure_state'] = {
                'pressure_source': cs.pressure_source,
                'nemesis': cs.nemesis,
                'losing_streak': cs.losing_streak,
                'recent_losses_count': len(cs.recent_losses) if hasattr(cs, 'recent_losses') else 0,
            }

        psychology_data[player_name] = player_data

    return jsonify({
        'game_id': game_id,
        'player_count': len(psychology_data),
        'players': psychology_data
    })


@debug_bp.route('/api/game/<game_id>/relationships', methods=['GET'])
def get_relationships_debug(game_id):
    """Dump the full relationship-state view for a game.

    Shows the data the AI bots see (or would see if `relationship_context`
    were on): for every `(observer, opponent)` pair the memory layer
    knows about, return the projected heat / respect / likability, the
    bucket label (rival / friendly / neutral), and the most-recent
    memorable hands. Includes neutral pairs — debug view wants the full
    state, not the filtered view the prompt-side formatter shows.

    Two paths:
      1. **In-memory**: when the game is still active in this Flask
         process, walk the in-memory `OpponentModelManager` (most
         up-to-date — includes unsaved memorable hands).
      2. **DB fallback**: when the game's been evicted (common for
         cash-mode sessions returned to between visits), reconstruct
         the pair list from `opponent_models WHERE game_id = ?` plus
         the persistent `relationship_states` and `memorable_hands`
         tables. `relationship_states` is cross-session-persistent so
         axis values survive evictions intact.

    The DB fallback returns the same JSON shape as the in-memory path
    so the frontend doesn't need to care which one served the response.
    """
    from datetime import datetime
    from poker.memory.relationship_prompt import _classify
    from ..extensions import game_repo, relationship_repo

    now = datetime.utcnow()
    game_data = game_state_service.get_game(game_id)

    if game_data:
        memory_manager = game_data.get('memory_manager')
        if memory_manager is not None:
            opp_manager = memory_manager.get_opponent_model_manager()
            if opp_manager is not None and opp_manager.has_relationship_repo:
                return jsonify(_build_relationships_response_from_memory(
                    game_id, opp_manager, now,
                ))

    # Fallback: read everything from the DB. `relationship_states`
    # rows are cross-game (keyed on personality_id pairs), so they
    # survive game eviction; `opponent_models` rows scoped to the
    # game tell us which pairs to surface. `memorable_hands` is
    # already attached to the loaded model dict via game_repo.
    if game_repo is None or relationship_repo is None:
        return jsonify({'error': 'Repositories not initialized'}), 500

    models_dict = game_repo.load_opponent_models(game_id)
    if not models_dict:
        return jsonify({
            'error': (
                f'No relationship data for game {game_id} — '
                'no opponent_models rows in DB.'
            ),
        }), 404

    return jsonify(_build_relationships_response_from_db(
        game_id, models_dict, relationship_repo, now,
    ))


def _build_relationships_response_from_memory(game_id, opp_manager, now):
    """Walk the in-memory OpponentModelManager and serialize pairs."""
    from poker.memory.relationship_prompt import _classify

    repo = opp_manager._relationship_repo
    pairs = []
    for observer_name, opp_map in opp_manager.models.items():
        observer_id = opp_manager.resolve_player_id(observer_name)
        for opponent_name, model in opp_map.items():
            opponent_id = opp_manager.resolve_player_id(opponent_name)
            if not observer_id or not opponent_id or observer_id == opponent_id:
                continue

            state = repo.load_relationship_state(
                observer_id, opponent_id, now=now,
            )
            if state is None:
                pairs.append(_neutral_pair_payload(
                    observer_name, opponent_name, observer_id, opponent_id,
                ))
                continue

            label = _classify(state)
            memorable = sorted(
                model.memorable_hands,
                key=lambda h: h.timestamp,
                reverse=True,
            )[:5] if model and model.memorable_hands else []

            pairs.append({
                'observer': observer_name,
                'opponent': opponent_name,
                'observer_id': observer_id,
                'opponent_id': opponent_id,
                'heat': round(state.heat, 4),
                'respect': round(state.respect, 4),
                'likability': round(state.likability, 4),
                'label': label,
                'last_seen': state.last_seen.isoformat() if state.last_seen else None,
                'memorable_hands': [
                    {
                        'hand_id': h.hand_id,
                        'event': h.event.value,
                        'impact_score': round(h.impact_score, 3),
                        'narrative': h.narrative,
                        'timestamp': h.timestamp.isoformat(),
                    }
                    for h in memorable
                ],
            })

    return {
        'game_id': game_id,
        'pair_count': len(pairs),
        'now': now.isoformat(),
        'source': 'memory',
        'pairs': pairs,
    }


def _build_relationships_response_from_db(game_id, models_dict, rel_repo, now):
    """Reconstruct the pair list from the DB.

    `models_dict` is the dict returned by `game_repo.load_opponent_models`:
    nested observer_name → opponent_name → entry dict with
    observer_id / opponent_id / memorable_hands. The dict also carries
    a `__name_to_id__` sidecar entry (a flat name→id registry, mirror
    of `OpponentModelManager.to_dict`) which we use as a fallback id
    resolver for rows that pre-date the v86 id columns.
    """
    from poker.memory.relationship_prompt import _classify

    name_to_id = models_dict.get('__name_to_id__') or {}
    pairs = []
    for observer_name, opp_map in models_dict.items():
        if observer_name == '__name_to_id__':
            # Sidecar entry — not a real observer.
            continue
        for opponent_name, entry in opp_map.items():
            observer_id = (
                entry.get('observer_id')
                or name_to_id.get(observer_name)
                or observer_name
            )
            opponent_id = (
                entry.get('opponent_id')
                or name_to_id.get(opponent_name)
                or opponent_name
            )
            if not observer_id or not opponent_id or observer_id == opponent_id:
                continue

            state = rel_repo.load_relationship_state(
                observer_id, opponent_id, now=now,
            )
            if state is None:
                pairs.append(_neutral_pair_payload(
                    observer_name, opponent_name, observer_id, opponent_id,
                ))
                continue

            label = _classify(state)
            raw_hands = entry.get('memorable_hands') or []
            # DB rows store memory_type, not RelationshipEvent — pass
            # through verbatim so the frontend can render the string.
            # Sort by timestamp descending (DB returns insert order).
            sorted_hands = sorted(
                raw_hands,
                key=lambda h: h.get('timestamp') or '',
                reverse=True,
            )[:5]

            pairs.append({
                'observer': observer_name,
                'opponent': opponent_name,
                'observer_id': observer_id,
                'opponent_id': opponent_id,
                'heat': round(state.heat, 4),
                'respect': round(state.respect, 4),
                'likability': round(state.likability, 4),
                'label': label,
                'last_seen': state.last_seen.isoformat() if state.last_seen else None,
                'memorable_hands': [
                    {
                        'hand_id': h.get('hand_id'),
                        'event': h.get('memory_type'),
                        'impact_score': round(h.get('impact_score') or 0.0, 3),
                        'narrative': h.get('narrative') or '',
                        'timestamp': h.get('timestamp') or '',
                    }
                    for h in sorted_hands
                ],
            })

    return {
        'game_id': game_id,
        'pair_count': len(pairs),
        'now': now.isoformat(),
        'source': 'db',
        'pairs': pairs,
    }


def _neutral_pair_payload(observer_name, opponent_name, observer_id, opponent_id):
    """Default-state pair payload — no row yet in relationship_states."""
    return {
        'observer': observer_name,
        'opponent': opponent_name,
        'observer_id': observer_id,
        'opponent_id': opponent_id,
        'heat': 0.0,
        'respect': 0.5,
        'likability': 0.5,
        'label': None,
        'last_seen': None,
        'memorable_hands': [],
    }


@debug_bp.route('/api/game/<game_id>/trajectory-viewer', methods=['GET'])
def game_trajectory_viewer(game_id):
    """Generate and serve interactive psychology trajectory viewer for any game."""
    try:
        from experiments.generate_trajectory_viewer import extract_data_for_game, generate_html
        data = extract_data_for_game(persistence_db_path, game_id)
        if not data:
            return jsonify({'error': f'No psychology data for game {game_id}'}), 404
        html = generate_html(data)
        return Response(html, mimetype='text/html')
    except Exception as e:
        logger.error(f"Error generating trajectory viewer for game {game_id}: {e}")
        return jsonify({'error': str(e)}), 500
