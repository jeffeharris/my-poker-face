#!/usr/bin/env python3
"""
Simple demonstration of the personality elasticity system.

Shows how traits change in response to game events and recover over time.
"""

from poker.elasticity_manager import ElasticityManager, ElasticPersonality
from poker.pressure_detector import PressureEventDetector


def print_personality_state(personality: ElasticPersonality):
    """Pretty print personality state."""
    print(f"\n{personality.name}")
    print("-" * 40)
    print(f"Current Mood: {personality.get_current_mood()}")
    print("\nTraits:")
    for trait_name, trait in personality.traits.items():
        deviation = trait.value - trait.anchor
        status = "↑" if deviation > 0.05 else "↓" if deviation < -0.05 else "="
        print(f"  {trait_name:15} {trait.value:.2f} ({status}) [pressure: {trait.pressure:+.2f}]")


def main():
    print("=== Personality Elasticity Demo ===\n")
    
    # Create elasticity manager
    manager = ElasticityManager()
    detector = PressureEventDetector(manager)
    
    # Add some players with different personalities
    personalities = {
        "Eeyore": {
            "play_style": "tight and passive",
            "personality_traits": {
                "bluff_tendency": 0.1,
                "aggression": 0.2,
                "chattiness": 0.3,
                "emoji_usage": 0.1
            }
        },
        "Gordon Ramsay": {
            "play_style": "aggressive and confrontational",
            "personality_traits": {
                "bluff_tendency": 0.6,
                "aggression": 0.95,
                "chattiness": 0.9,
                "emoji_usage": 0.2
            }
        },
        "Bob Ross": {
            "play_style": "calm and optimistic",
            "personality_traits": {
                "bluff_tendency": 0.3,
                "aggression": 0.1,
                "chattiness": 0.6,
                "emoji_usage": 0.5
            }
        }
    }
    
    # Add players to manager
    for name, config in personalities.items():
        manager.add_player(name, config)
    
    print("Initial States:")
    for name in personalities:
        print_personality_state(manager.personalities[name])
    
    # Simulate some game events
    print("\n\n=== Simulating Game Events ===")
    
    # Event 1: Gordon Ramsay loses big
    print("\n1. Gordon Ramsay loses a big pot!")
    manager.apply_game_event("big_loss", ["Gordon Ramsay"])
    print_personality_state(manager.personalities["Gordon Ramsay"])
    
    # Event 2: Eeyore wins big (shocking!)
    print("\n2. Eeyore wins a huge pot!")
    manager.apply_game_event("big_win", ["Eeyore"])
    print_personality_state(manager.personalities["Eeyore"])
    
    # Event 3: Bob Ross successfully bluffs
    print("\n3. Bob Ross pulls off a big bluff!")
    manager.apply_game_event("successful_bluff", ["Bob Ross"])
    print_personality_state(manager.personalities["Bob Ross"])
    
    # Event 4: Gordon gets bluffed
    print("\n4. Gordon Ramsay gets bluffed by Bob Ross!")
    manager.apply_game_event("bluff_called", ["Gordon Ramsay"])
    print_personality_state(manager.personalities["Gordon Ramsay"])
    
    # Apply recovery over several rounds
    print("\n\n=== Applying Recovery (5 rounds) ===")
    for i in range(5):
        manager.recover_all()
    
    print("\nAfter Recovery:")
    for name in personalities:
        print_personality_state(manager.personalities[name])
    
    # Show mood changes
    print("\n\n=== Mood Summary ===")
    for name in personalities:
        personality = manager.personalities[name]
        print(f"{name:15} → {personality.get_current_mood()}")
    
    # Demonstrate chat events
    print("\n\n=== Chat Interaction ===")
    print("Gordon says: 'That was terrible play, you donkey!'")
    events = detector.detect_chat_events(
        "Gordon Ramsay", 
        "That was terrible play, you donkey!",
        ["Eeyore", "Bob Ross"]
    )
    detector.apply_detected_events(events)
    
    print("\nEeyore's reaction:")
    print_personality_state(manager.personalities["Eeyore"])
    
    print("\n\nBob Ross says: 'No mistakes, just happy accidents! Great game everyone!'")
    events = detector.detect_chat_events(
        "Bob Ross",
        "No mistakes, just happy accidents! Great game everyone!",
        ["Gordon Ramsay", "Eeyore"]
    )
    detector.apply_detected_events(events)
    
    print("\nGordon's mood after friendly chat:")
    print(f"Gordon Ramsay → {manager.personalities['Gordon Ramsay'].get_current_mood()}")


if __name__ == '__main__':
    main()