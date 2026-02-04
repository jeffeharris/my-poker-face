"""Tests for the tilt modifier system."""

import unittest
import sys
import os

# Add project root to path to import module directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import directly from the module file to avoid poker package dependencies
from poker.tilt_modifier import TiltState, TiltPromptModifier, INTRUSIVE_THOUGHTS, TILTED_STRATEGY


class TestTiltState(unittest.TestCase):
    """Test TiltState tracking."""

    def test_initial_state(self):
        """New tilt state should be neutral."""
        state = TiltState()
        self.assertEqual(state.tilt_level, 0.0)
        self.assertEqual(state.get_tilt_category(), 'none')

    def test_loss_increases_tilt(self):
        """Losing hands should increase tilt."""
        state = TiltState()
        state.update_from_hand('lost', -500, opponent='Donald Trump')
        self.assertGreater(state.tilt_level, 0)

    def test_big_loss_increases_tilt_more(self):
        """Big losses (> 15 BB) should increase tilt more than regular losses."""
        state1 = TiltState()
        state2 = TiltState()

        # Regular loss: 100 chips at 100 BB = 1 BB (not a big loss)
        state1.update_from_hand('lost', -100, big_blind=100)
        # Big loss: 2000 chips at 100 BB = 20 BB (> 15 BB threshold)
        state2.update_from_hand('lost', -2000, big_blind=100)

        self.assertGreater(state2.tilt_level, state1.tilt_level)

    def test_bad_beat_is_worst(self):
        """Bad beats should cause the most tilt."""
        state = TiltState()
        state.update_from_hand('lost', -500, was_bad_beat=True)

        self.assertGreaterEqual(state.tilt_level, 0.15)  # Reduced from 0.25 for slower tilt buildup
        self.assertEqual(state.tilt_source, 'bad_beat')

    def test_winning_reduces_tilt(self):
        """Winning should reduce tilt."""
        state = TiltState()
        state.tilt_level = 0.5
        state.update_from_hand('won', 500)

        self.assertLess(state.tilt_level, 0.5)

    def test_losing_streak_tracked(self):
        """Consecutive losses should build losing streak."""
        state = TiltState()
        state.update_from_hand('lost', -100)
        state.update_from_hand('lost', -100)
        state.update_from_hand('lost', -100)

        self.assertEqual(state.losing_streak, 3)
        self.assertEqual(state.tilt_source, 'losing_streak')

    def test_win_resets_streak(self):
        """Winning should reset losing streak."""
        state = TiltState()
        state.losing_streak = 5
        state.update_from_hand('won', 100)

        self.assertEqual(state.losing_streak, 0)

    def test_nemesis_tracked(self):
        """Should track who caused the tilt."""
        state = TiltState()
        state.update_from_hand('lost', -500, opponent='Eeyore')

        self.assertEqual(state.nemesis, 'Eeyore')

    def test_tilt_categories(self):
        """Test tilt category thresholds."""
        state = TiltState()

        state.tilt_level = 0.1
        self.assertEqual(state.get_tilt_category(), 'none')

        state.tilt_level = 0.3
        self.assertEqual(state.get_tilt_category(), 'mild')

        state.tilt_level = 0.5
        self.assertEqual(state.get_tilt_category(), 'moderate')

        state.tilt_level = 0.8
        self.assertEqual(state.get_tilt_category(), 'severe')

    def test_big_loss_relative_to_big_blind(self):
        """Big loss threshold should be relative to big blind (15 BB)."""
        state = TiltState()

        # At 100 BB, threshold is 1500. A 1000 loss is NOT a big loss.
        state.update_from_hand('lost', -1000, big_blind=100)
        self.assertNotEqual(state.tilt_source, 'big_loss')  # Regular loss, not big_loss

        # Reset and test actual big loss (> 15 BB)
        state2 = TiltState()
        state2.update_from_hand('lost', -2000, big_blind=100)  # 20 BB loss
        self.assertEqual(state2.tilt_source, 'big_loss')

    def test_big_win_relative_to_big_blind(self):
        """Big win threshold should be relative to big blind (15 BB)."""
        state = TiltState()
        state.tilt_level = 0.5

        # Win 1000 at 100 BB = 10 BB win, not "big"
        initial_tilt = state.tilt_level
        state.update_from_hand('won', 1000, big_blind=100)
        regular_reduction = initial_tilt - state.tilt_level

        # Win 2000 at 100 BB = 20 BB win, should be "big" and reduce more
        state2 = TiltState()
        state2.tilt_level = 0.5
        state2.update_from_hand('won', 2000, big_blind=100)
        big_reduction = 0.5 - state2.tilt_level

        self.assertGreater(big_reduction, regular_reduction)

    def test_decay_rate_increased(self):
        """Decay should be 0.05 per call (up from 0.02)."""
        state = TiltState()
        state.tilt_level = 0.5
        state.decay()
        self.assertEqual(state.tilt_level, 0.45)  # 0.5 - 0.05 = 0.45


