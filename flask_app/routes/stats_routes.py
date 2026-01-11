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
from poker.memory.hand_history import RecordedHand
from typing import Optional, Dict, Any, Tuple
from collections import defaultdict

# Module-level prompt manager instance
_prompt_manager = PromptManager()
from ..services import game_state_service
from .. import config

logger = logging.getLogger(__name__)

stats_bp = Blueprint('stats', __name__)

# Module-level constants for prompt guidance
LENGTH_GUIDANCE = {
    'short': 'Keep it VERY short - under 8 words.',
    'long': 'Can be 1-2 full sentences.',
}
INTENSITY_GUIDANCE = {
    'chill': 'Keep it playful and light.',
    'spicy': 'Go hard. No filter. Cut deep.',
}


def format_message_history(messages: list, max_messages: int = 10, text_limit: int = 100) -> str:
    """
    Format game messages into a context string for prompts.

    Filters out System messages and formats player messages with their actions.

    Args:
        messages: List of message dicts with sender, content/message, and optional action
        max_messages: Maximum number of messages to include in output
        text_limit: Character limit for message text truncation

    Returns:
        Formatted string of recent table talk, or empty string if no messages
    """
    if not messages:
        return ""

    chat_lines = []
    for msg in messages:
        sender = msg.get('sender', 'Unknown')
        text = msg.get('content', msg.get('message', ''))[:text_limit]
        action = msg.get('action')  # e.g., "raises to $500"

        # Filter out System messages (debug noise)
        if sender == 'System':
            continue

        # For AI messages with actions, show both the chat and action
        if action and sender != 'Table':
            chat_lines.append(f"- {sender} ({action}): {text}")
        elif sender == 'Table' and text:
            # Table messages are usually action announcements
            chat_lines.append(f"- {text}")
        elif text and sender != 'Table':
            chat_lines.append(f"- {sender}: {text}")

    if chat_lines:
        return "\n".join(chat_lines[-max_messages:])
    return ""


def build_hand_context_from_recorded_hand(
    hand: RecordedHand,
    player_name: str
) -> Dict[str, Any]:
    """
    Build comprehensive hand context for post-round chat suggestions.

    Returns a dict with:
        - outcome: 'WON_SHOWDOWN', 'WON_BY_FOLD', 'LOST_SHOWDOWN', or 'FOLDED'
        - player_cards: player's hole cards (if available)
        - opponent_name: main opponent's name
        - opponent_cards: opponent's hole cards (if showdown)
        - opponent_hand_name: opponent's hand name (if showdown)
        - player_hand_name: player's hand name (if showdown and available)
        - timeline: formatted string of actions by street
        - community_cards: the board
        - pot_size: final pot
        - drama_note: optional note about bad beats, river hits, etc.
    """
    result = {
        'outcome': None,
        'player_cards': None,
        'opponent_name': None,
        'opponent_cards': None,
        'opponent_hand_name': None,
        'player_hand_name': None,
        'timeline': '',
        'community_cards': list(hand.community_cards) if hand.community_cards else [],
        'pot_size': hand.pot_size,
        'drama_note': None,
    }

    # Determine player outcome
    player_outcome = hand.get_player_outcome(player_name)  # 'won', 'lost', 'folded'

    # Determine full outcome type (4 scenarios)
    if player_outcome == 'won':
        if hand.was_showdown:
            result['outcome'] = 'WON_SHOWDOWN'
        else:
            result['outcome'] = 'WON_BY_FOLD'
    elif player_outcome == 'folded':
        result['outcome'] = 'FOLDED'
    else:  # lost
        result['outcome'] = 'LOST_SHOWDOWN'

    # Get player's cards
    logger.info(f"[PostRound] DEBUG hole_cards keys: {list(hand.hole_cards.keys())}, looking for: '{player_name}'")
    if player_name in hand.hole_cards:
        result['player_cards'] = hand.hole_cards[player_name]
    else:
        logger.warning(f"[PostRound] Player '{player_name}' not found in hole_cards!")

    # Get opponent info based on outcome
    winner_names = [w.name for w in hand.winners]

    if player_outcome == 'won':
        if hand.was_showdown:
            # WON_SHOWDOWN: Find opponent who was in showdown (didn't fold) but lost
            for p in hand.players:
                if p.name != player_name:
                    p_outcome = hand.get_player_outcome(p.name)
                    if p_outcome == 'lost':  # Was in showdown but lost
                        result['opponent_name'] = p.name
                        break
        else:
            # WON_BY_FOLD: Find opponent who put the most in the pot
            pot_contributions = defaultdict(int)
            for action in hand.actions:
                if action.player_name != player_name:
                    pot_contributions[action.player_name] += action.amount
            if pot_contributions:
                # Get player with highest contribution
                result['opponent_name'] = max(pot_contributions, key=pot_contributions.get)
    else:
        # Player lost or folded - opponent is the winner
        for w in hand.winners:
            if w.name != player_name:
                result['opponent_name'] = w.name
                result['opponent_hand_name'] = w.hand_name
                break

    # Get opponent cards if showdown
    if result['opponent_name'] and result['opponent_name'] in hand.hole_cards:
        result['opponent_cards'] = hand.hole_cards[result['opponent_name']]

    # Get player's hand name if they won at showdown
    if player_outcome == 'won' and hand.was_showdown:
        for w in hand.winners:
            if w.name == player_name:
                result['player_hand_name'] = w.hand_name
                break

    # Build timeline by phase
    phases = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER']
    actions_by_phase = defaultdict(list)
    for action in hand.actions:
        actions_by_phase[action.phase].append(action)

    # Map community cards to phases
    community = list(hand.community_cards) if hand.community_cards else []
    phase_cards = {
        'FLOP': community[0:3] if len(community) >= 3 else [],
        'TURN': [community[3]] if len(community) >= 4 else [],
        'RIVER': [community[4]] if len(community) >= 5 else [],
    }

    timeline_parts = []
    for phase in phases:
        phase_actions = actions_by_phase.get(phase, [])
        if not phase_actions:
            continue

        # Format phase header with cards
        cards = phase_cards.get(phase, [])
        if cards:
            phase_header = f"{phase} [{', '.join(cards)}]"
        else:
            phase_header = phase

        # Format actions
        action_strs = []
        for a in phase_actions:
            # Use "You" for the player, name for others
            actor = "You" if a.player_name == player_name else a.player_name
            if a.action in ('fold', 'check'):
                action_strs.append(f"{actor} {a.action}ed" if a.action == 'fold' else f"{actor} checked")
            elif a.action == 'call':
                action_strs.append(f"{actor} called" + (f" ${a.amount}" if a.amount > 0 else ""))
            elif a.action in ('raise', 'bet'):
                action_strs.append(f"{actor} {'raised' if a.action == 'raise' else 'bet'} ${a.amount}")
            elif a.action == 'all_in':
                action_strs.append(f"{actor} went all-in (${a.amount})")
            else:
                action_strs.append(f"{actor} {a.action}")

        timeline_parts.append(f"{phase_header}: {', '.join(action_strs)}")

    result['timeline'] = '\n'.join(timeline_parts)

    # Add drama note for notable situations
    if hand.was_showdown and result['opponent_hand_name']:
        # Check if opponent hit on river
        river_actions = actions_by_phase.get('RIVER', [])
        if river_actions and len(community) == 5:
            river_card = community[4]
            # Simple heuristic: if opponent won with trips/pair and river card matches
            hand_name = result['opponent_hand_name'].lower()
            if 'three' in hand_name or 'trips' in hand_name:
                result['drama_note'] = f"{result['opponent_name']} hit on the river!"

    return result


