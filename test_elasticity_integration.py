#!/usr/bin/env python3
"""
Test the elasticity system integration without requiring OpenAI API.
This uses mock AI responses to demonstrate the elasticity features.
"""

import json
from poker.poker_game import PokerGameState, Player, initialize_game_state
from poker.elasticity_manager import ElasticityManager
from poker.pressure_detector import PressureEventDetector
from poker.controllers import AIPlayerController
from poker.poker_player import AIPokerPlayer


def create_mock_game_state():
    """Create a mock game state for testing."""
    players = [
        Player(name="Eeyore", stack=1000, bet=0, hand=[], 
               is_human=False, is_all_in=False, is_folded=False, has_acted=False),
        Player(name="Gordon Ramsay", stack=2000, bet=0, hand=[], 
               is_human=False, is_all_in=False, is_folded=False, has_acted=False),
        Player(name="Bob Ross", stack=1500, bet=0, hand=[], 
               is_human=False, is_all_in=False, is_folded=False, has_acted=False),
    ]
    
    return PokerGameState(
        players=tuple(players),
        deck=tuple(),
        community_cards=tuple(),
        pot={'total': 0, 'side_pots': []},
        current_player_idx=0,
        current_dealer_idx=0,
        current_ante=10
    )


def test_elasticity_with_game_events():
    """Test elasticity system responding to game events."""
    print("=== Testing Elasticity Integration ===\n")
    
    # Initialize elasticity system
    elasticity_manager = ElasticityManager()
    pressure_detector = PressureEventDetector(elasticity_manager)
    
    # Create game state
    game_state = create_mock_game_state()
    
    # Create AI players and add to elasticity manager
    ai_players = {}
    for player in game_state.players:
        ai_player = AIPokerPlayer(player.name)
        ai_players[player.name] = ai_player
        
        # Add to elasticity manager
        elasticity_manager.add_player(
            player.name,
            ai_player.personality_config
        )
    
    # Display initial states
    print("Initial Personality States:")
    for name, personality in elasticity_manager.personalities.items():
        traits = elasticity_manager.get_player_traits(name)
        mood = elasticity_manager.get_player_mood(name)
        print(f"{name:15} - Mood: {mood:12} - Aggression: {traits['aggression']:.2f}")
    
    # Simulate game events
    print("\n--- Game Event: Gordon Ramsay wins big pot ---")
    winner_info = {
        'winner_name': 'Gordon Ramsay',
        'hand_rank': 3,  # Full house (strong)
        'winning_hand': 'Full House'
    }
    
    # Update pot to make it significant
    game_state = game_state.update(pot={'total': 3000, 'side_pots': []})
    
    events = pressure_detector.detect_showdown_events(game_state, winner_info)
    print(f"Detected events: {[e[0] for e in events]}")
    pressure_detector.apply_detected_events(events)
    
    # Show updated states
    print("\nUpdated Personality States:")
    for name, personality in elasticity_manager.personalities.items():
        traits = elasticity_manager.get_player_traits(name)
        mood = elasticity_manager.get_player_mood(name)
        print(f"{name:15} - Mood: {mood:12} - Aggression: {traits['aggression']:.2f}")
    
    # Test trait usage in AI controller
    print("\n--- Testing Trait Usage in AI Controller ---")
    
    # Create controller for Gordon (who just won big)
    class MockStateMachine:
        def __init__(self, game_state):
            self.game_state = game_state
    
    controller = AIPlayerController("Gordon Ramsay", MockStateMachine(game_state))
    controller.ai_player = ai_players["Gordon Ramsay"]
    
    # Get current traits (should reflect elasticity changes)
    current_traits = controller.get_current_personality_traits()
    print(f"Gordon's current traits from controller:")
    for trait, value in current_traits.items():
        print(f"  {trait}: {value:.2f}")
    
    # Simulate bluff detection
    print("\n--- Game Event: Eeyore's bluff gets called ---")
    elasticity_manager.apply_game_event("bluff_called", ["Eeyore"])
    
    print("\nEeyore's state after failed bluff:")
    traits = elasticity_manager.get_player_traits("Eeyore")
    mood = elasticity_manager.get_player_mood("Eeyore")
    print(f"Mood: {mood}")
    print(f"Bluff tendency: {traits['bluff_tendency']:.2f}")
    print(f"Aggression: {traits['aggression']:.2f}")
    
    # Apply recovery
    print("\n--- Applying Recovery (10 rounds) ---")
    for _ in range(10):
        elasticity_manager.recover_all()
    
    print("\nFinal Personality States:")
    for name, personality in elasticity_manager.personalities.items():
        traits = elasticity_manager.get_player_traits(name)
        mood = elasticity_manager.get_player_mood(name)
        
        # Check deviation from anchor
        personality_obj = elasticity_manager.personalities[name]
        max_deviation = max(
            abs(trait.value - trait.anchor) 
            for trait in personality_obj.traits.values()
        )
        
        print(f"{name:15} - Mood: {mood:12} - Max deviation: {max_deviation:.3f}")
    
    # Test persistence
    print("\n--- Testing Persistence ---")
    
    # Serialize elasticity manager
    elasticity_data = elasticity_manager.to_dict()
    print(f"Serialized {len(elasticity_data['personalities'])} personalities")
    
    # Create new manager from serialized data
    new_manager = ElasticityManager.from_dict(elasticity_data)
    
    # Verify restoration
    print("\nRestored personality states:")
    for name in new_manager.personalities:
        restored_traits = new_manager.get_player_traits(name)
        original_traits = elasticity_manager.get_player_traits(name)
        
        matches = all(
            abs(restored_traits[t] - original_traits[t]) < 0.001 
            for t in restored_traits
        )
        print(f"{name:15} - Correctly restored: {matches}")
    
    print("\n=== Elasticity Integration Test Complete ===")


if __name__ == '__main__':
    test_elasticity_with_game_events()