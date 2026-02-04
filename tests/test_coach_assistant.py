"""Tests for coach_assistant helper functions.

Tests _normalize_action and _parse_coach_response for LLM response parsing.
"""

import unittest
from unittest.mock import patch

from flask_app.services.coach_assistant import (
    _normalize_action,
    _parse_coach_response,
    CoachResponse,
)


class TestNormalizeAction(unittest.TestCase):
    """Tests for _normalize_action validation and normalization."""

    # --- None/empty input tests ---

    def test_none_input_returns_none(self):
        """None action input returns None."""
        result = _normalize_action(None, ['fold', 'call', 'raise'])
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        result = _normalize_action('', ['fold', 'call', 'raise'])
        self.assertIsNone(result)

    # --- Whitespace and case normalization ---

    def test_strips_whitespace(self):
        """Leading/trailing whitespace is stripped."""
        result = _normalize_action('  fold  ', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'fold')

    def test_normalizes_to_lowercase(self):
        """Action is normalized to lowercase."""
        result = _normalize_action('FOLD', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'fold')

    def test_case_and_whitespace_combined(self):
        """Both case and whitespace normalization applied."""
        result = _normalize_action('  CALL  ', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'call')

    # --- Bet/raise mapping ---

    def test_maps_bet_to_raise(self):
        """'bet' is mapped to 'raise'."""
        result = _normalize_action('bet', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'raise')

    def test_raise_matches_bet_in_available(self):
        """When only 'bet' is available, 'raise' normalizes to 'raise' anyway."""
        result = _normalize_action('raise', ['fold', 'call', 'bet'])
        self.assertEqual(result, 'raise')

    # --- All-in variations ---

    def test_maps_all_in_hyphen_to_raise(self):
        """'all-in' maps to 'raise'."""
        result = _normalize_action('all-in', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'raise')

    def test_maps_allin_to_raise(self):
        """'allin' maps to 'raise'."""
        result = _normalize_action('allin', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'raise')

    def test_maps_all_in_space_to_raise(self):
        """'all in' maps to 'raise'."""
        result = _normalize_action('all in', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'raise')

    def test_maps_all_in_underscore_to_raise(self):
        """'all_in' maps to 'raise'."""
        result = _normalize_action('all_in', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'raise')

    def test_all_in_uppercase_to_raise(self):
        """'ALL IN' (uppercase with space) maps to 'raise'."""
        result = _normalize_action('ALL IN', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'raise')

    # --- Check variations ---

    def test_maps_pass_to_check(self):
        """'pass' is mapped to 'check'."""
        result = _normalize_action('pass', ['check', 'bet'])
        self.assertEqual(result, 'check')

    def test_check_returns_check(self):
        """'check' returns 'check' when available."""
        result = _normalize_action('check', ['check', 'bet'])
        self.assertEqual(result, 'check')

    # --- Call variations ---

    def test_maps_match_to_call(self):
        """'match' is mapped to 'call'."""
        result = _normalize_action('match', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'call')

    def test_call_returns_call(self):
        """'call' returns 'call' when available."""
        result = _normalize_action('call', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'call')

    # --- Fold variations ---

    def test_maps_muck_to_fold(self):
        """'muck' is mapped to 'fold'."""
        result = _normalize_action('muck', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'fold')

    def test_fold_returns_fold(self):
        """'fold' returns 'fold' when available."""
        result = _normalize_action('fold', ['fold', 'call', 'raise'])
        self.assertEqual(result, 'fold')

    # --- Validation against available actions ---

    def test_validates_against_available_actions(self):
        """Action must be in available_actions list."""
        result = _normalize_action('raise', ['fold', 'call'])  # No raise available
        self.assertIsNone(result)

    def test_unknown_action_returns_none(self):
        """Unknown/invalid action returns None with warning."""
        with patch('flask_app.services.coach_assistant.logger') as mock_logger:
            result = _normalize_action('gibberish', ['fold', 'call', 'raise'])
            self.assertIsNone(result)
            mock_logger.warning.assert_called()

    def test_empty_available_actions_returns_none(self):
        """Empty available actions list returns None."""
        result = _normalize_action('fold', [])
        self.assertIsNone(result)


