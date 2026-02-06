"""Coach routes — REST endpoints for the poker coaching feature."""

import logging
import os
from typing import Optional

from flask import Blueprint, jsonify, request

from ..extensions import limiter, game_repo, coach_repo, auth_manager
from ..services import game_state_service
from ..services.coach_engine import compute_coaching_data_with_progression
from ..services.coach_assistant import get_or_create_coach_with_mode
from ..services.coach_progression import CoachProgressionService
from .stats_routes import build_hand_context_from_recorded_hand, format_hand_context_for_prompt
from poker.authorization import require_permission
from ..services.skill_definitions import ALL_SKILLS, ALL_GATES

logger = logging.getLogger(__name__)

coach_bp = Blueprint('coach', __name__)

# RBAC decorator — requires 'can_access_coach' permission (user + admin groups)
_coach_required = require_permission('can_access_coach')


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
@_coach_required
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
        game_data=game_data, coach_repo=coach_repo,
    )
    if data is None:
        return jsonify({'error': 'Could not compute stats'}), 500

    # Include any pending feedback prompt from session memory
    session_memory = game_data.get('coach_session_memory')
    if session_memory:
        feedback_prompt = session_memory.get_feedback_prompt()
        if feedback_prompt:
            data['feedback_prompt'] = feedback_prompt

    return jsonify(data)


