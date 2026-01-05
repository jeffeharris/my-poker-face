"""Stats and utility routes."""

import os
import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request
from openai import OpenAI

from core.llm import LLMClient, CallType

from ..extensions import persistence, auth_manager, limiter
from ..services import game_state_service
from .. import config

logger = logging.getLogger(__name__)

stats_bp = Blueprint('stats', __name__)


@stats_bp.route('/api/career-stats', methods=['GET'])
def get_career_stats():
    """Get career stats for the authenticated user."""
    current_user = auth_manager.get_current_user()
    if not current_user:
        return jsonify({'error': 'Not authenticated'}), 401

    player_name = current_user.get('name')
    if not player_name:
        return jsonify({'error': 'No player name found'}), 400

    stats = persistence.get_career_stats(player_name)
    history = persistence.get_tournament_history(player_name, limit=10)
    eliminated = persistence.get_eliminated_personalities(player_name)

    return jsonify({
        'stats': stats,
        'recent_tournaments': history,
        'eliminated_personalities': eliminated
    })


@stats_bp.route('/api/models', methods=['GET'])
def get_available_models():
    """Get available OpenAI models for game configuration."""
    from core.llm import DEFAULT_MODEL, DEFAULT_REASONING_EFFORT, AVAILABLE_MODELS
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        models = client.models.list()
        available = [m.id for m in models.data if m.id.startswith(('gpt-5', 'gpt-4o'))]
        return jsonify({
            'success': True,
            'models': sorted(available),
            'default_model': DEFAULT_MODEL,
            'reasoning_levels': ['minimal', 'low', 'medium', 'high'],
            'default_reasoning': DEFAULT_REASONING_EFFORT
        })
    except Exception as e:
        logger.error(f"Error fetching models: {e}")
        return jsonify({
            'success': True,
            'models': AVAILABLE_MODELS,
            'default_model': DEFAULT_MODEL,
            'reasoning_levels': ['minimal', 'low', 'medium', 'high'],
            'default_reasoning': DEFAULT_REASONING_EFFORT
        })


@stats_bp.route('/settings/<game_id>')
def settings(game_id):
    """Deprecated: Settings are now handled in React."""
    game_state = game_state_service.get_game(game_id)
    if not game_state:
        return jsonify({'error': 'Game not found'}), 404
    return jsonify({'message': 'Settings should be accessed through the React app'})


@stats_bp.route('/api/game/<game_id>/chat-suggestions', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def get_chat_suggestions(game_id):
    """Generate smart chat suggestions based on game context."""
    if not game_state_service.get_game(game_id):
        return jsonify({"error": "Game not found"}), 404

    # Get owner_id for tracking
    current_user = auth_manager.get_current_user()
    owner_id = current_user.get('id') if current_user else None

    try:
        data = request.get_json()
        game_data = game_state_service.get_game(game_id)
        state_machine = game_data['state_machine']
        game_state = state_machine.game_state

        context_parts = []

        player_name = data.get('playerName', 'Player')

        last_action = data.get('lastAction')
        if last_action:
            action_text = f"{last_action['player']} just {last_action['type']}"
            if last_action.get('amount'):
                action_text += f" ${last_action['amount']}"
            context_parts.append(action_text)

        context_parts.append(f"Game phase: {str(state_machine.current_phase).split('.')[-1]}")
        context_parts.append(f"Pot size: ${game_state.pot['total']}")

        chip_position = data.get('chipPosition', '')
        if chip_position:
            context_parts.append(f"You are {chip_position}")

        context_str = ". ".join(context_parts)

        prompt = f"""Generate exactly 3 short poker table chat messages for player "{player_name}".
Context: {context_str}

Requirements:
- Each message should be 2-4 words max
- Make them fun, casual, and appropriate for online poker
- Include one reaction, one strategic comment, and one social/fun message
- Keep them varied and natural
- No profanity or negativity

Return as JSON with this format:
{{
    "suggestions": [
        {{"text": "message here", "type": "reaction"}},
        {{"text": "message here", "type": "strategic"}},
        {{"text": "message here", "type": "social"}}
    ]
}}"""

        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OpenAI API key not configured")

        client = LLMClient(model=config.FAST_AI_MODEL)
        messages = [
            {"role": "system", "content": "You are a friendly poker player giving brief chat suggestions."},
            {"role": "user", "content": prompt}
        ]

        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.CHAT_SUGGESTION,
            game_id=game_id,
            owner_id=owner_id
        )
        result = json.loads(response.content)

        return jsonify(result)

    except Exception as e:
        print(f"Error generating chat suggestions: {str(e)}")
        return jsonify({
            "suggestions": [
                {"text": "Nice play!", "type": "reaction"},
                {"text": "Interesting move", "type": "strategic"},
                {"text": "Let's go!", "type": "social"}
            ]
        })


