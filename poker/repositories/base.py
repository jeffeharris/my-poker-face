"""
Base repository interfaces and domain models for the poker game.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any, Protocol
from poker.poker_state_machine import PokerStateMachine


@dataclass
class Game:
    """Domain model representing a complete game."""
    id: str
    state_machine: PokerStateMachine
    created_at: datetime
    updated_at: datetime
    
    @property
    def phase(self) -> str:
        """Get current game phase."""
        return self.state_machine.phase.name
    
    @property
    def num_players(self) -> int:
        """Get number of players."""
        return len(self.state_machine.game_state.players)
    
    @property
    def pot_size(self) -> float:
        """Get current pot size."""
        return self.state_machine.game_state.pot.get('total', 0)


@dataclass
class GameMessage:
    """Domain model for game messages/chat."""
    id: Optional[int]
    game_id: str
    sender: str
    message: str
    message_type: str
    timestamp: datetime


@dataclass
class AIPlayerState:
    """Domain model for AI player state."""
    game_id: str
    player_name: str
    conversation_history: List[Dict[str, str]]
    personality_state: Dict[str, Any]
    last_updated: datetime


class GameRepository(Protocol):
    """Repository interface for game persistence."""
    
    def save(self, game: Game) -> None:
        """Save or update a game."""
        ...
    
    def find_by_id(self, game_id: str) -> Optional[Game]:
        """Find a game by ID."""
        ...
    
    def find_recent(self, limit: int = 10) -> List[Game]:
        """Find recent games."""
        ...
    
    def delete(self, game_id: str) -> None:
        """Delete a game and all related data."""
        ...
    
    def exists(self, game_id: str) -> bool:
        """Check if a game exists."""
        ...


class MessageRepository(Protocol):
    """Repository interface for game messages."""
    
    def save(self, message: GameMessage) -> GameMessage:
        """Save a message and return it with ID."""
        ...
    
    def find_by_game_id(self, game_id: str) -> List[GameMessage]:
        """Find all messages for a game."""
        ...
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all messages for a game."""
        ...


class AIStateRepository(Protocol):
    """Repository interface for AI player states."""
    
    def save(self, ai_state: AIPlayerState) -> None:
        """Save or update AI player state."""
        ...
    
    def find_by_game_and_player(self, game_id: str, player_name: str) -> Optional[AIPlayerState]:
        """Find AI state for a specific player in a game."""
        ...
    
    def find_by_game_id(self, game_id: str) -> List[AIPlayerState]:
        """Find all AI states for a game."""
        ...
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all AI states for a game."""
        ...