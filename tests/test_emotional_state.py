"""Tests for the v2.1 emotional-state module (narration only).

The deprecated 4D dimensional model (valence/arousal/control/focus) was
removed in v130. Emotion *labels* now come from the quadrant/family matrix
(see test_emotion_families.py); this module only carries the LLM-narrated
narrative/inner_voice and builds the narrator context.
"""

import unittest

from poker.emotional_state import (
    EMOTIONAL_NARRATION_SCHEMA,
    EmotionalState,
    EmotionalStateGenerator,
    _composure_descriptor,
    _confidence_descriptor,
    _energy_descriptor,
)


class TestEmotionalStateSerialization(unittest.TestCase):
    """Round-trip and legacy tolerance for the narration-only dataclass."""

    def test_round_trip(self):
        state = EmotionalState(
            narrative="Frustrated.",
            inner_voice="Come on...",
            generated_at_hand=5,
            source_events=['lost', 'bad_beat'],
            used_fallback=False,
        )
        restored = EmotionalState.from_dict(state.to_dict())
        self.assertEqual(restored.narrative, state.narrative)
        self.assertEqual(restored.inner_voice, state.inner_voice)
        self.assertEqual(restored.generated_at_hand, state.generated_at_hand)
        self.assertEqual(restored.source_events, state.source_events)
        self.assertEqual(restored.used_fallback, state.used_fallback)

    def test_to_dict_has_no_4d_scalars(self):
        """The serialized form must not carry the removed dimensional keys."""
        d = EmotionalState(narrative="x").to_dict()
        for key in ('valence', 'arousal', 'control', 'focus'):
            self.assertNotIn(key, d)

    def test_from_legacy_row_ignores_4d_keys(self):
        """Old persisted rows still carrying 4D keys must load without error."""
        legacy = {
            'valence': 0.3,
            'arousal': 0.7,
            'control': 0.2,
            'focus': 0.4,
            'narrative': 'Stewing.',
            'inner_voice': 'Unreal.',
        }
        state = EmotionalState.from_dict(legacy)
        self.assertEqual(state.narrative, 'Stewing.')
        self.assertEqual(state.inner_voice, 'Unreal.')
        self.assertFalse(hasattr(state, 'valence'))

    def test_neutral(self):
        state = EmotionalState.neutral()
        self.assertTrue(state.narrative)
        self.assertTrue(state.inner_voice)


class TestNarrationSchema(unittest.TestCase):
    """The narration schema is text-only — no dimensional fields."""

    def test_narration_schema_has_text_fields_only(self):
        self.assertIn('narrative', EMOTIONAL_NARRATION_SCHEMA.fields)
        self.assertIn('inner_voice', EMOTIONAL_NARRATION_SCHEMA.fields)
        for key in ('valence', 'arousal', 'control', 'focus'):
            self.assertNotIn(key, EMOTIONAL_NARRATION_SCHEMA.fields)


class TestNarrationContext(unittest.TestCase):
    """The narrator context is quadrant-derived, not scalar-derived."""

    def setUp(self):
        self.gen = EmotionalStateGenerator()

    def _ctx(self, confidence, composure, energy, outcome='lost', amount=-500):
        return self.gen._build_narration_context(
            personality_name='Vacation Greg',
            personality_config={'play_style': 'loose tourist'},
            hand_outcome={'outcome': outcome, 'amount': amount},
            confidence=confidence,
            composure=composure,
            energy=energy,
            tilt_source='bad_beat',
            nemesis='Batman',
            session_context={},
        )

    def test_context_describes_quadrant_state_not_scalars(self):
        ctx = self._ctx(0.55, 0.26, 0.79)
        self.assertIn('CURRENT EMOTIONAL STATE', ctx)
        self.assertIn('Feeling:', ctx)
        self.assertIn('Confidence:', ctx)
        self.assertIn('Composure:', ctx)
        self.assertIn('Energy:', ctx)
        # No leakage of the removed 4D vocabulary
        self.assertNotIn('valence', ctx.lower())
        self.assertNotIn('arousal', ctx.lower())

    def test_context_includes_outcome(self):
        ctx = self._ctx(0.5, 0.7, 0.5, outcome='won', amount=800)
        self.assertIn('WHAT JUST HAPPENED', ctx)
        self.assertIn('WON', ctx)
        self.assertIn('800', ctx)

    def test_psych_block_only_when_composure_slipping(self):
        steady = self._ctx(0.6, 0.8, 0.5)
        self.assertNotIn('PSYCHOLOGICAL STATE', steady)
        rattled = self._ctx(0.5, 0.3, 0.5)
        self.assertIn('PSYCHOLOGICAL STATE', rattled)
        self.assertIn('bad_beat', rattled)


class TestDescriptors(unittest.TestCase):
    """Threshold descriptors used to colour the narrator context."""

    def test_composure_bands(self):
        self.assertEqual(_composure_descriptor(0.9), 'focused')
        self.assertEqual(_composure_descriptor(0.65), 'alert')
        self.assertEqual(_composure_descriptor(0.45), 'rattled')
        self.assertEqual(_composure_descriptor(0.2), 'tilted')

    def test_confidence_bands(self):
        self.assertEqual(_confidence_descriptor(0.8), 'riding high')
        self.assertEqual(_confidence_descriptor(0.5), 'steady')
        self.assertEqual(_confidence_descriptor(0.35), 'shaky')
        self.assertEqual(_confidence_descriptor(0.1), 'crushed')

    def test_energy_bands(self):
        self.assertEqual(_energy_descriptor(0.8), 'high')
        self.assertEqual(_energy_descriptor(0.5), 'moderate')
        self.assertEqual(_energy_descriptor(0.1), 'low')


if __name__ == '__main__':
    unittest.main()
