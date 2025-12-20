"""
Tests for AI resilience and error handling.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import time

from poker.ai_resilience import (
    CircuitBreaker,
    with_ai_fallback,
    expects_json,
    parse_json_response,
    validate_ai_response,
    get_fallback_chat_response,
    AIError,
    AIResponseError,
    AIFallbackStrategy
)


class TestCircuitBreaker(unittest.TestCase):
    """Test the circuit breaker pattern implementation."""
    
    def setUp(self):
        self.cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
    
    def test_initial_state(self):
        """Circuit breaker should start closed."""
        self.assertTrue(self.cb.can_attempt())
        self.assertFalse(self.cb.is_open)
        self.assertEqual(self.cb.failure_count, 0)
    
    def test_opens_after_threshold(self):
        """Circuit breaker should open after failure threshold."""
        # Record failures up to threshold
        for i in range(3):
            self.cb.record_failure()
            if i < 2:
                self.assertTrue(self.cb.can_attempt())
                self.assertFalse(self.cb.is_open)
            else:
                self.assertFalse(self.cb.can_attempt())
                self.assertTrue(self.cb.is_open)
    
    def test_resets_on_success(self):
        """Circuit breaker should reset on success."""
        self.cb.record_failure()
        self.cb.record_failure()
        self.assertEqual(self.cb.failure_count, 2)
        
        self.cb.record_success()
        self.assertEqual(self.cb.failure_count, 0)
        self.assertFalse(self.cb.is_open)
    
    def test_recovery_timeout(self):
        """Circuit breaker should allow retry after recovery timeout."""
        # Open the circuit
        for _ in range(3):
            self.cb.record_failure()
        self.assertFalse(self.cb.can_attempt())
        
        # Wait for recovery timeout
        time.sleep(1.1)
        self.assertTrue(self.cb.can_attempt())
        self.assertFalse(self.cb.is_open)


class TestJSONParsing(unittest.TestCase):
    """Test JSON response parsing and validation."""
    
    def test_parse_valid_json(self):
        """Should parse valid JSON."""
        response = '{"action": "call", "amount": 100}'
        result = parse_json_response(response)
        self.assertEqual(result, {"action": "call", "amount": 100})
    
    def test_parse_json_from_markdown(self):
        """Should extract JSON from markdown code blocks."""
        response = '''Here's my decision:
```json
{"action": "raise", "amount": 200}
```
That's my move!'''
        result = parse_json_response(response)
        self.assertEqual(result, {"action": "raise", "amount": 200})
    
    def test_parse_json_from_generic_code_block(self):
        """Should extract JSON from generic code blocks."""
        response = '''```
{"action": "fold", "amount": 0}
```'''
        result = parse_json_response(response)
        self.assertEqual(result, {"action": "fold", "amount": 0})
    
    def test_fix_single_quotes(self):
        """Should fix JSON with single quotes."""
        response = "{'action': 'check', 'amount': 0}"
        result = parse_json_response(response)
        self.assertEqual(result, {"action": "check", "amount": 0})
    
    def test_extract_json_from_text(self):
        """Should extract JSON object from surrounding text."""
        response = 'I think I\'ll {"action": "call", "amount": 50} because the pot odds are good.'
        result = parse_json_response(response)
        self.assertEqual(result, {"action": "call", "amount": 50})
    
    def test_empty_response_raises_error(self):
        """Should raise error for empty response."""
        with self.assertRaises(AIResponseError):
            parse_json_response("")
    
    def test_invalid_json_raises_error(self):
        """Should raise error for unparseable JSON."""
        with self.assertRaises(AIResponseError):
            parse_json_response("not json at all {broken:")


class TestResponseValidation(unittest.TestCase):
    """Test AI response validation."""
    
    def test_validate_valid_response(self):
        """Should pass through valid response."""
        response = {"action": "call", "adding_to_pot": 100}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result, {"action": "call", "adding_to_pot": 100})
    
    def test_validate_invalid_action(self):
        """Should fix invalid action."""
        response = {"action": "bluff", "amount": 100}
        valid_actions = ["fold", "check", "call"]
        result = validate_ai_response(response, valid_actions)
        self.assertIn(result["action"], valid_actions)
        self.assertEqual(result["adding_to_pot"], 0)  # Check doesn't need amount
    
    def test_validate_negative_amount(self):
        """Should fix negative amounts."""
        response = {"action": "raise", "amount": -50}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["adding_to_pot"], 0)
    
    def test_validate_non_dict_response(self):
        """Should handle non-dict responses."""
        result = validate_ai_response("not a dict", ["fold"])
        self.assertEqual(result, {"action": "fold", "adding_to_pot": 0})
    
    def test_validate_string_amount(self):
        """Should convert string amounts to int."""
        response = {"action": "call", "amount": "100"}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["adding_to_pot"], 100)
    
    def test_validate_capitalized_action(self):
        """Should normalize capitalized actions to lowercase."""
        # Test "Raise" -> "raise"
        response = {"action": "Raise", "adding_to_pot": 100}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["action"], "raise")
        self.assertEqual(result["adding_to_pot"], 100)
        
        # Test "Call" -> "call"
        response = {"action": "Call", "adding_to_pot": 50}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["action"], "call")
        self.assertEqual(result["adding_to_pot"], 50)
        
        # Test "Fold" -> "fold"
        response = {"action": "Fold", "adding_to_pot": 0}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["action"], "fold")
        self.assertEqual(result["adding_to_pot"], 0)
        
        # Test "Check" -> "check"
        response = {"action": "Check", "adding_to_pot": 0}
        valid_actions = ["fold", "check"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["action"], "check")
        self.assertEqual(result["adding_to_pot"], 0)
    
    def test_validate_mixed_case_action(self):
        """Should normalize mixed-case actions to lowercase."""
        response = {"action": "CALL", "adding_to_pot": 75}
        valid_actions = ["fold", "call", "raise"]
        result = validate_ai_response(response, valid_actions)
        self.assertEqual(result["action"], "call")
        self.assertEqual(result["adding_to_pot"], 75)


class TestFallbackDecorator(unittest.TestCase):
    """Test the with_ai_fallback decorator."""
    
    def test_successful_call(self):
        """Should return result on successful call."""
        @with_ai_fallback()
        def mock_ai_call():
            return {"action": "call", "amount": 50}
        
        result = mock_ai_call()
        self.assertEqual(result, {"action": "call", "amount": 50})
    
    def test_retry_on_failure(self):
        """Should retry on failure."""
        call_count = 0
        
        @with_ai_fallback(max_retries=3)
        def mock_ai_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("API Error")
            return {"action": "fold", "amount": 0}
        
        result = mock_ai_call()
        self.assertEqual(call_count, 3)
        self.assertEqual(result, {"action": "fold", "amount": 0})
    
    def test_fallback_on_all_failures(self):
        """Should use fallback after all retries fail."""
        @with_ai_fallback(max_retries=2)
        def mock_ai_call(valid_actions=None):
            raise Exception("API Error")
        
        result = mock_ai_call(valid_actions=["fold", "check"])
        self.assertIn(result["action"], ["fold", "check"])
    
    def test_custom_fallback_function(self):
        """Should use custom fallback function if provided."""
        def custom_fallback(*args, **kwargs):
            return {"action": "all_in", "amount": 1000}
        
        @with_ai_fallback(fallback_fn=custom_fallback, max_retries=1)
        def mock_ai_call():
            raise Exception("API Error")
        
        result = mock_ai_call()
        self.assertEqual(result, {"action": "all_in", "amount": 1000})
    
    def test_json_validation_with_decorator(self):
        """Should validate JSON when expects_json is used."""
        @with_ai_fallback()
        @expects_json
        def mock_ai_call(**kwargs):
            return '{"action": "raise", "amount": 100}'
        
        # Mark the function as expecting JSON
        mock_ai_call._expects_json = True
        
        result = mock_ai_call(valid_actions=["fold", "call", "raise"])
        self.assertEqual(result, {"action": "raise", "amount": 100})


class TestFallbackStrategies(unittest.TestCase):
    """Test different fallback strategies."""
    
    def test_conservative_fallback(self):
        """Conservative strategy should check/call, never raise."""
        @with_ai_fallback(fallback_strategy=AIFallbackStrategy.CONSERVATIVE)
        def mock_ai_call(**kwargs):
            raise Exception("API Error")
        
        # Should check when available
        result = mock_ai_call(valid_actions=["fold", "check", "raise"])
        self.assertEqual(result["action"], "check")
        
        # Should call when check not available
        result = mock_ai_call(valid_actions=["fold", "call", "raise"], call_amount=50)
        self.assertEqual(result["action"], "call")
        self.assertEqual(result["adding_to_pot"], 50)
        
        # Should fold when no other option
        result = mock_ai_call(valid_actions=["fold"])
        self.assertEqual(result["action"], "fold")
    
    def test_random_valid_fallback(self):
        """Random strategy should pick from valid actions."""
        @with_ai_fallback(fallback_strategy=AIFallbackStrategy.RANDOM_VALID)
        def mock_ai_call(**kwargs):
            raise Exception("API Error")
        
        valid_actions = ["fold", "check", "call", "raise"]
        results = set()
        
        # Run multiple times to see randomness
        for _ in range(20):
            result = mock_ai_call(valid_actions=valid_actions, call_amount=50, min_raise=10, max_raise=100)
            results.add(result["action"])
        
        # Should have picked multiple different actions
        self.assertGreater(len(results), 1)
        self.assertTrue(all(action in valid_actions for action in results))
    
    def test_personality_based_fallback(self):
        """Personality-based strategy should respect traits."""
        # Test aggressive personality
        aggressive_controller = Mock()
        aggressive_controller.personality_traits = {"aggression": 0.9, "bluff_tendency": 0.8}
        
        @with_ai_fallback(fallback_strategy=AIFallbackStrategy.MIMIC_PERSONALITY)
        def mock_aggressive_call(self, **kwargs):
            raise Exception("API Error")
        
        # Bind the method to the mock controller
        mock_aggressive_call = mock_aggressive_call.__get__(aggressive_controller, type(aggressive_controller))
        
        # Should favor aggressive actions
        results = []
        for _ in range(10):
            result = mock_aggressive_call(
                valid_actions=["fold", "call", "raise"],
                call_amount=50,
                min_raise=10,
                max_raise=100
            )
            results.append(result["action"])
        
        # Should rarely fold with high aggression
        fold_count = results.count("fold")
        self.assertLess(fold_count, 3)  # Less than 30% folds


class TestFallbackChatResponse(unittest.TestCase):
    """Test fallback chat message generation."""
    
    def test_default_messages(self):
        """Should return appropriate default messages."""
        message = get_fallback_chat_response("Unknown Player")
        self.assertIsInstance(message, str)
        self.assertGreater(len(message), 0)
    
    def test_personality_specific_messages(self):
        """Should return personality-specific messages when available."""
        # Test known personality types
        aggressive_msg = get_fallback_chat_response("aggressive")
        self.assertIn(aggressive_msg, [
            "Time to show who's boss.",
            "I'm not backing down.",
            "Let's see what you got.",
            "Bring it on!"
        ])
        
        conservative_msg = get_fallback_chat_response("conservative")
        self.assertIn(conservative_msg, [
            "Need to play this carefully.",
            "Better safe than sorry.",
            "Patience is key.",
            "Slow and steady..."
        ])


if __name__ == '__main__':
    unittest.main()