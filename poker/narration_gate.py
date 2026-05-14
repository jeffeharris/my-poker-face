"""Shared narration gate for AI player table-talk decisions.

A `NarrationGate` is the two-axis answer to "what should this bot do at the
table this turn?" — independent rolls for speech (chattiness-driven) and
physical reactions (energy-driven). Both axes are computed in one place
(`AIPlayerController.compute_narration_gate`) so hybrid/chaos and tiered
bots share identical "when to speak" behavior.

Consumers:
- Hybrid/chaos (`AIPlayerController._get_ai_decision`): currently uses
  `should_speak` to gate the dramatic_sequence prompt + post-LLM strip.
- Tiered (`TieredBotController._attach_expression`): uses both axes —
  `should_speak` for chattiness, `should_gesture` for energy-driven
  gesture-only mode. When both are False, skips the LLM call entirely.

The dataclass is intentionally minimal; situational nuance (game context,
drama, modifiers) lives in `ChattinessManager` and the controller method
that builds the rolls.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NarrationGate:
    """Per-turn answer for "should this bot speak / gesture at the table?"

    should_speak  — chattiness trait + situational modifiers (big_pot,
                    all_in, heads_up, recent silence, etc.). When False,
                    speech beats are stripped from dramatic_sequence.

    should_gesture — energy axis from psychology + drama-level boost.
                    Independent of speech: a silent character can still
                    react physically on big moments. When False AND
                    should_speak is also False, the caller may skip the
                    expression LLM call entirely.
    """

    should_speak: bool = True
    should_gesture: bool = True

    @property
    def fully_silent(self) -> bool:
        """True when neither axis fires — no narration expected."""
        return not self.should_speak and not self.should_gesture
