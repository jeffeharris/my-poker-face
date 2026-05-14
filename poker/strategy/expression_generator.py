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
from typing import Any, Dict, Optional, Tuple

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
        cleanup_client=None,
    ):
        self.llm_client = llm_client
        self.prompt_manager = prompt_manager
        if drama_contexts is None or tone_modifiers is None:
            from poker.prompt_manager import DRAMA_CONTEXTS, TONE_MODIFIERS
            drama_contexts = drama_contexts or DRAMA_CONTEXTS
            tone_modifiers = tone_modifiers or TONE_MODIFIERS
        self.drama_contexts = drama_contexts
        self.tone_modifiers = tone_modifiers
        # Fast-tier client for post-LLM beat normalization. Created lazily
        # on first use so tests / callers that never produce malformed
        # beats never instantiate a second client.
        self._cleanup_client = cleanup_client

    def _get_cleanup_client(self):
        """Lazy fast-tier LLM client for dramatic_sequence cleanup."""
        if self._cleanup_client is not None:
            return self._cleanup_client
        try:
            from core.llm import LLMClient
            from core.llm.settings import get_fast_provider, get_fast_model
            self._cleanup_client = LLMClient(
                provider=get_fast_provider(),
                model=get_fast_model(),
            )
        except Exception as e:
            logger.warning(
                f"[EXPRESSION] Could not build fast cleanup client; "
                f"falling back to narration client: {e}"
            )
            self._cleanup_client = self.llm_client
        return self._cleanup_client

    def generate(
        self,
        context: ExpressionContext,
        call_type=None,
        game_id: Optional[str] = None,
        capture_id_holder: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Generate narration for the decided action.

        On any failure (LLM error, malformed JSON, missing template), returns
        empty narration fields without raising. The caller's decision dict is
        unaffected; the game proceeds.

        If capture_id_holder is provided as a single-element list, the
        prompt_captures row id is written to capture_id_holder[0] after a
        successful capture, letting the caller link decision_analysis to the
        narration capture.
        """
        try:
            system_text, user_text = self._render_prompt(context)
        except Exception as e:
            logger.warning(
                f"[EXPRESSION] Failed to render prompt for "
                f"{context.personality_name}: {e}"
            )
            return _empty()

        capture_enricher = None
        if capture_id_holder is not None:
            def capture_enricher(capture_data):
                capture_data['_on_captured'] = (
                    lambda cid: capture_id_holder.__setitem__(0, cid)
                )
                return capture_data

        # Default the tracked call_type to COMMENTARY so narration calls
        # land in api_usage under a meaningful category instead of UNKNOWN.
        if call_type is None:
            from core.llm.tracking import CallType
            call_type = CallType.COMMENTARY

        try:
            response = self.llm_client.complete(
                messages=[
                    {'role': 'system', 'content': system_text},
                    {'role': 'user', 'content': user_text},
                ],
                json_format=True,
                call_type=call_type,
                game_id=game_id,
                player_name=context.personality_name,
                prompt_template='decision_expression',
                capture_enricher=capture_enricher,
            )
        except Exception as e:
            logger.warning(
                f"[EXPRESSION] LLM call failed for "
                f"{context.personality_name}: {e}"
            )
            return _empty()

        parsed = self._parse_response(response, context)

        # LLM-based beat cleanup. When the narration model fumbles
        # formatting (mixed action+speech, missing asterisks, quote-
        # wrapped gestures), a fast-tier model repairs the beats while
        # preserving wording. Heuristic-gated to skip cleanup on
        # already-clean output. Failures are silent.
        seq = parsed.get('dramatic_sequence')
        if seq:
            from poker.response_validator import (
                needs_llm_normalization, llm_normalize_beats,
            )
            if needs_llm_normalization(seq):
                parsed['dramatic_sequence'] = llm_normalize_beats(
                    seq,
                    self._get_cleanup_client(),
                    game_id=game_id,
                    player_name=context.personality_name,
                )
        return parsed

    # The decision_expression prompt is split into two messages:
    #   - system: stable per personality. Holds the persona intro, the
    #     output JSON schema, and ALL three narration-mode rule blocks
    #     (the active mode is selected per-turn via the user-side
    #     mode_indicator). This shape is cache-friendly — providers that
    #     support prompt caching keep the system prefix between turns.
    #   - user: per-turn dynamic context — situation, hand read, recent
    #     actions, emotional state, drama, mode indicator. The
    #     narration_facts adapter is also appended on the user side.
    _SYSTEM_SECTIONS = (
        'intro',
        'output_format',
        'dramatic_sequence_format',
        'gesture_only',
        'silence',
    )
    _USER_SECTIONS = (
        'situation',
        'hand_read',
        'recent_actions',
        'emotional_state',
        'drama',
        'mode_indicator',
    )

    # Per-turn narration-mode descriptors. Keys match the active mode
    # picked from ctx.should_speak / ctx.should_gesture. The hint points
    # the LLM at the matching rule block in the system message.
    _MODE_LABELS = {
        'speak': 'SPEAK',
        'gesture': 'GESTURE-ONLY',
        'silent': 'SILENT',
    }
    _MODE_HINTS = {
        'speak': (
            'Follow the SPEAK rules from the system message — full table '
            'talk and *action* gestures both allowed.'
        ),
        'gesture': (
            'Follow the GESTURE-ONLY rules — no speech beats; you may '
            'include 1–2 short *action* beats if the moment warrants it.'
        ),
        'silent': (
            'Follow the SILENT rules — dramatic_sequence must be [] this '
            'turn. Focus on inner_monologue and hand_strategy only.'
        ),
    }

    def _active_mode(self, ctx: ExpressionContext) -> str:
        """Pick the active narration mode from the gate state."""
        if ctx.should_speak:
            return 'speak'
        if ctx.should_gesture:
            return 'gesture'
        return 'silent'

    def _render_prompt(self, ctx: ExpressionContext) -> Tuple[str, str]:
        """Render the decision_expression prompt as (system, user) messages.

        System carries the stable persona + output format + all three
        narration rule blocks. User carries per-turn dynamic context and
        a mode indicator pointing at the active rule block.
        """
        template = self.prompt_manager.get_template('decision_expression')
        drama_context = self.drama_contexts.get(ctx.drama_level, '')
        tone_modifier = self.tone_modifiers.get(ctx.drama_tone, '')

        raise_clause = (
            f" (to {ctx.raise_to})"
            if ctx.action_taken in ('raise', 'all_in') and ctx.raise_to
            else ''
        )

        mode = self._active_mode(ctx)
        vars_ = {
            'personality_name': ctx.personality_name,
            'play_style': ctx.play_style,
            'default_attitude': ctx.default_attitude,
            'verbal_tics': ', '.join(ctx.verbal_tics) if ctx.verbal_tics else '(none)',
            'physical_tics': ', '.join(ctx.physical_tics) if ctx.physical_tics else '(none)',
            'hand_cards': ', '.join(ctx.hand_cards) if ctx.hand_cards else '(hidden)',
            'community_cards': ', '.join(ctx.community_cards) if ctx.community_cards else '(none)',
            'phase': ctx.phase,
            'pot_size': ctx.pot_size,
            'opponent_count': ctx.opponent_count,
            'action_taken': ctx.action_taken,
            'raise_clause': raise_clause,
            'emotional_state': ctx.emotional_state,
            'emotional_severity': ctx.emotional_severity,
            'drama_context': drama_context,
            'tone_modifier': tone_modifier,
            'position': ctx.position or '(unknown)',
            'stack_bb': f"{ctx.stack_bb:.1f}",
            'pot_bb': f"{ctx.pot_bb:.1f}",
            'cost_to_call_bb': f"{ctx.cost_to_call_bb:.1f}",
            'hand_name': ctx.hand_name,
            'recent_actions': ctx.recent_actions,
            'mode_label': self._MODE_LABELS[mode],
            'mode_hint': self._MODE_HINTS[mode],
        }

        # Optional user-side sections — skipped when empty so the prompt
        # doesn't carry placeholder blocks.
        skip = set()
        if not ctx.hand_name:
            skip.add('hand_read')
        if not ctx.recent_actions:
            skip.add('recent_actions')

        def _render(names):
            out = []
            for name in names:
                if name in skip:
                    continue
                section = template.sections.get(name)
                if section is None:
                    continue
                out.append(section.format(**vars_))
            return "\n\n".join(out)

        system_text = _render(self._SYSTEM_SECTIONS)
        user_text = _render(self._USER_SECTIONS)

        # Phase 7.6 Step 5: append narration_facts block to the user
        # message. These are per-decision strategic reads, so they belong
        # with the rest of the dynamic context, not the stable system
        # prefix.
        facts_block = self._render_narration_facts_block(ctx)
        if facts_block:
            user_text = f"{user_text}\n\n{facts_block}"

        return system_text, user_text

    def _render_narration_facts_block(self, ctx: ExpressionContext) -> str:
        """Render the NarrationFacts block as a prompt suffix.

        Returns empty string when ctx.narration_facts is None or empty —
        the existing prompt template is used verbatim in that case.
        Defensive against malformed NarrationFacts; logs a WARN and
        returns empty rather than corrupting the prompt.
        """
        facts = getattr(ctx, 'narration_facts', None)
        if not facts:
            return ''
        try:
            from .narration_facts import render_narration_prompt
            return render_narration_prompt(facts)
        except Exception as e:  # noqa: BLE001 — graceful degradation
            logger.warning(
                f"[EXPRESSION] Failed to render narration_facts for "
                f"{ctx.personality_name}: {e}"
            )
            return ''

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

        # Narration-mode filter. Defensive — even when the prompt told the
        # LLM to stay quiet or gesture-only, the model may still produce
        # beats. Mirrors ResponseValidator.clean_response in the hybrid
        # path, extended for gesture-only mode.
        if not ctx.should_speak:
            if ctx.should_gesture:
                # Keep only *action* beats (gestures wrapped in asterisks)
                sequence = [
                    b for b in sequence
                    if isinstance(b, str)
                    and b.strip().startswith('*')
                    and b.strip().endswith('*')
                ]
            else:
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
