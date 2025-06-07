#!/usr/bin/env python3
"""Showcase different AI personalities making decisions."""

import os
import json
from dotenv import load_dotenv

# Load environment variables (override=True to use .env over shell variables)
load_dotenv(override=True)

from poker.poker_player import AIPokerPlayer

def showcase_personality(name, scenario):
    """Show how a personality responds to a poker scenario."""
    print(f"\n{'='*60}")
    print(f"🎭 {name}")
    print('='*60)
    
    # Create AI player
    ai = AIPokerPlayer(name=name, starting_money=10000)
    
    # Show personality info
    config = ai.personality_config
    traits = config.get('personality_traits', {})
    
    print(f"Play Style: {config.get('play_style', 'unknown')}")
    print(f"Confidence: {ai.confidence}")
    print(f"Attitude: {ai.attitude}")
    print(f"Bluff Tendency: {traits.get('bluff_tendency', 0):.0%}")
    print(f"Aggression: {traits.get('aggression', 0):.0%}")
    print(f"Chattiness: {traits.get('chattiness', 0):.0%}")
    
    # Show modifiers
    modifier = ai.get_personality_modifier()
    if modifier:
        print(f"\nStrategy: {modifier}")
    
    # Show scenario
    print(f"\nScenario: {scenario['description']}")
    print(f"Your hand: {scenario['hand']}")
    print(f"Community: {scenario['community']}")
    print(f"Pot: ${scenario['pot']}, To call: ${scenario['to_call']}")
    
    # Build prompt
    prompt = f"""Your cards: {scenario['hand']}
Community Cards: {scenario['community']}
Pot Total: ${scenario['pot']}
Your cost to call: ${scenario['to_call']}
You must select from these options: {scenario['options']}

{modifier if modifier else ''}

Please respond with your decision in the required JSON format."""
    
    # Get AI response
    print("\nThinking...")
    try:
        response = ai.assistant.chat(prompt)
        decision = json.loads(response)
        
        print(f"\n💭 Inner thoughts: \"{decision.get('inner_monologue', '...')[:100]}...\"")
        print(f"\n🎯 Decision: {decision.get('action', 'unknown').upper()}")
        
        if decision.get('adding_to_pot', 0) > 0:
            print(f"💰 Amount: ${decision.get('adding_to_pot')}")
            
        print(f"\n💬 Says: \"{decision.get('persona_response', '...')}\"")
        
        if decision.get('physical'):
            print(f"\n🎪 Actions: {', '.join(decision.get('physical', []))}")
            
        print(f"\n😊 New confidence: {decision.get('new_confidence', ai.confidence)}")
        
    except Exception as e:
        print(f"Error: {e}")

def main():
    print("🎰 AI Personality Showcase 🎰")
    print("See how different personalities approach the same situations!\n")
    
    scenarios = [
        {
            'description': "Weak hand, early position",
            'hand': "[7♣, 2♦]",
            'community': "[]",
            'pot': 150,
            'to_call': 50,
            'options': ['fold', 'call', 'raise']
        },
        {
            'description': "Monster hand on the flop",
            'hand': "[A♠, A♥]", 
            'community': "[A♣, K♦, 7♥]",
            'pot': 800,
            'to_call': 100,
            'options': ['call', 'raise', 'all_in']
        },
        {
            'description': "Drawing hand with good odds",
            'hand': "[J♥, 10♥]",
            'community': "[Q♥, K♦, 3♥]",
            'pot': 1200,
            'to_call': 100,
            'options': ['fold', 'call', 'raise']
        }
    ]
    
    personalities = ["Eeyore", "Donald Trump", "Gordon Ramsay", "Bob Ross", "A Mime"]
    
    # Show each personality with first scenario
    print("\n🎲 Scenario 1: Weak hand, early position")
    print("How do different personalities handle a bad hand?")
    
    for name in personalities[:3]:  # Just show 3 for brevity
        showcase_personality(name, scenarios[0])
        input("\nPress Enter for next player...")
    
    print("\n" + "="*60)
    print("Notice how:")
    print("- Eeyore (passive) likely folded immediately")
    print("- Trump (aggressive) might have tried to bluff")
    print("- Ramsay (confrontational) probably got angry about the cards")
    
    input("\nPress Enter to see a monster hand scenario...")
    
    # Show monster hand
    print("\n🎲 Scenario 2: Monster hand (three aces!)")
    showcase_personality("Donald Trump", scenarios[1])
    
    input("\nPress Enter to see a drawing hand...")
    
    # Show drawing hand
    print("\n🎲 Scenario 3: Drawing hand")
    showcase_personality("Bob Ross", scenarios[2])
    
    print("\n" + "="*60)
    print("✨ Summary: The prompt management system creates unique behaviors!")
    print("Each personality has distinct traits that influence their decisions.")

if __name__ == "__main__":
    main()