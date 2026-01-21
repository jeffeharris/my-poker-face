#!/usr/bin/env python3
"""Golden path tests for the prompt management system."""

import unittest
from unittest.mock import patch, MagicMock
import json
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.prompt_manager import PromptManager, PromptTemplate, RESPONSE_FORMAT, PERSONA_EXAMPLES
from poker.poker_player import AIPokerPlayer


# Load personalities from JSON for mocking in tests
def load_personalities_json():
    """Load personalities from the JSON file for test fixtures."""
    json_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'poker', 'personalities.json'
    )
    with open(json_path, 'r') as f:
        data = json.load(f)
        # Handle nested structure
        return data.get('personalities', data)


PERSONALITIES_FIXTURE = load_personalities_json()


class TestPromptTemplate(unittest.TestCase):
    """Test the PromptTemplate class."""
    
    def setUp(self):
        self.template = PromptTemplate(
            name='test_template',
            sections={
                'greeting': 'Hello {name}!',
                'info': 'You have ${money} in your account.',
                'combined': '{greeting} Your status is {status}.'
            }
        )
    
    def test_render_basic(self):
        """Test basic template rendering."""
        result = self.template.render(
            name='Alice',
            money=1000,
            greeting='Hi there',
            status='active'
        )
        
        self.assertIn('Hello Alice!', result)
        self.assertIn('You have $1000 in your account.', result)
        self.assertIn('Hi there Your status is active.', result)
    
    def test_render_missing_variable(self):
        """Test rendering with missing variables raises error."""
        with self.assertRaises(ValueError) as context:
            self.template.render(name='Alice')  # Missing money, greeting, status
        
        self.assertIn('Missing variable', str(context.exception))
    
    def test_sections_joined_properly(self):
        """Test that sections are joined with double newlines."""
        result = self.template.render(
            name='Bob',
            money=500,
            greeting='Welcome',
            status='new'
        )
        
        sections = result.split('\n\n')
        self.assertEqual(len(sections), 3)


class TestPromptManager(unittest.TestCase):
    """Test the PromptManager class."""
    
    def setUp(self):
        self.manager = PromptManager()
    
    def test_default_templates_loaded(self):
        """Test that default templates are loaded."""
        self.assertIn('poker_player', self.manager.templates)
        self.assertIn('decision', self.manager.templates)
    
    def test_get_template(self):
        """Test retrieving templates."""
        template = self.manager.get_template('poker_player')
        self.assertIsInstance(template, PromptTemplate)
        self.assertEqual(template.name, 'poker_player')
    
    def test_get_nonexistent_template(self):
        """Test retrieving non-existent template raises error."""
        with self.assertRaises(ValueError) as context:
            self.manager.get_template('nonexistent')
        
        self.assertIn("Template 'nonexistent' not found", str(context.exception))
    
    def test_render_poker_player_prompt(self):
        """Test rendering the poker player prompt."""
        prompt = self.manager.render_prompt(
            'poker_player',
            name='Test Player',
            attitude='confident',
            confidence='high',
            money=10000,
            json_template='{"test": "format"}'
        )
        
        # Check key components are present
        self.assertIn('Persona: Test Player', prompt)
        self.assertIn('Attitude: confident', prompt)
        self.assertIn('Confidence: high', prompt)
        self.assertIn('Starting money: $10000', prompt)
        self.assertIn('{"test": "format"}', prompt)
        self.assertIn('RIVALS', prompt)  # Check competitive context is there
        self.assertIn('JSON format', prompt)  # Check instructions
    
    def test_render_decision_prompt(self):
        """Test rendering the decision prompt."""
        # Use render_decision_prompt which conditionally includes sections
        # (render_prompt tries to render ALL sections including pot_committed
        # which requires variables)
        prompt = self.manager.render_decision_prompt(
            message='Test game state message',
            include_mind_games=True,
            include_persona_response=True
        )

        self.assertIn('Test game state message', prompt)
        self.assertIn('only respond with the JSON', prompt)
        self.assertIn('SECRET', prompt)