class TestTiltPromptModifier(unittest.TestCase):
    """Test prompt modification based on tilt."""

    def setUp(self):
        """Set up a sample prompt."""
        self.base_prompt = (
            "Persona: Donald Trump\n"
            "Your Cards: [Ace of Spades, King of Hearts]\n"
            "Your Money: $5000\n\n"
            "Consider the pot odds, the amount of money in the pot, "
            "and how much you would have to risk. "
            "Preserve your chips for when the odds are in your favor, "
            "and remember that sometimes folding or checking is the best move.\n"
            "What is your move, Donald Trump?"
        )

    def test_no_modification_when_not_tilted(self):
        """Prompt should be unchanged when not tilted."""
        state = TiltState(tilt_level=0.1)
        modifier = TiltPromptModifier(state)

        result = modifier.modify_prompt(self.base_prompt)
        self.assertEqual(result, self.base_prompt)

    def test_intrusive_thoughts_injected(self):
        """Should inject intrusive thoughts when tilted."""
        state = TiltState(tilt_level=0.4, tilt_source='big_loss')
        modifier = TiltPromptModifier(state)

        result = modifier.modify_prompt(self.base_prompt)
        self.assertIn("[What's running through your mind:", result)

    def test_strategic_advice_removed(self):
        """Should remove strategic advice at moderate tilt."""
        state = TiltState(tilt_level=0.5)
        modifier = TiltPromptModifier(state)

        result = modifier.modify_prompt(self.base_prompt)
        self.assertNotIn("Preserve your chips", result)

    def test_tilted_strategy_added(self):
        """Should add tilted strategy advice."""
        state = TiltState(tilt_level=0.5)
        modifier = TiltPromptModifier(state)

        result = modifier.modify_prompt(self.base_prompt)
        self.assertIn("[Current mindset:", result)

    def test_severe_tilt_removes_pot_odds(self):
        """Severe tilt should remove pot odds guidance."""
        state = TiltState(tilt_level=0.8)
        modifier = TiltPromptModifier(state)

        result = modifier.modify_prompt(self.base_prompt)
        self.assertNotIn("pot odds", result.lower())

    def test_revenge_thoughts_with_nemesis(self):
        """Should include revenge thoughts when there's a nemesis."""
        state = TiltState(tilt_level=0.6, tilt_source='big_loss', nemesis='Eeyore')
        modifier = TiltPromptModifier(state)

        result = modifier.modify_prompt(self.base_prompt)
        self.assertIn("Eeyore", result)

    def test_info_to_hide_scales_with_tilt(self):
        """Higher tilt should hide more information."""
        low_tilt = TiltState(tilt_level=0.3)
        high_tilt = TiltState(tilt_level=0.8)

        low_modifier = TiltPromptModifier(low_tilt)
        high_modifier = TiltPromptModifier(high_tilt)

        self.assertEqual(len(low_modifier.get_info_to_hide()), 0)
        self.assertGreater(len(high_modifier.get_info_to_hide()), 0)


class TestTiltPromptExamples(unittest.TestCase):
    """Show example outputs for different tilt levels."""

    def test_show_mild_tilt_example(self):
        """Display what mild tilt looks like."""
        state = TiltState(tilt_level=0.3, tilt_source='big_loss')
        modifier = TiltPromptModifier(state)

        prompt = "What is your move, Player?"
        result = modifier.modify_prompt(prompt)

        print("\n=== MILD TILT EXAMPLE ===")
        print(result)
        print("=" * 40)

    def test_show_moderate_tilt_example(self):
        """Display what moderate tilt looks like."""
        state = TiltState(tilt_level=0.5, tilt_source='bad_beat', nemesis='Eeyore')
        modifier = TiltPromptModifier(state)

        prompt = (
            "Preserve your chips for when the odds are in your favor.\n"
            "What is your move, Player?"
        )
        result = modifier.modify_prompt(prompt)

        print("\n=== MODERATE TILT EXAMPLE ===")
        print(result)
        print("=" * 40)

    def test_show_severe_tilt_example(self):
        """Display what severe tilt looks like."""
        state = TiltState(tilt_level=0.85, tilt_source='losing_streak',
                         nemesis='Donald Trump', losing_streak=5)
        modifier = TiltPromptModifier(state)

        prompt = (
            "Consider the pot odds, the amount of money in the pot, "
            "and how much you would have to risk. "
            "Preserve your chips for when the odds are in your favor.\n"
            "What is your move, Player?"
        )
        result = modifier.modify_prompt(prompt)

        print("\n=== SEVERE TILT EXAMPLE ===")
        print(result)
        print("=" * 40)


if __name__ == '__main__':
    # Run with verbose output to see examples
    unittest.main(verbosity=2)
