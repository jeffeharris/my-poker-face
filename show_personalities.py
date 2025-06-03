#!/usr/bin/env python3
"""Show the personality configurations without API calls."""

import json
from pathlib import Path

def main():
    print("🎰 Poker AI Personality Showcase 🎰")
    print("=" * 60)
    
    # Load personalities
    personalities_file = Path("poker/personalities.json")
    with open(personalities_file, 'r') as f:
        data = json.load(f)
    
    personalities = data['personalities']
    
    print("\nConfigured AI Personalities:\n")
    
    for name, config in personalities.items():
        print(f"🎭 {name}")
        print("-" * 40)
        print(f"Play Style: {config['play_style']}")
        print(f"Default Confidence: {config['default_confidence']}")
        print(f"Default Attitude: {config['default_attitude']}")
        
        traits = config['personality_traits']
        print(f"\nPersonality Traits:")
        print(f"  • Bluff Tendency: {traits['bluff_tendency']:.0%}")
        print(f"  • Aggression: {traits['aggression']:.0%}")
        print(f"  • Chattiness: {traits['chattiness']:.0%}")
        print(f"  • Emoji Usage: {traits['emoji_usage']:.0%}")
        
        if 'verbal_tics' in config:
            print(f"\nVerbal Tics: {', '.join(config['verbal_tics'][:2])}...")
        
        if 'physical_tics' in config:
            print(f"Physical Tics: {config['physical_tics'][0]}")
        
        print("\n")
    
    print("=" * 60)
    print("\nHow these traits affect gameplay:\n")
    
    print("🎯 Bluff Tendency:")
    print("  • Low (0-30%): Rarely bluffs, plays honest poker")
    print("  • Medium (30-70%): Balanced bluffing")
    print("  • High (70-100%): Frequently bluffs, deceptive play")
    
    print("\n💪 Aggression:")
    print("  • Low (0-30%): Passive, checks/calls often")
    print("  • Medium (30-70%): Balanced aggression")
    print("  • High (70-100%): Raises frequently, puts pressure on")
    
    print("\n💬 Chattiness:")
    print("  • Low (0-30%): Quiet, minimal table talk")
    print("  • Medium (30-70%): Normal conversation")
    print("  • High (70-100%): Very talkative, lots of banter")
    
    print("\n" + "=" * 60)
    print("\nExample Decision Patterns:\n")
    
    examples = [
        ("Eeyore", "Weak hand", "Likely FOLDS immediately - too pessimistic to bluff"),
        ("Donald Trump", "Strong hand", "Goes ALL-IN - maximum aggression with confidence"),
        ("Gordon Ramsay", "Medium hand", "RAISES aggressively with intense commentary"),
        ("Bob Ross", "Drawing hand", "CALLS peacefully - 'happy little cards'"),
        ("A Mime", "Bluff opportunity", "RAISES silently - 90% bluff tendency!")
    ]
    
    for name, situation, likely_action in examples:
        print(f"• {name} + {situation} = {likely_action}")
    
    print("\n" + "=" * 60)
    print("\nThe prompt management system makes each AI unique!")
    print("Personalities are easily configurable in poker/personalities.json")

if __name__ == "__main__":
    main()