class TestParseCoachResponse(unittest.TestCase):
    """Tests for _parse_coach_response JSON parsing and validation."""

    def _make_coaching_data(self, available_actions=None):
        """Create minimal coaching data dict."""
        return {
            'available_actions': available_actions or ['fold', 'call', 'raise'],
        }

    # --- Valid JSON parsing ---

    def test_valid_json_all_fields(self):
        """Valid JSON with all fields parses correctly."""
        response = '{"advice": "Call here.", "action": "call", "raise_to": null}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['advice'], 'Call here.')
        self.assertEqual(result['action'], 'call')
        self.assertIsNone(result['raise_to'])

    def test_valid_json_missing_optional_fields(self):
        """JSON with only advice parses, action defaults to None."""
        response = '{"advice": "Just some advice."}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['advice'], 'Just some advice.')
        self.assertIsNone(result['action'])
        self.assertIsNone(result['raise_to'])

    def test_valid_json_extra_fields_ignored(self):
        """Extra fields in JSON are ignored."""
        response = '{"advice": "Call.", "action": "call", "extra_field": "ignored"}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['advice'], 'Call.')
        self.assertEqual(result['action'], 'call')
        self.assertNotIn('extra_field', result)

    # --- Invalid JSON fallback ---

    def test_invalid_json_returns_fallback(self):
        """Invalid JSON returns cleaned response as advice."""
        response = 'This is not JSON at all.'
        with patch('flask_app.services.coach_assistant.logger'):
            result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['advice'], 'This is not JSON at all.')
        self.assertIsNone(result['action'])
        self.assertIsNone(result['raise_to'])

    def test_empty_string_returns_fallback(self):
        """Empty string returns default fallback message."""
        response = ''
        with patch('flask_app.services.coach_assistant.logger'):
            result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['advice'], "I couldn't format my response properly.")
        self.assertIsNone(result['action'])

    def test_fallback_truncates_long_response(self):
        """Fallback truncates response to 500 characters."""
        long_response = 'A' * 600
        with patch('flask_app.services.coach_assistant.logger'):
            result = _parse_coach_response(long_response, self._make_coaching_data())

        self.assertEqual(len(result['advice']), 500)
        self.assertTrue(result['advice'].startswith('A' * 500))

    # --- raise_to validation ---

    def test_raise_to_converted_to_int(self):
        """raise_to is converted to integer."""
        response = '{"advice": "Raise.", "action": "raise", "raise_to": 200}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['raise_to'], 200)
        self.assertIsInstance(result['raise_to'], int)

    def test_raise_to_float_rounded(self):
        """Float raise_to is rounded to nearest integer."""
        response = '{"advice": "Raise.", "action": "raise", "raise_to": 199.7}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['raise_to'], 200)

    def test_raise_to_string_number_converted(self):
        """String number raise_to is converted."""
        response = '{"advice": "Raise.", "action": "raise", "raise_to": "150"}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['raise_to'], 150)

    def test_raise_to_negative_becomes_none(self):
        """Negative raise_to becomes None."""
        response = '{"advice": "Raise.", "action": "raise", "raise_to": -50}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertIsNone(result['raise_to'])

    def test_raise_to_zero_becomes_none(self):
        """Zero raise_to becomes None."""
        response = '{"advice": "Raise.", "action": "raise", "raise_to": 0}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertIsNone(result['raise_to'])

    def test_raise_to_non_numeric_becomes_none(self):
        """Non-numeric raise_to becomes None."""
        response = '{"advice": "Raise.", "action": "raise", "raise_to": "big"}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertIsNone(result['raise_to'])

    def test_raise_to_stripped_when_not_raise_action(self):
        """raise_to is None when action is not 'raise'."""
        response = '{"advice": "Call.", "action": "call", "raise_to": 200}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['action'], 'call')
        self.assertIsNone(result['raise_to'])

    # --- Action validation ---

    def test_action_validated_against_available(self):
        """Action is validated against available_actions."""
        response = '{"advice": "Raise.", "action": "raise"}'
        coaching_data = self._make_coaching_data(['fold', 'call'])  # No raise

        with patch('flask_app.services.coach_assistant.logger'):
            result = _parse_coach_response(response, coaching_data)

        self.assertIsNone(result['action'])  # Raise not available

    def test_action_normalized_before_validation(self):
        """Action is normalized (bet→raise) before validation."""
        response = '{"advice": "Bet big.", "action": "bet"}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertEqual(result['action'], 'raise')

    def test_null_action_in_json_returns_none(self):
        """Explicit null action in JSON returns None action."""
        response = '{"advice": "Figure it out.", "action": null}'
        result = _parse_coach_response(response, self._make_coaching_data())

        self.assertIsNone(result['action'])


class TestCoachResponseType(unittest.TestCase):
    """Tests for CoachResponse TypedDict structure."""

    def test_coach_response_has_required_keys(self):
        """CoachResponse TypedDict has expected keys."""
        # This is a structural test - CoachResponse should have these fields
        response: CoachResponse = {
            'advice': 'Test advice',
            'action': 'call',
            'raise_to': None,
        }
        self.assertIn('advice', response)
        self.assertIn('action', response)
        self.assertIn('raise_to', response)


if __name__ == '__main__':
    unittest.main()