def format_hand_context_for_prompt(context: Dict[str, Any], player_name: str) -> str:
    """Format the hand context dict into a string for the AI prompt."""
    parts = []

    # Outcome description
    outcome_descriptions = {
        'WON_SHOWDOWN': f"You WON this hand at showdown",
        'WON_BY_FOLD': f"You WON this hand - everyone folded to you",
        'LOST_SHOWDOWN': f"You LOST this hand at showdown",
        'FOLDED': f"You FOLDED this hand",
    }
    parts.append(f"OUTCOME: {outcome_descriptions.get(context['outcome'], context['outcome'])}")

    # Cards section
    if context['player_cards']:
        cards_str = ', '.join(context['player_cards'])
        if context.get('player_hand_name'):
            parts.append(f"YOUR CARDS: {cards_str} ({context['player_hand_name']})")
        else:
            parts.append(f"YOUR CARDS: {cards_str}")

    if context['opponent_name']:
        opp_str = f"OPPONENT: {context['opponent_name']}"
        if context['opponent_cards']:
            opp_str += f" - {', '.join(context['opponent_cards'])}"
        if context['opponent_hand_name']:
            opp_str += f" ({context['opponent_hand_name']})"
        parts.append(opp_str)

    # Board
    if context['community_cards']:
        parts.append(f"BOARD: {', '.join(context['community_cards'])}")

    # Timeline
    if context['timeline']:
        parts.append(f"\nHAND TIMELINE:\n{context['timeline']}")

    # Pot
    parts.append(f"\nFinal pot: ${context['pot_size']}")

    # Drama note
    if context.get('drama_note'):
        parts.append(f"\n{context['drama_note']}")

    return '\n'.join(parts)


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
        formatted_history = format_message_history(game_messages, max_messages=10)
        chat_context = f"\nRecent table talk:\n{formatted_history}" if formatted_history else ""

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
                length_guidance=LENGTH_GUIDANCE.get(length, LENGTH_GUIDANCE['short']),
                intensity_guidance=INTENSITY_GUIDANCE.get(intensity, INTENSITY_GUIDANCE['chill']),
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


