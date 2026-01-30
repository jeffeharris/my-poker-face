"""Tests for the two-layer deterministic emotional state system.

Tests the core computation functions (baseline mood, reactive spike, blending,
decay) and their integration with PlayerPsychology and display emotions.
"""

import unittest

from poker.emotional_state import (
    compute_baseline_mood,
    compute_reactive_spike,
    blend_emotional_state,
    EmotionalState,
    EMOTIONAL_NARRATION_SCHEMA,
    _clamp,
)
from poker.elasticity_manager import ElasticTrait


def _make_traits(aggression=0.5, bluff=0.5, chat=0.5, emoji=0.3,
                 agg_anchor=0.5, bluff_anchor=0.5, chat_anchor=0.5, emoji_anchor=0.3):
    """Helper to build elastic traits dict for testing."""
    return {
        'aggression': ElasticTrait(value=aggression, anchor=agg_anchor, elasticity=0.3),
        'bluff_tendency': ElasticTrait(value=bluff, anchor=bluff_anchor, elasticity=0.2),
        'chattiness': ElasticTrait(value=chat, anchor=chat_anchor, elasticity=0.3),
        'emoji_usage': ElasticTrait(value=emoji, anchor=emoji_anchor, elasticity=0.1),
    }


class TestComputeBaselineMood(unittest.TestCase):
    """Test deterministic baseline mood from elastic traits."""

    def test_neutral_traits_produce_near_zero_valence(self):
        """Traits at anchor should produce near-zero valence."""
        traits = _make_traits()  # all at anchor
        baseline = compute_baseline_mood(traits)
        self.assertAlmostEqual(baseline['valence'], 0.0, places=2)

    def test_winning_session_positive_valence(self):
        """Traits above anchor (from wins) should produce positive valence."""
        traits = _make_traits(aggression=0.7, bluff=0.7, chat=0.6, emoji=0.4)
        baseline = compute_baseline_mood(traits)
        self.assertGreater(baseline['valence'], 0)

    def test_losing_session_negative_valence(self):
        """Traits below anchor (from losses) should produce negative valence."""
        traits = _make_traits(aggression=0.3, bluff=0.3, chat=0.3, emoji=0.1)
        baseline = compute_baseline_mood(traits)
        self.assertLess(baseline['valence'], 0)

    def test_winning_has_higher_valence_than_losing(self):
        """Winning session should have higher valence than losing."""
        winning = compute_baseline_mood(
            _make_traits(aggression=0.7, bluff=0.7, chat=0.6, emoji=0.4))
        losing = compute_baseline_mood(
            _make_traits(aggression=0.3, bluff=0.3, chat=0.3, emoji=0.1))
        self.assertGreater(winning['valence'], losing['valence'])

    def test_large_drift_reduces_control(self):
        """Big trait shifts from anchor should reduce sense of control."""
        neutral = compute_baseline_mood(_make_traits())
        drifted = compute_baseline_mood(
            _make_traits(aggression=0.8, bluff=0.8, chat=0.8, emoji=0.6))
        self.assertGreater(neutral['control'], drifted['control'])

    def test_large_drift_increases_arousal(self):
        """Any big trait shift should increase arousal (more activated)."""
        neutral = compute_baseline_mood(_make_traits())
        drifted = compute_baseline_mood(
            _make_traits(aggression=0.8, bluff=0.8, chat=0.8, emoji=0.6))
        self.assertGreater(drifted['arousal'], neutral['arousal'])

    def test_high_chattiness_reduces_focus(self):
        """High chattiness/emoji should reduce focus (more scattered)."""
        quiet = compute_baseline_mood(_make_traits(chat=0.2, emoji=0.1))
        chatty = compute_baseline_mood(_make_traits(chat=0.8, emoji=0.6))
        self.assertGreater(quiet['focus'], chatty['focus'])

    def test_all_dimensions_in_valid_range(self):
        """All output dimensions should be within valid ranges."""
        for agg in [0.0, 0.3, 0.5, 0.7, 1.0]:
            for bluff in [0.0, 0.5, 1.0]:
                traits = _make_traits(aggression=agg, bluff=bluff)
                baseline = compute_baseline_mood(traits)
                self.assertGreaterEqual(baseline['valence'], -1.0)
                self.assertLessEqual(baseline['valence'], 1.0)
                self.assertGreaterEqual(baseline['arousal'], 0.0)
                self.assertLessEqual(baseline['arousal'], 1.0)
                self.assertGreaterEqual(baseline['control'], 0.0)
                self.assertLessEqual(baseline['control'], 1.0)
                self.assertGreaterEqual(baseline['focus'], 0.0)
                self.assertLessEqual(baseline['focus'], 1.0)

    def test_empty_traits(self):
        """Empty traits dict should not crash."""
        baseline = compute_baseline_mood({})
        self.assertIn('valence', baseline)
        self.assertIn('arousal', baseline)

    def test_dict_traits_instead_of_objects(self):
        """Should work with dict-style traits (from serialization)."""
        traits = {
            'aggression': {'value': 0.7, 'anchor': 0.5, 'pressure': 0.1},
            'bluff_tendency': {'value': 0.6, 'anchor': 0.5, 'pressure': 0.05},
        }
        baseline = compute_baseline_mood(traits)
        self.assertGreater(baseline['valence'], 0)


