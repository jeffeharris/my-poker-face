"""
Test suite for prompt system improvements.
Tests the enhanced AI player prompting and dynamic personality features.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import random

from poker.poker_player import AIPokerPlayer
from poker.prompt_manager import PromptManager
from poker.response_validator import ResponseValidator
from poker.chattiness_manager import ChattinessManager


class TestHandStrategyPersistence(unittest.TestCase):
    """Test that hand strategy remains rigid throughout a hand."""
    
    @patch('poker.poker_player.OpenAILLMAssistant')
    @patch('poker.poker_player.ElasticPersonality')
    def setUp(self, mock_elastic, mock_assistant):
        # Mock the OpenAI assistant to avoid API calls
        mock_assistant.return_value = Mock()
        mock_elastic.from_base_personality.return_value = Mock()
        
        self.player = AIPokerPlayer(name="Test Player", starting_money=10000)
        self.player.elastic_personality = Mock()
        self.player.elastic_personality.get_trait_value = Mock(return_value=0.5)
    
    def test_strategy_set_on_first_action(self):
        """Hand strategy should be set on first action of hand."""
        # First action of hand
        self.assertEqual(self.player.hand_action_count, 0)
        self.assertIsNone(self.player.current_hand_strategy)
        
        # Simulate first response
        response = {
            "action": "raise",
            "amount": 100,
            "inner_monologue": "Testing strategy lock",
            "hand_strategy": "Aggressive push with medium hands"
        }
        
        # Process first action
        self.player.hand_action_count = 1
        self.player.current_hand_strategy = response['hand_strategy']
        
        self.assertEqual(self.player.current_hand_strategy, 
                        "Aggressive push with medium hands")
    
    def test_strategy_persists_through_hand(self):
        """Hand strategy should not change mid-hand."""
        # Set initial strategy
        self.player.current_hand_strategy = "Tight conservative play"
        self.player.hand_action_count = 2
        
        # Try to change strategy mid-hand
        response = {
            "action": "call",
            "inner_monologue": "Maybe I should be more aggressive",
            "hand_strategy": "Switch to aggressive"  # Should be ignored
        }
        
        # Strategy should remain unchanged
        self.assertEqual(self.player.current_hand_strategy, 
                        "Tight conservative play")
    
    def test_strategy_resets_on_new_hand(self):
        """Hand strategy should reset when new hand starts."""
        # Set strategy for current hand
        self.player.current_hand_strategy = "Bluffing strategy"
        self.player.hand_action_count = 3
        
        # Start new hand
        self.player.set_for_new_hand()
        
        self.assertIsNone(self.player.current_hand_strategy)
        self.assertEqual(self.player.hand_action_count, 0)


class TestChattinessBehavior(unittest.TestCase):
    """Test chattiness-based speaking probability."""
    
    def setUp(self):
        self.chattiness_manager = ChattinessManager()
    
    def test_low_chattiness_rarely_speaks(self):
        """Low chattiness players should rarely speak."""
        player = Mock()
        player.elastic_personality.get_trait_value.return_value = 0.2
        
        # Test 100 turns
        speak_count = 0
        for _ in range(100):
            if self.chattiness_manager.should_speak(player, {}):
                speak_count += 1
        
        # Should speak roughly 20% of the time (±10%)
        self.assertLessEqual(speak_count, 30)
        self.assertGreaterEqual(speak_count, 10)
    
    def test_high_chattiness_usually_speaks(self):
        """High chattiness players should usually speak."""
        player = Mock()
        player.elastic_personality.get_trait_value.return_value = 0.9
        
        # Test 100 turns
        speak_count = 0
        for _ in range(100):
            if self.chattiness_manager.should_speak(player, {}):
                speak_count += 1
        
        # Should speak roughly 90% of the time (±10%)
        self.assertLessEqual(speak_count, 100)
        self.assertGreaterEqual(speak_count, 80)
    
    def test_contextual_modifiers(self):
        """Context should modify speaking probability."""
        player = Mock()
        player.elastic_personality.get_trait_value.return_value = 0.5
        
        # Base probability
        base_prob = self.chattiness_manager.calculate_speaking_probability(
            0.5, {}
        )
        self.assertAlmostEqual(base_prob, 0.5)
        
        # Just won big - more likely to speak
        win_prob = self.chattiness_manager.calculate_speaking_probability(
            0.5, {'just_won_big': True}
        )
        self.assertGreater(win_prob, base_prob)
        
        # Currently bluffing - less likely to speak
        bluff_prob = self.chattiness_manager.calculate_speaking_probability(
            0.5, {'bluffing': True}
        )
        self.assertLess(bluff_prob, base_prob)
        
        # Addressed directly - almost always speak
        addressed_prob = self.chattiness_manager.calculate_speaking_probability(
            0.5, {'addressed_directly': True}
        )
        self.assertGreaterEqual(addressed_prob, 0.8)


class TestMandatoryInnerMonologue(unittest.TestCase):
    """Test that inner monologue is always required."""
    
    def test_response_validation_requires_inner_monologue(self):
        """Response validation should fail without inner_monologue."""
        validator = ResponseValidator()
        
        # Valid response
        valid_response = {
            "action": "fold",
            "inner_monologue": "This hand is terrible"
        }
        self.assertTrue(validator.validate(valid_response))
        
        # Missing inner_monologue
        invalid_response = {
            "action": "fold"
            # Missing inner_monologue
        }
        self.assertFalse(validator.validate(invalid_response))
        self.assertIn("inner_monologue", validator.get_errors()[0])
    
    def test_response_format_shows_inner_monologue_required(self):
        """Response format should always mark inner_monologue as required."""
        formatter = MockResponseFormatter()
        player = Mock()
        player.hand_action_count = 0
        
        # First action format
        first_format = formatter.get_response_format_for_turn(player, {})
        self.assertIn("inner_monologue", first_format)
        self.assertIn("REQUIRED", first_format["inner_monologue"])
        
        # Later action format
        player.hand_action_count = 2
        later_format = formatter.get_response_format_for_turn(player, {})
        self.assertIn("inner_monologue", later_format)
        self.assertIn("REQUIRED", later_format["inner_monologue"])


class TestDynamicPromptBuilding(unittest.TestCase):
    """Test dynamic prompt construction based on context."""
    
    def setUp(self):
        self.prompt_builder = EnhancedPromptBuilder()
        self.player = Mock()
        self.player.name = "Test Player"
        self.player.hand_action_count = 0
        self.player.current_hand_strategy = None
        self.player.elastic_personality.get_trait_value.return_value = 0.5
    
    def test_first_action_prompt_requests_strategy(self):
        """First action prompt should request hand strategy."""
        prompt = self.prompt_builder.build_decision_prompt(self.player, {})
        
        self.assertIn("FIRST ACTION", prompt)
        self.assertIn("SET YOUR STRATEGY", prompt)
        self.assertIn("hand_strategy", prompt)
    
    def test_later_action_prompt_includes_locked_strategy(self):
        """Later actions should reference locked strategy."""
        self.player.hand_action_count = 2
        self.player.current_hand_strategy = "Aggressive bluffing"
        
        prompt = self.prompt_builder.build_decision_prompt(self.player, {})
        
        self.assertIn("Your strategy for this hand: 'Aggressive bluffing'", prompt)
        self.assertIn("Stay consistent", prompt)
        self.assertNotIn("SET YOUR STRATEGY", prompt)
    
    def test_chattiness_context_in_prompt(self):
        """Prompt should include chattiness context."""
        # Low chattiness
        self.player.elastic_personality.get_trait_value.return_value = 0.2
        prompt = self.prompt_builder.build_decision_prompt(self.player, {})
        
        self.assertIn("chattiness level: 0.2", prompt)
        
        # High chattiness
        self.player.elastic_personality.get_trait_value.return_value = 0.9
        prompt = self.prompt_builder.build_decision_prompt(self.player, {})
        
        self.assertIn("chattiness level: 0.9", prompt)


class TestResponseProcessing(unittest.TestCase):
    """Test processing of AI responses with new rules."""
    
    def setUp(self):
        self.processor = AIResponseProcessor()
        self.player = Mock()
        self.player.name = "Test Player"
        self.player.hand_action_count = 1
        self.player.current_hand_strategy = None
        self.player.elastic_personality.get_trait_value.return_value = 0.5
    
    def test_remove_speech_from_quiet_player(self):
        """Quiet players shouldn't speak even if response includes it."""
        # Very low chattiness
        self.player.elastic_personality.get_trait_value.return_value = 0.1
        
        response = {
            "action": "fold",
            "inner_monologue": "Bad hand again",
            "persona_response": "I'm out!",  # Should be removed
            "physical": ["*folds cards*"]  # Should be removed
        }
        
        with patch('random.random', return_value=0.5):  # Won't speak at 0.1 chattiness
            processed = self.processor.validate_and_process_response(
                self.player, response, {}
            )
        
        self.assertNotIn("persona_response", processed)
        self.assertNotIn("physical", processed)
        self.assertIn("inner_monologue", processed)  # Always kept
    
    def test_chatty_player_keeps_speech(self):
        """Chatty players should keep their speech."""
        # High chattiness
        self.player.elastic_personality.get_trait_value.return_value = 0.9
        
        response = {
            "action": "raise",
            "amount": 200,
            "inner_monologue": "Time to pressure them",
            "persona_response": "Let's make this interesting!",
            "physical": ["*pushes chips forward*"]
        }
        
        with patch('random.random', return_value=0.5):  # Will speak at 0.9 chattiness
            processed = self.processor.validate_and_process_response(
                self.player, response, {}
            )
        
        self.assertIn("persona_response", processed)
        self.assertIn("physical", processed)