class TestAIPokerPlayerPrompts(unittest.TestCase):
    """Test AI poker player prompt generation and personality loading."""

    def setUp(self):
        # Skip if no API key
        if not os.getenv('OPENAI_API_KEY'):
            self.skipTest("OPENAI_API_KEY not set")

    @patch.object(AIPokerPlayer, '_load_personality_config')
    def test_personality_loading(self, mock_load_config):
        """Test that personalities load correctly from JSON."""
        # Mock to return the Eeyore personality from our fixtures
        mock_load_config.return_value = PERSONALITIES_FIXTURE['Eeyore']

        # Test known personality
        player = AIPokerPlayer(name='Eeyore', starting_money=10000)
        self.assertEqual(player.personality_config['play_style'], 'tight and passive')
        self.assertEqual(player.confidence, 'pessimistic')
        self.assertEqual(player.attitude, 'gloomy')

        # Test personality traits
        traits = player.personality_config['personality_traits']
        self.assertEqual(traits['bluff_tendency'], 0.1)
        self.assertEqual(traits['aggression'], 0.2)
        self.assertEqual(traits['chattiness'], 0.3)
        self.assertEqual(traits['emoji_usage'], 0.1)
    
    def test_unknown_personality_gets_default(self):
        """Test that unknown personalities get default config."""
        player = AIPokerPlayer(name='Unknown Celebrity', starting_money=10000)
        self.assertIn('play_style', player.personality_config)
        self.assertIn('personality_traits', player.personality_config)
        
        # Should have valid trait values
        traits = player.personality_config['personality_traits']
        self.assertGreaterEqual(traits['bluff_tendency'], 0)
        self.assertLessEqual(traits['bluff_tendency'], 1)
    
    def test_persona_prompt_generation(self):
        """Test persona prompt generation."""
        player = AIPokerPlayer(name='Donald Trump', starting_money=15000)
        prompt = player.persona_prompt()
        
        # Check prompt contains key elements
        self.assertIn('Persona: Donald Trump', prompt)
        self.assertIn('$15000', prompt)
        self.assertIn('Example response:', prompt)
        
        # Check it includes JSON format
        self.assertIn('"play_style":', prompt)
        self.assertIn('"action":', prompt)
        self.assertIn('"persona_response":', prompt)
    
    @unittest.skip("TODO: Update test to match current personality modifier text")
    @patch.object(AIPokerPlayer, '_load_personality_config')
    def test_personality_modifiers(self, mock_load_config):
        """Test personality modifier generation."""
        # High bluff tendency - use Donald Trump from fixtures
        mock_load_config.return_value = PERSONALITIES_FIXTURE['Donald Trump']
        bluffer = AIPokerPlayer(name='Donald Trump', starting_money=10000)
        bluffer_mod = bluffer.get_personality_modifier()
        self.assertIn('bluff', bluffer_mod.lower())
        self.assertIn('aggressive', bluffer_mod.lower())

        # Low bluff tendency - use Bob Ross from fixtures
        mock_load_config.return_value = PERSONALITIES_FIXTURE['Bob Ross']
        honest = AIPokerPlayer(name='Bob Ross', starting_money=10000)
        honest_mod = honest.get_personality_modifier()
        # Bob Ross has low aggression (0.1) so should get the cautious modifier
        self.assertIn('cautiously', honest_mod.lower())
        self.assertIn('avoid', honest_mod.lower())
    
    def test_strategy_adjustment(self):
        """Test dynamic strategy adjustment based on stack size."""
        player = AIPokerPlayer(name='Test Player', starting_money=500)
        
        # Test low stack
        player.money = 500
        strategy = player.adjust_strategy_based_on_state()
        self.assertIn('conservatively', strategy)
        
        # Test chip leader
        player.money = 25000
        strategy = player.adjust_strategy_based_on_state()
        self.assertIn('chip leader', strategy)
        
        # Test normal stack
        player.money = 10000
        strategy = player.adjust_strategy_based_on_state()
        self.assertEqual(strategy, '')


class TestResponseFormat(unittest.TestCase):
    """Test the response format structure."""
    
    def test_response_format_keys(self):
        """Test that response format has all required keys."""
        required_keys = [
            'play_style', 'action', 'adding_to_pot',
            'persona_response', 'new_confidence', 'new_attitude'
        ]
        
        for key in required_keys:
            self.assertIn(key, RESPONSE_FORMAT)
    
    def test_persona_examples(self):
        """Test that persona examples are properly structured."""
        self.assertIn('Eeyore', PERSONA_EXAMPLES)
        self.assertIn('Clint Eastwood', PERSONA_EXAMPLES)
        
        # Check Eeyore example
        eeyore = PERSONA_EXAMPLES['Eeyore']
        self.assertEqual(eeyore['play_style'], 'tight')
        self.assertIn('sample_response', eeyore)
        
        sample = eeyore['sample_response']
        self.assertEqual(sample['play_style'], 'tight')
        self.assertEqual(sample['action'], 'check')
        self.assertEqual(sample['new_confidence'], 'abysmal')
        self.assertEqual(sample['new_attitude'], 'gloomy')


class TestIntegration(unittest.TestCase):
    """Integration tests for the prompt system."""
    
    def setUp(self):
        if not os.getenv('OPENAI_API_KEY'):
            self.skipTest("OPENAI_API_KEY not set")
    
    def test_ai_player_creation_and_prompt(self):
        """Test creating an AI player and generating a full prompt."""
        players_to_test = ['Eeyore', 'Gordon Ramsay', 'A Mime']
        
        for name in players_to_test:
            with self.subTest(player=name):
                player = AIPokerPlayer(name=name, starting_money=10000)
                
                # Check player created successfully
                self.assertEqual(player.name, name)
                self.assertEqual(player.money, 10000)
                
                # Check prompt generation
                prompt = player.persona_prompt()
                self.assertGreater(len(prompt), 1000)  # Should be substantial
                self.assertIn(name, prompt)
                
                # Check assistant configured
                self.assertIsNotNone(player.assistant)
                self.assertEqual(player.assistant.system_message, prompt)
    
    def test_json_response_format_in_prompt(self):
        """Test that JSON response format is properly included in prompts."""
        player = AIPokerPlayer(name='Test Player', starting_money=10000)
        prompt = player.persona_prompt()
        
        # Convert RESPONSE_FORMAT to JSON and check it's in the prompt
        json_format = json.dumps(RESPONSE_FORMAT, indent=2)
        # The prompt should contain the JSON format (though it might be formatted differently)
        for key in RESPONSE_FORMAT:
            self.assertIn(f'"{key}"', prompt)


if __name__ == '__main__':
    # Run tests with verbosity
    unittest.main(verbosity=2)