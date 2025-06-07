"""
Integration tests for the complete prompt improvements system.
Tests all components working together for natural gameplay.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import time
from typing import Dict, List

from poker.controllers import AIPlayerController
from poker.poker_player import AIPokerPlayer
from poker.prompt_manager import PromptManager
from poker.response_validator import ResponseValidator
from poker.chattiness_manager import ChattinessManager
from poker.poker_game import PokerGameState
from old_files.deck import Card


class TestFullIntegration(unittest.TestCase):
    """Test the complete prompt improvements system integration."""
    
    def setUp(self):
        """Set up test components."""
        # Mock OpenAI
        self.mock_assistant = Mock()
        self.mock_assistant.system_message = "Test system message"
        self.mock_assistant.chat.return_value = '{"action": "call", "adding_to_pot": 10}'
        
        # Create components
        self.prompt_manager = PromptManager()
        self.response_validator = ResponseValidator()
        self.chattiness_manager = ChattinessManager()
    
    def create_mock_game_state(self, current_player_name="Test Player") -> Mock:
        """Create a mock game state for testing."""
        game_state = Mock(spec=PokerGameState)
        
        # Current player
        current_player = Mock()
        current_player.name = current_player_name
        current_player.bet = 10
        current_player.stack = 990
        current_player.is_all_in = False
        game_state.current_player = current_player
        
        # Other players
        other_player = Mock()
        other_player.name = "Other Player"
        other_player.bet = 20
        other_player.is_active = True
        other_player.is_all_in = False
        game_state.players = [current_player, other_player]
        
        # Game state properties
        game_state.highest_bet = 20
        game_state.current_player_options = ['fold', 'call', 'raise']
        game_state.pot = {'total': 50}
        game_state.community_cards = []
        
        return game_state
    
    def simulate_full_hand(self, ai_personality: str, chattiness: float) -> Dict:
        """Simulate a full hand with the given AI personality."""
        results = {
            'personality': ai_personality,
            'chattiness': chattiness,
            'actions': [],
            'spoke_count': 0,
            'total_actions': 0,
            'strategies': []
        }
        
        with patch('poker.poker_player.OpenAILLMAssistant') as mock_llm:
            mock_llm.return_value = self.mock_assistant
            
            # Create controller (it creates its own AI player)
            controller = AIPlayerController(
                player_name=ai_personality,
                ai_temp=0.9
            )
            
            # Update the AI player's chattiness
            controller.ai_player.personality_config['personality_traits']['chattiness'] = chattiness
            
            # Simulate 4 betting rounds (pre-flop, flop, turn, river)
            for round_num in range(4):
                # Create game state for this round
                game_state = self.create_mock_game_state(ai_personality)
                
                # Determine if AI should speak this turn
                context = {'round': round_num}
                should_speak = self.chattiness_manager.should_speak(
                    ai_personality, chattiness, context
                )
                
                # Build AI response
                response = {
                    "action": "check" if round_num % 2 == 0 else "call",
                    "adding_to_pot": 0 if round_num % 2 == 0 else 10,
                    "inner_monologue": f"Thinking in round {round_num}..."
                }
                
                # Add strategy on first action
                if controller.ai_player.hand_action_count == 0:
                    strategy = f"Play tight-aggressive with position"
                    response["hand_strategy"] = strategy
                    results['strategies'].append(strategy)
                
                # Add speech if appropriate
                if should_speak:
                    response["persona_response"] = f"Round {round_num} comment"
                    response["physical"] = "nods"
                    results['spoke_count'] += 1
                
                # Mock the AI response
                self.mock_assistant.chat.return_value = json.dumps(response)
                
                # Get AI decision
                decision = controller.decide_action(
                    game_state=game_state,
                    hand_state={'round': round_num}
                )
                
                results['actions'].append(decision)
                results['total_actions'] += 1
                
                # Verify response was cleaned appropriately
                if not should_speak:
                    self.assertNotIn('persona_response', decision)
                    self.assertNotIn('physical', decision)
                else:
                    self.assertIn('persona_response', decision)
        
        return results
    
    @patch('core.assistants.OpenAILLMAssistant')
    def test_quiet_personalities_stay_quiet(self, mock_llm_class):
        """Test that quiet personalities speak less frequently."""
        mock_llm_class.return_value = self.mock_assistant
        
        # Test with a quiet personality
        results = self.simulate_full_hand("Silent Bob", chattiness=0.1)
        
        # Quiet personalities should speak less than 50% of the time
        speaking_rate = results['spoke_count'] / results['total_actions']
        self.assertLess(speaking_rate, 0.5)
        print(f"\nSilent Bob spoke {results['spoke_count']}/{results['total_actions']} times ({speaking_rate:.1%})")
    
    @patch('core.assistants.OpenAILLMAssistant')
    def test_chatty_personalities_speak_often(self, mock_llm_class):
        """Test that chatty personalities speak frequently."""
        mock_llm_class.return_value = self.mock_assistant
        
        # Test with a chatty personality
        results = self.simulate_full_hand("Gordon Ramsay", chattiness=0.9)
        
        # Chatty personalities should speak more than 70% of the time
        speaking_rate = results['spoke_count'] / results['total_actions']
        self.assertGreater(speaking_rate, 0.7)
        print(f"\nGordon spoke {results['spoke_count']}/{results['total_actions']} times ({speaking_rate:.1%})")
    
    @patch('core.assistants.OpenAILLMAssistant')
    def test_hand_strategy_persists(self, mock_llm_class):
        """Test that hand strategy is set once and persists."""
        mock_llm_class.return_value = self.mock_assistant
        
        # Simulate a hand
        results = self.simulate_full_hand("Sherlock Holmes", chattiness=0.5)
        
        # Should have exactly one strategy
        self.assertEqual(len(results['strategies']), 1)
        print(f"\nStrategy locked: {results['strategies'][0]}")
    
    @patch('core.assistants.OpenAILLMAssistant')
    def test_inner_monologue_always_present(self, mock_llm_class):
        """Test that inner monologue is always present."""
        mock_llm_class.return_value = self.mock_assistant
        
        # Test multiple personalities
        for personality in ["Silent Bob", "Eeyore", "Gordon Ramsay"]:
            results = self.simulate_full_hand(personality, chattiness=0.5)
            
            # Every action should have inner monologue
            for action in results['actions']:
                self.assertIn('inner_monologue', action)
                self.assertTrue(action['inner_monologue'])
    
    @patch('core.assistants.OpenAILLMAssistant')
    def test_response_cleaning_works(self, mock_llm_class):
        """Test that responses are cleaned based on context."""
        mock_llm_class.return_value = self.mock_assistant
        
        with patch('poker.chattiness_manager.ChattinessManager.should_speak') as mock_should_speak:
            # Force no speaking
            mock_should_speak.return_value = False
            
            # AI always tries to speak
            self.mock_assistant.chat.return_value = json.dumps({
                "action": "fold",
                "adding_to_pot": 0,
                "inner_monologue": "Time to fold",
                "persona_response": "I'm out!",
                "physical": "throws cards"
            })
            
            results = self.simulate_full_hand("Chatty Player", chattiness=0.9)
            
            # No actions should have speech
            for action in results['actions']:
                self.assertNotIn('persona_response', action)
                self.assertNotIn('physical', action)
                # But inner monologue should remain
                self.assertIn('inner_monologue', action)
    
    def test_comprehensive_personality_range(self):
        """Test the full range of personalities and chattiness levels."""
        personalities = [
            ("Silent Bob", 0.1),
            ("Eeyore", 0.3),
            ("Sherlock Holmes", 0.5),
            ("Bob Ross", 0.7),
            ("Gordon Ramsay", 0.9)
        ]
        
        print("\n=== Comprehensive Personality Test ===")
        for name, chattiness in personalities:
            with patch('core.assistants.OpenAILLMAssistant') as mock_llm:
                mock_llm.return_value = self.mock_assistant
                
                results = self.simulate_full_hand(name, chattiness)
                speaking_rate = results['spoke_count'] / results['total_actions']
                
                print(f"{name} (chattiness={chattiness}): "
                      f"Spoke {results['spoke_count']}/{results['total_actions']} times "
                      f"({speaking_rate:.1%})")
                
                # Verify speaking rates align with chattiness
                if chattiness < 0.3:
                    self.assertLess(speaking_rate, 0.5)
                elif chattiness > 0.7:
                    self.assertGreater(speaking_rate, 0.6)


if __name__ == '__main__':
    unittest.main()