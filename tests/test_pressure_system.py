"""
Tests for the pressure detection and stats tracking system.
"""

import unittest
from datetime import datetime
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker, PressureEvent
from poker.poker_game import PokerGameState, Player, initialize_game_state
from core.card import Card


class TestPressureSystem(unittest.TestCase):

    def setUp(self):
        """Set up test components."""
        self.pressure_detector = PressureEventDetector()
        self.stats_tracker = PressureStatsTracker()

        # Create a test game state with 3 players
        self.game_state = initialize_game_state(
            player_names=["Gordon Ramsay", "Donald Trump", "Bob Ross"],
            starting_stack=1000
        )
        
    def test_big_win_detection(self):
        """Test that big wins are properly detected and tracked."""
        # Set up a game state where pot is big (> 1.5x average stack)
        # Average stack = 1000, so big pot = 1500+
        game_state = self.game_state.update(pot={'total': 2000})

        # Create winner info using pot_breakdown format (current code format)
        winner_info = {
            'pot_breakdown': [
                {
                    'winners': [{'name': 'Gordon Ramsay', 'amount': 2000}],
                    'hand_name': "Pair of Aces"
                }
            ],
            'winnings': {'Gordon Ramsay': 2000},
            'winning_hand': [14, 14, 13, 12, 11],  # Pair of aces
            'hand_name': "Pair of Aces"
        }
        
        # Detect showdown events
        events = self.pressure_detector.detect_showdown_events(game_state, winner_info)

        # Verify big_win event was detected
        event_types = [event[0] for event in events]
        self.assertIn("big_win", event_types, "Big win event should be detected")
        # Also verify "win" event was emitted (always fires before big_win)
        self.assertIn("win", event_types, "Win event should be detected")

        # Find the big_win event
        big_win_event = next(e for e in events if e[0] == "big_win")
        self.assertEqual(big_win_event[1], ["Gordon Ramsay"], "Winner should be Gordon Ramsay")

        # Track events in stats (both win and big_win, as the detector emits)
        self.stats_tracker.record_event(
            "win",
            ["Gordon Ramsay"],
            {'pot_size': 2000}
        )
        self.stats_tracker.record_event(
            "big_win",
            ["Gordon Ramsay"],
            {'pot_size': 2000}
        )
        
        # Verify stats were updated
        gordon_stats = self.stats_tracker.get_player_stats("Gordon Ramsay")
        self.assertEqual(gordon_stats['big_wins'], 1, "Should have 1 big win")
        self.assertEqual(gordon_stats['biggest_pot_won'], 2000, "Biggest pot should be 2000")
        
        # Verify leaderboard
        leaderboards = self.stats_tracker.get_leaderboard()
        biggest_winners = leaderboards['biggest_winners']
        self.assertTrue(len(biggest_winners) > 0, "Should have at least one winner")
        self.assertEqual(biggest_winners[0]['name'], "Gordon Ramsay")
        self.assertEqual(biggest_winners[0]['wins'], 1)
        self.assertEqual(biggest_winners[0]['biggest_pot'], 2000)
        
    def test_small_pot_not_big_win(self):
        """Test that small pots don't trigger big win events."""
        # Small pot (500 < 1.5x average stack of 1000)
        game_state = self.game_state.update(pot={'total': 500})
        
        winner_info = {
            'pot_breakdown': [
                {
                    'winners': [{'name': 'Bob Ross', 'amount': 500}],
                    'hand_name': "Pair of Kings"
                }
            ],
            'winnings': {'Bob Ross': 500},
            'winning_hand': [13, 13, 12, 11, 10],
            'hand_name': "Pair of Kings"
        }
        
        events = self.pressure_detector.detect_showdown_events(game_state, winner_info)
        
        # Should not have big_win event
        event_types = [event[0] for event in events]
        self.assertNotIn("big_win", event_types, "Small pot should not trigger big win")
        
    def test_multiple_events_tracking(self):
        """Test tracking multiple events and stats accumulation."""
        # Gordon wins big (detector emits both "win" and "big_win")
        self.stats_tracker.record_event("win", ["Gordon Ramsay"], {'pot_size': 2000})
        self.stats_tracker.record_event("big_win", ["Gordon Ramsay"], {'pot_size': 2000})

        # Gordon wins again
        self.stats_tracker.record_event("win", ["Gordon Ramsay"], {'pot_size': 3000})
        self.stats_tracker.record_event("big_win", ["Gordon Ramsay"], {'pot_size': 3000})

        # Trump wins once
        self.stats_tracker.record_event("win", ["Donald Trump"], {'pot_size': 1500})
        self.stats_tracker.record_event("big_win", ["Donald Trump"], {'pot_size': 1500})
        
        # Gordon gets bluffed
        self.stats_tracker.record_event(
            "bluff_called", 
            ["Gordon Ramsay"], 
            {}
        )
        
        # Check Gordon's stats
        gordon_stats = self.stats_tracker.get_player_stats("Gordon Ramsay")
        self.assertEqual(gordon_stats['big_wins'], 2, "Should have 2 big wins")
        self.assertEqual(gordon_stats['biggest_pot_won'], 3000, "Biggest pot should be 3000")
        self.assertEqual(gordon_stats['bluffs_caught'], 1, "Should have been bluffed once")
        
        # Check leaderboard order
        leaderboards = self.stats_tracker.get_leaderboard()
        biggest_winners = leaderboards['biggest_winners']
        self.assertEqual(biggest_winners[0]['name'], "Gordon Ramsay", "Gordon should be #1")
        self.assertEqual(biggest_winners[0]['wins'], 2)
        self.assertEqual(biggest_winners[1]['name'], "Donald Trump", "Trump should be #2")
        self.assertEqual(biggest_winners[1]['wins'], 1)
        
    def test_session_summary(self):
        """Test that session summary correctly aggregates all stats."""
        # Add various events
        events = [
            ("big_win", ["Gordon Ramsay"], {'pot_size': 2500}),
            ("big_loss", ["Donald Trump"], {'pot_size': 2500}),
            ("successful_bluff", ["Bob Ross"], {}),
            ("bad_beat", ["Donald Trump"], {}),
        ]
        
        for event_type, players, details in events:
            self.stats_tracker.record_event(event_type, players, details)
            
        # Get session summary
        summary = self.stats_tracker.get_session_summary()
        
        # Verify summary contents
        self.assertEqual(summary['total_events'], 4)
        self.assertEqual(summary['biggest_pot'], 2500)
        self.assertIn('Gordon Ramsay', summary['player_summaries'])
        self.assertIn('leaderboards', summary)
        self.assertIn('fun_facts', summary)
        
        # Verify player summaries
        gordon_summary = summary['player_summaries']['Gordon Ramsay']
        self.assertEqual(gordon_summary['big_wins'], 1)
        self.assertEqual(gordon_summary['signature_move'], "Steady Player")  # Only 1 win
        
    def test_pressure_event_integration(self):
        """Test the full integration from detection to stats."""
        # Simulate a full showdown scenario
        game_state = self.game_state.update(
            pot={'total': 3000},
            players=tuple(
                player.update(is_folded=False if player.name in ["Gordon Ramsay", "Donald Trump"] else True)
                for player in self.game_state.players
            )
        )
        
        winner_info = {
            'pot_breakdown': [
                {
                    'winners': [{'name': 'Gordon Ramsay', 'amount': 3000}],
                    'hand_name': "Straight"
                }
            ],
            'winnings': {'Gordon Ramsay': 3000},
            'winning_hand': [10, 9, 8, 7, 6],  # Straight
            'hand_name': "Straight"
        }
        
        # Detect events
        events = self.pressure_detector.detect_showdown_events(game_state, winner_info)
        
        # Apply to stats tracker
        for event_type, players in events:
            self.stats_tracker.record_event(
                event_type, 
                players, 
                {'pot_size': game_state.pot['total']}
            )
        
        # Verify Gordon won
        gordon_stats = self.stats_tracker.get_player_stats("Gordon Ramsay")
        self.assertGreater(gordon_stats['big_wins'], 0, "Gordon should have wins")
        
        # Verify Trump lost
        trump_stats = self.stats_tracker.get_player_stats("Donald Trump")
        self.assertGreater(trump_stats['big_losses'], 0, "Trump should have losses")


    def test_successful_bluff_does_not_penalize_folders(self):
        """When everyone folds to a bluff, only winner gets successful_bluff, not folders."""
        # Set up: Trump bluffs (weak hand), everyone else folded
        game_state = self.game_state.update(
            pot={'total': 2000},
            players=tuple(
                player.update(is_folded=(player.name != "Donald Trump"))
                for player in self.game_state.players
            )
        )

        winner_info = {
            'pot_breakdown': [{
                'winners': [{'name': 'Donald Trump', 'amount': 2000}],
                'hand_name': 'High Card'
            }],
            'hand_rank': 10,  # High card = weak hand (bluff)
        }

        events = self.pressure_detector.detect_showdown_events(game_state, winner_info)
        event_types = [e[0] for e in events]

        # Should have successful_bluff for Trump
        self.assertIn("successful_bluff", event_types)
        bluff_event = next(e for e in events if e[0] == "successful_bluff")
        self.assertEqual(bluff_event[1], ["Donald Trump"])

        # Should NOT have bluff_called for folders
        self.assertNotIn("bluff_called", event_types,
            "Folders should not receive bluff_called - they made correct decisions")


if __name__ == '__main__':
    unittest.main()