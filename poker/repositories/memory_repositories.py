"""
In-memory implementation of repositories for testing.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
import copy

from .base import Game, GameMessage, AIPlayerState, GameRepository, MessageRepository, AIStateRepository


class InMemoryGameRepository(GameRepository):
    """In-memory implementation of GameRepository for testing."""
    
    def __init__(self):
        self._games: Dict[str, Game] = {}
    
    def save(self, game: Game) -> None:
        """Save or update a game."""
        # Deep copy to ensure immutability
        self._games[game.id] = copy.deepcopy(game)
    
    def find_by_id(self, game_id: str) -> Optional[Game]:
        """Find a game by ID."""
        game = self._games.get(game_id)
        return copy.deepcopy(game) if game else None
    
    def find_recent(self, limit: int = 10) -> List[Game]:
        """Find recent games."""
        # Sort by updated_at descending
        sorted_games = sorted(
            self._games.values(),
            key=lambda g: g.updated_at,
            reverse=True
        )
        return [copy.deepcopy(g) for g in sorted_games[:limit]]
    
    def delete(self, game_id: str) -> None:
        """Delete a game."""
        self._games.pop(game_id, None)
    
    def exists(self, game_id: str) -> bool:
        """Check if a game exists."""
        return game_id in self._games


class InMemoryMessageRepository(MessageRepository):
    """In-memory implementation of MessageRepository for testing."""
    
    def __init__(self):
        self._messages: List[GameMessage] = []
        self._next_id = 1
    
    def save(self, message: GameMessage) -> GameMessage:
        """Save a message and return it with ID."""
        # Assign ID if not present
        if message.id is None:
            message.id = self._next_id
            self._next_id += 1
        
        self._messages.append(copy.deepcopy(message))
        return message
    
    def find_by_game_id(self, game_id: str) -> List[GameMessage]:
        """Find all messages for a game."""
        return [
            copy.deepcopy(msg) 
            for msg in self._messages 
            if msg.game_id == game_id
        ]
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all messages for a game."""
        self._messages = [
            msg for msg in self._messages 
            if msg.game_id != game_id
        ]


class InMemoryAIStateRepository(AIStateRepository):
    """In-memory implementation of AIStateRepository for testing."""
    
    def __init__(self):
        # Key: (game_id, player_name)
        self._states: Dict[tuple, AIPlayerState] = {}
    
    def save(self, ai_state: AIPlayerState) -> None:
        """Save or update AI player state."""
        key = (ai_state.game_id, ai_state.player_name)
        self._states[key] = copy.deepcopy(ai_state)
    
    def find_by_game_and_player(self, game_id: str, player_name: str) -> Optional[AIPlayerState]:
        """Find AI state for a specific player in a game."""
        key = (game_id, player_name)
        state = self._states.get(key)
        return copy.deepcopy(state) if state else None
    
    def find_by_game_id(self, game_id: str) -> List[AIPlayerState]:
        """Find all AI states for a game."""
        return [
            copy.deepcopy(state)
            for (gid, _), state in self._states.items()
            if gid == game_id
        ]
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all AI states for a game."""
        keys_to_delete = [
            key for key in self._states
            if key[0] == game_id
        ]
        for key in keys_to_delete:
            del self._states[key]