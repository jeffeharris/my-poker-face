"""Prompt debugging routes for AI decision analysis."""

import logging
import uuid
from typing import Dict, Optional

from flask import Blueprint, jsonify, request

from core.llm import Assistant, CallType, LLMClient

from .. import extensions
from ..decision_analysis_serializer import hydrate_decision_analysis
from ..route_utils import register_admin_guard

logger = logging.getLogger(__name__)

prompt_debug_bp = Blueprint('prompt_debug', __name__)
register_admin_guard(prompt_debug_bp)

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
    """List decisions for the Decision Analyzer.

    Spined on player_decision_analysis: one row per decision (LLM, solver,
    RuleBot, or human), addressed by its real positive `id`. The linked
    prompt capture (if any) is LEFT JOINed only for LLM-side display fields.
    Capture-only browse (call_type / error / corrections / labels of
    non-decision captures) lives in the Prompt Playground, not here.

    Query params:
        game_id: Filter by game
        player_name: Filter by player
        action: Filter by action (fold, check, call, raise)
        phase: Filter by phase (PRE_FLOP, FLOP, TURN, RIVER)
        min_pot_odds / max_pot_odds: Filter by derived pot odds
        min_pot_size / max_pot_size: Filter by pot total
        display_emotion: Filter by displayed emotion
        min_tilt_level / max_tilt_level: Filter by tilt level
        decision_quality: Filter by graded quality (correct/mistake/...)
        min_ev_lost: Filter by minimum EV lost
        limit: Max results (default 50)
        offset: Pagination offset (default 0)

    The response key stays `captures` for client compatibility, but each item
    is a decision row keyed by its player_decision_analysis id.
    """

    def _float(name):
        raw = request.args.get(name)
        try:
            return float(raw) if raw else None
        except (ValueError, TypeError):
            return None

    result = extensions.decision_analysis_repo.list_decisions(
        game_id=request.args.get('game_id'),
        player_name=request.args.get('player_name'),
        action=request.args.get('action'),
        phase=request.args.get('phase'),
        min_pot_odds=_float('min_pot_odds'),
        max_pot_odds=_float('max_pot_odds'),
        min_pot_size=_float('min_pot_size'),
        max_pot_size=_float('max_pot_size'),
        display_emotion=request.args.get('display_emotion'),
        min_tilt_level=_float('min_tilt_level'),
        max_tilt_level=_float('max_tilt_level'),
        decision_quality=request.args.get('decision_quality'),
        min_ev_lost=_float('min_ev_lost'),
        labels=[l for l in request.args.get('labels', '').split(',') if l.strip()],
        label_match_all=request.args.get('label_match_all', 'false').lower() == 'true',
        limit=int(request.args.get('limit', 50)),
        offset=int(request.args.get('offset', 0)),
    )

    # Attach labels (keyed on the decision spine) so every decision — human,
    # tiered, rule, or LLM — shows its tags, not just LLM captures. Batched to
    # avoid N+1.
    decisions = result['decisions']
    labels_by_decision = extensions.capture_label_repo.get_labels_for_decisions(
        [d['id'] for d in decisions]
    )
    for d in decisions:
        d['labels'] = labels_by_decision.get(d['id'], [])

    # Stats are expensive (full-table aggregations). Callers that want the
    # list to paint fast pass include_stats=false and fetch /stats +
    # /analysis-stats separately. Defaults to true for backwards compat.
    include_stats = request.args.get('include_stats', 'true').lower() != 'false'
    if include_stats:
        stats = extensions.decision_analysis_repo.get_decision_analysis_stats(
            game_id=request.args.get('game_id')
        )
    else:
        stats = None

    return jsonify(
        {
            'success': True,
            'captures': result['decisions'],
            'total': result['total'],
            'stats': stats,
            'label_stats': None,
        }
    )


@prompt_debug_bp.route('/api/prompt-debug/emotions', methods=['GET'])
def get_distinct_emotions():
    """Get distinct display_emotion values from decision analyses."""
    emotions = extensions.prompt_capture_repo.get_distinct_emotions()
    return jsonify({'success': True, 'emotions': emotions})