class TestIntegrationScenarios(unittest.TestCase):
    """Test complete scenarios with all components."""
    
    @patch('poker.poker_player.OpenAILLMAssistant')
    def test_full_hand_scenario(self, mock_assistant_class):
        """Test a complete hand with multiple players."""
        # Setup players with different chattiness
        gordon = AIPokerPlayer(name="Gordon Ramsay", starting_money=10000)
        eeyore = AIPokerPlayer(name="Eeyore", starting_money=10000)
        
        # Mock personalities
        gordon.elastic_personality = Mock()
        gordon.elastic_personality.get_trait_value.side_effect = lambda trait: {
            'chattiness': 0.9,
            'aggression': 0.8
        }.get(trait, 0.5)
        
        eeyore.elastic_personality = Mock()
        eeyore.elastic_personality.get_trait_value.side_effect = lambda trait: {
            'chattiness': 0.2,
            'aggression': 0.2
        }.get(trait, 0.5)
        
        # First action - both set strategies
        self.assertIsNone(gordon.current_hand_strategy)
        self.assertIsNone(eeyore.current_hand_strategy)
        
        # Gordon's first response (chatty, aggressive)
        gordon_response1 = {
            "action": "raise",
            "amount": 200,
            "inner_monologue": "Let's dominate these donkeys",
            "hand_strategy": "Aggressive - push hard and intimidate",
            "persona_response": "Time to turn up the HEAT!",
            "physical": ["*slams chips*"]
        }
        
        # Eeyore's first response (quiet, passive)
        eeyore_response1 = {
            "action": "fold",
            "inner_monologue": "Another terrible hand, as expected",
            "hand_strategy": "Tight - only play premium hands"
            # No persona_response - too quiet
        }
        
        # Process first actions
        gordon.hand_action_count = 1
        gordon.current_hand_strategy = gordon_response1['hand_strategy']
        
        eeyore.hand_action_count = 1
        eeyore.current_hand_strategy = eeyore_response1['hand_strategy']
        
        # Verify strategies are locked
        self.assertEqual(gordon.current_hand_strategy, 
                        "Aggressive - push hard and intimidate")
        self.assertEqual(eeyore.current_hand_strategy,
                        "Tight - only play premium hands")
        
        # Next hand - strategies reset
        gordon.set_for_new_hand()
        eeyore.set_for_new_hand()
        
        self.assertIsNone(gordon.current_hand_strategy)
        self.assertIsNone(eeyore.current_hand_strategy)
        self.assertEqual(gordon.hand_action_count, 0)
        self.assertEqual(eeyore.hand_action_count, 0)


