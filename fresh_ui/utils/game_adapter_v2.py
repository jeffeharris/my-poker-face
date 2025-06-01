"""Improved adapter to interface with the poker engine"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, replace
import logging

from poker import (
    PokerGameState, Player, initialize_game_state,
    PokerStateMachine, PokerPhase,
    get_celebrities
)
from poker.poker_game import setup_hand, deal_community_cards
from poker.poker_action import PokerAction, PlayerAction

# Import mock AI for testing
from fresh_ui.utils.mock_ai import MockAIController

logger = logging.getLogger(__name__)


@dataclass 
class GameAdapterV2:
    """Improved adapter with better poker engine integration"""
    
    game_state: PokerGameState
    state_machine: PokerStateMachine
    ai_controllers: Dict[str, MockAIController]
    game_messages: List[Dict[str, str]]
    
    @classmethod
    def create_new_game(cls, player_name: str, ai_names: List[str],
                       starting_stack: int = 10000, ante: int = 50,
                       use_mock_ai: bool = True) -> 'GameAdapterV2':
        """Create a new game with specified players"""
        logger.info(f"Creating new game with player: {player_name}, AIs: {ai_names}")
        
        # Initialize game state - this adds "Jeff" as human player
        game_state = initialize_game_state(ai_names)
        logger.debug(f"Initial players: {[p.name for p in game_state.players]}")
        
        # Create state machine with initial state
        state_machine = PokerStateMachine(game_state)
        state_machine.phase = PokerPhase.INITIALIZING_HAND
        
        # Setup the hand (deal cards, set positions)
        game_state = setup_hand(game_state)
        state_machine.game_state = game_state
        logger.debug(f"Hand setup complete. Players have cards: {all(len(p.hand) == 2 for p in game_state.players)}")
        
        # Move to pre-flop
        state_machine.phase = PokerPhase.PRE_FLOP
        
        # Create AI controllers
        ai_controllers = {}
        for ai_name in ai_names:
            if use_mock_ai:
                ai_controllers[ai_name] = MockAIController(
                    player_name=ai_name,
                    state_machine=state_machine
                )
            else:
                # Would use real AI controller here
                from poker.controllers import AIPlayerController
                ai_controllers[ai_name] = AIPlayerController(
                    player_name=ai_name,
                    state_machine=state_machine
                )
        
        return cls(
            game_state=game_state,
            state_machine=state_machine,
            ai_controllers=ai_controllers,
            game_messages=[]
        )
    
    def get_current_player(self) -> Optional[Player]:
        """Get the current player"""
        idx = self.game_state.current_player_idx
        if idx is not None and 0 <= idx < len(self.game_state.players):
            return self.game_state.players[idx]
        return None
    
    def get_human_player(self) -> Optional[Player]:
        """Get the human player"""
        for player in self.game_state.players:
            if player.is_human:
                return player
        return None
    
    def process_player_action(self, player_name: str, action: str, amount: int = 0) -> Tuple[bool, Optional[str]]:
        """Process a player action and update game state"""
        logger.debug(f"Processing action: {player_name} {action} {amount}")
        
        current_player = self.get_current_player()
        if not current_player or current_player.name != player_name:
            return False, "Not this player's turn"
        
        # Update game state based on action
        # We bypass PokerAction since we're managing state directly
        if action == "fold":
            self.game_state = self._fold_player(current_player)
        elif action == "check":
            if current_player.bet < self.game_state.highest_bet:
                return False, "Cannot check - must call or fold"
            self.game_state = self._advance_to_next_player()
        elif action == "call":
            self.game_state = self._call_bet(current_player)
        elif action == "raise":
            self.game_state = self._raise_bet(current_player, amount)
        elif action == "all-in":
            self.game_state = self._all_in(current_player)
        else:
            return False, f"Unknown action: {action}"
        
        # Check if betting round is complete
        if self._is_betting_round_complete():
            self.game_state = self._end_betting_round()
        
        # Update state machine
        self.state_machine.game_state = self.game_state
        
        return True, None
    
    def _fold_player(self, player: Player) -> PokerGameState:
        """Fold a player"""
        players = list(self.game_state.players)
        player_idx = players.index(player)
        players[player_idx] = Player(
            name=player.name,
            stack=player.stack,
            is_human=player.is_human,
            bet=player.bet,
            hand=player.hand,
            is_folded=True,
            is_all_in=player.is_all_in,
            has_acted=True
        )
        
        return replace(self.game_state,
            players=tuple(players),
            current_player_idx=self._get_next_active_player_index()
        )
    
    def _call_bet(self, player: Player) -> PokerGameState:
        """Call the current bet"""
        call_amount = min(self.game_state.highest_bet - player.bet, player.stack)
        
        players = list(self.game_state.players)
        player_idx = players.index(player)
        
        new_stack = player.stack - call_amount
        new_bet = player.bet + call_amount
        is_all_in = new_stack == 0
        
        players[player_idx] = Player(
            name=player.name,
            stack=new_stack,
            is_human=player.is_human,
            bet=new_bet,
            hand=player.hand,
            is_folded=player.is_folded,
            is_all_in=is_all_in,
            has_acted=True
        )
        
        new_pot = {'total': self.game_state.pot['total'] + call_amount}
        
        return replace(self.game_state,
            players=tuple(players),
            pot=new_pot,
            current_player_idx=self._get_next_active_player_index()
        )
    
    def _raise_bet(self, player: Player, raise_to: int) -> PokerGameState:
        """Raise the bet"""
        raise_amount = raise_to - player.bet
        
        if raise_amount > player.stack:
            # Convert to all-in
            return self._all_in(player)
        
        players = list(self.game_state.players)
        player_idx = players.index(player)
        
        players[player_idx] = Player(
            name=player.name,
            stack=player.stack - raise_amount,
            is_human=player.is_human,
            bet=raise_to,
            hand=player.hand,
            is_folded=player.is_folded,
            is_all_in=False,
            has_acted=True
        )
        
        # Reset other players' has_acted flags
        for i, p in enumerate(players):
            if i != player_idx and not p.is_folded:
                players[i] = replace(p, has_acted=False)
        
        new_pot = {'total': self.game_state.pot['total'] + raise_amount}
        
        return replace(self.game_state,
            players=tuple(players),
            pot=new_pot,
            current_player_idx=self._get_next_active_player_index()
        )
    
    def _all_in(self, player: Player) -> PokerGameState:
        """Go all in"""
        all_in_amount = player.stack
        new_bet = player.bet + all_in_amount
        
        players = list(self.game_state.players)
        player_idx = players.index(player)
        
        players[player_idx] = Player(
            name=player.name,
            stack=0,
            is_human=player.is_human,
            bet=new_bet,
            hand=player.hand,
            is_folded=player.is_folded,
            is_all_in=True,
            has_acted=True
        )
        
        # Update current bet if this is higher
        if new_bet > self.game_state.highest_bet:
            # Reset other players' has_acted flags
            for i, p in enumerate(players):
                if i != player_idx and not p.is_folded and not p.is_all_in:
                    players[i] = replace(p, has_acted=False)
        
        new_pot = {'total': self.game_state.pot['total'] + all_in_amount}
        
        return replace(self.game_state,
            players=tuple(players),
            pot=new_pot,
            current_player_idx=self._get_next_active_player_index()
        )
    
    def _advance_to_next_player(self) -> PokerGameState:
        """Move to next player"""
        players = list(self.game_state.players)
        current_idx = self.game_state.current_player_idx
        
        # Mark current player as acted
        if current_idx is not None:
            players[current_idx] = replace(players[current_idx], has_acted=True)
        
        return replace(self.game_state,
            players=tuple(players),
            current_player_idx=self._get_next_active_player_index()
        )
    
    def _get_next_active_player_index(self) -> Optional[int]:
        """Get next active player who hasn't acted"""
        current_idx = self.game_state.current_player_idx
        if current_idx is None:
            current_idx = -1
        
        num_players = len(self.game_state.players)
        
        # Look for next player who can act
        for i in range(1, num_players + 1):
            idx = (current_idx + i) % num_players
            player = self.game_state.players[idx]
            
            if (not player.is_folded and 
                not player.is_all_in and 
                (not player.has_acted or player.bet < self.game_state.highest_bet)):
                return idx
        
        return None
    
    def _is_betting_round_complete(self) -> bool:
        """Check if betting round is complete"""
        active_players = [p for p in self.game_state.players 
                         if not p.is_folded and not p.is_all_in]
        
        if len(active_players) == 0:
            return True
        
        # All active players have acted and bets are equal
        for player in active_players:
            if not player.has_acted:
                return False
            if player.bet < self.game_state.highest_bet:
                return False
        
        return True
    
    def _end_betting_round(self) -> PokerGameState:
        """End the betting round and deal next cards"""
        # Reset has_acted flags
        players = list(self.game_state.players)
        for i, player in enumerate(players):
            if not player.is_folded:
                players[i] = replace(player, has_acted=False)
        
        # Deal community cards based on phase
        new_community_cards = list(self.game_state.community_cards)
        phase = self.state_machine.phase
        
        if phase == PokerPhase.PRE_FLOP:
            # Deal flop (3 cards)
            new_cards = self._deal_cards(3)
            new_community_cards.extend(new_cards)
            self.state_machine.phase = PokerPhase.FLOP
        elif phase == PokerPhase.FLOP:
            # Deal turn (1 card)
            new_cards = self._deal_cards(1)
            new_community_cards.extend(new_cards)
            self.state_machine.phase = PokerPhase.TURN
        elif phase == PokerPhase.TURN:
            # Deal river (1 card)
            new_cards = self._deal_cards(1)
            new_community_cards.extend(new_cards)
            self.state_machine.phase = PokerPhase.RIVER
        elif phase == PokerPhase.RIVER:
            # Move to showdown
            self.state_machine.phase = PokerPhase.SHOWDOWN
        
        # Find first active player
        first_active = None
        for i, player in enumerate(players):
            if not player.is_folded and not player.is_all_in:
                first_active = i
                break
        
        return replace(self.game_state,
            players=tuple(players),
            community_cards=tuple(new_community_cards),
            current_player_idx=first_active
        )
    
    def _deal_cards(self, num_cards: int) -> List[Dict]:
        """Deal cards from the deck"""
        # For now, create random cards
        # In real implementation, would track deck state
        import random
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        suits = ['Spades', 'Hearts', 'Diamonds', 'Clubs']
        
        cards = []
        for _ in range(num_cards):
            cards.append({
                'rank': random.choice(ranks),
                'suit': random.choice(suits)
            })
        
        return cards
    
    def get_available_actions(self) -> List[str]:
        """Get available actions for current player"""
        current_player = self.get_current_player()
        if not current_player or current_player.is_folded:
            return []
        
        actions = ['fold']
        
        if current_player.bet == self.game_state.highest_bet:
            actions.append('check')
        else:
            if current_player.stack > 0:
                actions.append('call')
        
        if current_player.stack > 0:
            actions.append('raise')
            actions.append('all-in')
        
        return actions
    
    def is_hand_complete(self) -> bool:
        """Check if hand is complete"""
        active_players = [p for p in self.game_state.players if not p.is_folded]
        
        if len(active_players) <= 1:
            return True
        
        if self.state_machine.phase == PokerPhase.SHOWDOWN:
            return True
        
        return False
    
    def get_winners(self) -> List[Tuple[Player, int]]:
        """Determine winners - simplified version"""
        active_players = [p for p in self.game_state.players if not p.is_folded]
        
        if len(active_players) == 1:
            # Last player standing wins
            return [(active_players[0], self.game_state.pot['total'])]
        
        # For now, random winner
        # Real implementation would evaluate hands
        import random
        winner = random.choice(active_players)
        return [(winner, self.game_state.pot['total'])]