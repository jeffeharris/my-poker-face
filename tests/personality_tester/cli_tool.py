#!/usr/bin/env python3
"""
Command-line test of the personality tester functionality
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from poker.poker_player import AIPokerPlayer

def test_scenario():
    """Test a scenario with multiple personalities"""
    
    # Scenario: Medium pair vs dangerous board
    scenario = {
        'hand': '7♥ 7♦',
        'community': 'K♠ Q♠ J♣',
        'pot': 500,
        'to_call': 100,
        'options': ['fold', 'call', 'raise']
    }
    
    # Test personalities
    personalities = ['Eeyore', 'Donald Trump', 'Bob Ross']
    
    # Build message
    message = f"""You have {scenario['hand']} in your hand.
Community Cards: {scenario['community']}
Pot Total: ${scenario['pot']}
Your cost to call: ${scenario['to_call']}
You must select from these options: {scenario['options']}
What is your move?"""
    
    print("SCENARIO: Pocket 7s vs K-Q-J board, facing $100 bet")
    print("="*60)
    
    for name in personalities:
        print(f"\n{name}:")
        
        ai = AIPokerPlayer(name=name, starting_money=10000)
        config = ai.personality_config
        traits = config.get('personality_traits', {})
        
        print(f"  Style: {config.get('play_style')}")
        print(f"  Aggression: {traits.get('aggression', 0):.0%}")
        
        response = ai.get_player_response(message)
        
        decision = response.get('action', 'unknown').upper()
        if response.get('raise_to', 0) > 0:
            decision += f" ${response.get('raise_to')}"
            
        print(f"  Decision: {decision}")
        beats = response.get("dramatic_sequence", [])
        if beats:
            print(f'  Dramatic Sequence: {beats}')

        
        if response.get('inner_monologue'):
            print(f'  Thinking: "{response.get("inner_monologue")}"')
        if response.get('hand_strategy'):
            print(f'  Strategy: {response.get("hand_strategy")}')

if __name__ == "__main__":
    test_scenario()