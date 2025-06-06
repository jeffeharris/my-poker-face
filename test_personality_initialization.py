"""
Test that AI players are initialized with their configured personalities.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from poker.utils import get_celebrities
from poker.poker_game import initialize_game_state
from poker.poker_player import AIPokerPlayer
from poker.controllers import AIPlayerController

def test_personality_initialization():
    """Test that all AI players get their proper personalities from the JSON config."""
    
    print("Testing AI Player Personality Initialization")
    print("=" * 50)
    
    # Get random AI players like the game does
    ai_player_names = get_celebrities(shuffled=True)[:3]
    print(f"\nSelected AI players: {ai_player_names}")
    
    # Initialize game state
    game_state = initialize_game_state(player_names=ai_player_names, human_name="TestHuman")
    
    print("\nChecking each AI player's personality configuration:")
    print("-" * 50)
    
    # Check each AI player
    for player in game_state.players:
        if not player.is_human:
            # Create an AI controller like the game does
            controller = AIPlayerController(player_name=player.name)
            ai_player = controller.ai_player
            
            print(f"\nPlayer: {player.name}")
            print(f"  Configured personality traits:")
            print(f"    - Play style: {ai_player.personality_config.get('play_style', 'NOT FOUND')}")
            print(f"    - Default confidence: {ai_player.personality_config.get('default_confidence', 'NOT FOUND')}")
            print(f"    - Default attitude: {ai_player.personality_config.get('default_attitude', 'NOT FOUND')}")
            print(f"    - Bluff tendency: {ai_player.personality_config['personality_traits'].get('bluff_tendency', 'NOT FOUND')}")
            print(f"    - Aggression: {ai_player.personality_config['personality_traits'].get('aggression', 'NOT FOUND')}")
            print(f"    - Chattiness: {ai_player.personality_config['personality_traits'].get('chattiness', 'NOT FOUND')}")
            
            print(f"\n  Actual AI player state:")
            print(f"    - Confidence: {ai_player.confidence}")
            print(f"    - Attitude: {ai_player.attitude}")
            
            # Check if they match
            expected_confidence = ai_player.personality_config.get('default_confidence', 'Unsure')
            expected_attitude = ai_player.personality_config.get('default_attitude', 'Distracted')
            
            if ai_player.confidence == expected_confidence and ai_player.attitude == expected_attitude:
                print(f"    ✓ Personality loaded correctly!")
            else:
                print(f"    ✗ MISMATCH! Expected {expected_confidence}/{expected_attitude}, got {ai_player.confidence}/{ai_player.attitude}")
    
    print("\n" + "=" * 50)
    print("Test complete!")

if __name__ == "__main__":
    test_personality_initialization()