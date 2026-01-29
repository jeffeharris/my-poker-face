#!/usr/bin/env python3
"""
Test to demonstrate how different AI personalities respond to the same poker scenario.
This test mocks the OpenAI API to show predictable personality-based responses.
"""

import unittest
from unittest.mock import Mock, patch
import json
from poker.poker_player import AIPokerPlayer


class TestPersonalityResponses(unittest.TestCase):
    """Test how different personalities respond to identical game situations."""
    
    def create_mock_response(self, personality_name, scenario):
        """Create a mock AI response based on personality traits."""
        # Load actual personality config
        ai_player = AIPokerPlayer(
            name=personality_name,
            starting_money=10000
        )
        
        traits = ai_player.personality_config['personality_traits']
        
        # Decision logic based on personality traits
        if scenario == "facing_bet":
            # Facing a bet with medium hand (pocket 7s vs high board)
            if traits['aggression'] > 0.8:
                action = "raise"
                amount = 2000
            elif traits['aggression'] < 0.3:
                action = "fold"
                amount = 0
            elif traits['bluff_tendency'] > 0.6:
                action = "raise"
                amount = 1000
            else:
                action = "call"
                amount = 100
        else:  # no_bet scenario
            if traits['aggression'] > 0.7:
                action = "raise"
                amount = 1500
            else:
                action = "check"
                amount = 0
                
        # Generate personality-appropriate responses
        responses = self._get_personality_responses(personality_name, action)
        
        return {
            "action": action,
            "raise_to": amount,
            "inner_monologue": responses['thought'],
            "bluff_likelihood": int(traits['bluff_tendency'] * 100),
            "hand_strategy": responses['strategy'],
            "stage_direction": responses['physical'] + [responses['verbal']],
        }
    
    def _get_personality_responses(self, name, action):
        """Get personality-specific verbal and physical responses."""
        responses = {
            "Eeyore": {
                "raise": {
                    "verbal": "I suppose I'll raise... though it probably won't help.",
                    "physical": ["*sighs heavily*", "*looks down sadly*"],
                    "thought": "Why bother? I'll probably lose anyway.",
                    "strategy": "Might as well lose faster with a raise.",
                    "confidence": "pessimistic",
                    "attitude": "gloomy"
                },
                "check": {
                    "verbal": "Oh bother, I'll just check. No point in losing more.",
                    "physical": ["*slumps in chair*", "*stares at cards glumly*"],
                    "thought": "Another terrible hand, as expected.",
                    "strategy": "Checking to minimize losses, as usual.",
                    "confidence": "abysmal",
                    "attitude": "dejected"
                },
                "fold": {
                    "verbal": "Of course I fold. Story of my life. *sighs*",
                    "physical": ["*pushes cards away slowly*", "*sighs deeply*"],
                    "thought": "I knew this would happen.",
                    "strategy": "Folding to avoid more disappointment.",
                    "confidence": "hopeless",
                    "attitude": "melancholy"
                },
                "call": {
                    "verbal": "I'll call, but it won't end well...",
                    "physical": ["*reluctantly pushes chips*", "*shakes head*"],
                    "thought": "Here goes nothing... or rather, here goes everything.",
                    "strategy": "Calling despite expecting to lose.",
                    "confidence": "doubtful",
                    "attitude": "resigned"
                }
            },
            "Donald Trump": {
                "raise": {
                    "verbal": "I'm raising BIGLY! Nobody raises like me, believe me! This pot is gonna be YUGE!",
                    "physical": ["*makes expansive hand gestures*", "*leans forward dominantly*"],
                    "thought": "These losers don't stand a chance against me!",
                    "strategy": "Dominate with huge raises. Show tremendous strength!",
                    "confidence": "supreme",
                    "attitude": "domineering"
                },
                "call": {
                    "verbal": "I'll call, but only because this pot is already TREMENDOUS!",
                    "physical": ["*adjusts tie*", "*points at opponents*"],
                    "thought": "I'm the best poker player ever.",
                    "strategy": "Call to stay in control of this tremendous pot.",
                    "confidence": "unshakeable",
                    "attitude": "boastful"
                },
                "check": {
                    "verbal": "I'm checking, but I have the BEST cards, believe me!",
                    "physical": ["*waves hand dismissively*", "*smirks*"],
                    "thought": "Let them think they have a chance.",
                    "strategy": "Check to trap these losers.",
                    "confidence": "overconfident",
                    "attitude": "condescending"
                }
            },
            "Gordon Ramsay": {
                "raise": {
                    "verbal": "RAISE! This pot is RAW and I'm cooking you donkeys! Bloody hell!",
                    "physical": ["*slams table*", "*points aggressively*"],
                    "thought": "These amateurs don't know what hit them!",
                    "strategy": "Aggressive raise to put pressure on these muppets.",
                    "confidence": "intense",
                    "attitude": "confrontational"
                },
                "call": {
                    "verbal": "Fine, I'll call, but this hand better be worth it, you muppets!",
                    "physical": ["*shakes head in disgust*", "*glares intensely*"],
                    "thought": "This better not be a waste of my bloody time.",
                    "strategy": "Calling to see if these idiots are bluffing.",
                    "confidence": "irritated",
                    "attitude": "critical"
                },
                "fold": {
                    "verbal": "This hand is GARBAGE! I'm out! Bloody waste of time!",
                    "physical": ["*throws cards down*", "*crosses arms*"],
                    "thought": "Not worth my time with these donkeys.",
                    "strategy": "Fold this rubbish hand immediately.",
                    "confidence": "disgusted",
                    "attitude": "dismissive"
                }
            },
            "Bob Ross": {
                "check": {
                    "verbal": "Let's just check and see what happy little cards come next. No pressure.",
                    "physical": ["*smiles warmly*", "*nods gently*"],
                    "thought": "No mistakes in poker, only happy accidents.",
                    "strategy": "Checking peacefully, no need to rush.",
                    "confidence": "serene",
                    "attitude": "peaceful"
                },
                "fold": {
                    "verbal": "I'll fold this one. Sometimes you need to let go to find joy.",
                    "physical": ["*gently places cards down*", "*smiles serenely*"],
                    "thought": "Every fold is a chance for a new beginning.",
                    "strategy": "Folding gracefully to wait for better opportunities.",
                    "confidence": "content",
                    "attitude": "accepting"
                },
                "raise": {
                    "verbal": "Let's add a happy little raise here. Just a touch of aggression.",
                    "physical": ["*pushes chips forward gently*", "*chuckles softly*"],
                    "thought": "Sometimes you need to take chances to create something beautiful.",
                    "strategy": "A gentle raise to see where this leads.",
                    "confidence": "optimistic",
                    "attitude": "encouraging"
                }
            }
        }
        
        # Default response if specific action not found
        default = {
            "verbal": f"{name} makes a move.",
            "physical": ["*plays cards*"],
            "thought": "Playing poker.",
            "strategy": "Standard play.",
            "confidence": "neutral",
            "attitude": "focused"
        }
        
        return responses.get(name, {}).get(action, default)
    
    @patch('poker.poker_player.Assistant')
    def test_same_scenario_different_responses(self, mock_assistant):
        """Test how each personality responds to facing a bet with pocket 7s."""
        print("\n" + "="*80)
        print("SCENARIO: All players have 7♥7♦, flop is K♠Q♠J♣, facing $100 bet")
        print("="*80)
        
        results = {}
        
        for player_name in ["Eeyore", "Donald Trump", "Gordon Ramsay", "Bob Ross"]:
            # Create AI player
            ai_player = AIPokerPlayer(
                name=player_name,
                starting_money=10000
            )
            
            # Mock the response based on personality
            mock_response = self.create_mock_response(player_name, "facing_bet")
            mock_assistant.return_value.chat.return_value = json.dumps(mock_response)
            
            # Get response
            ai_player.assistant = mock_assistant.return_value
            response = ai_player.get_player_response("Test message")
            
            results[player_name] = response
            
            # Print results
            print(f"\n{player_name}:")
            print(f"  Personality Traits:")
            print(f"    - Bluff Tendency: {ai_player.personality_config['personality_traits']['bluff_tendency']:.0%}")
            print(f"    - Aggression: {ai_player.personality_config['personality_traits']['aggression']:.0%}")
            print(f"  Decision: {response['action'].upper()}" +
                  (f" ${response.get('raise_to', 0)}" if response.get('raise_to', 0) > 0 else ""))
            beats = response.get('stage_direction', [])
            speech = [b for b in beats if not b.startswith('*')]
            actions = [b for b in beats if b.startswith('*')]
            print(f"  Says: \"{'; '.join(speech)}\"")
            print(f"  Actions: {', '.join(actions)}")
            print(f"  Strategy: {response['hand_strategy']}")
            
        # Verify personality-based decisions
        self.assertEqual(results["Eeyore"]["action"], "fold")  # Low aggression = fold
        self.assertIn(results["Donald Trump"]["action"], ["raise", "call"])  # High aggression
        self.assertEqual(results["Gordon Ramsay"]["action"], "raise")  # Highest aggression
        self.assertEqual(results["Bob Ross"]["action"], "fold")  # Lowest aggression
        
    @patch('poker.poker_player.Assistant')
    def test_no_bet_scenario(self, mock_assistant):
        """Test responses when no one has bet yet (checking scenario)."""
        print("\n" + "="*80)
        print("SCENARIO: All players have A♥A♦, no bets yet (can check or raise)")
        print("="*80)
        
        for player_name in ["Eeyore", "Donald Trump", "Gordon Ramsay", "Bob Ross"]:
            ai_player = AIPokerPlayer(
                name=player_name,
                starting_money=10000
            )
            
            mock_response = self.create_mock_response(player_name, "no_bet")
            mock_assistant.return_value.chat.return_value = json.dumps(mock_response)
            
            ai_player.assistant = mock_assistant.return_value
            response = ai_player.get_player_response("Test message")
            
            print(f"\n{player_name}:")
            print(f"  Action: {response['action'].upper()}" + 
                  (f" ${response.get('raise_to', 0)}" if response.get('raise_to', 0) > 0 else ""))
            beats = response.get('stage_direction', [])
            speech = [b for b in beats if not b.startswith('*')]
            print(f"  Says: \"{'; '.join(speech)}\"")

            # Verify decisions match personality
            if ai_player.personality_config['personality_traits']['aggression'] > 0.7:
                self.assertEqual(response['action'], "raise")
            else:
                self.assertEqual(response['action'], "check")


if __name__ == "__main__":
    # Run with verbose output to see the personality responses
    unittest.main(verbosity=2)