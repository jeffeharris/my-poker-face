"""
Unit tests for BettingContext betting validation and calculations.
"""

import unittest

from poker.betting_context import BettingContext
from poker.poker_game import PokerGameState, Player, create_deck


class TestBettingContextComputedProperties(unittest.TestCase):
    """Test computed properties of BettingContext."""

    def test_cost_to_call_when_behind(self):
        """Player needs to add chips to match highest bet."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=50,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        self.assertEqual(context.cost_to_call, 50)

    def test_cost_to_call_when_matching(self):
        """No cost to call when already matching highest bet."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=100,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('check', 'raise'),
        )
        self.assertEqual(context.cost_to_call, 0)

    def test_cost_to_call_never_negative(self):
        """Cost to call should never be negative."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=150,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('check', 'raise'),
        )
        self.assertEqual(context.cost_to_call, 0)

    def test_min_raise_to(self):
        """Minimum raise TO is highest_bet + min_raise_amount."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        self.assertEqual(context.min_raise_to, 150)

    def test_max_raise_to(self):
        """Maximum raise TO is current_bet + stack (all-in)."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=50,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        self.assertEqual(context.max_raise_to, 1050)

    def test_effective_stack(self):
        """Effective stack is stack minus cost to call."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=50,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        # Stack 1000 - cost to call 50 = 950
        self.assertEqual(context.effective_stack, 950)

    def test_effective_stack_never_negative(self):
        """Effective stack should never be negative."""
        context = BettingContext(
            player_stack=30,
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('all_in', 'fold'),
        )
        self.assertEqual(context.effective_stack, 0)


class TestValidateAndSanitize(unittest.TestCase):
    """Test validate_and_sanitize method."""

    def setUp(self):
        self.context = BettingContext(
            player_stack=1000,
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,  # min_raise_to = 150
            available_actions=('call', 'raise', 'fold'),
        )

    def test_valid_raise_within_range(self):
        """A raise within valid range is accepted."""
        is_valid, amount, msg = self.context.validate_and_sanitize(200)
        self.assertTrue(is_valid)
        self.assertEqual(amount, 200)
        self.assertEqual(msg, "")

    def test_raise_below_minimum_adjusted_to_min(self):
        """A raise below minimum is adjusted to min_raise_to."""
        is_valid, amount, msg = self.context.validate_and_sanitize(120)
        self.assertFalse(is_valid)
        self.assertEqual(amount, 150)  # min_raise_to
        self.assertIn("below minimum", msg)

    def test_raise_above_stack_adjusted_to_all_in(self):
        """A raise above stack is converted to all-in."""
        is_valid, amount, msg = self.context.validate_and_sanitize(1500)
        self.assertFalse(is_valid)
        self.assertEqual(amount, 1000)  # max_raise_to (all-in)
        self.assertIn("all-in", msg)

    def test_all_in_always_valid(self):
        """All-in is always valid, even if below min raise."""
        # Short stack scenario
        context = BettingContext(
            player_stack=60,  # can only reach 60, below min_raise_to of 150
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('all_in', 'fold'),
        )
        # Request any amount - should get all-in
        is_valid, amount, msg = context.validate_and_sanitize(60)
        # All-in below min is valid
        self.assertEqual(amount, 60)

    def test_exact_min_raise_is_valid(self):
        """Exactly min_raise_to is valid."""
        is_valid, amount, msg = self.context.validate_and_sanitize(150)
        self.assertTrue(is_valid)
        self.assertEqual(amount, 150)

    def test_exact_all_in_is_valid(self):
        """Exactly all-in amount is valid."""
        is_valid, amount, msg = self.context.validate_and_sanitize(1000)
        self.assertTrue(is_valid)
        self.assertEqual(amount, 1000)