@coach_bp.route('/api/coach/<game_id>/ask', methods=['POST'])
@limiter.limit("10/minute")
@_coach_required
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
        game_data=game_data, coach_repo=coach_repo,
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
            result = coach.get_proactive_tip(stats or {})
        else:
            result = coach.ask(question, stats or {})
    except json.JSONDecodeError as e:
        logger.error(f"Coach response parse failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach response error'}), 500
    except TimeoutError as e:
        logger.error(f"Coach request timed out: {e}", exc_info=True)
        return jsonify({'error': 'Coach is taking too long, please try again'}), 504
    except Exception as e:
        logger.error(f"Coach ask failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    # Extract structured response fields
    answer = result.get('advice', '')
    coach_action = result.get('action')
    coach_raise_to = result.get('raise_to')

    # Check environment variable for highlight source
    highlight_source = os.getenv('COACH_HIGHLIGHT_SOURCE', 'coach')

    # When source is 'coach' and coach provided an action, use it for highlighting
    if highlight_source == 'coach' and coach_action and stats:
        stats['recommendation'] = coach_action
        stats['raise_to'] = coach_raise_to

    return jsonify({
        'answer': answer,
        'coach_action': coach_action,
        'coach_raise_to': coach_raise_to,
        'stats': stats,
    })


@coach_bp.route('/api/coach/<game_id>/config', methods=['GET'])
@limiter.limit("30/minute")
@_coach_required
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
@_coach_required
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
@_coach_required
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
    explanation = body.get('explanation', '').strip()

    hand = completed_hands[-1]

    # Build context and format for LLM
    context = build_hand_context_from_recorded_hand(hand, player_name)
    hand_text = format_hand_context_for_prompt(context, player_name)

    # Append skill evaluations from SessionMemory (if available)
    session_memory = game_data.get('coach_session_memory')
    hand_number = getattr(hand, 'hand_number', None)
    if session_memory and hand_number is not None:
        evaluations = session_memory.get_hand_evaluations(hand_number)
        if evaluations:
            skill_eval_text = "\n\nSKILL EVALUATIONS FOR THIS HAND:\n"
            for ev in evaluations:
                skill_eval_text += f"- {ev.skill_id}: {ev.evaluation} — {ev.reasoning}\n"
            hand_text += skill_eval_text

    # Append player explanation
    if explanation:
        hand_text += f"\n\nPlayer's explanation: {explanation}"

    # Use mode-aware coach with REVIEW mode
    coach = get_or_create_coach_with_mode(
        game_data, game_id,
        player_name=request_player_name or player_name,
        mode='review',
        skill_context='',
    )

    try:
        review = coach.review_hand(hand_text)
    except Exception as e:
        logger.error(f"Coach hand review failed: {e}", exc_info=True)
        return jsonify({'error': 'Coach unavailable'}), 503

    return jsonify({
        'review': review,
        'hand_number': hand_number,
    })


@coach_bp.route('/api/coach/<game_id>/progression')
@limiter.limit("30/minute")
@_coach_required
def coach_progression(game_id: str):
    """Return the player's skill progression state."""
    user_id = _get_current_user_id()

    try:
        service = CoachProgressionService(coach_repo)
        state = service.get_or_initialize_player(user_id)

        return jsonify({
            'skill_states': {
                sid: {
                    'state': ss.state.value,
                    'total_opportunities': ss.total_opportunities,
                    'total_correct': ss.total_correct,
                    'window_accuracy': round(ss.window_accuracy, 2),
                    'streak_correct': ss.streak_correct,
                    'name': ALL_SKILLS[sid].name if sid in ALL_SKILLS else sid,
                    'description': ALL_SKILLS[sid].description if sid in ALL_SKILLS else '',
                    'gate': ALL_SKILLS[sid].gate if sid in ALL_SKILLS else 0,
                }
                for sid, ss in state['skill_states'].items()
            },
            'gate_progress': {
                str(gn): {
                    'unlocked': state['gate_progress'][gn].unlocked
                    if gn in state['gate_progress'] else False,
                    'unlocked_at': state['gate_progress'][gn].unlocked_at
                    if gn in state['gate_progress'] else None,
                    'name': gate_def.name,
                    'description': gate_def.description,
                }
                for gn, gate_def in ALL_GATES.items()
            },
            'profile': state['profile'],
        })
    except Exception as e:
        logger.error(f"Coach progression failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load progression'}), 500


# Canned responses for preset feedback options
_FEEDBACK_RESPONSES = {
    'read': "Trust your instincts - just track if your reads are accurate over time.",
    'unsure': "No worries, position awareness takes practice. Keep at it!",
}


@coach_bp.route('/api/coach/<game_id>/feedback', methods=['POST'])
@limiter.limit("30/minute")
@_coach_required
def coach_feedback(game_id: str):
    """Record player feedback on a coach evaluation.

    When a player folds a hand that was in their range, the coach may ask why.
    This endpoint stores the player's explanation for learning and adjustment.
    Returns a coach response (canned for presets, LLM for custom).
    """
    user_id = _get_current_user_id()

    body = request.get_json(silent=True) or {}
    hand = body.get('hand', '')
    position = body.get('position', '')
    action = body.get('action', 'fold')
    reason = body.get('reason', '')
    hand_number = body.get('hand_number')

    if not reason:
        return jsonify({'error': 'No reason provided'}), 400
    if len(reason) > 500:
        return jsonify({'error': 'Reason too long (max 500 characters)'}), 400
    if len(hand) > 10:
        return jsonify({'error': 'Invalid hand value'}), 400
    if len(position) > 30:
        return jsonify({'error': 'Invalid position value'}), 400
    if hand_number is not None and not isinstance(hand_number, int):
        try:
            hand_number = int(hand_number)
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid hand_number'}), 400

    try:
        game_data = game_state_service.get_game(game_id)

        # Read context BEFORE recording feedback (which clears pending_feedback_prompt)
        hand_context = None
        if game_data:
            session_memory = game_data.get('coach_session_memory')
            if session_memory:
                feedback_prompt = session_memory.get_feedback_prompt()
                if feedback_prompt:
                    hand_context = feedback_prompt.get('context')

        # Store feedback in session memory
        feedback_stored = False
        if game_data:
            session_memory = game_data.get('coach_session_memory')
            if session_memory:
                session_memory.record_player_feedback(
                    hand_number=hand_number,
                    feedback={
                        'hand': hand,
                        'position': position,
                        'action': action,
                        'reason': reason,
                    }
                )
                feedback_stored = True

        logger.info(
            f"Coach feedback recorded: user={user_id}, hand={hand}, "
            f"position={position}, action={action}, reason={reason}"
        )

        # Generate response - canned for presets, LLM for custom
        if reason in _FEEDBACK_RESPONSES:
            response = _FEEDBACK_RESPONSES[reason]
        else:
            # Custom reason - generate LLM response with hand context
            response = _generate_feedback_response(hand, position, reason, hand_context)

        return jsonify({'status': 'ok', 'response': response, 'feedback_stored': feedback_stored})
    except Exception as e:
        logger.error(f"Coach feedback failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not record feedback'}), 500


def _generate_feedback_response(hand: str, position: str, reason: str, context: dict = None) -> str:
    """Generate a brief coach response to custom feedback using LLM."""
    from core.llm import LLMClient, CallType

    # Sanitize inputs: strip control characters and enforce length limits
    reason = ''.join(ch for ch in reason if ch.isprintable() or ch in ('\n', ' '))[:500]
    hand = ''.join(ch for ch in hand if ch.isprintable())[:10]
    position = ''.join(ch for ch in position if ch.isprintable())[:30]

    try:
        client = LLMClient()

        # Build context summary
        context_str = ""
        if context:
            parts = []
            if context.get('phase'):
                parts.append(f"Phase: {context['phase']}")
            if context.get('pot_total'):
                parts.append(f"Pot: ${context['pot_total']}")
            if context.get('cost_to_call'):
                parts.append(f"Cost to call: ${context['cost_to_call']}")
            if context.get('equity') is not None:
                parts.append(f"Equity: {round(context['equity'] * 100)}%")
            if context.get('hand_strength'):
                parts.append(f"Hand: {context['hand_strength']}")
            if context.get('opponent_count'):
                parts.append(f"Opponents in hand: {context['opponent_count']}")
            if context.get('hand_actions'):
                actions = context['hand_actions'][-3:]  # Last 3 actions
                action_strs = [f"{a.get('player', '?')}: {a.get('action', '?')}" for a in actions]
                if action_strs:
                    parts.append(f"Recent actions: {', '.join(action_strs)}")
            context_str = "\n".join(parts)

        prompt = f"""You are a friendly poker coach. A student folded {hand or 'a hand'} from {position or 'their position'}, even though it was in their recommended opening range.

Hand situation:
{context_str if context_str else 'No additional context available.'}

Their reason for folding: "{reason}"

Write a brief 1-2 sentence response. Consider whether their reasoning makes sense given the situation. Be supportive but honest - if their fold was actually reasonable despite being in range, acknowledge that. If they might be playing too tight, gently suggest being more aggressive in that spot."""

        logger.info(f"Generating feedback response: hand={hand}, position={position}, reason={reason}, context={context}")
        response = client.complete(
            messages=[{"role": "user", "content": prompt}],
            call_type=CallType.COACHING,
            max_tokens=150,
        )
        result = response.content.strip().strip('"') if response.content else ""
        logger.info(f"Feedback response generated: {result}")
        if not result:
            return "Thanks for sharing - that context helps!"
        return result
    except Exception as e:
        logger.warning(f"Failed to generate feedback response: {e}")
        return "Got it, thanks for sharing your thinking!"


@coach_bp.route('/api/coach/<game_id>/feedback/dismiss', methods=['POST'])
@limiter.limit("30/minute")
@_coach_required
def coach_feedback_dismiss(game_id: str):
    """Dismiss the feedback prompt without recording a response."""
    try:
        game_data = game_state_service.get_game(game_id)
        if game_data:
            session_memory = game_data.get('coach_session_memory')
            if session_memory:
                session_memory.clear_feedback_prompt()

        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Coach feedback dismiss failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not dismiss feedback'}), 500


@coach_bp.route('/api/coach/<game_id>/onboarding', methods=['POST'])
@limiter.limit("5/minute")
@_coach_required
def coach_onboarding(game_id: str):
    """Initialize or update the player's coaching profile.

    If the player has no existing profile, initializes from scratch.
    If the player already has a profile (with accumulated stats),
    only updates the level and unlocks new gates without wiping stats.
    """
    user_id = _get_current_user_id()

    body = request.get_json(silent=True) or {}
    level = body.get('level', 'beginner')
    if level not in ('beginner', 'intermediate', 'experienced'):
        return jsonify({'error': 'Invalid level'}), 400

    try:
        service = CoachProgressionService(coach_repo)

        # Check if player already has a profile with accumulated stats
        existing_state = service.get_player_state(user_id)
        if existing_state['profile']:
            # Player exists - update level without wiping stats
            state = service.update_player_level(user_id, level=level)
            logger.info(f"Updated existing player {user_id} to level {level}")
        else:
            # New player - full initialization
            state = service.initialize_player(user_id, level=level)
            logger.info(f"Initialized new player {user_id} at level {level}")

        return jsonify({
            'status': 'ok',
            'profile': state['profile'],
        })
    except Exception as e:
        logger.error(f"Coach onboarding failed: {e}", exc_info=True)
        return jsonify({'error': 'Onboarding failed'}), 500


# --- Admin-only metrics endpoints ---

_admin_required = require_permission('can_access_admin_tools')


@coach_bp.route('/api/coach/metrics/overview')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_overview():
    """Aggregate overview of coach progression usage."""
    try:
        stats = coach_repo.get_profile_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Coach metrics overview failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load metrics'}), 500


@coach_bp.route('/api/coach/metrics/skills')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_skills():
    """Per-skill distribution and advancement stats."""
    try:
        stats = coach_repo.get_skill_distribution()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Coach metrics skills failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load skill metrics'}), 500


@coach_bp.route('/api/coach/metrics/advancement')
@limiter.limit("30/minute")
@_admin_required
def coach_metrics_advancement():
    """Skill advancement timing and difficulty analysis."""
    try:
        stats = coach_repo.get_skill_advancement_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Coach metrics advancement failed: {e}", exc_info=True)
        return jsonify({'error': 'Could not load advancement metrics'}), 500