class TestComputeReactiveSpike(unittest.TestCase):
    """Test reactive spike computation from hand outcomes."""

    def test_win_positive_valence(self):
        """Winning should produce positive valence spike."""
        spike = compute_reactive_spike('won', 500, tilt_level=0.0, big_blind=100)
        self.assertGreater(spike['valence'], 0)

    def test_loss_negative_valence(self):
        """Losing should produce negative valence spike."""
        spike = compute_reactive_spike('lost', -500, tilt_level=0.0, big_blind=100)
        self.assertLess(spike['valence'], 0)

    def test_fold_mild_negative(self):
        """Folding should produce mild negative valence."""
        spike = compute_reactive_spike('folded', -20, tilt_level=0.0, big_blind=100)
        self.assertLess(spike['valence'], 0)
        # Fold should be milder than a big loss
        loss_spike = compute_reactive_spike('lost', -500, tilt_level=0.0, big_blind=100)
        self.assertGreater(spike['valence'], loss_spike['valence'])

    def test_big_win_spikes_more_than_small_win(self):
        """Bigger wins should spike valence more."""
        small = compute_reactive_spike('won', 50, tilt_level=0.0, big_blind=100)
        big = compute_reactive_spike('won', 1000, tilt_level=0.0, big_blind=100)
        self.assertGreater(big['valence'], small['valence'])

    def test_tilt_amplifies_spike(self):
        """Higher tilt should amplify spike magnitude."""
        calm = compute_reactive_spike('lost', -500, tilt_level=0.0, big_blind=100)
        tilted = compute_reactive_spike('lost', -500, tilt_level=0.5, big_blind=100)
        self.assertGreater(abs(tilted['valence']), abs(calm['valence']))
        self.assertGreater(tilted['arousal'], calm['arousal'])

    def test_big_blind_normalization(self):
        """Same dollar amount should spike less with higher big blind."""
        low_stakes = compute_reactive_spike('won', 200, tilt_level=0.0, big_blind=50)
        high_stakes = compute_reactive_spike('won', 200, tilt_level=0.0, big_blind=500)
        self.assertGreater(low_stakes['valence'], high_stakes['valence'])

    def test_magnitude_caps_at_10bb(self):
        """Amount significance should cap at 10 big blinds."""
        ten_bb = compute_reactive_spike('won', 1000, tilt_level=0.0, big_blind=100)
        hundred_bb = compute_reactive_spike('won', 10000, tilt_level=0.0, big_blind=100)
        self.assertAlmostEqual(ten_bb['valence'], hundred_bb['valence'], places=3)

    def test_loss_increases_arousal(self):
        """Losses should increase arousal (frustration)."""
        spike = compute_reactive_spike('lost', -500, tilt_level=0.0, big_blind=100)
        self.assertGreater(spike['arousal'], 0)

    def test_loss_decreases_control(self):
        """Losses should decrease control."""
        spike = compute_reactive_spike('lost', -500, tilt_level=0.0, big_blind=100)
        self.assertLess(spike['control'], 0)

    def test_win_increases_control(self):
        """Wins should increase control."""
        spike = compute_reactive_spike('won', 500, tilt_level=0.0, big_blind=100)
        self.assertGreater(spike['control'], 0)

    def test_unknown_outcome(self):
        """Unknown outcome should be treated like fold (mild)."""
        spike = compute_reactive_spike('unknown', 0, tilt_level=0.0, big_blind=100)
        self.assertLessEqual(abs(spike['valence']), 0.1)

    def test_zero_big_blind_no_crash(self):
        """Zero big blind should not cause division by zero."""
        spike = compute_reactive_spike('won', 500, tilt_level=0.0, big_blind=0)
        self.assertIn('valence', spike)


