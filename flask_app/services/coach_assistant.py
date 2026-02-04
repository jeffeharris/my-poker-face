"""LLM-powered conversational poker coaching.

Provides a CoachAssistant that wraps the core Assistant class
with a poker-coaching system prompt and stat formatting.
"""

import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, TypedDict

from core.llm.assistant import Assistant
from core.llm.tracking import CallType
from core.llm.settings import get_default_provider, get_default_model
from .skill_definitions import get_skill_by_id

logger = logging.getLogger(__name__)

COACH_SYSTEM_PROMPT = """\
You are a professional poker coach helping a player in real-time during a game.

Rules:
- I will provide pre-calculated statistics. Reference them directly - do not recalculate.
- CRITICAL: Only recommend actions from the "Available actions" list. If raise/bet is not listed, don't suggest it (e.g., when all opponents are all-in, you can only call or fold).
- Consider position when giving advice: early position requires tighter ranges, late position allows wider opening ranges.
- Note opponent stack sizes and all-in status — this affects what actions make sense.
- Be concise and actionable. For proactive tips: 1-2 sentences max. For questions: 2-3 short paragraphs.
- Explain the math simply (e.g., "You need 22% equity to call, and you have 45% — easy call")
- Mention opponent tendencies when relevant
- Be encouraging but honest about mistakes
- Use poker terminology naturally but explain concepts for beginners when asked

RESPONSE FORMAT: Always respond with valid JSON in this exact format:
{
  "advice": "Your coaching message here (1-2 sentences for tips, 2-3 paragraphs for questions)",
  "action": "fold" | "check" | "call" | "raise" | null,
  "raise_to": <total chip amount if action is raise, omit otherwise>
}

- Set "action" to the action you recommend from Available actions, or null if you want the player to figure it out themselves.
- For raises, include "raise_to" with the specific total chip amount (not the raise increment).
- If no specific action recommendation, set "action" to null.
"""

LEARN_MODE_PROMPT = """\
You are in TEACHING mode. The player is learning a new poker concept.
- Explain the concept clearly and simply
- Use examples from the current hand to illustrate
- Be encouraging — they're building a new habit
- Keep explanations to 2-3 sentences max
"""

COMPETE_MODE_PROMPT = """\
You are in COMPETE mode. The player already understands the concept.
- Give brief, actionable reminders (1 sentence)
- Focus on execution, not explanation
- Trust that they know the theory
"""

REVIEW_MODE_PROMPT = """\
You are in REVIEW mode. Analyze what just happened.
- Reference the specific skill being practiced
- Note whether the action was correct for the concept
- Give one concrete takeaway
"""

PROACTIVE_TIP_PROMPT = """\
Given these stats, provide a brief 1-2 sentence coaching tip for the player's current situation. \
If a SKILL FOCUS is listed, your tip MUST teach or reinforce that specific concept using the current hand as an example. \
Start with the key action or insight — no preamble, no filler words, no greeting. \
Be direct and actionable.\
"""

HAND_REVIEW_PROMPT = """\
Review this completed hand from the player's perspective. Be concise (3-5 sentences).

Structure your review as:
1. One sentence summarizing what happened
2. One sentence on what the player did well OR the key mistake
3. One sentence of specific advice for similar situations

If SKILL EVALUATIONS FOR THIS HAND are provided above, reference them:
- Cover incorrect evaluations first (1-2 sentences each)
- Then mention correct applications briefly
- Keep each skill's review to 1-2 sentences

If the player provided an explanation, acknowledge their reasoning and compare it with the stats.

Be honest — if they played well, say so briefly. If they made an error, explain what the better play was and why (use pot odds/equity math if relevant). Don't sugarcoat, but don't be harsh either.\
"""


_MODE_PROMPTS = {
    'learn': LEARN_MODE_PROMPT,
    'compete': COMPETE_MODE_PROMPT,
    'review': REVIEW_MODE_PROMPT,
}


class CoachResponse(TypedDict, total=False):
    """Structured coach response with advice and optional action recommendation."""
    advice: str
    action: Optional[str]  # 'fold', 'check', 'call', 'raise', or None
    raise_to: Optional[int]


def _normalize_action(action: Optional[str], available_actions: List[str]) -> Optional[str]:
    """Normalize LLM action output to canonical form and validate against available actions."""
    if not action:
        return None

    action_lower = action.lower().strip()

    # Map variations to canonical actions
    if action_lower in ('bet', 'raise', 'all-in', 'allin', 'all in', 'all_in'):
        normalized = 'raise'
    elif action_lower in ('check', 'pass'):
        normalized = 'check'
    elif action_lower in ('call', 'match'):
        normalized = 'call'
    elif action_lower in ('fold', 'muck'):
        normalized = 'fold'
    else:
        normalized = action_lower

    # Validate against available actions (bet/raise are interchangeable)
    if normalized == 'raise' and ('raise' in available_actions or 'bet' in available_actions):
        return 'raise'
    if normalized in available_actions:
        return normalized

    # Action not available - return None to fall back to GTO
    logger.warning(f"Coach suggested unavailable action '{action}' (normalized: '{normalized}'), ignoring")
    return None


