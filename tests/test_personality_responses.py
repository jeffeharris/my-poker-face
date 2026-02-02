#!/usr/bin/env python3
"""
Test to demonstrate how different AI personalities respond to the same poker scenario.
This test mocks the OpenAI API to show predictable personality-based responses.
"""

import unittest
from unittest.mock import Mock, patch
import json
from poker.poker_player import AIPokerPlayer
from tests.conftest import load_personality_from_json


class TestPersonalityResponses(unittest.TestCase):
    """Test how different production personalities respond to identical game situations."""

    def setUp(self):
        # Patch personality loading to use JSON file directly (no DB/LLM needed)
        patcher = patch.object(
            AIPokerPlayer, '_load_personality_config',
            lambda self: load_personality_from_json(self.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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
            "dramatic_sequence": responses['physical'] + [responses['verbal']],
        }
    
    def _get_personality_responses(self, name, action):
        """Get personality-specific verbal and physical responses."""
        responses = {
            "Ebenezer Scrooge": {
                "fold": {
                    "verbal": "Bah! I shan't waste another penny on this hand. Humbug!",
                    "physical": ["*clutches chips protectively*", "*scowls at the table*"],
                    "thought": "Every chip saved is a chip earned.",
                    "strategy": "Folding to preserve my precious bankroll.",
                    "confidence": "miserly",
                    "attitude": "dismissive"
                },
                "check": {
                    "verbal": "I'll check. No sense throwing good money after bad.",
                    "physical": ["*taps table reluctantly*", "*eyes chips nervously*"],
                    "thought": "Not a single chip more than necessary.",
                    "strategy": "Checking to avoid any unnecessary expenditure.",
                    "confidence": "guarded",
                    "attitude": "stingy"
                },
                "call": {
                    "verbal": "Fine, I'll call... but this had better be worth every penny.",
                    "physical": ["*painfully pushes chips forward*", "*winces*"],
                    "thought": "This is practically highway robbery.",
                    "strategy": "Calling under protest.",
                    "confidence": "reluctant",
                    "attitude": "begrudging"
                }
            },
            "Blackbeard": {
                "raise": {
                    "verbal": "RAISE, ye scurvy dogs! I'll plunder every last chip from ye!",
                    "physical": ["*slams fist on table*", "*grins menacingly*"],
                    "thought": "These landlubbers don't stand a chance against a pirate king!",
                    "strategy": "Aggressive raise to intimidate and plunder.",
                    "confidence": "fearsome",
                    "attitude": "ruthless"
                },
                "call": {
                    "verbal": "Aye, I'll match yer bet. But mark me words, the treasure will be mine!",
                    "physical": ["*strokes beard*", "*narrows eyes*"],
                    "thought": "Let them think they have the upper hand.",
                    "strategy": "Calling to set up a devastating attack.",
                    "confidence": "cunning",
                    "attitude": "threatening"
                },
                "check": {
                    "verbal": "I'll bide me time... for now. The storm is coming.",
                    "physical": ["*drums fingers on table*", "*surveys opponents*"],
                    "thought": "Patience before the plunder.",
                    "strategy": "Checking to lull them into false security.",
                    "confidence": "calculating",
                    "attitude": "ominous"
                }
            },
            "Queen of Hearts": {
                "raise": {
                    "verbal": "RAISE! And if anyone dares challenge me — OFF WITH THEIR HEADS!",
                    "physical": ["*stands up imperiously*", "*points at opponents*"],
                    "thought": "I am the queen! No one defies me at this table!",
                    "strategy": "Dominate with a royal raise. Crush all opposition!",
                    "confidence": "absolute",
                    "attitude": "tyrannical"
                },
                "call": {
                    "verbal": "I shall call. But do NOT test my patience!",
                    "physical": ["*adjusts crown*", "*glares regally*"],
                    "thought": "They should be grateful I'm even playing with commoners.",
                    "strategy": "Calling to maintain royal presence at the table.",
                    "confidence": "imperious",
                    "attitude": "haughty"
                },
                "check": {
                    "verbal": "The queen checks. Do not mistake mercy for weakness!",
                    "physical": ["*waves hand dismissively*", "*sniffs disdainfully*"],
                    "thought": "Let the peasants think they have a chance.",
                    "strategy": "Checking to trap these insolent fools.",
                    "confidence": "regal",
                    "attitude": "condescending"
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
        
        for player_name in ["Ebenezer Scrooge", "Blackbeard", "Queen of Hearts", "Bob Ross"]:
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
            beats = response.get('dramatic_sequence', [])
            speech = [b for b in beats if not b.startswith('*')]
            actions = [b for b in beats if b.startswith('*')]
            print(f"  Says: \"{'; '.join(speech)}\"")
            print(f"  Actions: {', '.join(actions)}")
            print(f"  Strategy: {response['hand_strategy']}")

        # Verify personality-based decisions
        self.assertEqual(results["Ebenezer Scrooge"]["action"], "fold")  # Low aggression (0.2) = fold
        self.assertIn(results["Blackbeard"]["action"], ["raise", "call"])  # High aggression (0.9)
        self.assertEqual(results["Queen of Hearts"]["action"], "raise")  # Highest aggression (0.95)
        self.assertEqual(results["Bob Ross"]["action"], "fold")  # Lowest aggression (0.1)
        
    @patch('poker.poker_player.Assistant')
    def test_no_bet_scenario(self, mock_assistant):
        """Test responses when no one has bet yet (checking scenario)."""
        print("\n" + "="*80)
        print("SCENARIO: All players have A♥A♦, no bets yet (can check or raise)")
        print("="*80)
        
        for player_name in ["Ebenezer Scrooge", "Blackbeard", "Queen of Hearts", "Bob Ross"]:
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
            beats = response.get('dramatic_sequence', [])
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