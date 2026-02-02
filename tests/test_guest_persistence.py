#!/usr/bin/env python3
"""
Test script to verify guest user persistence with cookies.

This tests the flow where:
1. A guest logs in and gets a game
2. The guest "leaves" (clears session but keeps cookies)
3. The guest returns and can access their game
"""

import requests
import json
import time

# Base URL for the API
# Use port 5000 to match docker-compose.yml default backend port
BASE_URL = "http://localhost:5000"

def run_guest_persistence_test():
    """Manual integration test - requires a running server on localhost:5000.

    Run directly: python tests/test_guest_persistence.py
    """
    print("Testing guest user persistence flow...")
    
    # Create a session to maintain cookies
    session = requests.Session()
    
    # Step 1: Login as guest
    print("\n1. Logging in as guest...")
    login_response = session.post(f"{BASE_URL}/api/auth/login", json={
        "guest": True,
        "name": "TestGuest"
    })
    
    if login_response.status_code != 200:
        print(f"Login failed: {login_response.text}")
        return
    
    login_data = login_response.json()
    print(f"Guest created: {login_data['user']['id']}")
    print(f"Cookies after login: {dict(session.cookies)}")
    
    # Step 2: Create a game
    print("\n2. Creating a game...")
    game_response = session.post(f"{BASE_URL}/api/new-game", json={
        "player_name": "TestGuest"
    })
    
    if game_response.status_code != 200:
        print(f"Game creation failed: {game_response.text}")
        return
    
    game_data = game_response.json()
    game_id = game_data.get('game_id')
    print(f"Game created: {game_id}")
    
    # Step 3: List games to verify it's saved
    print("\n3. Listing games for this user...")
    games_response = session.get(f"{BASE_URL}/games")
    if games_response.status_code == 200:
        games_data = games_response.json()
        print(f"Found {len(games_data.get('games', []))} games")
    
    # Step 4: Simulate leaving the site (clear session but keep cookies)
    print("\n4. Simulating browser close (logging out but keeping cookies)...")
    # Save cookies
    saved_cookies = dict(session.cookies)
    print(f"Saved cookies: {saved_cookies}")
    
    # Create new session (simulating browser restart)
    session2 = requests.Session()
    # Restore only the guest_id cookie
    if 'guest_id' in saved_cookies:
        session2.cookies.set('guest_id', saved_cookies['guest_id'])
    
    # Step 5: Check auth status with cookie
    print("\n5. Checking auth status with cookie...")
    me_response = session2.get(f"{BASE_URL}/api/auth/me")
    if me_response.status_code == 200:
        me_data = me_response.json()
        if me_data.get('user'):
            print(f"User restored from cookie: {me_data['user']['id']}")
        else:
            print("No user found (cookie might not have worked)")
    
    # Step 6: Try to list games again
    print("\n6. Listing games with restored session...")
    games_response2 = session2.get(f"{BASE_URL}/games")
    if games_response2.status_code == 200:
        games_data2 = games_response2.json()
        print(f"Found {len(games_data2.get('games', []))} games")
        if games_data2.get('games'):
            print(f"Can access game: {games_data2['games'][0]['id']}")
    
    # Step 7: Try to login again with same name
    print("\n7. Logging in again as guest with same name...")
    login_response2 = session2.post(f"{BASE_URL}/api/auth/login", json={
        "guest": True,
        "name": "TestGuest"
    })
    
    if login_response2.status_code == 200:
        login_data2 = login_response2.json()
        print(f"Guest ID after re-login: {login_data2['user']['id']}")
        
        # Check if it's the same guest ID
        if login_data2['user']['id'] == login_data['user']['id']:
            print("✅ SUCCESS: Same guest ID maintained!")
        else:
            print("❌ FAIL: Different guest ID created")
    
    # Step 8: List games one more time
    print("\n8. Final games check...")
    games_response3 = session2.get(f"{BASE_URL}/games")
    if games_response3.status_code == 200:
        games_data3 = games_response3.json()
        print(f"Found {len(games_data3.get('games', []))} games")
        if game_id in [g['id'] for g in games_data3.get('games', [])]:
            print("✅ SUCCESS: Original game is accessible!")
        else:
            print("❌ FAIL: Original game not found")

if __name__ == "__main__":
    run_guest_persistence_test()