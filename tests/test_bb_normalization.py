"""
Tests for BB (Big Blind) normalization functionality.

Tests the _format_money helper, message conversion, and BB-to-dollar
conversion logic. BB mode is always active for AI prompts.
"""
import unittest
from unittest.mock import MagicMock, patch
from poker.controllers import _format_money, _convert_messages_to_bb, AIPlayerController
from poker.prompt_config import PromptConfig


class TestFormatMoney(unittest.TestCase):
    """Tests for the _format_money helper function."""

    def test_dollars_format(self):
        """When as_bb=False, should return dollar format."""
        result = _format_money(500, big_blind=50, as_bb=False)
        self.assertEqual(result, "$500")

    def test_dollars_format_zero(self):
        """Zero dollars should format correctly."""
        result = _format_money(0, big_blind=50, as_bb=False)
        self.assertEqual(result, "$0")

    def test_bb_format_basic(self):
        """Basic BB formatting: 500 / 100 = 5.00 BB."""
        result = _format_money(500, big_blind=100, as_bb=True)
        self.assertEqual(result, "5.00 BB")

    def test_bb_format_decimal(self):
        """Decimal BB values: 125 / 50 = 2.50 BB."""
        result = _format_money(125, big_blind=50, as_bb=True)
        self.assertEqual(result, "2.50 BB")

    def test_bb_format_small_fraction(self):
        """Small fractions: 25 / 100 = 0.25 BB."""
        result = _format_money(25, big_blind=100, as_bb=True)
        self.assertEqual(result, "0.25 BB")

    def test_bb_format_large_stack(self):
        """Large stack: 10000 / 50 = 200.00 BB."""
        result = _format_money(10000, big_blind=50, as_bb=True)
        self.assertEqual(result, "200.00 BB")

    def test_bb_zero_fallback(self):
        """When big_blind is 0, should fall back to dollar format."""
        result = _format_money(500, big_blind=0, as_bb=True)
        self.assertEqual(result, "$500")


class TestConvertMessagesToBB(unittest.TestCase):
    """Tests for converting dollar amounts in messages to BB format."""

    def test_raise_message(self):
        """Raise messages should convert dollar amounts to BB."""
        msg = "Batman raises to $500."
        result = _convert_messages_to_bb(msg, big_blind=50)
        self.assertEqual(result, "Batman raises to 10.00 BB.")

    def test_bet_message(self):
        """Bet messages should convert dollar amounts to BB."""
        msg = "Superman bets $100."
        result = _convert_messages_to_bb(msg, big_blind=50)
        self.assertEqual(result, "Superman bets 2.00 BB.")

    def test_no_dollar_amounts(self):
        """Messages without dollar amounts should pass through unchanged."""
        msg = "Batman checks."
        result = _convert_messages_to_bb(msg, big_blind=50)
        self.assertEqual(result, "Batman checks.")

    def test_multiple_amounts(self):
        """Multiple dollar amounts in one string should all convert."""
        msg = "Pot is $200, Batman raises to $500."
        result = _convert_messages_to_bb(msg, big_blind=100)
        self.assertEqual(result, "Pot is 2.00 BB, Batman raises to 5.00 BB.")

    def test_multiline_messages(self):
        """Conversion should work across multiple lines."""
        msg = "This hand:\n  Batman raises to $500.\n  Superman calls."
        result = _convert_messages_to_bb(msg, big_blind=100)
        self.assertEqual(result, "This hand:\n  Batman raises to 5.00 BB.\n  Superman calls.")

    def test_fractional_bb(self):
        """Non-round BB values should show 2 decimal places."""
        msg = "Batman raises to $75."
        result = _convert_messages_to_bb(msg, big_blind=100)
        self.assertEqual(result, "Batman raises to 0.75 BB.")

    def test_zero_big_blind_fallback(self):
        """When big_blind is 0, should return messages unchanged."""
        msg = "Batman raises to $500."
        result = _convert_messages_to_bb(msg, big_blind=0)
        self.assertEqual(result, "Batman raises to $500.")


def _make_controller():
    """Create a minimal AIPlayerController for testing internal methods."""
    with patch('poker.controllers.AIPokerPlayer') as mock_player, \
         patch('poker.controllers.PromptManager'), \
         patch('poker.controllers.ChattinessManager'), \
         patch('poker.controllers.ResponseValidator'), \
         patch('poker.controllers.PlayerPsychology') as mock_psych:
        mock_player.return_value.assistant = MagicMock()
        mock_player.return_value.personality_config = {}
        mock_psych.from_personality_config.return_value = MagicMock()
        controller = AIPlayerController('TestPlayer', prompt_config=PromptConfig())
    return controller


