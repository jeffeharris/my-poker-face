#!/usr/bin/env python3
"""
Debug script to test pressure detection and stats tracking.
"""

from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.elasticity_manager import ElasticityManager
from poker.poker_game import PokerGameState, Player

# Create test components
print("Creating elasticity manager and pressure detector...")
elasticity_manager = ElasticityManager()
pressure_detector = PressureEventDetector(elasticity_manager)
stats_tracker = PressureStatsTracker()

# Create a simple game state manually
print("\nCreating test game state...")
players = (
    Player(name="Gordon", stack=1000, is_human=False),
    Player(name="Trump", stack=1000, is_human=False),
    Player(name="Bob", stack=1000, is_human=False),
)

game_state = PokerGameState(
    players=players,
    deck=(),
    community_cards=(),
    pot={'total': 2000},  # Big pot!
    current_player_idx=0,
    current_dealer_idx=0,
    small_blind_idx=1,
    big_blind_idx=2,
    big_blind_has_option=False,
    pre_flop_action_taken=True
)

print(f"Game state created with pot: ${game_state.pot['total']}")
print(f"Average stack: ${sum(p.stack for p in players) / len(players)}")

# Create winner info as it would come from determine_winner
winner_info = {
    'winnings': {'Gordon': 2000},
    'winning_hand': [14, 14, 13, 12, 11],
    'hand_name': 'Pair of Aces',
    'hand_rank': 9  # One pair
}

print(f"\nWinner info: {winner_info}")

# Detect showdown events
print("\nDetecting pressure events...")
events = pressure_detector.detect_showdown_events(game_state, winner_info)
print(f"Detected events: {events}")

# Check what events were detected
event_types = [e[0] for e in events]
print(f"\nEvent types detected: {event_types}")
print(f"Has 'big_win' event: {'big_win' in event_types}")

# Track the events in stats
print("\nRecording events in stats tracker...")
for event_type, affected_players in events:
    print(f"  Recording {event_type} for players: {affected_players}")
    stats_tracker.record_event(
        event_type,
        affected_players,
        {'pot_size': game_state.pot['total']}
    )

# Check Gordon's stats
print("\nChecking Gordon's stats...")
gordon_stats = stats_tracker.get_player_stats("Gordon")
print(f"Gordon's stats: {gordon_stats}")

# Check leaderboard
print("\nChecking leaderboards...")
leaderboards = stats_tracker.get_leaderboard()
print(f"Biggest winners: {leaderboards['biggest_winners']}")

# Get session summary
print("\nSession summary:")
summary = stats_tracker.get_session_summary()
print(f"Total events: {summary['total_events']}")
print(f"Biggest pot: ${summary['biggest_pot']}")