class TestGetCallAndRaiseBreakdown(unittest.TestCase):
    """Test get_call_and_raise_breakdown method."""

    def test_breakdown_with_call_portion(self):
        """Breakdown shows call portion when behind."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=50,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        # Raise TO 200: need to add 150 total (50 call + 100 raise)
        breakdown = context.get_call_and_raise_breakdown(200)
        self.assertEqual(breakdown['call_portion'], 50)
        self.assertEqual(breakdown['raise_portion'], 100)
        self.assertEqual(breakdown['total_to_add'], 150)
        self.assertEqual(breakdown['stack_after'], 850)

    def test_breakdown_no_call_portion(self):
        """Breakdown shows no call when already matching."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=100,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('check', 'raise'),
        )
        # Raise TO 200: add 100 as pure raise
        breakdown = context.get_call_and_raise_breakdown(200)
        self.assertEqual(breakdown['call_portion'], 0)
        self.assertEqual(breakdown['raise_portion'], 100)
        self.assertEqual(breakdown['total_to_add'], 100)
        self.assertEqual(breakdown['stack_after'], 900)

    def test_breakdown_all_in(self):
        """Breakdown for all-in shows correct values."""
        context = BettingContext(
            player_stack=500,
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'all_in', 'fold'),
        )
        # All-in to 500
        breakdown = context.get_call_and_raise_breakdown(500)
        self.assertEqual(breakdown['call_portion'], 100)
        self.assertEqual(breakdown['raise_portion'], 400)
        self.assertEqual(breakdown['total_to_add'], 500)
        self.assertEqual(breakdown['stack_after'], 0)


class TestGetRaiseByAmount(unittest.TestCase):
    """Test get_raise_by_amount method."""

    def test_raise_by_calculation(self):
        """Correctly converts raise TO to raise BY."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        # Raise TO 200 means raising BY 100
        self.assertEqual(context.get_raise_by_amount(200), 100)
        # Raise TO 150 (min) means raising BY 50
        self.assertEqual(context.get_raise_by_amount(150), 50)

    def test_raise_by_never_negative(self):
        """Raise BY is never negative."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=0,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )
        # raise_to below highest_bet should return 0, not negative
        self.assertEqual(context.get_raise_by_amount(50), 0)


class TestFromGameState(unittest.TestCase):
    """Test from_game_state factory method."""

    def test_creates_context_from_game_state(self):
        """BettingContext is correctly created from PokerGameState."""
        player = Player(
            name='Player1',
            stack=1000,
            is_human=True,
            bet=50,
        )
        opponent = Player(
            name='Player2',
            stack=900,
            is_human=False,
            bet=100,
        )
        game_state = PokerGameState(
            players=(player, opponent),
            deck=create_deck(shuffled=True),
            pot={'total': 150},
            current_player_idx=0,
            current_ante=50,
            last_raise_amount=50,
        )

        context = BettingContext.from_game_state(game_state)

        self.assertEqual(context.player_stack, 1000)
        self.assertEqual(context.player_current_bet, 50)
        self.assertEqual(context.highest_bet, 100)
        self.assertEqual(context.pot_total, 150)
        self.assertEqual(context.min_raise_amount, 50)
        # Check computed properties
        self.assertEqual(context.cost_to_call, 50)
        self.assertEqual(context.min_raise_to, 150)
        self.assertEqual(context.max_raise_to, 1050)


class TestToDict(unittest.TestCase):
    """Test to_dict serialization method."""

    def test_to_dict_includes_all_fields(self):
        """to_dict includes all fields and computed properties."""
        context = BettingContext(
            player_stack=1000,
            player_current_bet=50,
            highest_bet=100,
            pot_total=200,
            min_raise_amount=50,
            available_actions=('call', 'raise', 'fold'),
        )

        result = context.to_dict()

        # Check base fields
        self.assertEqual(result['player_stack'], 1000)
        self.assertEqual(result['player_current_bet'], 50)
        self.assertEqual(result['highest_bet'], 100)
        self.assertEqual(result['pot_total'], 200)
        self.assertEqual(result['min_raise_amount'], 50)
        self.assertEqual(result['available_actions'], ['call', 'raise', 'fold'])

        # Check computed properties
        self.assertEqual(result['cost_to_call'], 50)
        self.assertEqual(result['min_raise_to'], 150)
        self.assertEqual(result['max_raise_to'], 1050)
        self.assertEqual(result['effective_stack'], 950)


if __name__ == '__main__':
    unittest.main()
