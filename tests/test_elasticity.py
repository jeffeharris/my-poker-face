"""
Unit tests for the personality elasticity system.
"""

import unittest
from poker.elasticity_manager import ElasticTrait, ElasticPersonality
from poker.pressure_detector import PressureEventDetector
from poker.poker_game import PokerGameState, Player, initialize_game_state


class TestElasticTrait(unittest.TestCase):
    """Test the ElasticTrait class."""
    
    def test_trait_initialization(self):
        """Test trait creates with correct bounds."""
        trait = ElasticTrait(value=0.5, anchor=0.5, elasticity=0.3)
        
        self.assertEqual(trait.value, 0.5)
        self.assertEqual(trait.anchor, 0.5)
        self.assertEqual(trait.elasticity, 0.3)
        self.assertEqual(trait.min, 0.2)  # 0.5 - 0.3
        self.assertEqual(trait.max, 0.8)  # 0.5 + 0.3
    
    def test_trait_bounds_clipping(self):
        """Test trait bounds are clipped to 0-1 range."""
        trait = ElasticTrait(value=0.1, anchor=0.1, elasticity=0.5)
        
        self.assertEqual(trait.min, 0.0)  # Clipped to 0
        self.assertEqual(trait.max, 0.6)  # 0.1 + 0.5
        
        trait2 = ElasticTrait(value=0.9, anchor=0.9, elasticity=0.5)
        self.assertEqual(trait2.min, 0.4)  # 0.9 - 0.5
        self.assertEqual(trait2.max, 1.0)  # Clipped to 1
    
    def test_apply_pressure(self):
        """Test pressure application changes trait value."""
        trait = ElasticTrait(value=0.5, anchor=0.5, elasticity=0.3)

        # Small pressure applies immediate effect (amount * elasticity * 0.3)
        trait.apply_pressure(0.1)
        self.assertAlmostEqual(trait.value, 0.509, places=3)  # 0.5 + 0.1*0.3*0.3
        self.assertEqual(trait.pressure, 0.1)
        
        # Large pressure should change value
        trait.apply_pressure(0.3)  # Total pressure now 0.4
        self.assertNotEqual(trait.value, 0.5)
        self.assertLess(trait.pressure, 0.4)  # Reduced after application
        
    def test_recovery(self):
        """Test trait recovery toward anchor."""
        trait = ElasticTrait(value=0.8, anchor=0.5, elasticity=0.3)
        
        initial_deviation = abs(trait.value - trait.anchor)
        trait.recover(recovery_rate=0.1)
        new_deviation = abs(trait.value - trait.anchor)
        
        self.assertLess(new_deviation, initial_deviation)
        self.assertGreater(trait.value, 0.5)  # Still above anchor
        self.assertLess(trait.value, 0.8)  # But moved toward it


class TestElasticPersonality(unittest.TestCase):
    """Test the ElasticPersonality class."""
    
    def test_from_base_personality(self):
        """Test creating elastic personality from base config."""
        base_config = {
            'personality_traits': {
                'bluff_tendency': 0.8,
                'aggression': 0.9,
                'chattiness': 0.6,
                'emoji_usage': 0.4
            }
        }
        
        personality = ElasticPersonality.from_base_personality(
            "Test Player", base_config
        )
        
        self.assertEqual(personality.name, "Test Player")
        self.assertEqual(len(personality.traits), 4)
        self.assertEqual(personality.get_trait_value('bluff_tendency'), 0.8)
        self.assertEqual(personality.get_trait_value('aggression'), 0.9)
    
    def test_apply_pressure_event(self):
        """Test applying pressure events."""
        base_config = {
            'personality_traits': {
                'bluff_tendency': 0.5,
                'aggression': 0.5,
                'chattiness': 0.5,
                'emoji_usage': 0.5
            }
        }
        
        personality = ElasticPersonality.from_base_personality(
            "Test Player", base_config
        )
        
        # Apply big win event
        personality.apply_pressure_event('big_win')
        
        # Check that pressure was applied
        self.assertGreater(personality.traits['aggression'].pressure, 0)
        self.assertGreater(personality.traits['chattiness'].pressure, 0)
    
    def test_mood_vocabulary(self):
        """Test mood vocabulary system."""
        base_config = {
            'personality_traits': {
                'bluff_tendency': 0.1,
                'aggression': 0.2,
                'chattiness': 0.3,
                'emoji_usage': 0.1
            }
        }
        
        # Test Eeyore's moods
        personality = ElasticPersonality.from_base_personality(
            "Eeyore", base_config
        )
        
        mood = personality.get_current_mood()
        self.assertIn(mood, ["pessimistic", "melancholy", "resigned"])
        
        # Apply negative pressure - need more pressure to change mood category
        personality.traits['aggression'].pressure = -0.5
        personality.traits['bluff_tendency'].pressure = -0.5
        personality.traits['chattiness'].pressure = -0.5
        mood = personality.get_current_mood()
        # Could be any Eeyore mood since pressure affects mood selection
        possible_moods = ["hopeless", "defeated", "miserable", "pessimistic", "melancholy", "resigned"]
        self.assertIn(mood, possible_moods)


