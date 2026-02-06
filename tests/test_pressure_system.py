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

        # Verify big_win event was detected (but NOT win — they don't stack)
        event_types = [event[0] for event in events]
        self.assertIn("big_win", event_types, "Big win event should be detected")
        self.assertNotIn("win", event_types, "win and big_win should not stack")

        # Find the big_win event
        big_win_event = next(e for e in events if e[0] == "big_win")
        self.assertEqual(big_win_event[1], ["Gordon Ramsay"], "Winner should be Gordon Ramsay")

        # Track events in stats (only big_win, since win doesn't stack)
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
        """Test that small pots trigger win (not big_win) and loss for losers."""
        # Small pot (500 < 1.5x average stack of 1000)
        # Need at least 2 active players for loss to fire
        game_state = self.game_state.update(
            pot={'total': 500},
            players=tuple(
                player.update(is_folded=(player.name == "Bob Ross"))
                for player in self.game_state.players
            )
        )

        winner_info = {
            'pot_breakdown': [
                {
                    'winners': [{'name': 'Gordon Ramsay', 'amount': 500}],
                    'hand_name': "Pair of Kings"
                }
            ],
            'winnings': {'Gordon Ramsay': 500},
            'winning_hand': [13, 13, 12, 11, 10],
            'hand_name': "Pair of Kings"
        }

        events = self.pressure_detector.detect_showdown_events(game_state, winner_info)

        event_types = [event[0] for event in events]
        self.assertNotIn("big_win", event_types, "Small pot should not trigger big win")
        self.assertIn("win", event_types, "Small pot should trigger win event")
        self.assertIn("loss", event_types, "Small pot should trigger loss for losers")
        
    def test_multiple_events_tracking(self):
        """Test tracking multiple events and stats accumulation."""
        # Gordon wins big (detector emits only big_win, not both)
        self.stats_tracker.record_event("big_win", ["Gordon Ramsay"], {'pot_size': 2000})

        # Gordon wins again
        self.stats_tracker.record_event("big_win", ["Gordon Ramsay"], {'pot_size': 3000})

        # Trump wins once
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