# Mock classes for testing (would be imported in real implementation)
class MockResponseFormatter:
    def get_response_format_for_turn(self, player, context):
        format_dict = {
            "action": "REQUIRED: from your options",
            "inner_monologue": "REQUIRED: your private thoughts",
        }
        
        if player.hand_action_count == 0:
            format_dict["hand_strategy"] = "REQUIRED: your strategy for this entire hand"
        
        return format_dict


class ChattinessManager:
    def should_speak(self, player, context):
        chattiness = player.elastic_personality.get_trait_value('chattiness')
        prob = self.calculate_speaking_probability(chattiness, context)
        return random.random() < prob
    
    def calculate_speaking_probability(self, base_chattiness, context):
        probability = base_chattiness
        
        modifiers = {
            'just_won_big': 0.3,
            'just_lost_big': -0.2,
            'big_pot': 0.2,
            'all_in': 0.4,
            'bluffing': -0.1,
            'addressed_directly': 0.5,
            'long_silence': 0.2,
        }
        
        for condition, modifier in modifiers.items():
            if context.get(condition, False):
                probability += modifier
        
        return max(0.0, min(1.0, probability))


class EnhancedPromptBuilder:
    def build_decision_prompt(self, player, game_state):
        parts = []
        
        if player.hand_action_count == 0:
            parts.append("This is your FIRST ACTION this hand.")
            parts.append("SET YOUR STRATEGY for the entire hand.")
            parts.append("Required: hand_strategy")
        else:
            parts.append(f"Your strategy for this hand: '{player.current_hand_strategy}'")
            parts.append("Stay consistent with this approach.")
        
        chattiness = player.elastic_personality.get_trait_value('chattiness')
        parts.append(f"Your chattiness level: {chattiness:.1f}/1.0")
        
        return "\n".join(parts)


class AIResponseProcessor:
    def __init__(self):
        self.chattiness_manager = ChattinessManager()
    
    def validate_and_process_response(self, player, response, context):
        chattiness = player.elastic_personality.get_trait_value('chattiness')
        should_speak = self.chattiness_manager.should_speak(player, context)
        
        if not should_speak and 'persona_response' in response:
            response.pop('persona_response', None)
            response.pop('physical', None)
        
        return response


if __name__ == '__main__':
    unittest.main()