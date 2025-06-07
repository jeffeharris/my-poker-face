#!/usr/bin/env python3
"""Test the pressure stats API endpoint."""

import requests
import json
import time

# Base URL for the API
BASE_URL = "http://localhost:5000"

def test_pressure_stats():
    """Test creating a game and checking pressure stats."""
    
    print("=== Testing Pressure Stats API ===\n")
    
    # Step 1: Create a new game
    print("1. Creating new game...")
    response = requests.post(f"{BASE_URL}/api/new-game", json={"playerName": "TestPlayer"})
    if response.status_code != 200:
        print(f"Failed to create game: {response.status_code}")
        print(response.text)
        return
    
    game_data = response.json()
    game_id = game_data.get('game_id')
    print(f"Game created with ID: {game_id}")
    
    # Step 2: Check initial pressure stats
    print("\n2. Checking initial pressure stats...")
    response = requests.get(f"{BASE_URL}/api/game/{game_id}/pressure-stats")
    if response.status_code != 200:
        print(f"Failed to get pressure stats: {response.status_code}")
        print(response.text)
    else:
        stats = response.json()
        print(f"Initial stats: {json.dumps(stats, indent=2)}")
    
    # Step 3: Get game state to see what's happening
    print("\n3. Getting game state...")
    response = requests.get(f"{BASE_URL}/api/game-state/{game_id}")
    if response.status_code == 200:
        game_state = response.json()
        print(f"Current player: {game_state['players'][game_state['current_player_idx']]['name']}")
        print(f"Phase: {game_state['phase']}")
        print(f"Pot: ${game_state['pot']}")
    
    # Step 4: Play some actions to generate events
    print("\n4. Playing some actions...")
    
    # If it's the human player's turn, make a move
    if game_state['players'][game_state['current_player_idx']]['is_human']:
        print("Making human player action: raise $50")
        response = requests.post(f"{BASE_URL}/api/game/{game_id}/action", 
                               json={"action": "raise", "amount": 50})
        if response.status_code != 200:
            print(f"Failed to make action: {response.status_code}")
        else:
            print("Action successful")
    
    # Wait a bit for AI actions
    print("\n5. Waiting for game to progress...")
    time.sleep(3)
    
    # Check stats again
    print("\n6. Checking pressure stats after some actions...")
    response = requests.get(f"{BASE_URL}/api/game/{game_id}/pressure-stats")
    if response.status_code != 200:
        print(f"Failed to get pressure stats: {response.status_code}")
        print(response.text)
    else:
        stats = response.json()
        print(f"Updated stats: {json.dumps(stats, indent=2)}")
        
        # Check if any events were recorded
        if stats['total_events'] == 0:
            print("\nNo events recorded yet!")
            print("This suggests pressure detection may not be triggering properly.")
        else:
            print(f"\nTotal events recorded: {stats['total_events']}")

if __name__ == "__main__":
    print("Make sure the Flask app is running on port 5000!")
    print("You can start it with: python3 -m flask_app.ui_web")
    print()
    
    try:
        test_pressure_stats()
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not connect to Flask app. Make sure it's running!")
    except Exception as e:
        print(f"ERROR: {e}")