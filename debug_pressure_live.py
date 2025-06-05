#!/usr/bin/env python3
"""
Debug the pressure system with a live game.
"""

import requests
import json
import time

# Configuration
BASE_URL = "http://localhost:5002"

print("Creating a new game...")
response = requests.post(f"{BASE_URL}/api/new-game")
if response.status_code != 200:
    print(f"Failed to create game: {response.text}")
    exit(1)

game_id = response.json()['game_id']
print(f"Created game: {game_id}")

# Get initial game state
print("\nGetting game state...")
response = requests.get(f"{BASE_URL}/api/game-state/{game_id}")
game_data = response.json()

print(f"Players: {[p['name'] for p in game_data['players']]}")
print(f"Current phase: {game_data['phase']}")
print(f"Pot: ${game_data['pot']['total']}")

# Play some actions to create a pot
print("\nPlaying some actions...")

# Make a bet to build the pot
if 'raise' in game_data['player_options']:
    print("Making a raise...")
    response = requests.post(f"{BASE_URL}/api/game/{game_id}/action", 
                           json={'action': 'raise', 'amount': 200})
    time.sleep(2)

# Continue playing until showdown
for i in range(10):
    response = requests.get(f"{BASE_URL}/api/game-state/{game_id}")
    game_data = response.json()
    
    print(f"\nRound {i+1}:")
    print(f"  Phase: {game_data['phase']}")
    print(f"  Pot: ${game_data['pot']['total']}")
    print(f"  Current player: {game_data['players'][game_data['current_player_idx']]['name']}")
    
    if game_data['phase'] in ['HAND_OVER', 'EVALUATING_HAND']:
        print("  Hand complete!")
        break
        
    # If it's human's turn, take an action
    if game_data['players'][game_data['current_player_idx']]['is_human']:
        options = game_data['player_options']
        print(f"  Options: {options}")
        
        if 'call' in options:
            action = 'call'
        elif 'check' in options:
            action = 'check'
        else:
            action = 'fold'
            
        print(f"  Taking action: {action}")
        response = requests.post(f"{BASE_URL}/api/game/{game_id}/action", 
                               json={'action': action})
        time.sleep(1)

# Check pressure stats
print("\n\nChecking pressure stats...")
response = requests.get(f"{BASE_URL}/api/game/{game_id}/pressure-stats")
if response.status_code == 200:
    stats = response.json()
    print(f"Total events: {stats['total_events']}")
    print(f"Biggest pot: ${stats['biggest_pot']}")
    
    if stats['leaderboards']['biggest_winners']:
        print("\nBiggest Winners:")
        for winner in stats['leaderboards']['biggest_winners']:
            print(f"  {winner['name']}: {winner['wins']} wins (${winner['biggest_pot']})")
    
    print("\nPlayer summaries:")
    for name, summary in stats['player_summaries'].items():
        print(f"  {name}:")
        print(f"    Big wins: {summary['big_wins']}")
        print(f"    Big losses: {summary['big_losses']}")
        print(f"    Total events: {summary['total_events']}")
else:
    print(f"Failed to get stats: {response.status_code}")

# Check elasticity data
print("\n\nChecking elasticity data...")
response = requests.get(f"{BASE_URL}/api/game/{game_id}/elasticity")
if response.status_code == 200:
    elasticity = response.json()
    for name, data in elasticity.items():
        print(f"\n{name}:")
        print(f"  Mood: {data['mood']}")
        for trait, values in data['traits'].items():
            if values['current'] != values['anchor']:
                print(f"  {trait}: {values['current']:.2f} (anchor: {values['anchor']:.2f}, pressure: {values['pressure']:.2f})")