#!/usr/bin/env python3
"""
Quick test to see personalities in action.
Run this to test specific scenarios with different personalities.
"""

import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def test_personality_decisions():
    """Test how different personalities would act in specific scenarios."""
    
    # Load personalities
    with open('poker/personalities.json', 'r') as f:
        personalities = json.load(f)['personalities']
    
    # Define test scenarios
    scenarios = [
        {
            "name": "Bluff Opportunity",
            "description": "You have 2♣3♦, board shows A♠K♠Q♣J♦10♥. Perfect bluff spot?",
            "expected": {
                "high_bluff": "Should want to bluff",
                "low_bluff": "Should check/fold"
            }
        },
        {
            "name": "Monster Hand",
            "description": "You have A♠A♦, board shows A♣K♠2♦. Three aces!",
            "expected": {
                "aggressive": "Should raise big",
                "passive": "Should check/call"
            }
        },
        {
            "name": "Drawing Hand",
            "description": "You have 9♠10♠, board shows J♠Q♦3♠. Flush and straight draws.",
            "expected": {
                "aggressive": "Should semi-bluff raise",
                "passive": "Should call or fold"
            }
        }
    ]
    
    # Test each personality
    for scenario in scenarios:
        print(f"\n{'='*70}")
        print(f"SCENARIO: {scenario['name']}")
        print(f"{scenario['description']}")
        print("="*70)
        
        for name, config in personalities.items():
            traits = config['personality_traits']
            
            # Predict behavior based on traits
            if scenario['name'] == "Bluff Opportunity":
                if traits['bluff_tendency'] > 0.7:
                    action = "BLUFF RAISE"
                elif traits['bluff_tendency'] < 0.3:
                    action = "CHECK/FOLD"
                else:
                    action = "MAYBE BLUFF"
            
            elif scenario['name'] == "Monster Hand":
                if traits['aggression'] > 0.7:
                    action = "BIG RAISE"
                elif traits['aggression'] < 0.3:
                    action = "CHECK/CALL"
                else:
                    action = "SMALL RAISE"
            
            else:  # Drawing Hand
                if traits['aggression'] > 0.7 and traits['bluff_tendency'] > 0.5:
                    action = "SEMI-BLUFF RAISE"
                elif traits['aggression'] < 0.3:
                    action = "FOLD"
                else:
                    action = "CALL"
            
            print(f"\n{name}: {action}")
            print(f"  (Bluff: {traits['bluff_tendency']:.0%}, Aggression: {traits['aggression']:.0%})")

if __name__ == "__main__":
    print("Testing AI Personality Decision Making")
    print("This shows how personality traits affect poker decisions\n")
    test_personality_decisions()
    
    print("\n\nTo test with real AI responses, run:")
    print("  python interactive_demo.py")
    print("  python personality_showcase.py")
    print("\nMake sure OPENAI_API_KEY is set in .env file")