class TestEventPriority(unittest.TestCase):
    """Tests for win/big_win/bluff event priority (no stacking)."""

    def setUp(self):
        self.detector = PressureEventDetector()
        self.game_state = initialize_game_state(
            player_names=["Alice", "Bob", "Charlie"],
            starting_stack=1000
        )

    def test_big_win_does_not_stack_with_win(self):
        """big_win and win should not co-occur for the same player."""
        game_state = self.game_state.update(
            pot={'total': 2000},
            players=tuple(
                p.update(is_folded=(p.name not in ("Alice", "Bob")))
                for p in self.game_state.players
            )
        )
        winner_info = {
            'pot_breakdown': [{'winners': [{'name': 'Alice', 'amount': 2000}]}],
            'winning_hand': [14, 14, 13, 12, 11],
        }

        events = self.detector.detect_showdown_events(game_state, winner_info)
        event_types = [e[0] for e in events]

        self.assertIn("big_win", event_types)
        self.assertNotIn("win", event_types, "win and big_win must not stack")

    def test_successful_bluff_does_not_stack_with_big_win(self):
        """successful_bluff winners should not also get big_win."""
        # Only one active player = bluff (everyone folded)
        game_state = self.game_state.update(
            pot={'total': 2000},
            players=tuple(
                p.update(is_folded=(p.name != "Alice"))
                for p in self.game_state.players
            )
        )
        winner_info = {
            'pot_breakdown': [{'winners': [{'name': 'Alice', 'amount': 2000}]}],
            'hand_rank': 10,  # Weak hand = bluff
        }

        events = self.detector.detect_showdown_events(game_state, winner_info)
        event_types = [e[0] for e in events]

        self.assertIn("successful_bluff", event_types)
        self.assertNotIn("big_win", event_types, "bluff winners should not also get big_win")
        self.assertNotIn("win", event_types, "bluff winners should not also get win")

    def test_loss_fires_for_small_pot_losers(self):
        """loss event should fire for showdown losers in small pots."""
        # Fold everyone except Alice and Bob (note: Player is also in game_state)
        game_state = self.game_state.update(
            pot={'total': 300},
            players=tuple(
                p.update(is_folded=(p.name not in ("Alice", "Bob")))
                for p in self.game_state.players
            )
        )
        winner_info = {
            'pot_breakdown': [{'winners': [{'name': 'Alice', 'amount': 300}]}],
            'winning_hand': [14, 13, 12, 11, 10],
        }

        events = self.detector.detect_showdown_events(game_state, winner_info)
        event_types = [e[0] for e in events]

        self.assertIn("loss", event_types)
        self.assertNotIn("big_loss", event_types)
        loss_event = next(e for e in events if e[0] == "loss")
        self.assertEqual(loss_event[1], ["Bob"])

    def test_big_loss_fires_instead_of_loss_for_big_pots(self):
        """big_loss (not loss) should fire for losers in big pots."""
        game_state = self.game_state.update(
            pot={'total': 2000},
            players=tuple(
                p.update(is_folded=(p.name not in ("Alice", "Bob")))
                for p in self.game_state.players
            )
        )
        winner_info = {
            'pot_breakdown': [{'winners': [{'name': 'Alice', 'amount': 2000}]}],
            'winning_hand': [14, 14, 13, 12, 11],
        }

        events = self.detector.detect_showdown_events(game_state, winner_info)
        event_types = [e[0] for e in events]

        self.assertIn("big_loss", event_types)
        self.assertNotIn("loss", event_types)

    def test_bluff_called_detection(self):
        """bluff_called fires when loser shows weak hand at multi-player showdown."""
        # Alice wins, Bob has weak hand (high card) — fold everyone else
        game_state = self.game_state.update(
            pot={'total': 300},
            community_cards=[
                Card('2', 'hearts'), Card('3', 'diamonds'),
                Card('7', 'clubs'), Card('9', 'spades'), Card('J', 'hearts')
            ],
            players=tuple(
                p.update(
                    is_folded=(p.name not in ("Alice", "Bob")),
                    hand=[Card('4', 'spades'), Card('5', 'hearts')] if p.name == "Bob"
                    else p.hand
                )
                for p in self.game_state.players
            )
        )
        winner_info = {
            'pot_breakdown': [{'winners': [{'name': 'Alice', 'amount': 300}]}],
            'winning_hand': [14, 14, 13, 12, 11],
            'hand_rank': 7,  # Pair
        }

        events = self.detector.detect_showdown_events(game_state, winner_info)
        event_types = [e[0] for e in events]

        self.assertIn("bluff_called", event_types)
        bluff_event = next(e for e in events if e[0] == "bluff_called")
        self.assertEqual(bluff_event[1], ["Bob"])


class TestStreakEvents(unittest.TestCase):
    """Tests for winning/losing streak event detection."""

    def setUp(self):
        self.detector = PressureEventDetector()

    def test_no_streak_below_threshold(self):
        """Streak events should not fire for streak_count < 3."""
        # 2-hand winning streak (below threshold)
        events = self.detector.detect_streak_events("Alice", {
            'streak_count': 2,
            'current_streak': 'winning'
        })
        self.assertEqual(events, [])

        # 2-hand losing streak (below threshold)
        events = self.detector.detect_streak_events("Alice", {
            'streak_count': 2,
            'current_streak': 'losing'
        })
        self.assertEqual(events, [])

    def test_winning_streak_detection(self):
        """Winning streak fires at 3+ consecutive wins."""
        events = self.detector.detect_streak_events("Alice", {
            'streak_count': 3,
            'current_streak': 'winning'
        })
        self.assertEqual(events, [("winning_streak", ["Alice"])])

        # Also works for longer streaks
        events = self.detector.detect_streak_events("Bob", {
            'streak_count': 5,
            'current_streak': 'winning'
        })
        self.assertEqual(events, [("winning_streak", ["Bob"])])

    def test_losing_streak_detection(self):
        """Losing streak fires at 3+ consecutive losses."""
        events = self.detector.detect_streak_events("Charlie", {
            'streak_count': 3,
            'current_streak': 'losing'
        })
        self.assertEqual(events, [("losing_streak", ["Charlie"])])

    def test_neutral_streak_no_event(self):
        """Neutral streak should not fire events."""
        events = self.detector.detect_streak_events("Alice", {
            'streak_count': 5,
            'current_streak': 'neutral'
        })
        self.assertEqual(events, [])