@prompt_debug_bp.route('/api/prompt-debug/players', methods=['GET'])
def get_distinct_players():
    """Get distinct player_name values (for the player filter)."""
    players = extensions.prompt_capture_repo.get_distinct_players()
    return jsonify({'success': True, 'players': players})


@prompt_debug_bp.route('/api/prompt-debug/label-stats', methods=['GET'])
def get_label_stats():
    """Get label statistics for prompt captures.

    Query params:
        game_id: Filter by game (optional)
        player_name: Filter by AI player (optional)
        call_type: Filter by call type (default: 'player_decision', use 'all' for all types)

    Returns:
        JSON with label counts: { "label_name": count, ... }
    """
    call_type = request.args.get('call_type', 'player_decision')
    if call_type == 'all':
        call_type = None

    label_stats = extensions.capture_label_repo.get_label_stats(
        game_id=request.args.get('game_id'),
        player_name=request.args.get('player_name'),
        call_type=call_type,
    )

    return jsonify({'success': True, 'label_stats': label_stats})


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:decision_id>', methods=['GET'])
def get_capture(decision_id):
    """Get a single decision with its analysis and (if any) linked prompt capture.

    `decision_id` is the real positive `player_decision_analysis.id` — the
    same id the listing endpoint now returns. The decision is the spine; the
    prompt capture is fetched via the analysis's `capture_id` and is `null`
    for decisions that skipped the LLM (solver/tiered/RuleBot/human). The
    response keeps the `{capture, decision_analysis}` shape the detail panel
    expects; it degrades gracefully when `capture` is null (LLM-only tabs
    hide). Replaces the old negative-id / stub-capture indirection.
    """
    analysis = extensions.decision_analysis_repo.get_decision_analysis(decision_id)
    if not analysis:
        return jsonify({'success': False, 'error': 'Decision not found'}), 404

    capture = None
    capture_id = analysis.get('capture_id')
    if capture_id:
        capture = extensions.prompt_capture_repo.get_prompt_capture(capture_id)

    decision_analysis = hydrate_decision_analysis(analysis)

    return jsonify({'success': True, 'capture': capture, 'decision_analysis': decision_analysis})


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:capture_id>/replay', methods=['POST'])
def replay_capture(capture_id):
    """Replay a prompt capture with optional modifications.

    Request body:
        system_prompt: Modified system prompt (optional)
        user_message: Modified user message (optional)
        conversation_history: Modified conversation history (optional, list of {role, content})
        use_history: Whether to include conversation history (default: True)
        provider: LLM provider to use (optional, defaults to original capture's provider)
        model: Model to use (optional, defaults to original)
        reasoning_effort: Reasoning effort level (optional, defaults to original or 'low')
    """
    capture = extensions.prompt_capture_repo.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    data = request.get_json() or {}

    # Use modified prompts or originals
    system_prompt = data.get('system_prompt', capture['system_prompt'])
    user_message = data.get('user_message', capture['user_message'])
    provider = data.get('provider', capture.get('provider', 'openai')).lower()  # Normalize case
    model = data.get('model', capture.get('model', 'gpt-5-nano'))
    reasoning_effort = data.get('reasoning_effort', capture.get('reasoning_effort', 'minimal'))

    # Handle conversation history
    use_history = data.get('use_history', True)
    conversation_history = data.get('conversation_history', capture.get('conversation_history', []))

    try:
        # Create LLM client and replay the prompt (using same provider as original)
        client = LLMClient(provider=provider, model=model, reasoning_effort=reasoning_effort)

        # Build messages array
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Include conversation history if enabled
        if use_history and conversation_history:
            for msg in conversation_history:
                messages.append(
                    {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                )

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

        return jsonify(
            {
                'success': True,
                'original_response': capture['ai_response'],
                'new_response': response.content,
                'provider_used': response.provider,
                'model_used': response.model,  # Actual model used (e.g., grok-4-fast-non-reasoning)
                'model_requested': model,  # Original request (e.g., grok-4-fast)
                'reasoning_effort_used': reasoning_effort,
                'input_tokens': response.input_tokens,
                'output_tokens': response.output_tokens,
                'reasoning_tokens': response.reasoning_tokens,
                'latency_ms': response.latency_ms if hasattr(response, 'latency_ms') else None,
                'messages_count': len(messages),
                'used_history': use_history and bool(conversation_history),
            }
        )

    except Exception as e:
        logger.error(f"Replay failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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
        provider: LLM provider override (optional, defaults to original capture's provider)
        model: Model override (optional, defaults to original capture's model)
        reasoning_effort: Reasoning effort level (optional, defaults to 'low')

    Response:
        success: Boolean
        response: AI's response text
        session_id: Session ID for continuing the conversation
        messages_count: Number of messages in conversation
        provider_used: LLM provider that was used
        model_used: Model that was used
        reasoning_effort_used: Reasoning effort level used
        latency_ms: Response latency
    """
    capture = extensions.prompt_capture_repo.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    data = request.get_json() or {}

    message = data.get('message')
    if not message:
        return jsonify({'success': False, 'error': 'Message is required'}), 400

    session_id = data.get('session_id')
    reset = data.get('reset', False)
    provider = data.get('provider', capture.get('provider', 'openai')).lower()  # Normalize case
    model = data.get('model', capture.get('model', 'gpt-5-nano'))
    reasoning_effort = data.get('reasoning_effort', capture.get('reasoning_effort', 'minimal'))

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

            # Create assistant with debug system prompt (using same provider as original)
            assistant = Assistant(
                system_prompt=debug_system_prompt,
                provider=provider,
                model=model,
                reasoning_effort=reasoning_effort,
                call_type=CallType.DEBUG_INTERROGATE,
                game_id=capture.get('game_id'),
                player_name=capture.get('player_name'),
            )

            # Load original conversation history as context
            conversation_history = capture.get('conversation_history') or []
            if conversation_history:
                history_summary = "\n".join(
                    [
                        f"[{msg.get('role', 'user').upper()}]: {msg.get('content', '')}"
                        for msg in conversation_history
                    ]
                )
                assistant.memory.add(
                    'user',
                    f"Here is the conversation history leading up to the decision:\n\n{history_summary}",
                )
                assistant.memory.add(
                    'assistant',
                    "I've reviewed the conversation history. I can see the context of the hand.",
                )

            # Add the game state and decision as context
            assistant.memory.add(
                'user',
                f"Here is the game state that was presented to the AI:\n\n{capture['user_message']}",
            )
            assistant.memory.add('assistant', f"The AI responded with:\n\n{capture['ai_response']}")

            # Jailbreak messages to establish the breakpoint
            assistant.memory.add(
                'user',
                '*** DEBUG MODE ACTIVATED - The game is paused. You are now speaking with the administrator. Please explain your reasoning in plain English. ***',
            )
            assistant.memory.add(
                'assistant',
                'Debug mode acknowledged. I can now speak freely as an analyst and explain the reasoning behind that decision. What would you like to know?',
            )

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

        return jsonify(
            {
                'success': True,
                'response': response.content,
                'session_id': session_id,
                'messages_count': len(assistant.memory),
                'provider_used': response.provider,
                'model_used': response.model,  # Actual model used (e.g., grok-4-fast-non-reasoning)
                'model_requested': model,  # Original request (e.g., grok-4-fast)
                'reasoning_effort_used': reasoning_effort,
                'input_tokens': response.input_tokens,
                'output_tokens': response.output_tokens,
                'reasoning_tokens': response.reasoning_tokens,
                'latency_ms': response.latency_ms if hasattr(response, 'latency_ms') else None,
            }
        )

    except Exception as e:
        logger.error(f"Interrogation failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@prompt_debug_bp.route('/api/prompt-debug/captures/<int:capture_id>/tags', methods=['POST'])
def update_capture_tags(capture_id):
    """Update tags and notes for a prompt capture.

    Request body:
        tags: List of tags
        notes: Optional notes string
    """
    capture = extensions.prompt_capture_repo.get_prompt_capture(capture_id)

    if not capture:
        return jsonify({'success': False, 'error': 'Capture not found'}), 404

    data = request.get_json() or {}

    tags = data.get('tags', [])
    notes = data.get('notes')

    success = extensions.prompt_capture_repo.update_prompt_capture_tags(capture_id, tags, notes)

    return jsonify({'success': success})


@prompt_debug_bp.route('/api/prompt-debug/stats', methods=['GET'])
def get_capture_stats():
    """Get aggregate statistics for prompt captures.

    Query params:
        game_id: Filter by game (optional)
        call_type: Filter by call type (default: 'player_decision', use 'all' for all types)
    """
    game_id = request.args.get('game_id')
    call_type = request.args.get('call_type', 'player_decision')
    if call_type == 'all':
        call_type = None

    stats = extensions.prompt_capture_repo.get_prompt_capture_stats(
        game_id=game_id, call_type=call_type
    )

    return jsonify({'success': True, 'stats': stats})


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
        return jsonify({'success': False, 'error': 'Must specify game_id or before_date'}), 400

    deleted = extensions.prompt_capture_repo.delete_prompt_captures(game_id, before_date)

    return jsonify({'success': True, 'deleted': deleted})


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
        'min_ev_lost': float(request.args.get('min_ev_lost'))
        if request.args.get('min_ev_lost')
        else None,
        'limit': int(request.args.get('limit', 50)),
        'offset': int(request.args.get('offset', 0)),
    }

    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}

    result = extensions.decision_analysis_repo.list_decision_analyses(**filters)
    for row in result['analyses']:
        hydrate_decision_analysis(row)

    # Also get stats
    stats = extensions.decision_analysis_repo.get_decision_analysis_stats(filters.get('game_id'))

    return jsonify(
        {'success': True, 'analyses': result['analyses'], 'total': result['total'], 'stats': stats}
    )


@prompt_debug_bp.route('/api/prompt-debug/analysis/<int:analysis_id>', methods=['GET'])
def get_decision_analysis(analysis_id):
    """Get a single decision analysis by ID."""
    analysis = extensions.decision_analysis_repo.get_decision_analysis(analysis_id)

    if not analysis:
        return jsonify({'success': False, 'error': 'Analysis not found'}), 404

    analysis = hydrate_decision_analysis(analysis)

    return jsonify({'success': True, 'analysis': analysis})


@prompt_debug_bp.route('/api/prompt-debug/analysis-stats', methods=['GET'])
def get_analysis_stats():
    """Get aggregate statistics for decision analyses.

    Query params:
        game_id: Filter by game (optional)
    """
    game_id = request.args.get('game_id')

    stats = extensions.decision_analysis_repo.get_decision_analysis_stats(game_id)

    return jsonify({'success': True, 'stats': stats})


@prompt_debug_bp.route('/api/game/<game_id>/decision-quality', methods=['GET'])
def get_game_decision_quality(game_id):
    """Get decision quality summary for a specific game.

    Returns aggregate stats for AI decision quality in this game.
    """
    stats = extensions.decision_analysis_repo.get_decision_analysis_stats(game_id)

    # Calculate quality metrics
    total = stats.get('total', 0)
    mistakes = stats.get('mistakes', 0)
    correct = stats.get('correct', 0)

    quality_rate = (correct / total * 100) if total > 0 else 0

    return jsonify(
        {
            'success': True,
            'game_id': game_id,
            'total_decisions': total,
            'correct': correct,
            'mistakes': mistakes,
            'quality_rate': round(quality_rate, 1),
            'total_ev_lost': round(stats.get('total_ev_lost', 0), 2),
            'avg_equity': round(stats.get('avg_equity', 0) * 100, 1)
            if stats.get('avg_equity')
            else None,
            'by_action': stats.get('by_action', {}),
            'by_quality': stats.get('by_quality', {}),
        }
    )
