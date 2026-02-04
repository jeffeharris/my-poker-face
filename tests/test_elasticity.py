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
    
    def test_from_base_personality_new_format(self):
        """Test creating elastic personality from new 5-trait config."""
        base_config = {
            'personality_traits': {
                'tightness': 0.4,
                'aggression': 0.9,
                'confidence': 0.7,
                'composure': 0.8,
                'table_talk': 0.6
            }
        }

        personality = ElasticPersonality.from_base_personality(
            "Test Player", base_config
        )

        self.assertEqual(personality.name, "Test Player")
        self.assertEqual(len(personality.traits), 5)
        self.assertEqual(personality.get_trait_value('tightness'), 0.4)
        self.assertEqual(personality.get_trait_value('aggression'), 0.9)
        self.assertEqual(personality.get_trait_value('confidence'), 0.7)
        self.assertEqual(personality.get_trait_value('composure'), 0.8)
        self.assertEqual(personality.get_trait_value('table_talk'), 0.6)

    def test_from_base_personality_old_format_conversion(self):
        """Test old 4-trait config is auto-converted to 5-trait model."""
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
        # Old 4-trait is converted to new 5-trait
        self.assertEqual(len(personality.traits), 5)
        # Aggression is preserved
        self.assertEqual(personality.get_trait_value('aggression'), 0.9)
        # tightness, confidence, composure, table_talk should exist
        self.assertIsNotNone(personality.get_trait_value('tightness'))
        self.assertIsNotNone(personality.get_trait_value('confidence'))
        self.assertIsNotNone(personality.get_trait_value('composure'))
        self.assertIsNotNone(personality.get_trait_value('table_talk'))
    
    def test_apply_pressure_event(self):
        """Test applying pressure events."""
        base_config = {
            'personality_traits': {
                'tightness': 0.5,
                'aggression': 0.5,
                'confidence': 0.5,
                'composure': 0.7,
                'table_talk': 0.5
            }
        }

        personality = ElasticPersonality.from_base_personality(
            "Test Player", base_config
        )

        # Apply big win event
        personality.apply_pressure_event('big_win')

        # Check that pressure was applied (big_win affects confidence, composure, aggression, table_talk)
        self.assertGreater(personality.traits['confidence'].pressure, 0)
        self.assertGreater(personality.traits['composure'].pressure, 0)
        self.assertGreater(personality.traits['table_talk'].pressure, 0)
    
    def test_mood_vocabulary(self):
        """Test mood vocabulary system."""
        base_config = {
            'personality_traits': {
                'tightness': 0.7,  # Tight player
                'aggression': 0.2,  # Passive
                'confidence': 0.3,  # Low confidence
                'composure': 0.5,  # Moderate composure
                'table_talk': 0.3  # Quiet
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
        personality.traits['confidence'].pressure = -0.5
        personality.traits['composure'].pressure = -0.5
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
        # Create an ElasticPersonality with new 5-trait model
        personality = ElasticPersonality.from_base_personality(
            "Gordon Ramsay",
            {
                'personality_traits': {
                    'tightness': 0.3,
                    'aggression': 0.95,
                    'confidence': 0.8,
                    'composure': 0.7,
                    'table_talk': 0.9
                }
            }
        )

        # Get initial trait values (big_loss primarily affects composure and confidence)
        initial_composure = personality.get_trait_value('composure')
        initial_confidence = personality.get_trait_value('confidence')

        # Apply a big loss event
        personality.apply_pressure_event("big_loss")

        # Check composure and confidence decreased (big_loss affects these)
        new_composure = personality.get_trait_value('composure')
        new_confidence = personality.get_trait_value('confidence')
        self.assertLess(new_composure, initial_composure)
        self.assertLess(new_confidence, initial_confidence)

        # Apply recovery several times
        for _ in range(10):
            personality.recover_traits()

        # Check traits moved back toward anchor
        recovered_composure = personality.get_trait_value('composure')
        self.assertGreater(recovered_composure, new_composure)
        # After 10 recoveries, should be closer but not exactly at anchor
        # due to exponential decay
        diff = abs(recovered_composure - initial_composure)
        self.assertLess(diff, 0.15)  # Within 0.15 of original


if __name__ == '__main__':
    unittest.main()