@stats_bp.route('/api/game/<game_id>/targeted-chat-suggestions', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def get_targeted_chat_suggestions(game_id):
    """Generate targeted chat suggestions to engage specific AI players."""
    if not game_state_service.get_game(game_id):
        return jsonify({"error": "Game not found"}), 404

    # Get owner_id for tracking
    current_user = auth_manager.get_current_user()
    owner_id = current_user.get('id') if current_user else None

    data = None
    try:
        data = request.get_json()
        game_data = game_state_service.get_game(game_id)
        state_machine = game_data['state_machine']
        game_state = state_machine.game_state

        player_name = data.get('playerName', 'Player')
        target_player = data.get('targetPlayer')
        tone = data.get('tone', 'encourage')

        tone_descriptions = {
            'encourage': 'supportive, friendly, complimentary about their play',
            'antagonize': 'playful trash talk, teasing, challenging their decisions (keep it fun, not mean)',
            'confuse': 'random non-sequiturs, weird observations, misdirection to throw them off',
            'flatter': 'over-the-top compliments, acknowledge their skill, be impressed',
            'challenge': 'direct dares, betting challenges, call them out to make a move'
        }

        tone_desc = tone_descriptions.get(tone, tone_descriptions['encourage'])

        context_parts = []
        context_parts.append(f"Game phase: {str(state_machine.current_phase).split('.')[-1]}")
        context_parts.append(f"Pot size: ${game_state.pot['total']}")

        last_action = data.get('lastAction')
        if last_action:
            action_text = f"{last_action.get('player', 'Someone')} just {last_action.get('type', 'acted')}"
            if last_action.get('amount'):
                action_text += f" ${last_action['amount']}"
            context_parts.append(action_text)

        context_str = ". ".join(context_parts)

        game_messages = game_data.get('messages', [])[-10:]
        chat_context = ""
        if game_messages:
            chat_lines = []
            for msg in game_messages:
                sender = msg.get('sender', 'Unknown')
                text = msg.get('content', msg.get('message', ''))[:100]
                if text:
                    chat_lines.append(f"- {sender}: {text}")
            if chat_lines:
                chat_context = "\nRecent table talk:\n" + "\n".join(chat_lines)

        game_situation = "\n".join(game_state.opponent_status)

        if game_state.community_cards:
            cards = [str(c) for c in game_state.community_cards]
            game_situation = f"Board: {', '.join(cards)}\n" + game_situation

        target_context = ""
        if target_player:
            try:
                personalities_file = Path(__file__).parent.parent.parent / 'poker' / 'personalities.json'
                with open(personalities_file, 'r') as f:
                    personalities_data = json.load(f)

                if target_player in personalities_data.get('personalities', {}):
                    personality = personalities_data['personalities'][target_player]
                    play_style = personality.get('play_style', 'unknown')
                    verbal_tics = personality.get('verbal_tics', [])[:3]
                    attitude = personality.get('default_attitude', 'neutral')

                    target_context = f"""
Target player: {target_player}
Their personality: {play_style}
Their attitude: {attitude}
Their catchphrases: {', '.join(verbal_tics) if verbal_tics else 'none known'}"""
            except Exception as e:
                logger.warning(f"Could not load personality for {target_player}: {e}")
                target_context = f"\nTarget player: {target_player}"

        if target_player:
            target_first_name = target_player.split()[0] if target_player else "them"
            prompt = f"""Generate exactly 2 short poker table chat messages for player "{player_name}" to say directly to {target_player}.
{target_context}

Tone: {tone_desc}
Game context: {context_str}
Table situation:
{game_situation}
{chat_context}

Requirements:
- Each message should be 5-15 words
- IMPORTANT: Include "{target_first_name}" or "{target_player}" in each message to make it clear who you're addressing
- Match the {tone} tone perfectly
- Reference the board, stacks, or recent conversation when relevant
- If you know their personality, play off their quirks
- Be playful but not offensive or mean-spirited
- Messages should feel natural for poker table banter

Example formats: "Hey {target_first_name}, ...", "{target_first_name}, you really think...", "What's the matter {target_first_name}..."

Return as JSON:
{{
    "suggestions": [
        {{"text": "message here", "tone": "{tone}"}},
        {{"text": "message here", "tone": "{tone}"}}
    ],
    "targetPlayer": "{target_player}"
}}"""
        else:
            prompt = f"""Generate exactly 2 short poker table chat messages to announce to the whole table.

Tone: {tone_desc}
Game context: {context_str}
Table situation:
{game_situation}
{chat_context}

Requirements:
- Each message should be 5-15 words
- Write in FIRST PERSON - these are things the player will say directly
- Do NOT include the speaker's name - they are saying this themselves
- Match the {tone} tone perfectly
- Reference the board, stacks, or recent conversation when relevant
- General table talk, not directed at anyone specific
- Be playful and engaging
- Messages should feel natural for poker table banter

Good examples: "Anyone else feeling lucky tonight?", "This pot is getting interesting!", "That ace on the turn changes everything!"
Bad examples: "Jeff says he's feeling lucky" (don't use 3rd person), "Player announces confidence" (don't narrate)

Return as JSON:
{{
    "suggestions": [
        {{"text": "message here", "tone": "{tone}"}},
        {{"text": "message here", "tone": "{tone}"}}
    ],
    "targetPlayer": null
}}"""

        if not os.environ.get("OPENAI_API_KEY"):
            logger.warning("No OpenAI API key found, returning fallback suggestions")
            raise ValueError("OpenAI API key not configured")

        logger.info(f"[QuickChat] Target: {target_player}, Tone: {tone}")
        logger.info(f"[QuickChat] Prompt: {prompt[:500]}...")

        client = LLMClient(model=config.FAST_AI_MODEL, reasoning_effort="minimal")
        messages = [
            {"role": "system", "content": "You are a witty poker player helping generate fun table talk. Keep it light and entertaining."},
            {"role": "user", "content": prompt}
        ]

        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.TARGETED_CHAT,
            game_id=game_id,
            owner_id=owner_id
        )
        raw_content = response.content
        logger.info(f"[QuickChat] Raw response: {raw_content}")
        result = json.loads(raw_content)

        return jsonify(result)

    except Exception as e:
        logger.error(f"[QuickChat] ERROR generating suggestions: {str(e)}")
        logger.exception("[QuickChat] Full traceback:")
        target = data.get('targetPlayer') if data else None
        fallback_messages = {
            'encourage': ["Nice hand!", "Good play there!"],
            'antagonize': ["You sure about that?", "Interesting choice..."],
            'confuse': ["Did anyone else hear that?", "The cards speak to me."],
            'flatter': ["Impressive as always!", "You're too good!"],
            'challenge': ["Prove it!", "Show me what you got!"]
        }
        tone = data.get('tone', 'encourage') if data else 'encourage'
        msgs = fallback_messages.get(tone, fallback_messages['encourage'])

        return jsonify({
            "suggestions": [
                {"text": msgs[0], "tone": tone},
                {"text": msgs[1], "tone": tone}
            ],
            "targetPlayer": target,
            "error": str(e),
            "fallback": True
        })