def _parse_coach_response(response: str, coaching_data: Dict) -> CoachResponse:
    """Parse JSON response from coach LLM, falling back gracefully on failure."""
    available_actions = coaching_data.get('available_actions', [])

    try:
        data = json.loads(response)
        advice = data.get('advice', response)
        raw_action = data.get('action')
        raise_to = data.get('raise_to')

        # Normalize and validate action
        action = _normalize_action(raw_action, available_actions)

        # Validate raise_to is a reasonable number (round floats to nearest int)
        if raise_to is not None:
            try:
                raise_to = int(round(float(raise_to)))
                if raise_to <= 0:
                    raise_to = None
            except (ValueError, TypeError):
                raise_to = None

        return CoachResponse(
            advice=advice,
            action=action,
            raise_to=raise_to if action == 'raise' else None,
        )
    except json.JSONDecodeError as e:
        logger.warning(f"Coach response JSON parse failed: {e}, using raw text")
        return CoachResponse(
            advice=response,
            action=None,
            raise_to=None,
        )


class CoachAssistant:
    """LLM-powered poker coaching assistant."""

    def __init__(self, game_id: str, owner_id: str, player_name: str = '',
                 mode: str = '', skill_context: str = ''):
        self.mode = mode
        system_prompt = COACH_SYSTEM_PROMPT
        if player_name:
            system_prompt += f"\nThe player's name is {player_name}. Use their name sparingly — at most once every few messages, never in proactive tips."
        if mode and mode in _MODE_PROMPTS:
            system_prompt += f"\n\n{_MODE_PROMPTS[mode]}"
        if skill_context:
            system_prompt += f"\n\n{skill_context}"
        self._assistant = Assistant(
            system_prompt=system_prompt,
            provider=get_default_provider(),
            model=get_default_model(),
            call_type=CallType.COACHING,
            game_id=game_id,
            owner_id=owner_id,
        )

    def ask(self, question: str, coaching_data: Dict) -> CoachResponse:
        """Answer a coaching question with current game stats as context.

        Returns a CoachResponse dict with 'advice', 'action', and optional 'raise_to'.
        """
        stats_text = _format_stats_for_prompt(coaching_data)
        message = f"Current stats:\n{stats_text}\n\nPlayer question: {question}"
        response = self._assistant.chat(message, json_format=True)
        return _parse_coach_response(response, coaching_data)

    def get_proactive_tip(self, coaching_data: Dict) -> CoachResponse:
        """Generate a brief proactive coaching tip.

        Returns a CoachResponse dict with 'advice', 'action', and optional 'raise_to'.
        """
        stats_text = _format_stats_for_prompt(coaching_data)
        message = f"Current stats:\n{stats_text}\n\n{PROACTIVE_TIP_PROMPT}"
        response = self._assistant.chat(message, json_format=True)
        return _parse_coach_response(response, coaching_data)

    def review_hand(self, hand_context_text: str) -> str:
        """Generate a post-hand review."""
        message = f"Completed hand:\n{hand_context_text}\n\n{HAND_REVIEW_PROMPT}"
        return self._assistant.chat(message)


