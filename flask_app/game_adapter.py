"""
Adapter layer to handle differences between the Flask app's expectations
and the actual poker module implementation.
"""
from typing import List, Optional
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.poker_game import PokerGameState


class GameStateAdapter:
    """Wraps PokerGameState to provide expected properties."""
    
    def __init__(self, game_state: PokerGameState):
        self._game_state = game_state
    
    def __getattr__(self, name):
        # Pass through to the underlying game state
        return getattr(self._game_state, name)
    
    @property
    def current_player_options(self) -> List[str]:
        """Get available actions for the current player."""
        options = []
        
        if self._game_state.awaiting_action:
            current_player = self._game_state.current_player
            
            # Always can fold unless already folded
            if not current_player.is_folded:
                options.append('fold')
            
            # Check if can check (no one has bet or we've matched the bet)
            if self._game_state.highest_bet == current_player.bet:
                options.append('check')
            else:
                # Can call if not all-in
                if current_player.stack > 0:
                    options.append('call')
            
            # Can raise if have chips and haven't reached bet limit
            if current_player.stack > 0:
                options.append('raise')
        
        return options
    
    @property 
    def no_action_taken(self) -> bool:
        """Check if this is a new betting round."""
        # This is a simplification - in reality we'd track this better
        return not self._game_state.pre_flop_action_taken
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        base_dict = self._game_state.to_dict()
        base_dict['current_player_options'] = self.current_player_options
        base_dict['no_action_taken'] = self.no_action_taken
        return base_dict


class StateMachineAdapter:
    """Wraps PokerStateMachine to provide expected methods."""
    
    def __init__(self, state_machine: PokerStateMachine):
        self._state_machine = state_machine
        self._game_state_adapter = GameStateAdapter(state_machine.game_state)
    
    @property
    def game_state(self) -> GameStateAdapter:
        """Get the adapted game state."""
        self._game_state_adapter._game_state = self._state_machine.game_state
        return self._game_state_adapter
    
    @game_state.setter
    def game_state(self, value):
        """Set the game state."""
        if isinstance(value, GameStateAdapter):
            self._state_machine = self._state_machine.with_game_state(value._game_state)
        else:
            self._state_machine = self._state_machine.with_game_state(value)
            self._game_state_adapter._game_state = value
    
    @property
    def current_phase(self):
        return self._state_machine.current_phase
    
    @current_phase.setter 
    def current_phase(self, value):
        self._state_machine = self._state_machine.with_phase(value)
    
    def run_until(self, phases: List[PokerPhase]):
        """Run the state machine until one of the given phases."""
        while self._state_machine.current_phase not in phases:
            self._state_machine = self._state_machine.advance()
            
            # Break if waiting for player action
            if self._state_machine.game_state.awaiting_action:
                break
    
    def run_until_player_action(self):
        """Run until player action is needed."""
        self._state_machine = self._state_machine.run_until_player_action()
    
    def update_phase(self):
        """Update to the next phase - handled automatically by advance()."""
        # The immutable state machine manages phase transitions internally
        # Just advance the state
        self._state_machine = self._state_machine.advance()
    
    def advance_state(self):
        """Advance the state machine."""
        self._state_machine = self._state_machine.advance()
    
    def __getattr__(self, name):
        # Pass through to the underlying state machine
        return getattr(self._state_machine, name)