@stats_bp.route('/api/game/<game_id>/post-round-chat-suggestions', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def get_post_round_chat_suggestions(game_id):
    """Generate post-round chat suggestions for winner screen reactions.

    Now derives all context from RecordedHand - frontend only needs to send:
    - playerName: human player's name
    - tone: 'gloat', 'humble', 'salty', or 'gracious'
    """
    if not game_state_service.get_game(game_id):
        return jsonify({"error": "Game not found"}), 404

    # Get owner_id for tracking
    current_user = auth_manager.get_current_user()
    owner_id = current_user.get('id') if current_user else None

    data = None
    try:
        data = request.get_json()
        game_data = game_state_service.get_game(game_id)

        # Get hand recorder and memory manager
        memory_manager = game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None

        player_name = data.get('playerName', 'Player')
        tone = data.get('tone', 'gracious')  # gloat, humble, salty, gracious

        # Validate tone
        allowed_tones = {'gloat', 'humble', 'salty', 'gracious'}
        if tone not in allowed_tones:
            logger.warning("Invalid tone value received for post-round chat: %r", tone)
            return jsonify(
                {
                    'error': 'Invalid tone',
                    'allowed_tones': sorted(allowed_tones),
                }
            ), 400

        # Get the most recent completed hand from RecordedHand
        hand_context_str = ""
        outcome = None
        recorded_hand = None

        if memory_manager and memory_manager.hand_recorder.completed_hands:
            recorded_hand = memory_manager.hand_recorder.completed_hands[-1]
            logger.info(f"[PostRound] Got hand from memory: hand #{recorded_hand.hand_number}, hole_cards: {list(recorded_hand.hole_cards.keys())}")
        else:
            # Try loading from database if memory is empty (e.g., after container restart)
            logger.warning(f"[PostRound] No completed hands in memory, trying database...")
            if memory_manager:
                hand_count = memory_manager.hand_count
                if hand_count > 0:
                    loaded_hand = persistence.load_hand_history(game_id, hand_count)
                    if loaded_hand:
                        recorded_hand = loaded_hand
                        logger.info(f"[PostRound] Loaded hand #{hand_count} from database")

        if recorded_hand:
            hand_context = build_hand_context_from_recorded_hand(recorded_hand, player_name)
            hand_context_str = format_hand_context_for_prompt(hand_context, player_name)
            outcome = hand_context.get('outcome')
        else:
            logger.warning(f"[PostRound] No recorded hand available for game {game_id}")
            hand_context_str = "No hand data available."

        # Build the prompt using the new template
        template_name = f'post_round_{tone}'
        prompt = _prompt_manager.render_prompt(
            template_name,
            player_name=player_name,
            hand_context=hand_context_str,
            outcome=outcome or "UNKNOWN",
        )

        if not os.environ.get("OPENAI_API_KEY"):
            logger.warning("No OpenAI API key found, returning fallback suggestions")
            raise ValueError("OpenAI API key not configured")

        # Detailed logging
        logger.info("=" * 80)
        logger.info("[PostRound] === POST-ROUND CHAT REQUEST ===")
        logger.info(f"[PostRound] Player: {player_name}, Tone: {tone}, Outcome: {outcome}")
        logger.info("[PostRound] --- HAND CONTEXT ---")
        logger.info(f"[PostRound]\n{hand_context_str}")
        logger.info("[PostRound] --- FULL PROMPT ---")
        logger.info(f"[PostRound]\n{prompt}")
        logger.info("[PostRound] --- END PROMPT ---")

        client = LLMClient(model=config.FAST_AI_MODEL, reasoning_effort="minimal")
        messages = [
            {"role": "system", "content": "You write short, punchy poker reactions. Keep it natural and under 10 words."},
            {"role": "user", "content": prompt}
        ]

        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.POST_ROUND_CHAT,
            game_id=game_id,
            owner_id=owner_id,
            hand_number=hand_number,
            prompt_template=template_name,
        )
        raw_content = response.content
        logger.info("[PostRound] --- RESPONSE ---")
        logger.info(f"[PostRound]\n{raw_content}")
        logger.info("[PostRound] === END POST-ROUND CHAT ===")
        logger.info("=" * 80)
        result = json.loads(raw_content)

        return jsonify(result)

    except Exception as e:
        logger.error(f"[PostRound] ERROR generating suggestions: {str(e)}")
        logger.exception("[PostRound] Full traceback:")
        tone = data.get('tone', 'gracious') if data else 'gracious'
        fallback_messages = {
            'gloat': ["Too easy.", "Thanks for the chips!"],
            'humble': ["Got lucky there.", "Good game."],
            'salty': ["Unreal.", "Of course."],
            'gracious': ["Nice hand.", "Well played."]
        }
        msgs = fallback_messages.get(tone, fallback_messages['gracious'])

        return jsonify({
            "suggestions": [
                {"text": msgs[0], "tone": tone},
                {"text": msgs[1], "tone": tone}
            ],
            "error": str(e),
            "fallback": True
        })
