#!/usr/bin/env python3
"""
Simple demonstration of how personality traits affect AI player decisions.
This creates a mock scenario to show the different responses.
"""

import json
from pathlib import Path


def load_personalities():
    """Load personality configurations from JSON."""
    filepath = Path(__file__).parent.parent / 'poker' / 'personalities.json'
    with open(filepath, 'r') as f:
        return json.load(f)['personalities']


def simulate_decision(personality_name, personality_config, scenario):
    """Simulate a decision based on personality traits."""
    traits = personality_config['personality_traits']
    
    if scenario == "facing_bet_with_medium_hand":
        # Pocket 7s facing bet with KQJ on board
        if traits['aggression'] > 0.8 and traits['bluff_tendency'] > 0.7:
            return "RAISE $2000", "I'm going all-in on aggression!"
        elif traits['aggression'] < 0.3:
            return "FOLD", "Too risky for my passive style"
        elif traits['bluff_tendency'] > 0.6:
            return "RAISE $1000", "Let's try a bluff"
        else:
            return "CALL $100", "I'll see where this goes"
    
    elif scenario == "no_bet_with_strong_hand":
        # Pocket Aces, no bet yet
        if traits['aggression'] > 0.7:
            return "RAISE $1500", "Time to build the pot!"
        else:
            return "CHECK", "Let's be cautious"
    
    return "CHECK", "Default play"


def get_personality_response(name, action):
    """Get personality-specific verbal responses."""
    responses = {
        "Eeyore": {
            "FOLD": "Of course I fold. Story of my life. *sighs heavily*",
            "CALL $100": "I'll call, but it won't end well...",
            "CHECK": "Oh bother, I'll just check. No point in losing more.",
            "RAISE $1000": "I suppose I'll raise... though it probably won't help."
        },
        "Donald Trump": {
            "RAISE $2000": "I'm raising BIGLY! Nobody plays like me, believe me!",
            "RAISE $1500": "TREMENDOUS raise! This pot is gonna be YUGE!",
            "CALL $100": "I'll call, but only because this pot is already TREMENDOUS!"
        },
        "Gordon Ramsay": {
            "RAISE $2000": "RAISE! This pot is RAW and I'm cooking you donkeys!",
            "RAISE $1500": "Time to turn up the HEAT! You muppets can't handle this!",
            "RAISE $1000": "This hand needs more SEASONING! Raise it up!"
        },
        "Bob Ross": {
            "FOLD": "I'll fold this one. Sometimes you need to let go to find joy.",
            "CHECK": "Let's just check and see what happy little cards come next.",
            "CALL $100": "I'll call. Every card is a friend waiting to be discovered."
        }
    }
    
    return responses.get(name, {}).get(action, f"{name} makes a {action}")


def main():
    """Run the personality demonstration."""
    personalities = load_personalities()
    
    print("=" * 80)
    print("POKER AI PERSONALITY DEMONSTRATION")
    print("=" * 80)
    
    # Scenario 1: Facing bet with medium hand
    print("\nSCENARIO 1: All players have 7♥7♦, flop is K♠Q♠J♣, facing $100 bet")
    print("-" * 80)
    
    for name in ["Eeyore", "Donald Trump", "Gordon Ramsay", "Bob Ross"]:
        if name in personalities:
            config = personalities[name]
            traits = config['personality_traits']
            
            action, reasoning = simulate_decision(name, config, "facing_bet_with_medium_hand")
            response = get_personality_response(name, action)
            
            print(f"\n{name}:")
            print(f"  Play Style: {config['play_style']}")
            print(f"  Traits: Bluff {traits['bluff_tendency']:.0%}, Aggression {traits['aggression']:.0%}")
            print(f"  Decision: {action}")
            print(f"  Says: \"{response}\"")
            print(f"  Reasoning: {reasoning}")
    
    # Scenario 2: No bet with strong hand
    print("\n\nSCENARIO 2: All players have A♥A♦, no bets yet")
    print("-" * 80)
    
    for name in ["Eeyore", "Donald Trump", "Gordon Ramsay", "Bob Ross"]:
        if name in personalities:
            config = personalities[name]
            traits = config['personality_traits']
            
            action, reasoning = simulate_decision(name, config, "no_bet_with_strong_hand")
            response = get_personality_response(name, action)
            
            print(f"\n{name}:")
            print(f"  Decision: {action}")
            print(f"  Says: \"{response}\"")
    
    print("\n" + "=" * 80)
    print("SUMMARY: Personality traits directly influence decisions!")
    print("- High aggression (>0.7) → More raises")
    print("- Low aggression (<0.3) → More folds/checks")
    print("- High bluff tendency → Willing to raise with weaker hands")
    print("- Each personality maintains consistent behavior patterns")
    print("=" * 80)


if __name__ == "__main__":
    main()