class TestNormalizeResponse(unittest.TestCase):
    """Tests for _normalize_response preserving float raise_to values."""

    def setUp(self):
        self.controller = _make_controller()

    def test_preserves_decimal_raise_to(self):
        """Decimal BB values (e.g., 8.5) should be preserved as float."""
        response = {'action': 'raise', 'raise_to': 8.5}
        result = self.controller._normalize_response(response)
        self.assertEqual(result['raise_to'], 8.5)
        self.assertIsInstance(result['raise_to'], float)

    def test_converts_string_raise_to_float(self):
        """String raise_to should be converted to float, not int."""
        response = {'action': 'raise', 'raise_to': '8.5'}
        result = self.controller._normalize_response(response)
        self.assertEqual(result['raise_to'], 8.5)

    def test_converts_int_raise_to_float(self):
        """Integer raise_to should be converted to float."""
        response = {'action': 'raise', 'raise_to': 8}
        result = self.controller._normalize_response(response)
        self.assertEqual(result['raise_to'], 8.0)
        self.assertIsInstance(result['raise_to'], float)

    def test_invalid_raise_to_defaults_to_zero(self):
        """Invalid raise_to should default to 0."""
        response = {'action': 'raise', 'raise_to': 'abc'}
        result = self.controller._normalize_response(response)
        self.assertEqual(result['raise_to'], 0)

    def test_missing_raise_to_defaults_to_zero(self):
        """Missing raise_to should default to 0."""
        response = {'action': 'call'}
        result = self.controller._normalize_response(response)
        self.assertEqual(result['raise_to'], 0)

    def test_lowercases_action(self):
        """Action should be lowercased."""
        response = {'action': 'RAISE', 'raise_to': 5}
        result = self.controller._normalize_response(response)
        self.assertEqual(result['action'], 'raise')


class TestApplyFinalFixes(unittest.TestCase):
    """Tests for _apply_final_fixes BB-to-dollar conversion."""

    def setUp(self):
        self.controller = _make_controller()

    def _make_game_state(self, current_ante=100, highest_bet=0):
        gs = MagicMock()
        gs.current_ante = current_ante
        gs.highest_bet = highest_bet
        return gs

    def test_bb_to_dollar_conversion(self):
        """8 BB with big_blind=100 should convert to $800."""
        response = {'action': 'raise', 'raise_to': 8.0}
        context = {'valid_actions': ['fold', 'call', 'raise']}
        result = self.controller._apply_final_fixes(response, context, self._make_game_state(100))
        self.assertEqual(result['raise_to'], 800)
        self.assertEqual(result['_raise_to_bb'], 8.0)

    def test_decimal_bb_to_dollar_conversion(self):
        """8.5 BB with big_blind=100 should convert to $850."""
        response = {'action': 'raise', 'raise_to': 8.5}
        context = {'valid_actions': ['fold', 'call', 'raise']}
        result = self.controller._apply_final_fixes(response, context, self._make_game_state(100))
        self.assertEqual(result['raise_to'], 850)
        self.assertEqual(result['_raise_to_bb'], 8.5)

    def test_fractional_bb_rounds(self):
        """2.5 BB with big_blind=30 = 75, should round correctly."""
        response = {'action': 'raise', 'raise_to': 2.5}
        context = {'valid_actions': ['fold', 'call', 'raise']}
        result = self.controller._apply_final_fixes(response, context, self._make_game_state(30))
        self.assertEqual(result['raise_to'], 75)

    def test_no_conversion_for_non_raise(self):
        """Non-raise actions should not be converted."""
        response = {'action': 'call', 'raise_to': 0}
        context = {'valid_actions': ['fold', 'call', 'raise']}
        result = self.controller._apply_final_fixes(response, context, self._make_game_state(100))
        self.assertEqual(result['raise_to'], 0)

    def test_zero_raise_gets_min_raise_fallback(self):
        """Raise with raise_to=0 should fall back to min raise."""
        response = {'action': 'raise', 'raise_to': 0}
        context = {'valid_actions': ['fold', 'call', 'raise'], 'min_raise': 200}
        gs = self._make_game_state(100, highest_bet=100)
        result = self.controller._apply_final_fixes(response, context, gs)
        # min_raise_to = highest_bet + min_raise = 100 + 200 = 300
        self.assertEqual(result['raise_to'], 300)
        self.assertTrue(result.get('raise_amount_corrected'))


if __name__ == '__main__':
    unittest.main()
