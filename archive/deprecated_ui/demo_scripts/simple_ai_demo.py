#!/usr/bin/env python3
"""
Simple AI personality demo that works without the full game state machine.
Shows how different personalities respond to poker scenarios.
"""

import os
import json
from dotenv import load_dotenv

# Load environment variables (override=True to use .env over shell variables)
load_dotenv(override=True)

from poker.poker_player import AIPokerPlayer

def demo_personality_response(name, scenario_description, message):
    """Show how a specific personality responds to a scenario."""
    print(f"\n{'='*60}")
    print(f"🎭 {name}")
    print('='*60)
    
    # Create AI player
    ai = AIPokerPlayer(name=name, starting_money=10000)
    
    # Show personality traits
    config = ai.personality_config
    traits = config.get('personality_traits', {})
    
    print(f"Play Style: {config.get('play_style', 'unknown')}")
    print(f"Bluff Tendency: {traits.get('bluff_tendency', 0):.0%}")
    print(f"Aggression: {traits.get('aggression', 0):.0%}")
    
    print(f"\nScenario: {scenario_description}")
    
    # Get AI response
    try:
        print(f"\nGetting {name}'s response...")
        response = ai.get_player_response(message)
        
        print(f"\nDecision: {response.get('action', 'unknown').upper()}")
        if response.get('adding_to_pot', 0) > 0:
            print(f"Amount: ${response.get('adding_to_pot', 0)}")
        
        print(f"\n{name} says: \"{response.get('persona_response', '...')}\"")
        
        if response.get('physical'):
            print(f"Physical: {', '.join(response.get('physical', []))}")
            
        if response.get('inner_monologue'):
            print(f"Thinking: \"{response.get('inner_monologue', '...')}\"")
            
    except Exception as e:
        print(f"Error getting response: {e}")
        print("Make sure your OPENAI_API_KEY is set in .env file")

def main():
    print("🎰 AI Poker Personality Demo 🎰")
    print("See how different personalities respond to poker scenarios!\n")
    
    # Test scenario
    scenario = {
        "description": "Pocket 7s, facing a $100 bet. Board shows K♠ Q♠ J♣",
        "message": """You have 7♥ 7♦ in your hand.
Community Cards: ['K♠', 'Q♠', 'J♣']
Pot Total: $500
Your cost to call: $100
You must select from these options: ['fold', 'call', 'raise']
What is your move?"""
    }
    
    # Test different personalities
    personalities = ["Eeyore", "Donald Trump", "Gordon Ramsay", "Bob Ross"]
    
    for name in personalities:
        demo_personality_response(name, scenario["description"], scenario["message"])

if __name__ == "__main__":
    main()