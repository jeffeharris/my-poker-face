#!/usr/bin/env python3
"""Test the personality manager functionality"""

import json
from pathlib import Path

# Load personalities
personalities_file = Path(__file__).parent.parent.parent / 'poker' / 'personalities.json'

with open(personalities_file, 'r') as f:
    data = json.load(f)

print("Current Personalities:")
print("=" * 50)

for name, config in data['personalities'].items():
    traits = config.get('personality_traits', {})
    print(f"\n{name}:")
    print(f"  Play Style: {config.get('play_style', 'unknown')}")
    print(f"  Confidence: {config.get('default_confidence', 'unknown')}")
    print(f"  Traits:")
    print(f"    - Bluff: {traits.get('bluff_tendency', 0):.0%}")
    print(f"    - Aggression: {traits.get('aggression', 0):.0%}")
    print(f"    - Chattiness: {traits.get('chattiness', 0):.0%}")
    print(f"    - Emoji Usage: {traits.get('emoji_usage', 0):.0%}")
    
    if config.get('verbal_tics'):
        print(f"  Verbal Tics: {len(config['verbal_tics'])} phrases")
    if config.get('physical_tics'):
        print(f"  Physical Tics: {len(config['physical_tics'])} actions")

print(f"\n\nTotal personalities: {len(data['personalities'])}")
print(f"File location: {personalities_file}")
print("\nTo edit these personalities, run:")
print("  python personality_manager.py")
print("  Then open http://localhost:5002")