#!/usr/bin/env python3
"""
Web utility for testing AI poker personalities with custom scenarios.
"""

from flask import Flask, render_template, request, jsonify
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables (override shell vars to use .env)
load_dotenv(override=True)

from poker.poker_player import AIPokerPlayer

app = Flask(__name__)

# Load available personalities
def get_available_personalities():
    """Load personality names from personalities.json"""
    personalities_path = project_root / 'poker' / 'personalities.json'
    with open(personalities_path, 'r') as f:
        data = json.load(f)
    return sorted(list(data['personalities'].keys()))

# Predefined scenarios
PRESET_SCENARIOS = {
    "pocket_aces_preflop": {
        "name": "Pocket Aces Pre-flop",
        "hand": "A♥ A♦",
        "community": "",
        "pot": 150,
        "to_call": 50,
        "options": ["fold", "call", "raise"],
        "description": "You have pocket aces pre-flop. Small blind raised to $50."
    },
    "bluff_opportunity": {
        "name": "Bluff Opportunity",
        "hand": "2♣ 3♦",
        "community": "A♠ K♠ Q♣ J♦ 10♥",
        "pot": 1000,
        "to_call": 200,
        "options": ["fold", "call", "raise"],
        "description": "Terrible hand but the board shows a straight. Perfect bluff spot?"
    },
    "medium_pair_dangerous_board": {
        "name": "Medium Pair vs Dangerous Board",
        "hand": "7♥ 7♦",
        "community": "K♠ Q♠ J♣",
        "pot": 500,
        "to_call": 100,
        "options": ["fold", "call", "raise"],
        "description": "Pocket 7s facing a bet with high cards on board."
    },
    "flush_draw": {
        "name": "Flush Draw",
        "hand": "A♠ 5♠",
        "community": "K♠ 7♠ 2♣",
        "pot": 300,
        "to_call": 75,
        "options": ["fold", "call", "raise"],
        "description": "Nut flush draw on the flop."
    },
    "monster_hand": {
        "name": "Monster Hand - Set",
        "hand": "9♥ 9♦",
        "community": "9♠ 5♣ 2♦",
        "pot": 400,
        "to_call": 0,
        "options": ["check", "bet"],
        "description": "Flopped a set of nines. No one has bet yet."
    }
}

@app.route('/')
def index():
    personalities = get_available_personalities()
    return render_template('index.html', 
                         personalities=personalities,
                         scenarios=PRESET_SCENARIOS)

@app.route('/test_personality', methods=['POST'])
def test_personality():
    """Test how a personality responds to a scenario"""
    try:
        data = request.json
        personality_name = data['personality']
        scenario = data['scenario']
        
        # Build the message for the AI
        message = f"""You have {scenario['hand']} in your hand.
Community Cards: {scenario['community'] if scenario['community'] else 'None yet'}
Pot Total: ${scenario['pot']}
Your cost to call: ${scenario['to_call']}
You must select from these options: {scenario['options']}
What is your move?"""
        
        # Create AI player and get response
        ai = AIPokerPlayer(name=personality_name, starting_money=10000)
        
        # Get personality config for display
        config = ai.personality_config
        traits = config.get('personality_traits', {})
        
        # Get AI response
        response = ai.get_player_response(message)
        
        # Format the result
        result = {
            'personality': personality_name,
            'traits': {
                'play_style': config.get('play_style', 'unknown'),
                'bluff_tendency': f"{traits.get('bluff_tendency', 0):.0%}",
                'aggression': f"{traits.get('aggression', 0):.0%}",
                'chattiness': f"{traits.get('chattiness', 0):.0%}",
            },
            'decision': response.get('action', 'unknown').upper(),
            'amount': response.get('adding_to_pot', 0),
            'says': response.get('persona_response', '...'),
            'physical': response.get('physical', []),
            'thinking': response.get('inner_monologue', ''),
            'confidence': response.get('new_confidence', response.get('confidence', 'unknown')),
            'attitude': response.get('new_attitude', response.get('attitude', 'unknown'))
        }
        
        return jsonify({'success': True, 'result': result})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/test_multiple', methods=['POST'])
def test_multiple():
    """Test multiple personalities at once"""
    try:
        data = request.json
        personalities = data['personalities']
        scenario = data['scenario']
        
        results = []
        for personality_name in personalities:
            # Build the message
            message = f"""You have {scenario['hand']} in your hand.
Community Cards: {scenario['community'] if scenario['community'] else 'None yet'}
Pot Total: ${scenario['pot']}
Your cost to call: ${scenario['to_call']}
You must select from these options: {scenario['options']}
What is your move?"""
            
            # Create AI player and get response
            ai = AIPokerPlayer(name=personality_name, starting_money=10000)
            config = ai.personality_config
            traits = config.get('personality_traits', {})
            
            response = ai.get_player_response(message)
            
            results.append({
                'personality': personality_name,
                'traits': {
                    'play_style': config.get('play_style', 'unknown'),
                    'bluff_tendency': f"{traits.get('bluff_tendency', 0):.0%}",
                    'aggression': f"{traits.get('aggression', 0):.0%}",
                },
                'decision': response.get('action', 'unknown').upper(),
                'amount': response.get('adding_to_pot', 0),
                'says': response.get('persona_response', '...'),
                'physical': response.get('physical', []),
                'thinking': response.get('inner_monologue', ''),
                'hand_strategy': response.get('hand_strategy', ''),
                'bluff_likelihood': response.get('bluff_likelihood', 0)
            })
        
        return jsonify({'success': True, 'results': results})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("Starting Personality Tester on http://localhost:5001")
    print("Available personalities:", get_available_personalities())
    app.run(debug=True, port=5001)