"""Layer 3: LLM Expression Generator.

Takes an already-decided action (from Layer 1+2) and generates personality
flavored narration: dramatic_sequence, inner_monologue, hand_strategy,
bluff_likelihood.

Critical invariant: the LLM never influences action selection. By the time
this code runs, the action is already in the decision dict; we only fill
in narrative fields.

Expression failures are isolated — any LLM error returns empty narration
fields. The action proceeds regardless.
"""

import json
import logging
from typing import Any, Dict, Optional

from .expression_context import ExpressionContext

logger = logging.getLogger(__name__)


# Empty narration returned on any failure — keeps the game flowing.
_EMPTY_NARRATION: Dict[str, Any] = {
    'dramatic_sequence': [],
    'inner_monologue': '',
    'hand_strategy': '',
    'bluff_likelihood': 0,
}


def _empty() -> Dict[str, Any]:
    """Fresh copy of the empty narration dict."""
    return {
        'dramatic_sequence': [],
        'inner_monologue': '',
        'hand_strategy': '',
        'bluff_likelihood': 0,
    }


class ExpressionGenerator:
    """Generates LLM narration for a decided poker action.

    Args:
        llm_client: An object with a .complete(messages, json_format, call_type, ...)
            method that returns an object with a .content attribute (str). The
            actual LLMClient from core.llm satisfies this duck-typed protocol.
        prompt_manager: A PromptManager instance for rendering the
            decision_expression template.
        drama_contexts: Optional dict mapping drama_level -> response style string.
            Defaults to poker.prompt_manager.DRAMA_CONTEXTS.
        tone_modifiers: Optional dict mapping drama_tone -> modifier suffix.
            Defaults to poker.prompt_manager.TONE_MODIFIERS.
    """

    def __init__(
        self,
        llm_client,
        prompt_manager,
        drama_contexts: Optional[Dict[str, str]] = None,
        tone_modifiers: Optional[Dict[str, str]] = None,
    ):
        self.llm_client = llm_client
        self.prompt_manager = prompt_manager
        if drama_contexts is None or tone_modifiers is None:
            from poker.prompt_manager import DRAMA_CONTEXTS, TONE_MODIFIERS
            drama_contexts = drama_contexts or DRAMA_CONTEXTS
            tone_modifiers = tone_modifiers or TONE_MODIFIERS
        self.drama_contexts = drama_contexts
        self.tone_modifiers = tone_modifiers

    def generate(
        self,
        context: ExpressionContext,
        call_type=None,
        game_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate narration for the decided action.

        On any failure (LLM error, malformed JSON, missing template), returns
        empty narration fields without raising. The caller's decision dict is
        unaffected; the game proceeds.
        """
        try:
            prompt = self._render_prompt(context)
        except Exception as e:
            logger.warning(
                f"[EXPRESSION] Failed to render prompt for "
                f"{context.personality_name}: {e}"
            )
            return _empty()

        try:
            response = self.llm_client.complete(
                messages=[{'role': 'user', 'content': prompt}],
                json_format=True,
                call_type=call_type,
                game_id=game_id,
                player_name=context.personality_name,
            )
        except Exception as e:
            logger.warning(
                f"[EXPRESSION] LLM call failed for "
                f"{context.personality_name}: {e}"
            )
            return _empty()

        return self._parse_response(response, context)

    def _render_prompt(self, ctx: ExpressionContext) -> str:
        template = self.prompt_manager.get_template('decision_expression')
        drama_context = self.drama_contexts.get(ctx.drama_level, '')
        tone_modifier = self.tone_modifiers.get(ctx.drama_tone, '')

        raise_clause = (
            f" (to {ctx.raise_to})"
            if ctx.action_taken in ('raise', 'all_in') and ctx.raise_to
            else ''
        )

        return template.render(
            personality_name=ctx.personality_name,
            play_style=ctx.play_style,
            default_attitude=ctx.default_attitude,
            verbal_tics=', '.join(ctx.verbal_tics) if ctx.verbal_tics else '(none)',
            physical_tics=', '.join(ctx.physical_tics) if ctx.physical_tics else '(none)',
            hand_cards=', '.join(ctx.hand_cards) if ctx.hand_cards else '(hidden)',
            community_cards=', '.join(ctx.community_cards) if ctx.community_cards else '(none)',
            phase=ctx.phase,
            pot_size=ctx.pot_size,
            opponent_count=ctx.opponent_count,
            action_taken=ctx.action_taken,
            raise_clause=raise_clause,
            emotional_state=ctx.emotional_state,
            emotional_severity=ctx.emotional_severity,
            drama_context=drama_context,
            tone_modifier=tone_modifier,
        )

    def _parse_response(self, response, ctx: ExpressionContext) -> Dict[str, Any]:
        """Extract narration fields from LLM response. Fail-safe on bad JSON."""
        content = getattr(response, 'content', None)
        if not content:
            return _empty()

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"[EXPRESSION] Bad JSON from LLM for "
                f"{ctx.personality_name}: {e}"
            )
            return _empty()

        if not isinstance(data, dict):
            return _empty()

        sequence = data.get('dramatic_sequence', [])
        if not isinstance(sequence, list):
            sequence = []

        bluff = data.get('bluff_likelihood', 0)
        try:
            bluff = max(0, min(100, int(bluff)))
        except (ValueError, TypeError):
            bluff = 0

        return {
            'dramatic_sequence': [str(beat) for beat in sequence],
            'inner_monologue': str(data.get('inner_monologue', '')),
            'hand_strategy': str(data.get('hand_strategy', '')),
            'bluff_likelihood': bluff,
        }