def _format_stats_for_prompt(data: Dict) -> str:
    """Convert coaching data dict into human-readable text for the LLM."""
    lines = []

    lines.append(f"Phase: {data.get('phase', '?')}")

    # Position with context
    position = data.get('position', '?')
    position_context = data.get('position_context', '')
    if position_context:
        lines.append(f"Position: {position} ({position_context})")
    else:
        lines.append(f"Position: {position}")

    big_blind = data.get('big_blind', 0)
    stack = data.get('stack', 0)
    if big_blind > 0:
        # Small blind is half big blind (truncated), matching poker/poker_game.py:722
        lines.append(f"Blinds: ${big_blind // 2}/${big_blind}")
        lines.append(f"Stack: ${stack} ({stack // big_blind} BB)")
    else:
        lines.append(f"Stack: ${stack}")
    lines.append(f"Pot: ${data.get('pot_total', 0)}")
    lines.append(f"Cost to call: ${data.get('cost_to_call', 0)}")

    # Available actions - critical for valid recommendations
    available = data.get('available_actions', [])
    if available:
        lines.append(f"Available actions: {', '.join(available)}")

    equity = data.get('equity')
    if equity is not None:
        lines.append(f"Equity: {equity * 100:.1f}%")

    pot_odds = data.get('pot_odds')
    if pot_odds is not None:
        lines.append(f"Pot odds: {pot_odds}:1")

    req = data.get('required_equity')
    if req is not None:
        lines.append(f"Required equity to call: {req * 100:.1f}%")

    ev = data.get('ev_call')
    if ev is not None:
        sign = '+' if ev >= 0 else ''
        lines.append(f"EV of calling: {sign}{ev}")

    is_pos = data.get('is_positive_ev')
    if is_pos is not None:
        lines.append(f"Positive EV: {'Yes' if is_pos else 'No'}")

    hs = data.get('hand_strength')
    if hs:
        lines.append(f"Hand: {hs}")

    outs = data.get('outs')
    if outs is not None:
        lines.append(f"Outs: {outs}")

    rec = data.get('recommendation')
    if rec:
        lines.append(f"Recommended action: {rec}")

    opponents = data.get('opponent_stats', [])
    if opponents:
        lines.append("Opponents:")
        for opp in opponents:
            parts = [opp['name']]

            # Stack and all-in status (critical for valid recommendations)
            stack = opp.get('stack')
            is_all_in = opp.get('is_all_in', False)
            if is_all_in:
                parts.append("ALL-IN")
            elif stack is not None:
                parts.append(f"${stack}")

            if opp.get('style') and opp['style'] != 'unknown':
                parts.append(opp['style'])
            if opp.get('vpip') is not None:
                parts.append(f"VPIP={opp['vpip']:.0%}")
            if opp.get('hands_observed', 0) > 0:
                parts.append(f"{opp['hands_observed']} hands")
            lines.append(f"  - {', '.join(parts)}")

    # Progression context — skill focus for coaching
    progression = data.get('progression', {})
    primary_skill = progression.get('primary_skill')
    if primary_skill:
        coaching_mode = progression.get('coaching_mode', '')
        skill_info = progression.get('skill_states', {}).get(primary_skill, {})
        skill_state = skill_info.get('state', '')
        accuracy = skill_info.get('window_accuracy', 0)
        opps = skill_info.get('total_opportunities', 0)

        skill_def = get_skill_by_id(primary_skill)
        skill_name = skill_def.name if skill_def else primary_skill
        skill_desc = skill_def.description if skill_def else ''

        lines.append(f"\nSKILL FOCUS: {skill_name}")
        if skill_desc:
            lines.append(f"Concept: {skill_desc}")
        if skill_state:
            lines.append(f"Player level: {skill_state} ({accuracy:.0%} accuracy, {opps} opportunities)")
        if coaching_mode:
            mode_labels = {
                'learn': 'Teaching — explain the concept using this hand',
                'compete': 'Reinforcing — brief reminder only',
                'review': 'Reviewing — analyze what happened',
            }
            label = mode_labels.get(coaching_mode, coaching_mode)
            lines.append(f"Coaching approach: {label}")

    # Hand timeline (actions so far this hand)
    timeline = _format_hand_timeline(
        data.get('hand_actions', []),
        data.get('hand_community_cards', []),
    )
    if timeline:
        lines.append(f"\nHand timeline:\n{timeline}")

    return '\n'.join(lines)


def _format_hand_timeline(actions: List[Dict], community_cards: List[str]) -> str:
    """Format in-progress hand actions into a readable timeline.

    Args:
        actions: List of action dicts (from RecordedAction.to_dict()).
        community_cards: Community card strings dealt so far.
    """
    if not actions:
        return ''

    phases = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER']
    actions_by_phase: Dict[str, list] = defaultdict(list)
    for a in actions:
        actions_by_phase[a['phase']].append(a)

    phase_cards = {
        'FLOP': community_cards[0:3] if len(community_cards) >= 3 else [],
        'TURN': [community_cards[3]] if len(community_cards) >= 4 else [],
        'RIVER': [community_cards[4]] if len(community_cards) >= 5 else [],
    }

    parts = []
    for phase in phases:
        phase_actions = actions_by_phase.get(phase, [])
        if not phase_actions:
            continue

        cards = phase_cards.get(phase, [])
        header = f"{phase} [{' '.join(cards)}]" if cards else phase

        action_strs = []
        for a in phase_actions:
            name = a['player_name']
            act = a['action']
            amount = a['amount']
            if act in ('fold', 'check'):
                action_strs.append(f"{name} {'folded' if act == 'fold' else 'checked'}")
            elif act == 'call':
                action_strs.append(f"{name} called" + (f" ${amount}" if amount > 0 else ""))
            elif act in ('raise', 'bet'):
                action_strs.append(f"{name} {'raised' if act == 'raise' else 'bet'} ${amount}")
            elif act == 'all_in':
                action_strs.append(f"{name} went all-in (${amount})")
            else:
                action_strs.append(f"{name} {act}")

        parts.append(f"  {header}: {', '.join(action_strs)}")

    return '\n'.join(parts)


def get_or_create_coach_with_mode(
    game_data: dict,
    game_id: str,
    player_name: str = '',
    mode: str = '',
    skill_context: str = '',
) -> CoachAssistant:
    """Get or create a CoachAssistant, replacing it when mode changes."""
    existing = game_data.get('coach_assistant')
    if existing and getattr(existing, 'mode', '') == mode:
        return existing

    owner_id = game_data.get('owner_id', '')
    coach = CoachAssistant(
        game_id, owner_id,
        player_name=player_name,
        mode=mode,
        skill_context=skill_context,
    )
    game_data['coach_assistant'] = coach
    return coach
