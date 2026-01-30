"""LLM-powered conversational poker coaching.

Provides a CoachAssistant that wraps the core Assistant class
with a poker-coaching system prompt and stat formatting.
"""

import logging
from typing import Dict, Optional

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


class CoachAssistant:
    """LLM-powered poker coaching assistant."""

    def __init__(self, game_id: str, owner_id: str):
        self._assistant = Assistant(
            system_prompt=COACH_SYSTEM_PROMPT,
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

    return '\n'.join(lines)


def get_or_create_coach(game_data: dict, game_id: str) -> CoachAssistant:
    """Get or lazily create the CoachAssistant for a game."""
    if 'coach_assistant' not in game_data:
        owner_id = game_data.get('owner_id', '')
        game_data['coach_assistant'] = CoachAssistant(game_id, owner_id)
    return game_data['coach_assistant']
