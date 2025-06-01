"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from core.card import Card


@dataclass
class SavedGame:
    """Represents a saved game with metadata."""
    game_id: str
    created_at: datetime
    updated_at: datetime
    phase: str
    num_players: int
    pot_size: float
    game_state_json: str


class GamePersistence:
    """Handles persistence of poker games to SQLite database."""
    
    def __init__(self, db_path: str = "poker_games.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    phase TEXT NOT NULL,
                    num_players INTEGER NOT NULL,
                    pot_size REAL NOT NULL,
                    game_state_json TEXT NOT NULL
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS game_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_type TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_games_updated 
                ON games(updated_at DESC)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_game_id 
                ON game_messages(game_id, timestamp)
            """)
    
    def save_game(self, game_id: str, state_machine: PokerStateMachine) -> None:
        """Save a game state to the database."""
        game_state = state_machine.game_state
        
        # Convert game state to dict and then to JSON
        state_dict = self._prepare_state_for_save(game_state)
        state_dict['current_phase'] = state_machine.current_phase.value
        
        game_json = json.dumps(state_dict)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO games 
                (game_id, updated_at, phase, num_players, pot_size, game_state_json)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
            """, (
                game_id,
                state_machine.current_phase.value,
                len(game_state.players),
                game_state.pot['total'],
                game_json
            ))
    
    def load_game(self, game_id: str) -> Optional[PokerStateMachine]:
        """Load a game state from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM games WHERE game_id = ?", 
                (game_id,)
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # Parse the JSON and recreate the game state
            state_dict = json.loads(row['game_state_json'])
            game_state = self._restore_state_from_dict(state_dict)
            
            # Create state machine with the loaded state
            state_machine = PokerStateMachine(game_state)
            state_machine.current_phase = PokerPhase(state_dict['current_phase'])
            
            return state_machine
    
    def list_games(self, limit: int = 20) -> List[SavedGame]:
        """List saved games, most recently updated first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM games 
                ORDER BY updated_at DESC 
                LIMIT ?
            """, (limit,))
            
            games = []
            for row in cursor:
                games.append(SavedGame(
                    game_id=row['game_id'],
                    created_at=datetime.fromisoformat(row['created_at']),
                    updated_at=datetime.fromisoformat(row['updated_at']),
                    phase=row['phase'],
                    num_players=row['num_players'],
                    pot_size=row['pot_size'],
                    game_state_json=row['game_state_json']
                ))
            
            return games
    
    def delete_game(self, game_id: str) -> None:
        """Delete a game and its messages."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM game_messages WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
    
    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO game_messages (game_id, message_type, message_text)
                VALUES (?, ?, ?)
            """, (game_id, message_type, message_text))
    
    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM game_messages 
                WHERE game_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (game_id, limit))
            
            messages = []
            for row in cursor:
                messages.append({
                    'timestamp': row['timestamp'],
                    'type': row['message_type'],
                    'text': row['message_text']
                })
            
            return list(reversed(messages))  # Return in chronological order
    
    def _prepare_state_for_save(self, game_state: PokerGameState) -> Dict[str, Any]:
        """Prepare game state for JSON serialization."""
        state_dict = game_state.to_dict()
        
        # The to_dict() method already handles most serialization,
        # but we need to ensure all custom objects are properly converted
        return state_dict
    
    def _restore_state_from_dict(self, state_dict: Dict[str, Any]) -> PokerGameState:
        """Restore game state from dictionary."""
        # Reconstruct players
        players = []
        for player_data in state_dict['players']:
            # Reconstruct hand if present
            hand = None
            if player_data.get('hand'):
                hand = tuple(Card.from_dict(card_dict) for card_dict in player_data['hand'])
            
            player = Player(
                name=player_data['name'],
                stack=player_data['stack'],
                is_human=player_data['is_human'],
                bet=player_data['bet'],
                hand=hand,
                is_all_in=player_data['is_all_in'],
                is_folded=player_data['is_folded'],
                has_acted=player_data['has_acted']
            )
            players.append(player)
        
        # Reconstruct deck
        deck = [Card.from_dict(card_dict) for card_dict in state_dict['deck']]
        
        # Reconstruct discard pile
        discard_pile = [Card.from_dict(card_dict) for card_dict in state_dict['discard_pile']]
        
        # Reconstruct community cards
        community_cards = [Card.from_dict(card_dict) for card_dict in state_dict['community_cards']]
        
        # Create the game state
        return PokerGameState(
            players=tuple(players),
            deck=deck,
            discard_pile=discard_pile,
            pot=state_dict['pot'],
            current_player_idx=state_dict['current_player_idx'],
            current_dealer_idx=state_dict['current_dealer_idx'],
            community_cards=community_cards,
            current_ante=state_dict['current_ante'],
            pre_flop_action_taken=state_dict['pre_flop_action_taken'],
            awaiting_action=state_dict['awaiting_action']
        )