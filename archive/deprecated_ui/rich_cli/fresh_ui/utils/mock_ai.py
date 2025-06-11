"""Mock AI controller for testing without OpenAI API"""

import random
from typing import Dict, List


class MockAIController:
    """Mock AI controller that makes random decisions"""
    
    def __init__(self, player_name: str, state_machine=None, ai_temp=0.9):
        self.player_name = player_name
        self.state_machine = state_machine
        self.ai_temp = ai_temp
        self.personality_responses = {
            "Gordon Ramsay": [
                "This hand is PERFECT!",
                "Bloody hell, what a terrible flop!",
                "You donkey! I'm raising!",
                "This pot is RAW!"
            ],
            "Bob Ross": [
                "What a happy little hand we have here",
                "Let's add a friendly bet to the pot",
                "No mistakes, only happy accidents",
                "Every card needs a friend"
            ],
            "Donald Trump": [
                "I have the best cards, believe me",
                "Nobody plays poker like me",
                "This is a tremendous hand",
                "I'm going to make poker great again"
            ],
            "Eeyore": [
                "I suppose I'll call...",
                "Oh bother, another bad hand",
                "It's not much of a hand anyway",
                "I knew I'd lose eventually"
            ]
        }
    
    def decide_action(self, game_messages: List[Dict[str, str]]) -> Dict:
        """Make a random but somewhat sensible decision"""
        # Get game state
        game_state = self.state_machine.game_state
        current_player_idx = game_state.current_player_idx
        
        if current_player_idx is None:
            return {"action": "fold", "adding_to_pot": 0, "persona_response": "Error", "physical": ""}
        
        current_player = game_state.players[current_player_idx]
        
        # Simple decision logic
        actions = []
        
        # Can always fold
        if not current_player.is_folded:
            actions.append(("fold", 0))
        
        # Check if can check
        if current_player.bet == game_state.highest_bet:
            actions.append(("check", 0))
        else:
            # Can call
            call_amount = min(game_state.highest_bet - current_player.bet, current_player.stack)
            if call_amount > 0:
                actions.append(("call", call_amount))
        
        # Can raise (simplified)
        if current_player.stack > 0:
            # Random small raise
            raise_amount = min(100, current_player.stack)
            actions.append(("raise", raise_amount))
            
            # Sometimes go all in
            if random.random() < 0.1:
                actions.append(("all_in", current_player.stack))
        
        # Make random choice
        action, amount = random.choice(actions) if actions else ("fold", 0)
        
        # Get personality response
        responses = self.personality_responses.get(
            self.player_name,
            ["I'll play this hand"]
        )
        response = random.choice(responses)
        
        # Add action-specific flavor
        if action == "fold":
            response += " *folds sadly*"
        elif action == "raise":
            response += " *pushes chips forward confidently*"
        elif action == "all_in":
            response += " ALL IN!"
        
        return {
            "action": action,
            "adding_to_pot": amount,
            "persona_response": response,
            "physical": f"*{action}s*"
        }