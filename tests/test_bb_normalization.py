"""
Tests for BB (Big Blind) normalization functionality.

Tests the _format_money helper, message conversion, and BB-to-dollar
conversion logic. BB mode is always active for AI prompts.
"""
import unittest
from poker.controllers import _format_money, _convert_messages_to_bb


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


if __name__ == '__main__':
    unittest.main()
