"""Prompt debugging routes for AI decision analysis."""

import json
import logging
import uuid
from typing import Dict
from flask import Blueprint, jsonify, request

from core.llm import LLMClient, CallType, Assistant
from ..extensions import persistence
from .. import config

logger = logging.getLogger(__name__)

prompt_debug_bp = Blueprint('prompt_debug', __name__)

# In-memory session storage for interrogation conversations
# Key: session_id, Value: Assistant instance
_interrogation_sessions: Dict[str, Assistant] = {}

# Context appended to system prompt for interrogation mode
INTERROGATION_CONTEXT = """

---
[INTERROGATION MODE]
You are now being asked follow-up questions about the decision you just made.
Please explain your reasoning clearly and honestly. If asked about specific
aspects of your decision (pot odds, hand strength, opponent reads, etc.),
provide detailed explanations. Stay in character and respond as you would
during the game, but focus on explaining your thought process.
---
"""


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


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:capture_id>/interrogate', methods=['POST'])
def interrogate_capture(capture_id):
    """Start or continue an interrogation conversation with the AI.

    Allows users to ask follow-up questions to understand why the AI
    made a specific decision. The AI responds in the same persona/context
    as when it made the original decision.

    Request body:
        message: User's question to ask the AI (required)
        session_id: Session ID for continuing a conversation (optional)
        reset: Boolean to reset the session and start fresh (optional)
        model: Model override (optional, defaults to original capture's model)

    Response:
        success: Boolean
        response: AI's response text
        session_id: Session ID for continuing the conversation
        messages_count: Number of messages in conversation
        model_used: Model that was used
        latency_ms: Response latency
    """
    capture = persistence.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    data = request.get_json() or {}

    message = data.get('message')
    if not message:
        return jsonify({'success': False, 'error': 'Message is required'}), 400

    session_id = data.get('session_id')
    reset = data.get('reset', False)
    model = data.get('model', capture.get('model', 'gpt-4o-mini'))

    try:
        # Get or create session
        if reset and session_id and session_id in _interrogation_sessions:
            del _interrogation_sessions[session_id]
            session_id = None

        if session_id and session_id in _interrogation_sessions:
            # Continue existing conversation
            assistant = _interrogation_sessions[session_id]
        else:
            # Create new session
            session_id = str(uuid.uuid4())

            # NEW APPROACH: Use a debug-mode system prompt, put original context in messages
            debug_system_prompt = f"""You are a debugging assistant helping analyze an AI poker player's decision-making.

You have access to the FULL CONTEXT of what the AI player was thinking when it made a decision.
Your job is to explain the AI's reasoning in plain conversational English.

IMPORTANT:
- Do NOT respond in JSON format
- Do NOT roleplay as the poker character
- DO explain what the character was thinking and why it made the decision it did
- Speak as a helpful analyst, not as the character itself

The AI player's original personality/instructions were:
---
{capture['system_prompt']}
---

Now help the administrator understand why this AI made the decision it did."""

            # Create assistant with debug system prompt
            assistant = Assistant(
                system_prompt=debug_system_prompt,
                model=model,
                call_type=CallType.DEBUG_INTERROGATE,
                game_id=capture.get('game_id'),
                player_name=capture.get('player_name'),
            )

            # Load original conversation history as context
            conversation_history = capture.get('conversation_history') or []
            if conversation_history:
                history_summary = "\n".join([f"[{msg.get('role', 'user').upper()}]: {msg.get('content', '')}" for msg in conversation_history])
                assistant.memory.add('user', f"Here is the conversation history leading up to the decision:\n\n{history_summary}")
                assistant.memory.add('assistant', "I've reviewed the conversation history. I can see the context of the hand.")

            # Add the game state and decision as context
            assistant.memory.add('user', f"Here is the game state that was presented to the AI:\n\n{capture['user_message']}")
            assistant.memory.add('assistant', f"The AI responded with:\n\n{capture['ai_response']}")

            # Jailbreak messages to establish the breakpoint
            assistant.memory.add('user', '*** DEBUG MODE ACTIVATED - The game is paused. You are now speaking with the administrator. Please explain your reasoning in plain English. ***')
            assistant.memory.add('assistant', 'Debug mode acknowledged. I can now speak freely as an analyst and explain the reasoning behind that decision. What would you like to know?')

            # Store session
            _interrogation_sessions[session_id] = assistant

        # Send the user's question
        response = assistant.chat_full(
            message,
            json_format=False,
            call_type=CallType.DEBUG_INTERROGATE,
            game_id=capture.get('game_id'),
            player_name=capture.get('player_name'),
        )

        return jsonify({
            'success': True,
            'response': response.content,
            'session_id': session_id,
            'messages_count': len(assistant.memory),
            'model_used': model,
            'latency_ms': response.latency_ms if hasattr(response, 'latency_ms') else None,
        })

    except Exception as e:
        logger.error(f"Interrogation failed: {e}")
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