class TestStackEvents(unittest.TestCase):
    """Tests for stack-based event detection (double_up, crippled, short_stack)."""

    def setUp(self):
        self.detector = PressureEventDetector()
        # Create test game state with players at various stack sizes
        self.game_state = initialize_game_state(
            player_names=["Alice", "Bob", "Charlie"],
            starting_stack=1000
        )

    def test_double_up_detection(self):
        """double_up fires when winner ends with 2x+ starting stack."""
        # Alice starts with 500, wins pot, now has 1100 (>2x)
        game_state = self.game_state.update(
            players=tuple(
                p.update(stack=1100 if p.name == "Alice" else p.stack)
                for p in self.game_state.players
            )
        )
        hand_start_stacks = {"Alice": 500, "Bob": 1000, "Charlie": 1000}
        was_short = set()

        events, _ = self.detector.detect_stack_events(
            game_state, ["Alice"], hand_start_stacks, was_short, big_blind=100
        )

        event_names = [e[0] for e in events]
        self.assertIn("double_up", event_names)
        double_up = next(e for e in events if e[0] == "double_up")
        self.assertEqual(double_up[1], ["Alice"])

    def test_double_up_requires_winner(self):
        """double_up should not fire for losers even if stack increased (side pot)."""
        # Alice not in winner list, even if stack looks doubled
        game_state = self.game_state.update(
            players=tuple(
                p.update(stack=1100 if p.name == "Alice" else p.stack)
                for p in self.game_state.players
            )
        )
        hand_start_stacks = {"Alice": 500, "Bob": 1000, "Charlie": 1000}

        events, _ = self.detector.detect_stack_events(
            game_state, ["Bob"], hand_start_stacks, set(), big_blind=100
        )

        event_names = [e[0] for e in events]
        self.assertNotIn("double_up", event_names)

    def test_crippled_detection(self):
        """crippled fires when loser loses 75%+ of stack."""
        # Bob starts with 1000, loses 800, now has 200 (80% loss)
        game_state = self.game_state.update(
            players=tuple(
                p.update(stack=200 if p.name == "Bob" else p.stack)
                for p in self.game_state.players
            )
        )
        hand_start_stacks = {"Alice": 1000, "Bob": 1000, "Charlie": 1000}

        events, _ = self.detector.detect_stack_events(
            game_state, ["Alice"], hand_start_stacks, set(), big_blind=100
        )

        event_names = [e[0] for e in events]
        self.assertIn("crippled", event_names)
        crippled = next(e for e in events if e[0] == "crippled")
        self.assertEqual(crippled[1], ["Bob"])

    def test_crippled_not_for_winners(self):
        """crippled should not fire for winners."""
        # Alice is winner but also has low stack (shouldn't happen normally)
        game_state = self.game_state.update(
            players=tuple(
                p.update(stack=200 if p.name == "Alice" else p.stack)
                for p in self.game_state.players
            )
        )
        hand_start_stacks = {"Alice": 1000, "Bob": 1000, "Charlie": 1000}

        events, _ = self.detector.detect_stack_events(
            game_state, ["Alice"], hand_start_stacks, set(), big_blind=100
        )

        event_names = [e[0] for e in events]
        self.assertNotIn("crippled", event_names)

    def test_short_stack_transition(self):
        """short_stack fires only when crossing threshold (not when already short)."""
        # Charlie drops below 10 BB (1000) for first time
        game_state = self.game_state.update(
            players=tuple(
                p.update(stack=800 if p.name == "Charlie" else p.stack)
                for p in self.game_state.players
            )
        )
        hand_start_stacks = {"Alice": 1000, "Bob": 1000, "Charlie": 1200}
        was_short = set()  # No one was short before

        events, current_short = self.detector.detect_stack_events(
            game_state, ["Alice"], hand_start_stacks, was_short, big_blind=100
        )

        event_names = [e[0] for e in events]
        self.assertIn("short_stack", event_names)
        self.assertIn("Charlie", current_short)

    def test_short_stack_no_repeat(self):
        """short_stack should NOT fire if already short."""
        # Charlie was already short, still short
        game_state = self.game_state.update(
            players=tuple(
                p.update(stack=800 if p.name == "Charlie" else p.stack)
                for p in self.game_state.players
            )
        )
        hand_start_stacks = {"Alice": 1000, "Bob": 1000, "Charlie": 900}
        was_short = {"Charlie"}  # Charlie was already short

        events, current_short = self.detector.detect_stack_events(
            game_state, ["Alice"], hand_start_stacks, was_short, big_blind=100
        )

        event_names = [e[0] for e in events]
        self.assertNotIn("short_stack", event_names)
        self.assertIn("Charlie", current_short)  # Still tracked as short


