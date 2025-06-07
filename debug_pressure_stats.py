#!/usr/bin/env python3
"""Debug script to test pressure detection and stats recording."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from poker.elasticity_manager import ElasticityManager
from poker.pressure_detector import PressureEventDetector
from poker.pressure_stats import PressureStatsTracker
from poker.poker_game import PokerGameState, Player, initialize_game_state
from poker.utils import get_celebrities
import json
from pathlib import Path

def load_personality_config(name):
    """Load personality config for a player."""
    personalities_file = Path(__file__).parent / 'poker' / 'personalities.json'
    with open(personalities_file, 'r') as f:
        data = json.load(f)
    return data['personalities'].get(name, {
        'play_style': 'balanced',
        'confidence': 'normal',
        'attitude': 'neutral',
        'personality_traits': {
            'bluff_tendency': 0.5,
            'aggression': 0.5,
            'chattiness': 0.5,
            'emoji_usage': 0.3
        }
    })

def test_pressure_detection():
    """Test pressure detection and stats recording."""
    print("=== Testing Pressure Detection and Stats Recording ===\n")
    
    # Initialize components
    elasticity_manager = ElasticityManager()
    pressure_detector = PressureEventDetector(elasticity_manager)
    pressure_stats = PressureStatsTracker()
    
    # Create a simple game state
    game_state = initialize_game_state(
        player_names=["Alice", "Bob", "Charlie"],
        human_name="Player"
    )
    
    # Add AI players to elasticity manager
    for player in game_state.players:
        if not player.is_human:
            # Load personality config
            config = load_personality_config(player.name)
            elasticity_manager.add_player(player.name, config)
    
    print("Initial setup complete")
    print(f"Players: {[p.name for p in game_state.players]}")
    print(f"Elasticity manager has {len(elasticity_manager.personalities)} personalities")
    print()
    
    # Test 1: Showdown event detection
    print("Test 1: Simulating showdown with winner")
    winner_info = {
        'winnings': {'Alice': 500},
        'winning_hand': [14, 14, 10, 9, 8],  # Pair of aces
        'hand_rank': 8,
        'hand_name': 'Pair of Aces'
    }
    
    # Create a new game state with pot for testing
    from dataclasses import replace
    game_state = replace(game_state, pot={'total': 500})
    
    events = pressure_detector.detect_showdown_events(game_state, winner_info)
    print(f"Detected events: {events}")
    
    # Apply events
    pressure_detector.apply_detected_events(events)
    
    # Record stats
    for event_name, affected_players in events:
        details = {
            'pot_size': 500,
            'hand_rank': winner_info.get('hand_rank'),
            'hand_name': winner_info.get('hand_name')
        }
        pressure_stats.record_event(event_name, affected_players, details)
    
    print()
    
    # Test 2: Big pot win
    print("Test 2: Simulating big pot win")
    game_state = replace(game_state, pot={'total': 1000})
    winner_info2 = {
        'winnings': {'Bob': 1000},
        'winning_hand': [14, 13, 12, 11, 10],  # Straight
        'hand_rank': 5,
        'hand_name': 'Straight'
    }
    
    events2 = pressure_detector.detect_showdown_events(game_state, winner_info2)
    print(f"Detected events: {events2}")
    
    pressure_detector.apply_detected_events(events2)
    
    for event_name, affected_players in events2:
        details = {
            'pot_size': 1000,
            'hand_rank': winner_info2.get('hand_rank'),
            'hand_name': winner_info2.get('hand_name')
        }
        pressure_stats.record_event(event_name, affected_players, details)
    
    print()
    
    # Test 3: Check stats
    print("Test 3: Checking recorded stats")
    session_summary = pressure_stats.get_session_summary()
    
    print(f"Total events recorded: {session_summary['total_events']}")
    print(f"Biggest pot: ${session_summary['biggest_pot']}")
    print()
    
    print("Player summaries:")
    for name, summary in session_summary['player_summaries'].items():
        print(f"\n{name}:")
        print(f"  Total events: {summary['total_events']}")
        print(f"  Wins: {summary['wins']}")
        print(f"  Big wins: {summary['big_wins']}")
        print(f"  Biggest pot won: ${summary['biggest_pot_won']}")
        print(f"  Signature move: {summary['signature_move']}")
    
    print("\nLeaderboards:")
    for category, entries in session_summary['leaderboards'].items():
        if entries:
            print(f"\n{category}:")
            for entry in entries:
                print(f"  - {entry}")
    
    print("\nFun facts:")
    for fact in session_summary['fun_facts']:
        print(f"  - {fact}")
    
    # Test 4: Test pressure summary
    print("\nTest 4: Pressure summary")
    pressure_summary = pressure_detector.get_pressure_summary()
    for name, data in pressure_summary.items():
        print(f"\n{name}:")
        print(f"  Average pressure: {data['avg_pressure']:.2f}")
        print(f"  Current mood: {data['mood']}")

if __name__ == "__main__":
    test_pressure_detection()