class TestBlendEmotionalState(unittest.TestCase):
    """Test blending baseline and spike."""

    def test_basic_blend(self):
        """Blend should add baseline and spike."""
        baseline = {'valence': 0.3, 'arousal': 0.4, 'control': 0.7, 'focus': 0.6}
        spike = {'valence': 0.2, 'arousal': 0.1, 'control': 0.1, 'focus': 0.0}
        blended = blend_emotional_state(baseline, spike)
        self.assertAlmostEqual(blended['valence'], 0.5, places=3)
        self.assertAlmostEqual(blended['arousal'], 0.5, places=3)

    def test_clamping_high(self):
        """Values should clamp to max of range."""
        baseline = {'valence': 0.8, 'arousal': 0.9, 'control': 0.9, 'focus': 0.9}
        spike = {'valence': 0.5, 'arousal': 0.5, 'control': 0.5, 'focus': 0.5}
        blended = blend_emotional_state(baseline, spike)
        self.assertLessEqual(blended['valence'], 1.0)
        self.assertLessEqual(blended['arousal'], 1.0)

    def test_clamping_low(self):
        """Values should clamp to min of range."""
        baseline = {'valence': -0.8, 'arousal': 0.1, 'control': 0.1, 'focus': 0.1}
        spike = {'valence': -0.5, 'arousal': -0.5, 'control': -0.5, 'focus': -0.5}
        blended = blend_emotional_state(baseline, spike)
        self.assertGreaterEqual(blended['valence'], -1.0)
        self.assertGreaterEqual(blended['arousal'], 0.0)

    def test_zero_spike_returns_baseline(self):
        """Zero spike should return baseline values."""
        baseline = {'valence': 0.3, 'arousal': 0.5, 'control': 0.7, 'focus': 0.6}
        spike = {'valence': 0.0, 'arousal': 0.0, 'control': 0.0, 'focus': 0.0}
        blended = blend_emotional_state(baseline, spike)
        self.assertAlmostEqual(blended['valence'], 0.3, places=3)


class TestEmotionalStateDecay(unittest.TestCase):
    """Test emotional state decay toward baseline."""

    def test_decay_moves_toward_baseline(self):
        """Decay should move each dimension toward the baseline."""
        state = EmotionalState(valence=-0.8, arousal=0.9, control=0.2, focus=0.3)
        baseline = {'valence': 0.3, 'arousal': 0.4, 'control': 0.7, 'focus': 0.7}
        decayed = state.decay_toward_baseline(baseline, rate=0.5)

        self.assertLess(
            abs(decayed.valence - baseline['valence']),
            abs(state.valence - baseline['valence']))
        self.assertLess(
            abs(decayed.arousal - baseline['arousal']),
            abs(state.arousal - baseline['arousal']))
        self.assertLess(
            abs(decayed.control - baseline['control']),
            abs(state.control - baseline['control']))

    def test_decay_converges_to_baseline(self):
        """Many decay steps should converge to the baseline."""
        state = EmotionalState(valence=-0.8, arousal=0.9, control=0.2, focus=0.3)
        baseline = {'valence': 0.3, 'arousal': 0.4, 'control': 0.7, 'focus': 0.7}

        for _ in range(50):
            state = state.decay_toward_baseline(baseline, rate=0.3)

        self.assertAlmostEqual(state.valence, baseline['valence'], places=2)
        self.assertAlmostEqual(state.arousal, baseline['arousal'], places=2)
        self.assertAlmostEqual(state.control, baseline['control'], places=2)
        self.assertAlmostEqual(state.focus, baseline['focus'], places=2)

    def test_decay_rate_zero_no_change(self):
        """Rate=0 should produce no change."""
        state = EmotionalState(valence=-0.8, arousal=0.9, control=0.2, focus=0.3)
        baseline = {'valence': 0.3, 'arousal': 0.4, 'control': 0.7, 'focus': 0.7}
        decayed = state.decay_toward_baseline(baseline, rate=0.0)
        self.assertAlmostEqual(decayed.valence, state.valence, places=5)

    def test_decay_rate_one_jumps_to_baseline(self):
        """Rate=1 should jump directly to baseline."""
        state = EmotionalState(valence=-0.8, arousal=0.9, control=0.2, focus=0.3)
        baseline = {'valence': 0.3, 'arousal': 0.4, 'control': 0.7, 'focus': 0.7}
        decayed = state.decay_toward_baseline(baseline, rate=1.0)
        self.assertAlmostEqual(decayed.valence, baseline['valence'], places=5)

    def test_decay_preserves_narrative(self):
        """Decay should preserve narrative text until regenerated."""
        state = EmotionalState(
            valence=-0.5, arousal=0.7, narrative="Still angry.", inner_voice="Ugh.")
        baseline = {'valence': 0.0, 'arousal': 0.4, 'control': 0.6, 'focus': 0.6}
        decayed = state.decay_toward_baseline(baseline, rate=0.5)
        self.assertEqual(decayed.narrative, "Still angry.")
        self.assertEqual(decayed.inner_voice, "Ugh.")


