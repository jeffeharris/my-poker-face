#!/usr/bin/env python3
"""
Test that AI player moods update correctly from elasticity changes.
"""

from poker.poker_player import AIPokerPlayer
from poker.elasticity_manager import ElasticPersonality


def test_mood_updates():
    """Test that AI player mood updates from elasticity."""
    print("=== Testing AI Player Mood Integration ===\n")
    
    # Create an AI player
    ai_player = AIPokerPlayer("Gordon Ramsay")
    
    print(f"Initial state:")
    print(f"  Confidence: {ai_player.confidence}")
    print(f"  Attitude: {ai_player.attitude}")
    print(f"  Aggression trait: {ai_player.elastic_personality.get_trait_value('aggression'):.2f}")
    
    # Apply pressure event
    print(f"\nApplying 'big_loss' event...")
    ai_player.apply_pressure_event('big_loss')
    
    print(f"\nAfter big loss:")
    print(f"  Confidence: {ai_player.confidence}")
    print(f"  Mood: {ai_player.elastic_personality.get_current_mood()}")
    print(f"  Aggression trait: {ai_player.elastic_personality.get_trait_value('aggression'):.2f}")
    
    # Apply multiple recovery cycles
    print(f"\nApplying recovery (5 cycles)...")
    for _ in range(5):
        ai_player.recover_traits()
    
    print(f"\nAfter recovery:")
    print(f"  Confidence: {ai_player.confidence}")
    print(f"  Mood: {ai_player.elastic_personality.get_current_mood()}")
    print(f"  Aggression trait: {ai_player.elastic_personality.get_trait_value('aggression'):.2f}")
    
    # Test personality modifier
    print(f"\nPersonality modifier instructions:")
    print(f"  {ai_player.get_personality_modifier()}")
    
    # Test successful bluff event
    print(f"\nApplying 'successful_bluff' event...")
    ai_player.apply_pressure_event('successful_bluff')
    
    print(f"\nAfter successful bluff:")
    print(f"  Bluff tendency: {ai_player.elastic_personality.get_trait_value('bluff_tendency'):.2f}")
    print(f"  New modifier: {ai_player.get_personality_modifier()}")
    
    # Test serialization with elastic personality
    print(f"\n--- Testing Serialization ---")
    player_dict = ai_player.to_dict()
    
    print(f"Serialized successfully: {'elastic_personality' in player_dict}")
    
    # Restore from dict
    restored_player = AIPokerPlayer.from_dict(player_dict)
    
    print(f"Restored player has elastic personality: {hasattr(restored_player, 'elastic_personality')}")
    print(f"Restored aggression: {restored_player.elastic_personality.get_trait_value('aggression'):.2f}")
    
    print("\n=== Mood Integration Test Complete ===")


if __name__ == '__main__':
    test_mood_updates()