class TestPressureEventDetector(unittest.TestCase):
    """Test the PressureEventDetector class."""

    def setUp(self):
        """Set up test fixtures."""
        self.detector = PressureEventDetector()
    
    def test_detect_showdown_events(self):
        """Test detecting events from showdown."""
        # Create a mock game state
        players = [
            Player(name="Player1", stack=1000, bet=100, hand=[],
                   is_human=False, is_all_in=False, is_folded=False, has_acted=True),
            Player(name="Player2", stack=500, bet=100, hand=[],
                   is_human=False, is_all_in=False, is_folded=False, has_acted=True),
            Player(name="Player3", stack=200, bet=0, hand=[],
                   is_human=False, is_all_in=False, is_folded=True, has_acted=True),
        ]

        game_state = PokerGameState(
            players=tuple(players),
            deck=tuple(),
            community_cards=tuple(),
            pot={'total': 2000, 'side_pots': []},  # Make it a big pot
            current_player_idx=0,
            current_dealer_idx=0,
            current_ante=10
        )

        # Winner info using pot_breakdown format (current code format)
        winner_info = {
            'pot_breakdown': [
                {
                    'winners': [{'name': 'Player1', 'amount': 2000}],
                    'hand_name': 'One Pair'
                }
            ],
            'hand_rank': 9,  # One pair (weak)
            'winnings': {'Player1': 2000}
        }

        events = self.detector.detect_showdown_events(game_state, winner_info)

        # Should detect big win/loss events (not bluff since multiple players still in)
        event_names = [e[0] for e in events]
        self.assertTrue(len(events) > 0)  # Should have some events
        self.assertIn("big_win", event_names)
    
    def test_detect_chat_events(self):
        """Test detecting events from chat."""
        # Friendly chat
        events = self.detector.detect_chat_events(
            "Player1", 
            "Nice hand! Great play there!",
            ["Player2", "Player3"]
        )
        
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "friendly_chat")
        self.assertEqual(events[0][1], ["Player2", "Player3"])
        
        # Aggressive chat
        events = self.detector.detect_chat_events(
            "Player1",
            "You're playing scared! Weak fold!",
            ["Player2"]
        )
        
        self.assertEqual(events[0][0], "rivalry_trigger")


class TestElasticityIntegration(unittest.TestCase):
    """Test the full elasticity system integration using ElasticPersonality directly."""

    def test_full_pressure_cycle(self):
        """Test a complete pressure application and recovery cycle."""
        # Create an ElasticPersonality directly (how it's used in the actual codebase)
        personality = ElasticPersonality.from_base_personality(
            "Gordon Ramsay",
            {
                'personality_traits': {
                    'bluff_tendency': 0.6,
                    'aggression': 0.95,
                    'chattiness': 0.9,
                    'emoji_usage': 0.2
                }
            }
        )

        # Get initial trait values
        initial_aggression = personality.get_trait_value('aggression')

        # Apply a big loss event
        personality.apply_pressure_event("big_loss")

        # Check traits changed
        new_aggression = personality.get_trait_value('aggression')
        self.assertLess(new_aggression, initial_aggression)

        # Apply recovery several times
        for _ in range(10):
            personality.recover_traits()

        # Check traits moved back toward anchor
        recovered_aggression = personality.get_trait_value('aggression')
        self.assertGreater(recovered_aggression, new_aggression)
        # After 10 recoveries, should be closer but not exactly at anchor
        # due to exponential decay
        diff = abs(recovered_aggression - initial_aggression)
        self.assertLess(diff, 0.1)  # Within 0.1 of original


if __name__ == '__main__':
    unittest.main()