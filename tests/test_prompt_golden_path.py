#!/usr/bin/env python3
"""Golden path test simulating actual gameplay with the prompt system."""

import unittest
import json
import sys
import os
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.poker_player import AIPokerPlayer
from poker.controllers import AIPlayerController
from poker.prompt_manager import PromptManager
from tests.conftest import load_personality_from_json


class TestGoldenPath(unittest.TestCase):
    """Golden path test for the complete prompt management system."""

    def setUp(self):
        if not os.getenv('OPENAI_API_KEY'):
            self.skipTest("OPENAI_API_KEY not set")
        # Patch personality loading to use JSON file directly (no DB/LLM needed)
        patcher = patch.object(
            AIPokerPlayer, '_load_personality_config',
            lambda self: load_personality_from_json(self.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
    
    def test_golden_path_ai_decision_flow(self):
        """Test the complete flow from game state to AI decision."""
        
        # Test different personality types
        test_cases = [
            {
                'name': 'Ebenezer Scrooge',
                'expected_traits': {
                    'conservative_play': True,  # Low bluff tendency (0.2)
                    'passive': True,  # Low aggression (0.2)
                    'quiet': False  # Chattiness is 0.5 (not < 0.5)
                }
            },
            {
                'name': 'Blackbeard',
                'expected_traits': {
                    'conservative_play': False,  # High bluff tendency (0.8)
                    'passive': False,  # High aggression (0.9)
                    'quiet': False  # High chattiness (0.6)
                }
            },
            {
                'name': 'A Mime',
                'expected_traits': {
                    'conservative_play': False,  # High bluff tendency
                    'passive': True,  # Medium aggression
                    'quiet': True  # Zero chattiness (it's a mime!)
                }
            }
        ]
        
        for test_case in test_cases:
            with self.subTest(player=test_case['name']):
                # Create AI player
                player = AIPokerPlayer(
                    name=test_case['name'],
                    starting_money=10000
                )
                
                # Verify personality loaded correctly
                self.assertIsNotNone(player.personality_config)
                
                # Check personality traits affect modifiers
                modifier = player.get_personality_modifier()
                traits = player.personality_config['personality_traits']
                
                # Verify trait-based expectations
                if test_case['expected_traits']['conservative_play']:
                    self.assertLess(traits['bluff_tendency'], 0.5)
                else:
                    self.assertGreater(traits['bluff_tendency'], 0.5)
                
                if test_case['expected_traits']['passive']:
                    self.assertLessEqual(traits['aggression'], 0.5)  # A Mime has exactly 0.5
                else:
                    self.assertGreater(traits['aggression'], 0.5)
                
                if test_case['expected_traits']['quiet']:
                    self.assertLess(traits['chattiness'], 0.5)
                else:
                    self.assertGreaterEqual(traits['chattiness'], 0.5)
                
                # Test prompt generation includes all components
                prompt = player.persona_prompt()
                
                # Verify prompt structure
                self.assertIn(f'Persona: {test_case["name"]}', prompt)
                self.assertIn('tournament', prompt)  # Context
                self.assertIn('JSON', prompt)  # Format requirement
                self.assertIn('Example response:', prompt)  # Example included
                
                # Test the prompt manager integration
                prompt_manager = PromptManager()

                # Test decision prompt rendering (uses render_decision_prompt
                # which selectively includes sections)
                decision_prompt = prompt_manager.render_decision_prompt(
                    message="Test game state"
                )
                self.assertIn('Test game state', decision_prompt)
                self.assertIn('JSON', decision_prompt)
    
    def test_personality_affects_prompt_content(self):
        """Test that personality traits directly affect prompt content."""
        
        # Create players with known personalities
        scrooge = AIPokerPlayer(name='Ebenezer Scrooge', starting_money=10000)
        blackbeard = AIPokerPlayer(name='Blackbeard', starting_money=10000)

        # Get their prompts
        scrooge_prompt = scrooge.persona_prompt()
        blackbeard_prompt = blackbeard.persona_prompt()

        # Verify confidence and attitude are set correctly
        self.assertEqual(scrooge.confidence, 'pessimistic')
        self.assertEqual(scrooge.attitude, 'grumpy and suspicious')
        self.assertEqual(blackbeard.confidence, 'overconfident')
        self.assertEqual(blackbeard.attitude, 'menacing')

        # Verify these appear in prompts
        self.assertIn('pessimistic', scrooge_prompt)
        self.assertIn('grumpy and suspicious', scrooge_prompt)
        self.assertIn('overconfident', blackbeard_prompt)
        self.assertIn('menacing', blackbeard_prompt)
    
    def test_dynamic_strategy_in_prompts(self):
        """Test that dynamic strategy adjustments work correctly."""
        
        player = AIPokerPlayer(name='Test Player', starting_money=10000)
        
        # Test different money situations
        test_scenarios = [
            (500, 'conservatively'),  # Low stack
            (25000, 'chip leader'),   # High stack
            (10000, '')               # Normal stack
        ]
        
        for money, expected_strategy in test_scenarios:
            with self.subTest(money=money):
                player.money = money
                strategy = player.adjust_strategy_based_on_state()
                
                if expected_strategy:
                    self.assertIn(expected_strategy, strategy)
                else:
                    self.assertEqual(strategy, '')
    
    def test_controller_integration(self):
        """Test that AIPlayerController properly uses the prompt system."""
        
        # Create a real AI player to test integration
        ai_player = AIPokerPlayer(name='Ebenezer Scrooge', starting_money=10000)

        # Test that the prompt manager is integrated
        self.assertIsNotNone(ai_player.prompt_manager)

        # Test that personality config was loaded
        self.assertIn('miserly and tight', ai_player.personality_config['play_style'])

        # Test that the assistant was initialized with the proper prompt
        self.assertIn('Ebenezer Scrooge', ai_player.assistant.system_message)
        self.assertIn('grumpy and suspicious', ai_player.assistant.system_message)
        self.assertIn('pessimistic', ai_player.assistant.system_message)
        
        # Verify the prompt contains the JSON format
        self.assertIn('"inner_monologue"', ai_player.assistant.system_message)
        self.assertIn('"action"', ai_player.assistant.system_message)


if __name__ == '__main__':
    unittest.main(verbosity=2)