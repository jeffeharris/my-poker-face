"""Stats and utility routes."""

import os
import json
import logging
from pathlib import Path

from flask import Blueprint, jsonify, request
from openai import OpenAI

from core.llm import LLMClient, CallType

from ..extensions import persistence, auth_manager, limiter, personality_generator
from poker.prompt_manager import PromptManager

# Module-level prompt manager instance
_prompt_manager = PromptManager()
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

        # Get hand number for tracking
        memory_manager = game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None

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

        # Detailed logging for debugging/iteration
        logger.info("=" * 80)
        logger.info("[ChatSuggestion] === CHAT SUGGESTION REQUEST ===")
        logger.info(f"[ChatSuggestion] Player: {player_name}")
        logger.info(f"[ChatSuggestion] Context: {context_str}")
        logger.info("[ChatSuggestion] --- FULL PROMPT ---")
        logger.info(f"[ChatSuggestion]\n{prompt}")
        logger.info("[ChatSuggestion] --- END PROMPT ---")

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
            owner_id=owner_id,
            hand_number=hand_number,
            prompt_template='chat_suggestion',
        )
        logger.info("[ChatSuggestion] --- RESPONSE ---")
        logger.info(f"[ChatSuggestion]\n{response.content}")
        logger.info("[ChatSuggestion] === END CHAT SUGGESTION ===")
        logger.info("=" * 80)
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

        # Get hand number for tracking
        memory_manager = game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None

        player_name = data.get('playerName', 'Player')
        target_player = data.get('targetPlayer')
        tone = data.get('tone', 'goad')
        length = data.get('length', 'short')
        intensity = data.get('intensity', 'chill')

        # Length and intensity modifiers for templates
        length_guidance = {
            'short': 'Keep it VERY short - under 8 words.',
            'long': 'Can be 1-2 full sentences.',
        }
        intensity_guidance = {
            'chill': 'Keep it playful and light.',
            'spicy': 'Go hard. No filter. Cut deep.',
        }

        # Map tones to template names
        template_map = {
            'tilt': 'quick_chat_tilt',
            'false_confidence': 'quick_chat_false_confidence',
            'doubt': 'quick_chat_doubt',
            'goad': 'quick_chat_goad',
            'mislead': 'quick_chat_mislead',
            'befriend': 'quick_chat_befriend',
        }

        # Tone descriptions for table talk (no target)
        tone_descriptions = {
            'tilt': 'Needle the table. Be cutting.',
            'false_confidence': 'Sound worried about the competition.',
            'doubt': 'Question what just happened.',
            'goad': 'Dare the table to act.',
            'mislead': 'Give false tells about your hand.',
            'befriend': 'Be warm to the table.',
        }

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

        game_messages = game_data.get('messages', [])[-15:]  # Get more, filter will reduce
        chat_context = ""
        if game_messages:
            chat_lines = []
            for msg in game_messages:
                sender = msg.get('sender', 'Unknown')
                text = msg.get('content', msg.get('message', ''))[:100]

                # Filter out System messages (debug noise)
                if sender == 'System':
                    continue

                if text:
                    chat_lines.append(f"- {sender}: {text}")
            if chat_lines:
                chat_context = "\nRecent table talk:\n" + "\n".join(chat_lines[-10:])  # Keep last 10 after filtering

        game_situation = "\n".join(game_state.opponent_status)

        if game_state.community_cards:
            cards = [str(c) for c in game_state.community_cards]
            game_situation = f"Board: {', '.join(cards)}\n" + game_situation

        target_context = ""
        if target_player:
            try:
                # Get personality from database via personality_generator
                personality = personality_generator.get_personality(target_player)
                if personality:
                    play_style = personality.get('play_style', 'unknown')
                    verbal_tics = personality.get('verbal_tics', [])[:3]
                    attitude = personality.get('default_attitude', 'neutral')

                    target_context = f"""
Target player: {target_player}
Their personality: {play_style}
Their attitude: {attitude}
Things THEY say (reference or play off these, don't copy): {', '.join(verbal_tics) if verbal_tics else 'none known'}"""
                else:
                    target_context = f"\nTarget player: {target_player}"
            except Exception as e:
                logger.warning(f"Could not load personality for {target_player}: {e}")
                target_context = f"\nTarget player: {target_player}"

        if target_player:
            target_first_name = target_player.split()[0] if target_player else "them"
            template_name = template_map.get(tone, 'quick_chat_goad')
            prompt = _prompt_manager.render_prompt(
                template_name,
                player_name=player_name,
                target_player=target_player,
                target_first_name=target_first_name,
                context_str=context_str,
                chat_context=chat_context,
                length_guidance=length_guidance.get(length, length_guidance['short']),
                intensity_guidance=intensity_guidance.get(intensity, intensity_guidance['chill']),
            )
        else:
            prompt = _prompt_manager.render_prompt(
                'quick_chat_table',
                player_name=player_name,
                context_str=context_str,
                chat_context=chat_context,
                tone=tone,
                tone_description=tone_descriptions.get(tone, tone_descriptions['goad']),
                length_guidance=length_guidance.get(length, length_guidance['short']),
                intensity_guidance=intensity_guidance.get(intensity, intensity_guidance['chill']),
            )

        if not os.environ.get("OPENAI_API_KEY"):
            logger.warning("No OpenAI API key found, returning fallback suggestions")
            raise ValueError("OpenAI API key not configured")

        # Detailed logging for debugging/iteration
        logger.info("=" * 80)
        logger.info("[QuickChat] === QUICK CHAT REQUEST ===")
        logger.info(f"[QuickChat] Target: {target_player}, Tone: {tone}, Length: {length}, Intensity: {intensity}, Player: {player_name}")
        logger.info(f"[QuickChat] Game context: {context_str}")
        logger.info(f"[QuickChat] Game situation:\n{game_situation}")
        logger.info(f"[QuickChat] Target context: {target_context}")
        logger.info(f"[QuickChat] Chat context: {chat_context}")
        logger.info("[QuickChat] --- FULL PROMPT ---")
        logger.info(f"[QuickChat]\n{prompt}")
        logger.info("[QuickChat] --- END PROMPT ---")

        client = LLMClient(model=config.FAST_AI_MODEL, reasoning_effort="minimal")
        messages = [
            {"role": "system", "content": "You write sharp, witty poker banter that responds to the actual conversation. Never generic - always specific callbacks, quotes, or reactions to what just happened. Short and punchy."},
            {"role": "user", "content": prompt}
        ]

        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.TARGETED_CHAT,
            game_id=game_id,
            owner_id=owner_id,
            player_name=target_player,  # The target of the chat
            hand_number=hand_number,
            prompt_template='targeted_chat',
        )
        raw_content = response.content
        logger.info("[QuickChat] --- RESPONSE ---")
        logger.info(f"[QuickChat]\n{raw_content}")
        logger.info("[QuickChat] === END QUICK CHAT ===")
        logger.info("=" * 80)
        result = json.loads(raw_content)

        return jsonify(result)

    except Exception as e:
        logger.error(f"[QuickChat] ERROR generating suggestions: {str(e)}")
        logger.exception("[QuickChat] Full traceback:")
        target = data.get('targetPlayer') if data else None
        fallback_messages = {
            'tilt': ["Still thinking about that last hand?", "Rough night, huh?"],
            'false_confidence': ["You've got this one for sure.", "I'm scared of that bet."],
            'doubt': ["Interesting timing...", "You sure about that read?"],
            'goad': ["Prove it.", "You wouldn't dare."],
            'mislead': ["I should've folded...", "This hand is killing me."],
            'befriend': ["Good game so far.", "Respect the play."]
        }
        tone = data.get('tone', 'goad') if data else 'goad'
        msgs = fallback_messages.get(tone, fallback_messages['goad'])

        return jsonify({
            "suggestions": [
                {"text": msgs[0], "tone": tone},
                {"text": msgs[1], "tone": tone}
            ],
            "targetPlayer": target,
            "error": str(e),
            "fallback": True
        })
