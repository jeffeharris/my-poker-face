"""
SQLite implementation of repository interfaces.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from .base import Game, GameMessage, AIPlayerState, GameRepository, MessageRepository, AIStateRepository
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.poker_game import PokerGameState, Player, create_deck
from poker.persistence import GamePersistence  # Reuse serialization logic


class SQLiteGameRepository(GameRepository):
    """SQLite implementation of GameRepository."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._persistence = GamePersistence(db_path)  # Reuse existing logic
        
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def save(self, game: Game) -> None:
        """Save or update a game."""
        # Delegate to existing persistence for now
        self._persistence.save_game(game.id, game.state_machine)
        
        # Update timestamps
        with self._get_connection() as conn:
            if self.exists(game.id):
                conn.execute(
                    "UPDATE games SET updated_at = ? WHERE game_id = ?",
                    (game.updated_at, game.id)
                )
            else:
                conn.execute(
                    "UPDATE games SET created_at = ?, updated_at = ? WHERE game_id = ?",
                    (game.created_at, game.updated_at, game.id)
                )
            conn.commit()
    
    def find_by_id(self, game_id: str) -> Optional[Game]:
        """Find a game by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # Parse the JSON game state
            state_dict = json.loads(row['game_state_json'])
            
            # Use existing persistence logic to restore game state
            game_state = self._persistence._restore_state_from_dict(state_dict)
            
            # Create immutable state machine
            state_machine = PokerStateMachine(game_state)
            
            # Handle phase - our new state machine needs to use with_phase
            try:
                phase_value = state_dict.get('current_phase', 0)
                if isinstance(phase_value, str):
                    phase_value = int(phase_value)
                phase = PokerPhase(phase_value)
                if phase != PokerPhase.INITIALIZING_GAME:
                    state_machine = state_machine.with_phase(phase)
            except (ValueError, KeyError):
                # Default phase is fine
                pass
            
            # Get timestamps
            created_at = datetime.fromisoformat(row['created_at'])
            updated_at = datetime.fromisoformat(row['updated_at'])
        
        return Game(
            id=game_id,
            state_machine=state_machine,
            created_at=created_at,
            updated_at=updated_at
        )
    
    def find_recent(self, limit: int = 10) -> List[Game]:
        """Find recent games."""
        games = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT game_id, created_at, updated_at 
                FROM games 
                ORDER BY updated_at DESC 
                LIMIT ?
                """,
                (limit,)
            )
            
            for row in cursor:
                # Load each game
                game = self.find_by_id(row['game_id'])
                if game:
                    games.append(game)
        
        return games
    
    def delete(self, game_id: str) -> None:
        """Delete a game and all related data."""
        self._persistence.delete_game(game_id)
    
    def exists(self, game_id: str) -> bool:
        """Check if a game exists."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM games WHERE game_id = ? LIMIT 1",
                (game_id,)
            )
            return cursor.fetchone() is not None


class SQLiteMessageRepository(MessageRepository):
    """SQLite implementation of MessageRepository."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def save(self, message: GameMessage) -> GameMessage:
        """Save a message and return it with ID."""
        with self._get_connection() as conn:
            # Encode sender in message_text if needed
            message_text = message.message
            if message.sender != "System":
                message_text = f"{message.sender}: {message.message}"
                
            cursor = conn.execute(
                """
                INSERT INTO game_messages 
                (game_id, message_text, message_type, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    message.game_id,
                    message_text,
                    message.message_type,
                    message.timestamp
                )
            )
            conn.commit()
            
            # Return message with ID
            message.id = cursor.lastrowid
            return message
    
    def find_by_game_id(self, game_id: str) -> List[GameMessage]:
        """Find all messages for a game."""
        messages = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM game_messages 
                WHERE game_id = ? 
                ORDER BY timestamp ASC
                """,
                (game_id,)
            )
            
            for row in cursor:
                # Parse sender from message_text if needed
                message_text = row['message_text']
                sender = "System"
                message = message_text
                
                # Check if message has sender prefix
                if ": " in message_text and row['message_type'] == 'player':
                    parts = message_text.split(": ", 1)
                    if len(parts) == 2:
                        sender = parts[0]
                        message = parts[1]
                
                messages.append(GameMessage(
                    id=row['id'],
                    game_id=row['game_id'],
                    sender=sender,
                    message=message,
                    message_type=row['message_type'],
                    timestamp=datetime.fromisoformat(row['timestamp'])
                ))
        
        return messages
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all messages for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM game_messages WHERE game_id = ?",
                (game_id,)
            )
            conn.commit()


