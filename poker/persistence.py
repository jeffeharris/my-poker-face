"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass

from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from core.card import Card
import logging

logger = logging.getLogger(__name__)

# Current schema version - increment when adding migrations
SCHEMA_VERSION = 2


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
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None


class GamePersistence:
    """Handles persistence of poker games to SQLite database."""

    def __init__(self, db_path: str = "poker_games.db"):
        self.db_path = db_path
        self._init_db()
        self._run_migrations()
    
    def _serialize_card(self, card) -> Dict[str, Any]:
        """Ensure card is properly serialized."""
        if hasattr(card, 'to_dict'):
            return card.to_dict()
        elif isinstance(card, dict):
            # Validate dict has required fields
            if 'rank' in card and 'suit' in card:
                return card
            else:
                raise ValueError(f"Invalid card dict: missing rank or suit in {card}")
        else:
            raise ValueError(f"Unknown card format: {type(card)}")
    
    def _deserialize_card(self, card_data) -> Card:
        """Ensure card is properly deserialized to Card object."""
        if isinstance(card_data, dict):
            return Card.from_dict(card_data)
        elif hasattr(card_data, 'rank'):  # Already a Card object
            return card_data
        else:
            raise ValueError(f"Cannot deserialize card: {card_data}")
    
    def _serialize_cards(self, cards) -> List[Dict[str, Any]]:
        """Serialize a collection of cards."""
        if not cards:
            return []
        return [self._serialize_card(card) for card in cards]
    
    def _deserialize_cards(self, cards_data) -> tuple:
        """Deserialize a collection of cards."""
        if not cards_data:
            return tuple()
        return tuple(self._deserialize_card(card_data) for card_data in cards_data)
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Schema version tracking table - must be created first
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    phase TEXT NOT NULL,
                    num_players INTEGER NOT NULL,
                    pot_size REAL NOT NULL,
                    game_state_json TEXT NOT NULL,
                    owner_id TEXT,
                    owner_name TEXT
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
            
            # AI state persistence tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_player_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    conversation_history TEXT,  -- JSON array of messages
                    personality_state TEXT,     -- JSON of current personality modifiers
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, player_name)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personality_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER,
                    personality_traits TEXT,  -- JSON with all trait values
                    pressure_levels TEXT,     -- JSON with pressure per trait
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            
            # Create indices for AI tables
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ai_player_game 
                ON ai_player_state(game_id, player_name)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_personality_snapshots 
                ON personality_snapshots(game_id, hand_number)
            """)
            
            # Pressure events tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pressure_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    details_json TEXT,
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
            """)
            
            # Create indices for pressure events
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pressure_events_game 
                ON pressure_events(game_id)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pressure_events_player 
                ON pressure_events(player_name)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pressure_events_type 
                ON pressure_events(event_type)
            """)
            
            # Personality storage for AI-generated personalities
            conn.execute("""
                CREATE TABLE IF NOT EXISTS personalities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_generated BOOLEAN DEFAULT 1,
                    source TEXT DEFAULT 'ai_generated',
                    times_used INTEGER DEFAULT 0
                )
            """)

            # Hand history for AI memory and learning
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hand_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    hand_number INTEGER NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    players_json TEXT NOT NULL,
                    hole_cards_json TEXT,
                    community_cards_json TEXT,
                    actions_json TEXT NOT NULL,
                    winners_json TEXT,
                    pot_size INTEGER,
                    showdown BOOLEAN,
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    UNIQUE(game_id, hand_number)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hand_history_game
                ON hand_history(game_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hand_history_timestamp
                ON hand_history(timestamp DESC)
            """)

            # Opponent models for AI learning across sessions
            conn.execute("""
                CREATE TABLE IF NOT EXISTS opponent_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    hands_observed INTEGER DEFAULT 0,
                    vpip REAL DEFAULT 0.5,
                    pfr REAL DEFAULT 0.5,
                    aggression_factor REAL DEFAULT 1.0,
                    fold_to_cbet REAL DEFAULT 0.5,
                    bluff_frequency REAL DEFAULT 0.3,
                    showdown_win_rate REAL DEFAULT 0.5,
                    recent_trend TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(observer_name, opponent_name)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_opponent_models_observer
                ON opponent_models(observer_name)
            """)

            # Memorable hands that AI players remember
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memorable_hands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observer_name TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    hand_id INTEGER NOT NULL,
                    memory_type TEXT NOT NULL,
                    impact_score REAL,
                    narrative TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (hand_id) REFERENCES hand_history(id)
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memorable_observer
                ON memorable_hands(observer_name)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memorable_opponent
                ON memorable_hands(opponent_name)
            """)
            
            # Add index for owner_id
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_games_owner
                ON games(owner_id)
            """)

    def _get_current_schema_version(self) -> int:
        """Get the current schema version from the database."""
        with sqlite3.connect(self.db_path) as conn:
            try:
                cursor = conn.execute("SELECT MAX(version) FROM schema_version")
                result = cursor.fetchone()[0]
                return result if result is not None else 0
            except sqlite3.OperationalError:
                # Table doesn't exist yet
                return 0

    def _run_migrations(self) -> None:
        """Run any pending schema migrations."""
        current_version = self._get_current_schema_version()

        if current_version >= SCHEMA_VERSION:
            return

        logger.info(f"Running database migrations from version {current_version} to {SCHEMA_VERSION}")

        migrations: Dict[int, tuple] = {
            1: (self._migrate_v1_add_owner_columns, "Add owner_id and owner_name to games table"),
            2: (self._migrate_v2_add_memory_tables, "Add AI memory and learning tables"),
        }

        with sqlite3.connect(self.db_path) as conn:
            for version in range(current_version + 1, SCHEMA_VERSION + 1):
                if version in migrations:
                    migrate_func, description = migrations[version]
                    try:
                        migrate_func(conn)
                        conn.execute(
                            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                            (version, description)
                        )
                        conn.commit()
                        logger.info(f"Applied migration v{version}: {description}")
                    except Exception as e:
                        logger.error(f"Migration v{version} failed: {e}")
                        raise

    def _migrate_v1_add_owner_columns(self, conn: sqlite3.Connection) -> None:
        """Migration v1: Add owner_id and owner_name columns to games table."""
        cursor = conn.execute("PRAGMA table_info(games)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'owner_id' not in columns:
            conn.execute("ALTER TABLE games ADD COLUMN owner_id TEXT")
            conn.execute("ALTER TABLE games ADD COLUMN owner_name TEXT")
            # Purge old games without owners
            conn.execute("DELETE FROM games")
            logger.info("Added owner_id column and purged old games without owners")

    def _migrate_v2_add_memory_tables(self, conn: sqlite3.Connection) -> None:
        """Migration v2: Add AI memory and learning tables.

        These tables may already exist from _init_db, but this migration
        ensures the schema_version table tracks their addition.
        """
        # Verify tables exist (they should from _init_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?)",
            ('hand_history', 'opponent_models', 'memorable_hands')
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {'hand_history', 'opponent_models', 'memorable_hands'}
        missing_tables = expected_tables - existing_tables

        if missing_tables:
            logger.warning(f"Memory tables missing (will be created by _init_db): {missing_tables}")

        logger.info("AI memory tables verified/registered in schema version")

    def save_game(self, game_id: str, state_machine: PokerStateMachine, 
                  owner_id: Optional[str] = None, owner_name: Optional[str] = None) -> None:
        """Save a game state to the database."""
        game_state = state_machine.game_state
        
        # Convert game state to dict and then to JSON
        state_dict = self._prepare_state_for_save(game_state)
        state_dict['current_phase'] = state_machine.current_phase.value
        
        game_json = json.dumps(state_dict)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO games 
                (game_id, updated_at, phase, num_players, pot_size, game_state_json, owner_id, owner_name)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                state_machine.current_phase.value,
                len(game_state.players),
                game_state.pot['total'],
                game_json,
                owner_id,
                owner_name
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

            # Restore the phase - handle both int and string values
            try:
                phase_value = state_dict.get('current_phase', 0)
                if isinstance(phase_value, str):
                    phase_value = int(phase_value)
                phase = PokerPhase(phase_value)
            except (ValueError, KeyError):
                print(f"Warning: Could not restore phase {state_dict.get('current_phase')}, using INITIALIZING_HAND")
                phase = PokerPhase.INITIALIZING_HAND

            # Create state machine with the loaded state and phase
            return PokerStateMachine.from_saved_state(game_state, phase)
    
    def list_games(self, owner_id: Optional[str] = None, limit: int = 20) -> List[SavedGame]:
        """List saved games, most recently updated first. Filter by owner_id if provided."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if owner_id:
                cursor = conn.execute("""
                    SELECT * FROM games 
                    WHERE owner_id = ?
                    ORDER BY updated_at DESC 
                    LIMIT ?
                """, (owner_id, limit))
            else:
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
                    game_state_json=row['game_state_json'],
                    owner_id=row['owner_id'],
                    owner_name=row['owner_name']
                ))
            
            return games
    
    def count_user_games(self, owner_id: str) -> int:
        """Count how many games a user owns."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM games WHERE owner_id = ?
            """, (owner_id,))
            return cursor.fetchone()[0]
    
    def delete_game(self, game_id: str) -> None:
        """Delete a game and all associated data."""
        with sqlite3.connect(self.db_path) as conn:
            # Delete all associated data (order matters for foreign keys)
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
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
                # Parse "sender: content" format back into separate fields
                text = row['message_text']
                if ': ' in text:
                    sender, content = text.split(': ', 1)
                else:
                    sender = 'System'
                    content = text
                messages.append({
                    'id': str(row['id']),
                    'timestamp': row['timestamp'],
                    'type': row['message_type'],
                    'sender': sender,
                    'content': content
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
                try:
                    hand = self._deserialize_cards(player_data['hand'])
                except Exception as e:
                    logger.warning(f"Error deserializing hand for {player_data['name']}: {e}")
                    hand = None
            
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
        try:
            deck = self._deserialize_cards(state_dict.get('deck', []))
        except Exception as e:
            logger.warning(f"Error deserializing deck: {e}")
            deck = tuple()
        
        # Reconstruct discard pile
        try:
            discard_pile = self._deserialize_cards(state_dict.get('discard_pile', []))
        except Exception as e:
            logger.warning(f"Error deserializing discard pile: {e}")
            discard_pile = tuple()
        
        # Reconstruct community cards
        try:
            community_cards = self._deserialize_cards(state_dict.get('community_cards', []))
        except Exception as e:
            logger.warning(f"Error deserializing community cards: {e}")
            community_cards = tuple()
        
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
    
    # AI State Persistence Methods
    def save_ai_player_state(self, game_id: str, player_name: str, 
                            messages: List[Dict[str, str]], 
                            personality_state: Dict[str, Any]) -> None:
        """Save AI player conversation history and personality state."""
        with sqlite3.connect(self.db_path) as conn:
            conversation_history = json.dumps(messages)
            personality_json = json.dumps(personality_state)
            
            conn.execute("""
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """, (game_id, player_name, conversation_history, personality_json))
    
    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT player_name, conversation_history, personality_state
                FROM ai_player_state
                WHERE game_id = ?
            """, (game_id,))
            
            ai_states = {}
            for row in cursor.fetchall():
                ai_states[row['player_name']] = {
                    'messages': json.loads(row['conversation_history']),
                    'personality_state': json.loads(row['personality_state'])
                }
            
            return ai_states
    
    def save_personality_snapshot(self, game_id: str, player_name: str, 
                                 hand_number: int, traits: Dict[str, Any], 
                                 pressure_levels: Optional[Dict[str, float]] = None) -> None:
        """Save a snapshot of personality state for elasticity tracking."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO personality_snapshots
                (player_name, game_id, hand_number, personality_traits, pressure_levels)
                VALUES (?, ?, ?, ?, ?)
            """, (
                player_name,
                game_id,
                hand_number,
                json.dumps(traits),
                json.dumps(pressure_levels or {})
            ))
    
    def save_personality(self, name: str, config: Dict[str, Any], source: str = 'ai_generated') -> None:
        """Save a personality configuration to the database."""
        # Extract elasticity_config if present in the main config
        elasticity_config = config.get('elasticity_config', {})
        
        # Remove elasticity_config from main config if it exists (to store separately)
        config_without_elasticity = {k: v for k, v in config.items() if k != 'elasticity_config'}
        
        with sqlite3.connect(self.db_path) as conn:
            # Check if elasticity_config column exists
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'elasticity_config' in columns:
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config_without_elasticity), json.dumps(elasticity_config), source))
            else:
                # Fallback for old schema
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, source, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config), source))
    
    def load_personality(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a personality configuration from the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Check if elasticity_config column exists
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'elasticity_config' in columns:
                cursor = conn.execute("""
                    SELECT config_json, elasticity_config FROM personalities
                    WHERE name = ?
                """, (name,))
            else:
                cursor = conn.execute("""
                    SELECT config_json FROM personalities
                    WHERE name = ?
                """, (name,))
            
            row = cursor.fetchone()
            if row:
                # Increment usage counter
                conn.execute("""
                    UPDATE personalities 
                    SET times_used = times_used + 1 
                    WHERE name = ?
                """, (name,))
                
                config = json.loads(row['config_json'])
                
                # Add elasticity_config if available
                if 'elasticity_config' in columns and row['elasticity_config']:
                    config['elasticity_config'] = json.loads(row['elasticity_config'])
                
                return config
            
            return None
    
    def increment_personality_usage(self, name: str) -> None:
        """Increment the usage counter for a personality."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE personalities 
                SET times_used = times_used + 1 
                WHERE name = ?
            """, (name,))
    
    def list_personalities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all personalities with metadata."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT name, source, created_at, updated_at, times_used, is_generated
                FROM personalities
                ORDER BY times_used DESC, updated_at DESC
                LIMIT ?
            """, (limit,))
            
            personalities = []
            for row in cursor:
                personalities.append({
                    'name': row['name'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'times_used': row['times_used'],
                    'is_generated': bool(row['is_generated'])
                })
            
            return personalities
    
    def delete_personality(self, name: str) -> bool:
        """Delete a personality from the database."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM personalities WHERE name = ?",
                    (name,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting personality {name}: {e}")
            return False