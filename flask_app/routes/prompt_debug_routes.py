"""Prompt debugging routes for AI decision analysis."""

import json
import logging
from flask import Blueprint, jsonify, request

from core.llm import LLMClient, CallType
from ..extensions import persistence
from .. import config

logger = logging.getLogger(__name__)

prompt_debug_bp = Blueprint('prompt_debug', __name__)


@prompt_debug_bp.route('/api/prompt-debug/captures', methods=['GET'])
def list_captures():
    """List prompt captures with optional filtering.

    Query params:
        game_id: Filter by game
        player_name: Filter by AI player
        action: Filter by action (fold, check, call, raise)
        phase: Filter by phase (PRE_FLOP, FLOP, TURN, RIVER)
        min_pot_odds: Filter by minimum pot odds
        max_pot_odds: Filter by maximum pot odds
        tags: Comma-separated tags to filter by
        limit: Max results (default 50)
        offset: Pagination offset (default 0)
    """
    filters = {
        'game_id': request.args.get('game_id'),
        'player_name': request.args.get('player_name'),
        'action': request.args.get('action'),
        'phase': request.args.get('phase'),
        'min_pot_odds': float(request.args.get('min_pot_odds')) if request.args.get('min_pot_odds') else None,
        'max_pot_odds': float(request.args.get('max_pot_odds')) if request.args.get('max_pot_odds') else None,
        'tags': request.args.get('tags', '').split(',') if request.args.get('tags') else None,
        'limit': int(request.args.get('limit', 50)),
        'offset': int(request.args.get('offset', 0)),
    }

    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}

    result = persistence.list_prompt_captures(**filters)

    # Also get stats
    stats = persistence.get_prompt_capture_stats(filters.get('game_id'))

    return jsonify({
        'success': True,
        'captures': result['captures'],
        'total': result['total'],
        'stats': stats
    })


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:capture_id>', methods=['GET'])
def get_capture(capture_id):
    """Get a single prompt capture with full details and linked decision analysis."""
    capture = persistence.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    # Get linked decision analysis if it exists
    decision_analysis = persistence.get_decision_analysis_by_capture(capture_id)

    return jsonify({
        'success': True,
        'capture': capture,
        'decision_analysis': decision_analysis
    })


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:capture_id>/replay', methods=['POST'])
def replay_capture(capture_id):
    """Replay a prompt capture with optional modifications.

    Request body:
        system_prompt: Modified system prompt (optional)
        user_message: Modified user message (optional)
        conversation_history: Modified conversation history (optional, list of {role, content})
        use_history: Whether to include conversation history (default: True)
        model: Model to use (optional, defaults to original)
    """
    capture = persistence.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    data = request.get_json() or {}

    # Use modified prompts or originals
    system_prompt = data.get('system_prompt', capture['system_prompt'])
    user_message = data.get('user_message', capture['user_message'])
    model = data.get('model', capture.get('model', 'gpt-4o-mini'))

    # Handle conversation history
    use_history = data.get('use_history', True)
    conversation_history = data.get('conversation_history', capture.get('conversation_history', []))

    try:
        # Create LLM client and replay the prompt
        client = LLMClient(model=model)

        # Build messages array
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Include conversation history if enabled
        if use_history and conversation_history:
            for msg in conversation_history:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        # Add the current user message
        messages.append({"role": "user", "content": user_message})

        # Only use json_format if the messages mention "json" (OpenAI requirement)
        combined_text = (system_prompt or '') + (user_message or '')
        use_json_format = 'json' in combined_text.lower()

        response = client.complete(
            messages=messages,
            json_format=use_json_format,
            call_type=CallType.DEBUG_REPLAY,
        )

        return jsonify({
            'success': True,
            'original_response': capture['ai_response'],
            'new_response': response.content,
            'model_used': model,
            'latency_ms': response.latency_ms if hasattr(response, 'latency_ms') else None,
            'messages_count': len(messages),
            'used_history': use_history and bool(conversation_history)
        })

    except Exception as e:
        logger.error(f"Replay failed: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:capture_id>/tags', methods=['POST'])
def update_capture_tags(capture_id):
    """Update tags and notes for a prompt capture.

    Request body:
        tags: List of tags
        notes: Optional notes string
    """
    capture = persistence.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    data = request.get_json() or {}

    tags = data.get('tags', [])
    notes = data.get('notes')

    success = persistence.update_prompt_capture_tags(capture_id, tags, notes)

    return jsonify({
        'success': success
    })


@prompt_debug_bp.route('/api/prompt-debug/stats', methods=['GET'])
def get_capture_stats():
    """Get aggregate statistics for prompt captures."""
    game_id = request.args.get('game_id')

    stats = persistence.get_prompt_capture_stats(game_id)

    return jsonify({
        'success': True,
        'stats': stats
    })


@prompt_debug_bp.route('/api/prompt-debug/game/<game_id>/debug-mode', methods=['POST'])
def toggle_debug_mode(game_id):
    """Toggle debug capture mode for a game.

    Request body:
        enabled: Boolean to enable/disable debug capture
    """
    from ..services import game_state_service

    data = request.get_json() or {}
    enabled = data.get('enabled', True)

    # Check if game exists
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({'success': False, 'error': 'Game not found'}), 404

    # Enable debug capture on all AI controllers
    controllers = game_state_service.get_ai_controllers(game_id)
    updated_count = 0
    for controller in controllers.values():
        if hasattr(controller, 'debug_capture'):
            controller.debug_capture = enabled
            controller._persistence = persistence if enabled else None
            updated_count += 1

    return jsonify({
        'success': True,
        'debug_capture': enabled,
        'game_id': game_id,
        'controllers_updated': updated_count
    })


@prompt_debug_bp.route('/api/prompt-debug/cleanup', methods=['POST'])
def cleanup_captures():
    """Delete old prompt captures.

    Request body:
        game_id: Delete captures for a specific game (optional)
        before_date: Delete captures before this ISO date (optional)
    """
    data = request.get_json() or {}

    game_id = data.get('game_id')
    before_date = data.get('before_date')

    if not game_id and not before_date:
        return jsonify({
            'success': False,
            'error': 'Must specify game_id or before_date'
        }), 400

    deleted = persistence.delete_prompt_captures(game_id, before_date)

    return jsonify({
        'success': True,
        'deleted': deleted
    })


# ========== Decision Analysis Endpoints ==========

@prompt_debug_bp.route('/api/prompt-debug/analysis', methods=['GET'])
def list_decision_analyses():
    """List decision analyses with optional filtering.

    Query params:
        game_id: Filter by game
        player_name: Filter by AI player
        decision_quality: Filter by quality (correct, mistake, unknown)
        min_ev_lost: Filter by minimum EV lost
        limit: Max results (default 50)
        offset: Pagination offset (default 0)
    """
    filters = {
        'game_id': request.args.get('game_id'),
        'player_name': request.args.get('player_name'),
        'decision_quality': request.args.get('decision_quality'),
        'min_ev_lost': float(request.args.get('min_ev_lost')) if request.args.get('min_ev_lost') else None,
        'limit': int(request.args.get('limit', 50)),
        'offset': int(request.args.get('offset', 0)),
    }

    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}

    result = persistence.list_decision_analyses(**filters)

    # Also get stats
    stats = persistence.get_decision_analysis_stats(filters.get('game_id'))

    return jsonify({
        'success': True,
        'analyses': result['analyses'],
        'total': result['total'],
        'stats': stats
    })


