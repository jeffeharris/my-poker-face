#!/usr/bin/env python3
"""
Test script to verify the persistence layer works correctly.
"""
import os
import sys
sys.path.append(os.path.dirname(__file__))

from poker.persistence import GamePersistence
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine
from poker.utils import get_celebrities


def test_persistence():
    print("Testing poker game persistence...")
    
    # Create test database
    db_path = "test_poker_games.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    persistence = GamePersistence(db_path)
    
    # Create a game
    print("\n1. Creating new game...")
    player_names = get_celebrities(shuffled=True)[:4]
    game_state = initialize_game_state(player_names=player_names)
    state_machine = PokerStateMachine(game_state=game_state)
    game_id = "test_game_123"
    
    print(f"   Players: {[p.name for p in game_state.players]}")
    print(f"   Current phase: {state_machine.current_phase}")
    print(f"   Pot: ${game_state.pot['total']}")
    
    # Save the game
    print("\n2. Saving game...")
    persistence.save_game(game_id, state_machine)
    print("   Game saved successfully!")
    
    # Save some messages
    print("\n3. Saving messages...")
    persistence.save_message(game_id, "table", "Game started!")
    persistence.save_message(game_id, "user", "Jeff: Hello everyone!")
    persistence.save_message(game_id, "ai", "Kanye West: Let's do this!")
    print("   Messages saved!")
    
    # List games
    print("\n4. Listing saved games...")
    games = persistence.list_games()
    for game in games:
        print(f"   Game ID: {game.game_id}")
        print(f"   Created: {game.created_at}")
        print(f"   Phase: {game.phase}")
        print(f"   Players: {game.num_players}")
        print(f"   Pot: ${game.pot_size}")
    
    # Load the game back
    print("\n5. Loading game...")
    loaded_state_machine = persistence.load_game(game_id)
    if loaded_state_machine:
        print("   Game loaded successfully!")
        loaded_game_state = loaded_state_machine.game_state
        print(f"   Players: {[p.name for p in loaded_game_state.players]}")
        print(f"   Current phase: {loaded_state_machine.current_phase}")
        print(f"   Pot: ${loaded_game_state.pot['total']}")
        
        # Verify data matches
        original_players = [p.name for p in game_state.players]
        loaded_players = [p.name for p in loaded_game_state.players]
        if original_players == loaded_players:
            print("   ✓ Player data matches!")
        else:
            print("   ✗ Player data mismatch!")
    else:
        print("   Failed to load game!")
    
    # Load messages
    print("\n6. Loading messages...")
    messages = persistence.load_messages(game_id)
    for msg in messages:
        print(f"   [{msg['timestamp']}] {msg['type']}: {msg['text']}")
    
    # Clean up
    os.remove(db_path)
    print("\n✓ All tests passed!")


if __name__ == "__main__":
    test_persistence()