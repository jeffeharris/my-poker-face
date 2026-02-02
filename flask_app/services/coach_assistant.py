"""LLM-powered conversational poker coaching.

Provides a CoachAssistant that wraps the core Assistant class
with a poker-coaching system prompt and stat formatting.
"""

import logging
from collections import defaultdict
from typing import Dict, List

from core.llm.assistant import Assistant
from core.llm.tracking import CallType
from core.llm.settings import get_default_provider, get_default_model

logger = logging.getLogger(__name__)

COACH_SYSTEM_PROMPT = """\
You are a professional poker coach helping a player in real-time during a game.

Rules:
- I will provide pre-calculated statistics. Reference them directly - do not recalculate.
- Be concise and actionable. For proactive tips: 1-2 sentences max. For questions: 2-3 short paragraphs.
- Explain the math simply (e.g., "You need 22% equity to call, and you have 45% — easy call")
- Mention opponent tendencies when relevant
- Be encouraging but honest about mistakes
- Use poker terminology naturally but explain concepts for beginners when asked
"""

PROACTIVE_TIP_PROMPT = """\
Given these stats, provide a brief 1-2 sentence coaching tip for the player's current situation. \
Focus on the most important decision factor right now. Be direct and actionable.\
"""

HAND_REVIEW_PROMPT = """\
Review this completed hand from the player's perspective. Be concise (3-5 sentences).

Structure your review as:
1. One sentence summarizing what happened
2. One sentence on what the player did well OR the key mistake
3. One sentence of specific advice for similar situations

Be honest — if they played well, say so briefly. If they made an error, explain what the better play was and why (use pot odds/equity math if relevant). Don't sugarcoat, but don't be harsh either.\
"""


class CoachAssistant:
    """LLM-powered poker coaching assistant."""

    def __init__(self, game_id: str, owner_id: str, player_name: str = ''):
        system_prompt = COACH_SYSTEM_PROMPT
        if player_name:
            system_prompt += f"\nThe player's name is {player_name}. Address them by name occasionally."
        self._assistant = Assistant(
            system_prompt=system_prompt,
            provider=get_default_provider(),
            model=get_default_model(),
            call_type=CallType.COACHING,
            game_id=game_id,
            owner_id=owner_id,
        )

    def ask(self, question: str, coaching_data: Dict) -> str:
        """Answer a coaching question with current game stats as context."""
        stats_text = _format_stats_for_prompt(coaching_data)
        message = f"Current stats:\n{stats_text}\n\nPlayer question: {question}"
        return self._assistant.chat(message)

    def get_proactive_tip(self, coaching_data: Dict) -> str:
        """Generate a brief proactive coaching tip."""
        stats_text = _format_stats_for_prompt(coaching_data)
        message = f"Current stats:\n{stats_text}\n\n{PROACTIVE_TIP_PROMPT}"
        return self._assistant.chat(message)

    def review_hand(self, hand_context_text: str) -> str:
        """Generate a post-hand review."""
        message = f"Completed hand:\n{hand_context_text}\n\n{HAND_REVIEW_PROMPT}"
        return self._assistant.chat(message)


def _format_stats_for_prompt(data: Dict) -> str:
    """Convert coaching data dict into human-readable text for the LLM."""
    lines = []

    lines.append(f"Phase: {data.get('phase', '?')}")
    lines.append(f"Position: {data.get('position', '?')}")
    lines.append(f"Stack: ${data.get('stack', 0)}")
    lines.append(f"Pot: ${data.get('pot_total', 0)}")
    lines.append(f"Cost to call: ${data.get('cost_to_call', 0)}")

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
            if opp.get('style') and opp['style'] != 'unknown':
                parts.append(opp['style'])
            if opp.get('vpip') is not None:
                parts.append(f"VPIP={opp['vpip']:.0%}")
            if opp.get('hands_observed', 0) > 0:
                parts.append(f"{opp['hands_observed']} hands")
            lines.append(f"  - {', '.join(parts)}")

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


def get_or_create_coach(game_data: dict, game_id: str,
                        player_name: str = '') -> CoachAssistant:
    """Get or lazily create the CoachAssistant for a game."""
    if 'coach_assistant' not in game_data:
        owner_id = game_data.get('owner_id', '')
        game_data['coach_assistant'] = CoachAssistant(game_id, owner_id, player_name=player_name)
    return game_data['coach_assistant']