@prompt_debug_bp.route('/api/prompt-debug/analysis/<int:analysis_id>', methods=['GET'])
def get_decision_analysis(analysis_id):
    """Get a single decision analysis by ID."""
    analysis = persistence.get_decision_analysis(analysis_id)

    if not analysis:
        return jsonify({'success': False, 'error': 'Analysis not found'}), 404

    return jsonify({
        'success': True,
        'analysis': analysis
    })


@prompt_debug_bp.route('/api/prompt-debug/analysis-stats', methods=['GET'])
def get_analysis_stats():
    """Get aggregate statistics for decision analyses.

    Query params:
        game_id: Filter by game (optional)
    """
    game_id = request.args.get('game_id')

    stats = persistence.get_decision_analysis_stats(game_id)

    return jsonify({
        'success': True,
        'stats': stats
    })


@prompt_debug_bp.route('/api/game/<game_id>/decision-quality', methods=['GET'])
def get_game_decision_quality(game_id):
    """Get decision quality summary for a specific game.

    Returns aggregate stats for AI decision quality in this game.
    """
    stats = persistence.get_decision_analysis_stats(game_id)

    # Calculate quality metrics
    total = stats.get('total', 0)
    mistakes = stats.get('mistakes', 0)
    correct = stats.get('correct', 0)

    quality_rate = (correct / total * 100) if total > 0 else 0

    return jsonify({
        'success': True,
        'game_id': game_id,
        'total_decisions': total,
        'correct': correct,
        'mistakes': mistakes,
        'quality_rate': round(quality_rate, 1),
        'total_ev_lost': round(stats.get('total_ev_lost', 0), 2),
        'avg_equity': round(stats.get('avg_equity', 0) * 100, 1) if stats.get('avg_equity') else None,
        'by_action': stats.get('by_action', {}),
        'by_quality': stats.get('by_quality', {}),
    })