class TestGetDisplayEmotion(unittest.TestCase):
    """Test mapping of dimensions to discrete avatar emotions."""

    def test_angry(self):
        """Very negative mood + high agitation = angry."""
        state = EmotionalState(valence=-0.5, arousal=0.8, control=0.3, focus=0.3)
        self.assertEqual(state.get_display_emotion(), 'angry')

    def test_elated(self):
        """High positive valence + high arousal = elated."""
        state = EmotionalState(valence=0.7, arousal=0.7, control=0.6, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'elated')

    def test_shocked(self):
        """Extreme arousal = shocked."""
        state = EmotionalState(valence=0.0, arousal=0.9, control=0.5, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'shocked')

    def test_smug(self):
        """Positive mood + high control = smug."""
        state = EmotionalState(valence=0.6, arousal=0.5, control=0.8, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'smug')

    def test_frustrated(self):
        """Negative mood + moderate arousal = frustrated."""
        state = EmotionalState(valence=-0.3, arousal=0.6, control=0.6, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'frustrated')

    def test_nervous(self):
        """Negative mood + low control = nervous."""
        state = EmotionalState(valence=-0.2, arousal=0.4, control=0.3, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'nervous')

    def test_confident(self):
        """Positive mood + good control = confident."""
        state = EmotionalState(valence=0.5, arousal=0.5, control=0.7, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'confident')

    def test_happy(self):
        """Positive mood without strong control = happy."""
        state = EmotionalState(valence=0.5, arousal=0.4, control=0.4, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'happy')

    def test_thinking(self):
        """High focus + calm = thinking."""
        state = EmotionalState(valence=0.1, arousal=0.3, control=0.5, focus=0.8)
        self.assertEqual(state.get_display_emotion(), 'thinking')

    def test_poker_face_default(self):
        """Neutral state should fall back to poker_face."""
        state = EmotionalState(valence=0.0, arousal=0.3, control=0.4, focus=0.4)
        self.assertEqual(state.get_display_emotion(), 'poker_face')

    def test_angry_priority_over_shocked(self):
        """Angry should take priority over shocked when both conditions met."""
        state = EmotionalState(valence=-0.5, arousal=0.9, control=0.3, focus=0.3)
        self.assertEqual(state.get_display_emotion(), 'angry')

    def test_smug_priority_over_confident(self):
        """Smug should take priority over confident for high-control positive states."""
        state = EmotionalState(valence=0.6, arousal=0.5, control=0.8, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'smug')

    def test_confident_priority_over_happy(self):
        """Confident should take priority over happy for controlled-positive states."""
        state = EmotionalState(valence=0.5, arousal=0.4, control=0.6, focus=0.5)
        self.assertEqual(state.get_display_emotion(), 'confident')


