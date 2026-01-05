"""
Game service that uses repositories for persistence.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
import uuid

from poker.repositories.base import (
    Game, GameMessage, AIPlayerState,
    GameRepository, MessageRepository, AIStateRepository
)
from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine
from poker.controllers import AIPlayerController


class GameService:
    """Service for managing poker games using repositories."""
    
    def __init__(
        self,
        game_repository: GameRepository,
        message_repository: MessageRepository,
        ai_state_repository: AIStateRepository
    ):
        self.game_repo = game_repository
        self.message_repo = message_repository
        self.ai_state_repo = ai_state_repository
    
    def create_game(self, player_names: List[str]) -> Game:
        """Create a new game."""
        # Initialize game state
        game_state = initialize_game_state(player_names)
        state_machine = PokerStateMachine(game_state)
        
        # Create game domain object
        game = Game(
            id=self._generate_game_id(),
            state_machine=state_machine,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        # Save to repository
        self.game_repo.save(game)
        
        # Add initial message
        self.add_system_message(
            game.id,
            "New game started! Good luck!"
        )
        
        return game
    
    def get_game(self, game_id: str) -> Optional[Game]:
        """Get a game by ID."""
        return self.game_repo.find_by_id(game_id)
    
    def update_game(self, game: Game) -> None:
        """Update a game."""
        game.updated_at = datetime.now()
        self.game_repo.save(game)
    
    def delete_game(self, game_id: str) -> None:
        """Delete a game and all associated data."""
        # Delete in order to avoid foreign key issues
        self.ai_state_repo.delete_by_game_id(game_id)
        self.message_repo.delete_by_game_id(game_id)
        self.game_repo.delete(game_id)
    
    def get_recent_games(self, limit: int = 10) -> List[Game]:
        """Get recent games."""
        return self.game_repo.find_recent(limit)
    
    def add_message(
        self,
        game_id: str,
        sender: str,
        message: str,
        message_type: str = "player"
    ) -> GameMessage:
        """Add a message to a game."""
        msg = GameMessage(
            id=None,
            game_id=game_id,
            sender=sender,
            message=message,
            message_type=message_type,
            timestamp=datetime.now()
        )
        return self.message_repo.save(msg)
    
    def add_system_message(self, game_id: str, message: str) -> GameMessage:
        """Add a system message to a game."""
        return self.add_message(
            game_id=game_id,
            sender="System",
            message=message,
            message_type="system"
        )
    
    def get_game_messages(self, game_id: str) -> List[GameMessage]:
        """Get all messages for a game."""
        return self.message_repo.find_by_game_id(game_id)
    
    def save_ai_state(
        self,
        game_id: str,
        player_name: str,
        conversation_history: List[Dict[str, str]],
        personality_state: Dict[str, Any]
    ) -> None:
        """Save AI player state."""
        ai_state = AIPlayerState(
            game_id=game_id,
            player_name=player_name,
            conversation_history=conversation_history,
            personality_state=personality_state,
            last_updated=datetime.now()
        )
        self.ai_state_repo.save(ai_state)
    
    def get_ai_state(self, game_id: str, player_name: str) -> Optional[AIPlayerState]:
        """Get AI state for a player."""
        return self.ai_state_repo.find_by_game_and_player(game_id, player_name)
    
    def get_all_ai_states(self, game_id: str) -> List[AIPlayerState]:
        """Get all AI states for a game."""
        return self.ai_state_repo.find_by_game_id(game_id)
    
    def restore_ai_controllers(self, game: Game) -> Dict[str, AIPlayerController]:
        """Restore AI controllers with saved state."""
        ai_controllers = {}
        ai_states = self.get_all_ai_states(game.id)
        
        # Create a mapping of player names to their saved states
        saved_states = {state.player_name: state for state in ai_states}
        
        # Create controllers for all AI players
        for player in game.state_machine.game_state.players:
            if not player.is_human:
                controller = AIPlayerController(
                    player_name=player.name,
                    state_machine=game.state_machine,
                    game_id=game.id
                )
                
                # Restore saved state if available
                if player.name in saved_states:
                    saved_state = saved_states[player.name]
                    # TODO: Restore conversation history and personality state
                    # This would require updating AIPlayerController to accept these
                
                ai_controllers[player.name] = controller
        
        return ai_controllers
    
    def _generate_game_id(self) -> str:
        """Generate a unique game ID."""
        return str(uuid.uuid4())[:8]