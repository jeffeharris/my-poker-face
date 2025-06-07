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


class TestGoldenPath(unittest.TestCase):
    """Golden path test for the complete prompt management system."""
    
    def setUp(self):
        if not os.getenv('OPENAI_API_KEY'):
            self.skipTest("OPENAI_API_KEY not set")
    
    def test_golden_path_ai_decision_flow(self):
        """Test the complete flow from game state to AI decision."""
        
        # Test different personality types
        test_cases = [
            {
                'name': 'Eeyore',
                'expected_traits': {
                    'conservative_play': True,  # Low bluff tendency
                    'passive': True,  # Low aggression
                    'quiet': True  # Low chattiness
                }
            },
            {
                'name': 'Donald Trump',
                'expected_traits': {
                    'conservative_play': False,  # High bluff tendency
                    'passive': False,  # High aggression
                    'quiet': False  # High chattiness
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
                    starting_money=10000,
                    ai_temp=0.7
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
                    self.assertGreater(traits['chattiness'], 0.5)
                
                # Test prompt generation includes all components
                prompt = player.persona_prompt()
                
                # Verify prompt structure
                self.assertIn(f'Persona: {test_case["name"]}', prompt)
                self.assertIn('charity', prompt)  # Context
                self.assertIn('JSON', prompt)  # Format requirement
                self.assertIn('Example response:', prompt)  # Example included
                
                # Test the prompt manager integration
                prompt_manager = PromptManager()
                
                # Test decision prompt rendering
                decision_prompt = prompt_manager.render_prompt(
                    'decision',
                    message="Test game state"
                )
                self.assertIn('Test game state', decision_prompt)
                self.assertIn('JSON', decision_prompt)
    
    def test_personality_affects_prompt_content(self):
        """Test that personality traits directly affect prompt content."""
        
        # Create players with known personalities
        eeyore = AIPokerPlayer(name='Eeyore', starting_money=10000)
        trump = AIPokerPlayer(name='Donald Trump', starting_money=10000)
        
        # Get their prompts
        eeyore_prompt = eeyore.persona_prompt()
        trump_prompt = trump.persona_prompt()
        
        # Verify confidence and attitude are set correctly
        self.assertEqual(eeyore.confidence, 'pessimistic')
        self.assertEqual(eeyore.attitude, 'gloomy')
        self.assertEqual(trump.confidence, 'supreme')
        self.assertEqual(trump.attitude, 'domineering')
        
        # Verify these appear in prompts
        self.assertIn('pessimistic', eeyore_prompt)
        self.assertIn('gloomy', eeyore_prompt)
        self.assertIn('supreme', trump_prompt)
        self.assertIn('domineering', trump_prompt)
    
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
        ai_player = AIPokerPlayer(name='Eeyore', starting_money=10000)
        
        # Test that the prompt manager is integrated
        self.assertIsNotNone(ai_player.prompt_manager)
        
        # Test that personality config was loaded
        self.assertEqual(ai_player.personality_config['play_style'], 'tight and passive')
        
        # Test that the assistant was initialized with the proper prompt
        self.assertIn('Eeyore', ai_player.assistant.system_message)
        self.assertIn('gloomy', ai_player.assistant.system_message)
        self.assertIn('pessimistic', ai_player.assistant.system_message)
        
        # Verify the prompt contains the JSON format
        self.assertIn('"play_style"', ai_player.assistant.system_message)
        self.assertIn('"action"', ai_player.assistant.system_message)


if __name__ == '__main__':
    unittest.main(verbosity=2)