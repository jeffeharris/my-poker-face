"""Stats and utility routes."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from core.llm import CallType, LLMClient
from flask_app.handlers.chat_reads import target_social_read
from flask_app.utils.hand_context import (
    build_hand_context_from_recorded_hand,
    format_hand_context_for_prompt,
)
from poker.authorization import get_authorization_service
from poker.config import is_development_mode
from poker.memory.hand_history import RecordedHand
from poker.prompt_manager import PromptManager

from .. import config, extensions
from ..extensions import limiter
from ..services import game_state_service

# Module-level prompt manager instance (with hot-reload in dev mode)
_prompt_manager = PromptManager(enable_hot_reload=is_development_mode())

logger = logging.getLogger(__name__)

stats_bp = Blueprint('stats', __name__)


def _is_admin(user_id: str) -> bool:
    """Check whether a user has admin tools permission."""
    auth_service = get_authorization_service()
    return bool(auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools'))


def _require_game_owner(game_id: str, game_data: dict):
    """Reject if caller doesn't own ``game_id`` and isn't an admin.

    Mirrors the deny semantics of game_routes._authorize_game_access:
    a NULL ``owner_id`` is treated as "not owned by this user" and
    rejected with 403 unless the caller is an admin. Returns a Flask
    response tuple on rejection, or ``None`` to continue.
    """
    user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    user_id = user.get('id') if user else ''
    if not user_id:
        return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401

    owner_id = (game_data or {}).get('owner_id')
    if owner_id is None:
        owner_info = extensions.game_repo.get_game_owner_info(game_id)
        if owner_info is not None:
            owner_id = owner_info.get('owner_id')
            if game_data is not None and owner_id is not None:
                game_data['owner_id'] = owner_id
                game_data.setdefault('owner_name', owner_info.get('owner_name'))

    if owner_id != user_id and not _is_admin(user_id):
        return jsonify({'error': 'Permission denied'}), 403
    return None


# Module-level constants for prompt guidance
LENGTH_GUIDANCE = {
    'short': 'Keep it VERY short - under 8 words.',
    'long': 'Can be 1-2 full sentences.',
}
INTENSITY_GUIDANCE = {
    'chill': 'Keep it playful and light.',
    'spicy': 'Go hard. No filter. Cut deep.',
    # Sarcastic = "don't read it literally." Say the opposite of what you mean,
    # dry and deadpan: a backhanded compliment on a warm tone, mock-friendly
    # ribbing on a hostile one. The irony carries the edge, not volume.
    'sarcastic': 'Be dry and sarcastic — say the opposite of what you mean. '
    'Deadpan, ironic, a little theatrical. Let the subtext do the cutting.',
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


@stats_bp.route('/api/career-stats', methods=['GET'])
def get_career_stats():
    """Get career stats for the authenticated user."""
    current_user = extensions.auth_manager.get_current_user()
    if not current_user:
        return jsonify({'error': 'Not authenticated'}), 401

    owner_id = current_user.get('id')
    if not owner_id:
        return jsonify({'error': 'No user ID found'}), 400

    stats = extensions.tournament_repo.get_career_stats(owner_id)
    history = extensions.tournament_repo.get_tournament_history(owner_id, limit=10)
    eliminated = extensions.tournament_repo.get_eliminated_personalities(owner_id)

    return jsonify(
        {'stats': stats, 'recent_tournaments': history, 'eliminated_personalities': eliminated}
    )


@stats_bp.route('/api/journey', methods=['GET'])
def get_journey():
    """The player's CIRCUIT (cash-mode) career story: one narrative beat per
    session, combined into an arc. Scoped to the owner's cash sessions — the
    actual career — not tournaments or quick-play. Facts are deterministic
    (hand_history). Pass ?voiced=1 to also get the LLM-narrated session beats +
    arc (grounded in those facts, fail-soft)."""
    current_user = extensions.auth_manager.get_current_user()
    if not current_user:
        return jsonify({'error': 'Not authenticated'}), 401
    owner_id = current_user.get('id')
    if not owner_id:
        return jsonify({'error': 'No user ID found'}), 400

    voiced = request.args.get('voiced') in ('1', 'true', 'yes')

    from cash_mode.stakes_ladder import STAKES_LADDER
    from poker.memory.hand_history import RecordedHand
    from poker.memory.journey import (
        ARC_CONTEXT,
        cash_pnl,
        journey_arc_facts,
        merge_counts,
        own_buy_in,
        preflop_counts,
        preflop_rates,
        session_facts,
        session_facts_text,
        session_result,
        stack_curve,
        summarize_session,
        voice_over,
    )
    from poker.repositories.hand_equity_repository import HandEquityRepository

    equity_repo = HandEquityRepository(extensions.hand_history_repo.db_path)

    # Circuit = cash mode. The owner's cash sessions are the career; session_id
    # IS the game_id ('cash-*'). Tournaments/quick-play are excluded by design.
    cash_sessions = extensions.cash_session_repo.list_for_owner(owner_id, limit=12)
    sessions = []
    preflop_parts = []  # raw preflop counts per session, summed into the overall
    player = None
    for cs in cash_sessions:
        try:
            rows = extensions.hand_history_repo.load_hand_history(cs.session_id)
            if not rows:
                continue
            hands = [RecordedHand.from_dict(r) for r in rows]
            human = next((p.name for h in hands for p in h.players if p.is_human), None)
            if not human:
                continue
            # Drama ranking: big blind (from the stake) sharpens the pot-size
            # signal; the stored equity history drives swing/lead-change/suckout.
            big_blind = (STAKES_LADDER.get(cs.stake_label or '') or {}).get('big_blind')
            equity_by_hand = equity_repo.get_equities_for_game(cs.session_id)
            facts = session_facts(hands, human, big_blind=big_blind, equity_by_hand=equity_by_hand)
            if facts['hands_played'] == 0:
                continue
            player = player or human
            # `net` = the TABLE result (how the stack ran — the real poker P&L,
            # negative on a staked loss the backer absorbed). `pocket` = what
            # actually hit the player's own bankroll after the backer settled.
            net = session_result(
                final_chips_at_table=cs.final_chips_at_table,
                total_buy_in=cs.total_buy_in,
                sponsor_principal=cs.sponsor_principal,
                ended_at=cs.ended_at,
            )
            pocket = cash_pnl(
                total_buy_in=cs.total_buy_in,
                sponsor_principal=cs.sponsor_principal,
                player_take_home=cs.player_take_home,
                ended_at=cs.ended_at,
            )
            buy_in = own_buy_in(cs.total_buy_in, cs.sponsor_principal)
            # The named room this session was played in (e.g. "Hotel Mezzanine").
            # Fail-soft: a missing table just drops the name, never the session.
            table_name = None
            if cs.cash_table_id:
                try:
                    table = extensions.cash_table_repo.load_table(
                        cs.cash_table_id, sandbox_id=cs.sandbox_id
                    )
                    table_name = table.name if table else None
                except Exception:
                    logger.debug(
                        "journey: could not load table name for %s",
                        cs.cash_table_id,
                        exc_info=True,
                    )
                    table_name = None
            summary = summarize_session(
                human,
                hands_played=facts['hands_played'],
                hands_won=facts['hands_won'],
                biggest_pot_won=facts['biggest_pot_won'],
                net=net,
                buy_in=buy_in if net is not None else None,
                take_home=cs.player_take_home if net is not None else None,
                stake_label=cs.stake_label,
                staked=bool(cs.is_staked),
                pocket=pocket,
            )
            stats = {
                'hands_played': facts['hands_played'],
                'hands_won': facts['hands_won'],
                'biggest_pot_won': facts['biggest_pot_won'],
                'net_chips': net,  # table result (the session's poker P&L)
                'pocket': pocket,  # what hit the player's own bankroll
                'staked': bool(cs.is_staked),
                'buy_in': buy_in,
                'take_home': cs.player_take_home,
                'in_progress': net is None,
            }
            counts = preflop_counts(hands, human)
            preflop_parts.append(counts)
            entry = {
                'game_id': cs.session_id,
                'summary': summary,
                'stats': stats,
                'beats': facts['beats'],
                # Per-hand chip stack across the session, for the sparkline.
                'stack_curve': stack_curve(hands, human),
                # Session header: which room, what stakes, when.
                'stake_label': cs.stake_label,
                'table_name': table_name,
                'started_at': cs.started_at.isoformat() if cs.started_at else None,
                # VPIP / PFR / starting-hand quality for the session.
                'preflop': preflop_rates(counts),
            }
            # Stash the grounded voice input ON the entry (stripped before the
            # response) so it can never drift out of alignment with `sessions`.
            entry['_voice'] = (session_facts_text(summary, facts['beats']), human)
            sessions.append(entry)
        except Exception:  # noqa: BLE001 — one bad session never sinks the whole story
            logger.warning("journey: skipped session %s", cs.session_id, exc_info=True)

    # Voicing: narrate every session CONCURRENTLY (each is an independent LLM
    # call), then narrate the arc once over all of them. Resolve the tier on the
    # request thread — the DB-backed settings accessors aren't safe in workers —
    # and thread the provider/model into each call.
    arc_beat = None
    if voiced and sessions:
        from concurrent.futures import ThreadPoolExecutor

        from core.llm import settings as llm_settings

        prov = llm_settings.get_assistant_provider()
        mdl = llm_settings.get_assistant_model()

        def _voice_session(entry):
            facts_text, hero = entry['_voice']
            return voice_over(facts_text, hero=hero, provider=prov, model=mdl)

        with ThreadPoolExecutor(max_workers=min(6, len(sessions))) as pool:
            beats = list(pool.map(_voice_session, sessions))
        for entry, beat in zip(sessions, beats, strict=False):
            entry['beat'] = beat

        # The arc = the session beats combined, in CHRONOLOGICAL order (sessions
        # arrive newest-first), each anchored with its date, so the narration
        # reads as a trajectory instead of starting from the latest session.
        # Undated sessions sort LAST ('~' > any digit) so a missing date never
        # masquerades as the earliest beat.
        chrono = sorted(sessions, key=lambda s: s.get('started_at') or '~')
        segments = [
            f"{(s.get('started_at') or '')[:10]}: {s.get('beat') or s['summary']}" for s in chrono
        ]
        arc_beat = voice_over(
            "\n\n".join(segments),
            hero=player or 'the player',
            length="3-5 sentences",
            provider=prov,
            model=mdl,
            context=ARC_CONTEXT,
        )

    arc_facts = journey_arc_facts([s['stats'] for s in sessions]) if sessions else None
    preflop_overall = preflop_rates(merge_counts(preflop_parts)) if preflop_parts else None
    for entry in sessions:  # drop the transient voice input before serializing
        entry.pop('_voice', None)
    return jsonify(
        {
            'player': player,
            'sessions': sessions,
            'arc': arc_facts,
            'arc_beat': arc_beat,
            'preflop_overall': preflop_overall,
        }
    )


@stats_bp.route('/api/journey/highlights', methods=['GET'])
def get_journey_highlights():
    """Lightweight career-highlights summary for the lobby card — aggregated
    straight from the cash_sessions ledger rows (no hand_history / equity
    loading, unlike /api/journey). Cheap enough to fetch on every lobby load."""
    current_user = extensions.auth_manager.get_current_user()
    if not current_user:
        return jsonify({'error': 'Not authenticated'}), 401
    owner_id = current_user.get('id')
    if not owner_id:
        return jsonify({'error': 'No user ID found'}), 400

    from poker.memory.journey import session_result

    rows = extensions.cash_session_repo.list_for_owner(owner_id, limit=500)
    played = [cs for cs in rows if (cs.hands_played or 0) > 0]
    # Use the TABLE result (session_result), matching /api/journey — so the lobby
    # card's net agrees with the story it opens (cash_pnl/pocket would read a
    # staked loss as flat and disagree with the per-session story).
    nets = [
        n
        for cs in played
        if (
            n := session_result(
                final_chips_at_table=cs.final_chips_at_table,
                total_buy_in=cs.total_buy_in,
                sponsor_principal=cs.sponsor_principal,
                ended_at=cs.ended_at,
            )
        )
        is not None
    ]
    return jsonify(
        {
            'has_story': bool(played),
            'sessions': len(played),
            'winning_sessions': sum(1 for n in nets if n > 0),
            'total_hands': sum(cs.hands_played or 0 for cs in played),
            'biggest_pot': max((cs.biggest_pot_won or 0 for cs in played), default=0),
            'total_net_chips': sum(nets),
            'best_session_net': max(nets, default=0),
        }
    )


@stats_bp.route('/api/models', methods=['GET'])
@limiter.limit("30/minute")
def get_available_models():
    """Get available models for game configuration.

    SBP-003: serves the curated static ``AVAILABLE_MODELS``. Previously this
    public, unauthenticated route made a live ``OpenAI().models.list()`` call
    with the server API key on every request — an outbound-provider abuse
    surface (latency, quota/throttle consumption) with no auth, cache, or limit.
    The current frontend uses the DB-backed ``/api/user-models``; this route is
    kept only for legacy callers and no longer touches a provider or server key.
    """
    from core.llm import AVAILABLE_MODELS, DEFAULT_REASONING_EFFORT

    return jsonify(
        {
            'success': True,
            'models': AVAILABLE_MODELS,
            'default_model': config.get_default_model(),
            'reasoning_levels': ['minimal', 'low', 'medium', 'high'],
            'default_reasoning': DEFAULT_REASONING_EFFORT,
        }
    )


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
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({"error": "Game not found"}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    # Get owner_id for tracking
    current_user = extensions.auth_manager.get_current_user()
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

        client = LLMClient(
            model=config.get_fast_model(),
            provider=config.get_fast_provider(),
            # Quick suggestions the user is waiting on: minimal reasoning (the
            # non-reasoning variant on toggleable FAST models) + a bounded timeout
            # so a provider stall can't hang the request on the 600s httpx default.
            reasoning_effort="minimal",
            default_timeout=config.FAST_LLM_TIMEOUT_SECONDS,
        )
        messages = [
            {
                "role": "system",
                "content": "You are a friendly poker player giving brief chat suggestions.",
            },
            {"role": "user", "content": prompt},
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
        logger.warning(f"Error generating chat suggestions: {str(e)}")
        return jsonify(
            {
                "suggestions": [
                    {"text": "Nice play!", "type": "reaction"},
                    {"text": "Interesting move", "type": "strategic"},
                    {"text": "Let's go!", "type": "social"},
                ]
            }
        )


@stats_bp.route('/api/game/<game_id>/targeted-chat-suggestions', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def get_targeted_chat_suggestions(game_id):
    """Generate targeted chat suggestions to engage specific AI players."""
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({"error": "Game not found"}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    # Get owner_id for tracking
    current_user = extensions.auth_manager.get_current_user()
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

        # Map tones to template names. The live palette is the six trait-keyed
        # intents (intimidate/dare/trash_talk/props/flatter/befriend); the
        # retired hostile near-duplicates (tilt/bait/needle/goad) keep their
        # templates for back-compat with stored prefs / in-flight requests.
        template_map = {
            'intimidate': 'quick_chat_intimidate',
            'dare': 'quick_chat_dare',
            'trash_talk': 'quick_chat_trash_talk',
            'props': 'quick_chat_props',
            'flatter': 'quick_chat_flatter',
            'befriend': 'quick_chat_befriend',
            'bluff': 'quick_chat_bluff',
            # Retired hostiles (folded into trash_talk):
            'tilt': 'quick_chat_tilt',
            'bait': 'quick_chat_bait',
            'needle': 'quick_chat_needle',
            'goad': 'quick_chat_goad',
        }

        # Tone descriptions for table talk (no target)
        tone_descriptions = {
            'intimidate': 'Project dominance — make them think twice about tangling with you.',
            'dare': 'Dare the table to act. Call them out for hesitating.',
            'trash_talk': 'Needle the table. Be cutting.',
            'props': 'Tip your cap to the table — genuine respect for the play.',
            'flatter': 'Lay it on thick — over-the-top praise, sincere or not.',
            'befriend': 'Be warm to the table.',
            'bluff': 'Give false tells about your hand.',
            # Retired hostiles:
            'tilt': 'Needle the table. Be cutting.',
            'bait': 'Sound mildly frustrated or grudgingly impressed by the competition.',
            'needle': 'Question what just happened. Be subtle.',
            'goad': 'Dare the table to act.',
        }

        context_parts = []
        context_parts.append(f"Game phase: {str(state_machine.current_phase).split('.')[-1]}")
        context_parts.append(f"Pot size: ${game_state.pot['total']}")

        last_action = data.get('lastAction')
        if last_action:
            action_text = (
                f"{last_action.get('player', 'Someone')} just {last_action.get('type', 'acted')}"
            )
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
                personality = extensions.personality_generator.get_personality(target_player)
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

        # Disposition-aware read: tilt the suggestions toward what would
        # actually land on this specific character. Folded into context_str
        # because that's the var the targeted templates render.
        social_read = target_social_read(game_data, target_player)
        if social_read:
            context_str = f"{context_str}\nOpponent read: {social_read}"

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
                length_guidance=LENGTH_GUIDANCE.get(length, LENGTH_GUIDANCE['short']),
                intensity_guidance=INTENSITY_GUIDANCE.get(intensity, INTENSITY_GUIDANCE['chill']),
            )

        if not os.environ.get("OPENAI_API_KEY"):
            logger.warning("No OpenAI API key found, returning fallback suggestions")
            raise ValueError("OpenAI API key not configured")

        # Detailed logging for debugging/iteration
        logger.info("=" * 80)
        logger.info("[QuickChat] === QUICK CHAT REQUEST ===")
        logger.info(
            f"[QuickChat] Target: {target_player}, Tone: {tone}, Length: {length}, Intensity: {intensity}, Player: {player_name}"
        )
        logger.info(f"[QuickChat] Game context: {context_str}")
        logger.info(f"[QuickChat] Game situation:\n{game_situation}")
        logger.info(f"[QuickChat] Target context: {target_context}")
        logger.info(f"[QuickChat] Chat context: {chat_context}")
        logger.info("[QuickChat] --- FULL PROMPT ---")
        logger.info(f"[QuickChat]\n{prompt}")
        logger.info("[QuickChat] --- END PROMPT ---")

        client = LLMClient(
            model=config.get_fast_model(),
            provider=config.get_fast_provider(),
            reasoning_effort="minimal",
            # Bounded so a provider stall can't hang the user's request on the
            # 600s shared-httpx default.
            default_timeout=config.FAST_LLM_TIMEOUT_SECONDS,
        )
        messages = [
            {
                "role": "system",
                "content": "You write sharp, witty poker banter that responds to the actual conversation. Never generic - always specific callbacks, quotes, or reactions to what just happened. Short and punchy.",
            },
            {"role": "user", "content": prompt},
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
            'bait': ["Nice bet.", "You've been running hot."],
            'needle': ["Interesting timing...", "You sure about that?"],
            'goad': ["Prove it.", "You wouldn't dare."],
            'bluff': ["I should've folded...", "This hand is killing me."],
            'befriend': ["Good game so far.", "Respect the play."],
            'props': ["Respect. Nicely played.", "That was a sharp read."],
            'flatter': ["You're a genius at this.", "Best player I've ever seen."],
        }
        tone = data.get('tone', 'goad') if data else 'goad'
        msgs = fallback_messages.get(tone, fallback_messages['goad'])

        return jsonify(
            {
                "suggestions": [{"text": msgs[0], "tone": tone}, {"text": msgs[1], "tone": tone}],
                "targetPlayer": target,
                "error": str(e),
                "fallback": True,
            }
        )


@stats_bp.route('/api/game/<game_id>/post-round-chat-suggestions', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def get_post_round_chat_suggestions(game_id):
    """Generate post-round chat suggestions for winner screen reactions.

    Now derives all context from RecordedHand - frontend only needs to send:
    - playerName: human player's name
    - tone: 'gloat', 'humble', 'salty', or 'gracious'
    """
    game_data = game_state_service.get_game(game_id)
    if not game_data:
        return jsonify({"error": "Game not found"}), 404

    forbidden = _require_game_owner(game_id, game_data)
    if forbidden:
        return forbidden

    # Get owner_id for tracking
    current_user = extensions.auth_manager.get_current_user()
    owner_id = current_user.get('id') if current_user else None

    data = None
    try:
        data = request.get_json()
        game_data = game_state_service.get_game(game_id)

        # Get hand recorder and memory manager
        memory_manager = game_data.get('memory_manager')
        hand_number = memory_manager.hand_count if memory_manager else None

        player_name = data.get('playerName', 'Player')
        tone = data.get('tone', 'gracious')
        # Delivery register. Post-round tones encode their own intensity, so
        # there's no chill/spicy here — but `sarcastic` rides on the warm ones
        # (gracious/humble/commiserate). Default empty → no extra guidance,
        # so sincere post-round text is unchanged.
        intensity = data.get('intensity')

        # Validate tone. After a WIN: gloat/gracious/humble/commiserate.
        # After a LOSS: salty/props/cry_luck/vow. props is shared.
        allowed_tones = {
            'gloat',
            'gracious',
            'humble',
            'commiserate',
            'salty',
            'props',
            'cry_luck',
            'vow',
        }
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
            logger.info(
                f"[PostRound] Got hand from memory: hand #{recorded_hand.hand_number}, hole_cards: {list(recorded_hand.hole_cards.keys())}"
            )
        else:
            # Try loading from database if memory is empty (e.g., after container restart)
            logger.warning("[PostRound] No completed hands in memory, trying database...")
            if memory_manager:
                hand_count = memory_manager.hand_count
                if hand_count > 0:
                    # load_single_hand returns Optional[Dict] for exactly this hand;
                    # wrap with RecordedHand.from_dict so downstream code (which
                    # accesses .community_cards etc.) gets the right object.
                    loaded_dict = extensions.hand_history_repo.load_single_hand(game_id, hand_count)
                    if loaded_dict:
                        recorded_hand = RecordedHand.from_dict(loaded_dict)
                        logger.info(f"[PostRound] Loaded hand #{hand_count} from database")

        if recorded_hand:
            hand_context = build_hand_context_from_recorded_hand(recorded_hand, player_name)
            # Pull big_blind from the live game state when available so amounts
            # render in BB (matches hybrid-bot decision prompts); the narrator
            # falls back to dollars when omitted.
            big_blind = None
            state_machine = game_data.get('state_machine') if game_data else None
            if state_machine is not None:
                live_state = getattr(state_machine, 'game_state', None)
                if live_state is not None:
                    big_blind = getattr(live_state, 'current_ante', None)
            hand_context_str = format_hand_context_for_prompt(
                hand_context,
                player_name,
                recorded_hand=recorded_hand,
                big_blind=big_blind,
            )
            outcome = hand_context.get('outcome')
        else:
            logger.warning(f"[PostRound] No recorded hand available for game {game_id}")
            hand_context_str = "No hand data available."

        # Build the prompt using the new template. intensity_guidance is passed
        # to every post-round template but only the sarcasm-able ones
        # (gracious/humble/commiserate) interpolate it; the rest ignore the
        # extra kwarg (str.format drops unused keys). Empty default keeps
        # sincere text unchanged.
        template_name = f'post_round_{tone}'
        prompt = _prompt_manager.render_prompt(
            template_name,
            player_name=player_name,
            hand_context=hand_context_str,
            intensity_guidance=INTENSITY_GUIDANCE.get(intensity, ''),
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

        client = LLMClient(
            model=config.get_fast_model(),
            provider=config.get_fast_provider(),
            reasoning_effort="minimal",
            # Bounded so a provider stall can't hang the user's request on the
            # 600s shared-httpx default.
            default_timeout=config.FAST_LLM_TIMEOUT_SECONDS,
        )
        messages = [
            {
                "role": "system",
                "content": "You write short, punchy poker reactions. Keep it natural and under 10 words.",
            },
            {"role": "user", "content": prompt},
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
            'gracious': ["Nice hand.", "Well played."],
        }
        msgs = fallback_messages.get(tone, fallback_messages['gracious'])

        return jsonify(
            {
                "suggestions": [{"text": msgs[0], "tone": tone}, {"text": msgs[1], "tone": tone}],
                "error": str(e),
                "fallback": True,
            }
        )
