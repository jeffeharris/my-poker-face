"""Adapter to interface with the poker engine"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging

from poker import (
    PokerGameState, Player, initialize_game_state,
    PokerStateMachine, PokerPhase,
    AIPlayerController,
    PokerAction,
    HandEvaluator,
    get_celebrities
)

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@dataclass
class GameAdapter:
    """Adapter to interface between Rich UI and poker engine"""
    
    game_state: PokerGameState
    state_machine: PokerStateMachine
    ai_controllers: Dict[str, AIPlayerController]
    
    @classmethod
    def create_new_game(cls, player_name: str, ai_names: List[str], 
                       starting_stack: int = 10000, ante: int = 50) -> 'GameAdapter':
        """Create a new game with specified players"""
        logger.info(f"Creating new game with player: {player_name}, AIs: {ai_names}")
        
        # Get celebrity personalities
        celebrities = get_celebrities()
        logger.debug(f"Available celebrities: {list(celebrities.keys())}")
        
        # Create players list
        players = [Player(name=player_name, stack=starting_stack, is_human=True)]
        
        # Add AI players
        for ai_name in ai_names:
            players.append(Player(name=ai_name, stack=starting_stack, is_human=False))
        
        # Initialize game state - it only takes AI player names
        logger.info("Initializing game state...")
        game_state = initialize_game_state(ai_names)
        logger.debug(f"Game state initialized. Players: {[p.name for p in game_state.players]}")
        logger.debug(f"Current player index: {game_state.current_player_index}")
        
        # Create state machine
        state_machine = PokerStateMachine()
        logger.debug(f"State machine created. Initial phase: {state_machine.phase}")
        
        # Initialize the state machine with the game state
        state_machine.game_state = game_state
        
        # Create AI controllers
        ai_controllers = {}
        for ai_name in ai_names:
            ai_controllers[ai_name] = AIPlayerController(
                player_name=ai_name,
                state_machine=state_machine,
                ai_temp=0.9
            )
            logger.debug(f"Created AI controller for {ai_name}")
        
        return cls(
            game_state=game_state,
            state_machine=state_machine,
            ai_controllers=ai_controllers
        )
    
    def get_current_player(self) -> Optional[Player]:
        """Get the current player"""
        if self.game_state.current_player_index is not None:
            return self.game_state.players[self.game_state.current_player_index]
        return None
    
    def get_human_player(self) -> Optional[Player]:
        """Get the human player"""
        for player in self.game_state.players:
            if player.is_human:
                return player
        return None
    
    def get_available_actions(self) -> List[str]:
        """Get available actions for current player"""
        current_player = self.get_current_player()
        if not current_player:
            return []
        
        actions = []
        
        # Always can fold (unless already folded)
        if not current_player.is_folded:
            actions.append('fold')
        
        # Check if can check
        if current_player.bet == self.game_state.current_bet:
            actions.append('check')
        else:
            # Can call if have chips
            if current_player.stack > 0:
                call_amount = min(self.game_state.current_bet - current_player.bet, 
                                current_player.stack)
                if call_amount > 0:
                    actions.append('call')
        
        # Can raise if have enough chips
        min_raise = self.game_state.current_bet * 2
        if current_player.stack + current_player.bet >= min_raise:
            actions.append('raise')
        
        # Can go all-in if have chips
        if current_player.stack > 0:
            actions.append('all_in')
        
        return actions
    
    def process_action(self, action: str, amount: int = 0) -> Tuple[PokerGameState, Optional[str]]:
        """Process a player action"""
        current_player = self.get_current_player()
        if not current_player:
            return self.game_state, "No current player"
        
        # Create poker action
        if action == 'fold':
            poker_action = PokerAction.fold()
        elif action == 'check':
            poker_action = PokerAction.check_action()
        elif action == 'call':
            poker_action = PokerAction.call()
        elif action == 'raise':
            poker_action = PokerAction.raise_to(amount + current_player.bet)
        elif action == 'all_in':
            poker_action = PokerAction.all_in()
        else:
            return self.game_state, f"Unknown action: {action}"
        
        # Process through state machine
        self.game_state, error = self.state_machine.process_action(
            self.game_state, poker_action
        )
        
        return self.game_state, error
    
    def get_ai_decision(self, player_name: str) -> Tuple[str, int, Optional[str]]:
        """Get AI player's decision"""
        controller = self.ai_controllers.get(player_name)
        if not controller:
            return 'fold', 0, None
        
        # Get the AI's action - it expects game_messages
        game_messages = []  # Empty for now, could add chat history later
        action_dict = controller.decide_action(game_messages)
        
        # Convert to our format
        action = action_dict.get('action', 'fold')
        amount = action_dict.get('adding_to_pot', 0)
        message = action_dict.get('persona_response', '')
        
        return action, amount, message
    
    def is_hand_complete(self) -> bool:
        """Check if the current hand is complete"""
        # Hand is complete if we're in showdown phase or only one active player
        active_players = [p for p in self.game_state.players if not p.is_folded]
        return (self.game_state.phase == PokerPhase.SHOWDOWN or 
                len(active_players) <= 1)
    
    def get_winners(self) -> List[Tuple[Player, int]]:
        """Get winners and their winnings"""
        # This would need to be implemented based on the poker engine
        # For now, return a simple version
        active_players = [p for p in self.game_state.players if not p.is_folded]
        if len(active_players) == 1:
            return [(active_players[0], self.game_state.pot)]
        
        # TODO: Implement proper hand evaluation
        return [(active_players[0], self.game_state.pot)]
    
    def start_new_hand(self) -> PokerGameState:
        """Start a new hand"""
        # Get AI player names
        ai_names = [p.name for p in self.game_state.players if not p.is_human]
        
        # Reset for new hand
        self.game_state = initialize_game_state(ai_names)
        return self.game_state