class TestEndToEndEmotionalFlow(unittest.TestCase):
    """Test the full flow from traits + outcome to avatar emotion."""

    def test_winning_session_big_win_shows_positive_emotion(self):
        """Winning session + big win should show a positive/high-energy emotion."""
        traits = _make_traits(aggression=0.7, bluff=0.7, chat=0.4, emoji=0.2)
        baseline = compute_baseline_mood(traits)
        spike = compute_reactive_spike('won', 800, tilt_level=0.0, big_blind=100)
        blended = blend_emotional_state(baseline, spike)
        state = EmotionalState(**blended)
        self.assertIn(state.get_display_emotion(),
                      ['elated', 'happy', 'confident', 'smug', 'shocked'])

    def test_losing_session_bad_beat_shows_angry_or_nervous(self):
        """Losing session + big loss + tilt should show angry or nervous."""
        traits = _make_traits(aggression=0.3, bluff=0.3, chat=0.3, emoji=0.1)
        baseline = compute_baseline_mood(traits)
        spike = compute_reactive_spike('lost', -800, tilt_level=0.6, big_blind=100)
        blended = blend_emotional_state(baseline, spike)
        state = EmotionalState(**blended)
        self.assertIn(state.get_display_emotion(), ['angry', 'nervous', 'shocked', 'frustrated'])

    def test_neutral_session_fold_shows_calm_emotion(self):
        """Neutral session + fold should show calm emotion."""
        traits = _make_traits()
        baseline = compute_baseline_mood(traits)
        spike = compute_reactive_spike('folded', -20, tilt_level=0.0, big_blind=100)
        blended = blend_emotional_state(baseline, spike)
        state = EmotionalState(**blended)
        self.assertIn(state.get_display_emotion(), ['thinking', 'confident', 'poker_face'])

    def test_spike_decays_back_to_baseline_emotion(self):
        """After many decay steps, emotion should match baseline-only state."""
        traits = _make_traits(aggression=0.6, bluff=0.6)
        baseline = compute_baseline_mood(traits)

        # Start with an angry spike
        spike = compute_reactive_spike('lost', -1000, tilt_level=0.5, big_blind=100)
        blended = blend_emotional_state(baseline, spike)
        state = EmotionalState(**blended)

        # Decay many times
        for _ in range(50):
            state = state.decay_toward_baseline(baseline, rate=0.3)

        # Should converge to baseline-only emotion
        baseline_state = EmotionalState(**baseline)
        self.assertEqual(
            state.get_display_emotion(),
            baseline_state.get_display_emotion())


class TestNarrationSchema(unittest.TestCase):
    """Test the narration-only schema."""

    def test_narration_schema_has_text_fields_only(self):
        """Narration schema should have narrative and inner_voice only."""
        self.assertIn('narrative', EMOTIONAL_NARRATION_SCHEMA.fields)
        self.assertIn('inner_voice', EMOTIONAL_NARRATION_SCHEMA.fields)
        self.assertNotIn('valence', EMOTIONAL_NARRATION_SCHEMA.fields)
        self.assertNotIn('arousal', EMOTIONAL_NARRATION_SCHEMA.fields)
        self.assertNotIn('control', EMOTIONAL_NARRATION_SCHEMA.fields)
        self.assertNotIn('focus', EMOTIONAL_NARRATION_SCHEMA.fields)


class TestEmotionalStateSerialization(unittest.TestCase):
    """Test serialization round-trip."""

    def test_round_trip(self):
        """to_dict/from_dict should preserve all fields."""
        state = EmotionalState(
            valence=-0.5, arousal=0.7, control=0.3, focus=0.4,
            narrative="Frustrated.", inner_voice="Come on...",
            generated_at_hand=5, source_events=['lost', 'bad_beat'],
            used_fallback=False)
        restored = EmotionalState.from_dict(state.to_dict())
        self.assertAlmostEqual(restored.valence, state.valence)
        self.assertAlmostEqual(restored.arousal, state.arousal)
        self.assertEqual(restored.narrative, state.narrative)
        self.assertEqual(restored.inner_voice, state.inner_voice)
        self.assertEqual(restored.source_events, state.source_events)
        self.assertEqual(restored.used_fallback, state.used_fallback)

    def test_from_old_format(self):
        """Should handle loading from older serialization format gracefully."""
        old_data = {'valence': 0.3, 'arousal': 0.5}  # missing fields
        state = EmotionalState.from_dict(old_data)
        self.assertAlmostEqual(state.valence, 0.3)
        self.assertEqual(state.narrative, '')


class TestClamp(unittest.TestCase):
    """Test the clamp utility."""

    def test_within_range(self):
        self.assertEqual(_clamp(0.5, 0.0, 1.0), 0.5)

    def test_below_min(self):
        self.assertEqual(_clamp(-0.5, 0.0, 1.0), 0.0)

    def test_above_max(self):
        self.assertEqual(_clamp(1.5, 0.0, 1.0), 1.0)

    def test_at_boundary(self):
        self.assertEqual(_clamp(0.0, 0.0, 1.0), 0.0)
        self.assertEqual(_clamp(1.0, 0.0, 1.0), 1.0)


if __name__ == '__main__':
    unittest.main()