class TestNemesisEvents(unittest.TestCase):
    """Tests for nemesis win/loss event detection."""

    def setUp(self):
        self.detector = PressureEventDetector()

    def test_nemesis_win_detection(self):
        """nemesis_win fires when player beats their nemesis."""
        # Alice's nemesis is Bob
        nemesis_map = {"Alice": "Bob", "Bob": None, "Charlie": None}

        # Alice wins, Bob loses
        events = self.detector.detect_nemesis_events(
            winner_names=["Alice"],
            loser_names=["Bob", "Charlie"],
            player_nemesis_map=nemesis_map
        )

        self.assertEqual(events, [("nemesis_win", ["Alice"])])

    def test_nemesis_loss_detection(self):
        """nemesis_loss fires when player loses to their nemesis."""
        # Alice's nemesis is Bob
        nemesis_map = {"Alice": "Bob", "Bob": None, "Charlie": None}

        # Bob wins, Alice loses
        events = self.detector.detect_nemesis_events(
            winner_names=["Bob"],
            loser_names=["Alice", "Charlie"],
            player_nemesis_map=nemesis_map
        )

        self.assertEqual(events, [("nemesis_loss", ["Alice"])])

    def test_no_event_without_nemesis(self):
        """No nemesis events if player has no nemesis."""
        nemesis_map = {"Alice": None, "Bob": None}

        events = self.detector.detect_nemesis_events(
            winner_names=["Alice"],
            loser_names=["Bob"],
            player_nemesis_map=nemesis_map
        )

        self.assertEqual(events, [])

    def test_no_event_nemesis_not_in_pot(self):
        """No nemesis event if nemesis folded/not involved."""
        # Alice's nemesis is Bob, but Bob folded (not in loser_names)
        nemesis_map = {"Alice": "Bob"}

        events = self.detector.detect_nemesis_events(
            winner_names=["Alice"],
            loser_names=["Charlie"],  # Bob not in pot
            player_nemesis_map=nemesis_map
        )

        self.assertEqual(events, [])

    def test_mutual_nemesis(self):
        """Both players can have nemesis events in same hand."""
        # Alice's nemesis is Bob, Bob's nemesis is Alice
        nemesis_map = {"Alice": "Bob", "Bob": "Alice"}

        # Alice wins, Bob loses
        events = self.detector.detect_nemesis_events(
            winner_names=["Alice"],
            loser_names=["Bob"],
            player_nemesis_map=nemesis_map
        )

        event_types = [e[0] for e in events]
        # Alice beat her nemesis
        self.assertIn("nemesis_win", event_types)
        # Bob lost to his nemesis
        self.assertIn("nemesis_loss", event_types)


if __name__ == '__main__':
    unittest.main()