class SQLiteAIStateRepository(AIStateRepository):
    """SQLite implementation of AIStateRepository."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def save(self, ai_state: AIPlayerState) -> None:
        """Save or update AI player state."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, 
                 personality_state, last_updated)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ai_state.game_id,
                    ai_state.player_name,
                    json.dumps(ai_state.conversation_history),
                    json.dumps(ai_state.personality_state),
                    ai_state.last_updated
                )
            )
            conn.commit()
    
    def find_by_game_and_player(self, game_id: str, player_name: str) -> Optional[AIPlayerState]:
        """Find AI state for a specific player in a game."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM ai_player_state
                WHERE game_id = ? AND player_name = ?
                """,
                (game_id, player_name)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return AIPlayerState(
                game_id=row['game_id'],
                player_name=row['player_name'],
                conversation_history=json.loads(row['conversation_history']),
                personality_state=json.loads(row['personality_state']),
                last_updated=datetime.fromisoformat(row['last_updated'])
            )
    
    def find_by_game_id(self, game_id: str) -> List[AIPlayerState]:
        """Find all AI states for a game."""
        states = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM ai_player_state
                WHERE game_id = ?
                """,
                (game_id,)
            )
            
            for row in cursor:
                states.append(AIPlayerState(
                    game_id=row['game_id'],
                    player_name=row['player_name'],
                    conversation_history=json.loads(row['conversation_history']),
                    personality_state=json.loads(row['personality_state']),
                    last_updated=datetime.fromisoformat(row['last_updated'])
                ))
        
        return states
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all AI states for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM ai_player_state WHERE game_id = ?",
                (game_id,)
            )
            conn.commit()


class PressureEventRepository:
    """Repository for managing pressure event persistence."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    @contextmanager
    def _get_connection(self):
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def save_event(self, game_id: str, player_name: str, event_type: str, 
                   details: Optional[Dict[str, Any]] = None) -> None:
        """Save a pressure event to the database."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pressure_events 
                (game_id, player_name, event_type, details_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    game_id,
                    player_name,
                    event_type,
                    json.dumps(details) if details else None
                )
            )
            conn.commit()
    
    def get_events_for_game(self, game_id: str) -> List[Dict[str, Any]]:
        """Get all pressure events for a specific game."""
        events = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM pressure_events
                WHERE game_id = ?
                ORDER BY timestamp ASC
                """,
                (game_id,)
            )
            
            for row in cursor:
                events.append({
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'player_name': row['player_name'],
                    'event_type': row['event_type'],
                    'timestamp': row['timestamp'],
                    'details': json.loads(row['details_json']) if row['details_json'] else {}
                })
        
        return events
    
    def get_events_for_player(self, player_name: str) -> List[Dict[str, Any]]:
        """Get all pressure events for a specific player across all games."""
        events = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM pressure_events
                WHERE player_name = ?
                ORDER BY timestamp DESC
                """,
                (player_name,)
            )
            
            for row in cursor:
                events.append({
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'player_name': row['player_name'],
                    'event_type': row['event_type'],
                    'timestamp': row['timestamp'],
                    'details': json.loads(row['details_json']) if row['details_json'] else {}
                })
        
        return events
    
    def get_aggregated_stats_for_game(self, game_id: str) -> Dict[str, Dict[str, int]]:
        """Get aggregated stats for all players in a game."""
        stats = {}
        
        with self._get_connection() as conn:
            # Get count of each event type per player
            cursor = conn.execute(
                """
                SELECT player_name, event_type, COUNT(*) as count
                FROM pressure_events
                WHERE game_id = ?
                GROUP BY player_name, event_type
                """,
                (game_id,)
            )
            
            for row in cursor:
                player_name = row['player_name']
                if player_name not in stats:
                    stats[player_name] = {}
                stats[player_name][row['event_type']] = row['count']
            
            # Get biggest pots from details
            cursor = conn.execute(
                """
                SELECT player_name, event_type, details_json
                FROM pressure_events
                WHERE game_id = ? AND event_type IN ('win', 'big_win', 'big_loss')
                """,
                (game_id,)
            )
            
            for row in cursor:
                player_name = row['player_name']
                if player_name not in stats:
                    stats[player_name] = {}
                
                details = json.loads(row['details_json']) if row['details_json'] else {}
                pot_size = details.get('pot_size', 0)
                
                if row['event_type'] in ('win', 'big_win'):
                    current_max = stats[player_name].get('biggest_pot_won', 0)
                    stats[player_name]['biggest_pot_won'] = max(current_max, pot_size)
                elif row['event_type'] == 'big_loss':
                    current_max = stats[player_name].get('biggest_pot_lost', 0)
                    stats[player_name]['biggest_pot_lost'] = max(current_max, pot_size)
        
        return stats
    
    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all pressure events for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM pressure_events WHERE game_id = ?",
                (game_id,)
            )
            conn.commit()