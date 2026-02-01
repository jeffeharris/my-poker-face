"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass


from poker.poker_game import PokerGameState, Player
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from core.card import Card
import logging

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.settings_repository import SettingsRepository
from poker.repositories.guest_tracking_repository import GuestTrackingRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.user_repository import UserRepository
from poker.repositories.experiment_repository import ExperimentRepository

logger = logging.getLogger(__name__)

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
    """Handles persistence of poker games to SQLite database.
    
    Schema management is delegated to SchemaManager.
    """

    def __init__(self, db_path: str = "data/poker_games.db"):
        self.db_path = db_path
        # Ensure directory exists
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            try:
                os.makedirs(db_dir, exist_ok=True)
            except OSError as e:
                logger.warning(f"Could not create database directory {db_dir}: {e}")

        # Delegate schema management
        schema_manager = SchemaManager(db_path)
        schema_manager.ensure_schema()

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

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new database connection with standard timeout."""
        return sqlite3.connect(self.db_path, timeout=5.0)

    # --- Repository lazy properties (T3-35 facade delegation) ---

    @property
    def _settings_repo(self):
        if not hasattr(self, '__settings_repo'):
            self.__settings_repo = SettingsRepository(self.db_path)
        return self.__settings_repo

    @property
    def _guest_tracking_repo(self):
        if not hasattr(self, '__guest_tracking_repo'):
            self.__guest_tracking_repo = GuestTrackingRepository(self.db_path)
        return self.__guest_tracking_repo

    @property
    def _personality_repo(self):
        if not hasattr(self, '__personality_repo'):
            self.__personality_repo = PersonalityRepository(self.db_path)
        return self.__personality_repo

    @property
    def _user_repo(self):
        if not hasattr(self, '__user_repo'):
            self.__user_repo = UserRepository(self.db_path)
        return self.__user_repo

    @property
    def _experiment_repo(self):
        if not hasattr(self, '__experiment_repo'):
            self.__experiment_repo = ExperimentRepository(self.db_path)
        return self.__experiment_repo

    def save_coach_mode(self, game_id: str, mode: str) -> None:
        """Persist coach mode preference for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE games SET coach_mode = ? WHERE game_id = ?",
                (mode, game_id)
            )
            conn.commit()

    def load_coach_mode(self, game_id: str) -> str:
        """Load coach mode preference for a game. Defaults to 'off'."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT coach_mode FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else 'off'

    def save_game(self, game_id: str, state_machine: PokerStateMachine,
                  owner_id: Optional[str] = None, owner_name: Optional[str] = None,
                  llm_configs: Optional[Dict] = None) -> None:
        """Save a game state to the database.

        Args:
            game_id: The game identifier
            state_machine: The game's state machine
            owner_id: The owner/user ID
            owner_name: The owner's display name
            llm_configs: Dict with 'player_llm_configs' and 'default_llm_config'
        """
        game_state = state_machine.game_state

        # Convert game state to dict and then to JSON
        state_dict = self._prepare_state_for_save(game_state)
        state_dict['current_phase'] = state_machine.current_phase.value

        game_json = json.dumps(state_dict)
        llm_configs_json = json.dumps(llm_configs) if llm_configs else None

        with self._get_connection() as conn:
            # Use ON CONFLICT DO UPDATE to preserve columns not being updated
            # (like debug_capture_enabled) instead of INSERT OR REPLACE which
            # deletes and re-inserts, resetting unspecified columns to defaults
            conn.execute("""
                INSERT INTO games
                (game_id, updated_at, phase, num_players, pot_size, game_state_json, owner_id, owner_name, llm_configs_json)
                VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    updated_at = CURRENT_TIMESTAMP,
                    phase = excluded.phase,
                    num_players = excluded.num_players,
                    pot_size = excluded.pot_size,
                    game_state_json = excluded.game_state_json,
                    owner_id = excluded.owner_id,
                    owner_name = excluded.owner_name,
                    llm_configs_json = COALESCE(excluded.llm_configs_json, games.llm_configs_json)
            """, (
                game_id,
                state_machine.current_phase.value,
                len(game_state.players),
                game_state.pot['total'],
                game_json,
                owner_id,
                owner_name,
                llm_configs_json
            ))
    
    def load_game(self, game_id: str) -> Optional[PokerStateMachine]:
        """Load a game state from the database."""
        with self._get_connection() as conn:
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
                logger.warning(f"[RESTORE] Could not restore phase {state_dict.get('current_phase')}, using INITIALIZING_HAND")
                phase = PokerPhase.INITIALIZING_HAND

            # Create state machine with the loaded state and phase
            return PokerStateMachine.from_saved_state(game_state, phase)

    def load_llm_configs(self, game_id: str) -> Optional[Dict]:
        """Load LLM configs for a game.

        Args:
            game_id: The game identifier

        Returns:
            Dict with 'player_llm_configs' and 'default_llm_config', or None if not found
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT llm_configs_json FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()

            if not row or not row['llm_configs_json']:
                return None

            return json.loads(row['llm_configs_json'])

    def save_tournament_tracker(self, game_id: str, tracker) -> None:
        """Save tournament tracker state to the database.

        Args:
            game_id: The game identifier
            tracker: TournamentTracker instance or dict from to_dict()
        """
        # Convert to dict if it's a TournamentTracker object
        if hasattr(tracker, 'to_dict'):
            tracker_dict = tracker.to_dict()
        else:
            tracker_dict = tracker

        tracker_json = json.dumps(tracker_dict)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO tournament_tracker (game_id, tracker_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(game_id) DO UPDATE SET
                    tracker_json = excluded.tracker_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (game_id, tracker_json))

    def load_tournament_tracker(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament tracker state from the database.

        Args:
            game_id: The game identifier

        Returns:
            Dict that can be passed to TournamentTracker.from_dict(), or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT tracker_json FROM tournament_tracker WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            return json.loads(row[0])

    def list_games(self, owner_id: Optional[str] = None, limit: int = 20) -> List[SavedGame]:
        """List saved games, most recently updated first. Filter by owner_id if provided."""
        with self._get_connection() as conn:
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
        return self._user_repo.count_user_games(owner_id)

    def get_last_game_creation_time(self, owner_id: str) -> Optional[float]:
        """Get the timestamp of the user's last game creation."""
        return self._user_repo.get_last_game_creation_time(owner_id)

    def update_last_game_creation_time(self, owner_id: str, timestamp: float) -> None:
        """Update the user's last game creation timestamp."""
        return self._user_repo.update_last_game_creation_time(owner_id, timestamp)

    def create_google_user(self, google_sub: str, email: str, name: str,
                           picture: Optional[str] = None,
                           linked_guest_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new user from Google OAuth."""
        return self._user_repo.create_google_user(google_sub, email, name, picture, linked_guest_id)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by their ID."""
        return self._user_repo.get_user_by_id(user_id)

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by their email address."""
        return self._user_repo.get_user_by_email(email)

    def get_user_by_linked_guest(self, guest_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by the guest ID they were linked from."""
        return self._user_repo.get_user_by_linked_guest(guest_id)

    def update_user_last_login(self, user_id: str) -> None:
        """Update the last login timestamp for a user."""
        return self._user_repo.update_user_last_login(user_id)

    def transfer_game_ownership(self, from_owner_id: str, to_owner_id: str, to_owner_name: str) -> int:
        """Transfer all games from one owner to another."""
        return self._user_repo.transfer_game_ownership(from_owner_id, to_owner_id, to_owner_name)

    def transfer_guest_to_user(self, from_id: str, to_id: str, to_name: str) -> int:
        """Transfer all owner_id references from guest to authenticated user."""
        return self._user_repo.transfer_guest_to_user(from_id, to_id, to_name)

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users with their groups."""
        return self._user_repo.get_all_users()

    def get_user_groups(self, user_id: str) -> List[str]:
        """Get all group names for a user."""
        return self._user_repo.get_user_groups(user_id)

    def get_user_permissions(self, user_id: str) -> List[str]:
        """Get all permissions for a user via their groups."""
        return self._user_repo.get_user_permissions(user_id)

    def assign_user_to_group(self, user_id: str, group_name: str, assigned_by: Optional[str] = None) -> bool:
        """Assign a user to a group."""
        return self._user_repo.assign_user_to_group(user_id, group_name, assigned_by)

    def remove_user_from_group(self, user_id: str, group_name: str) -> bool:
        """Remove a user from a group."""
        return self._user_repo.remove_user_from_group(user_id, group_name)

    def count_users_in_group(self, group_name: str) -> int:
        """Count the number of users in a group."""
        return self._user_repo.count_users_in_group(group_name)

    def get_all_groups(self) -> List[Dict[str, Any]]:
        """Get all available groups."""
        return self._user_repo.get_all_groups()

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """Get statistics for a user."""
        return self._user_repo.get_user_stats(user_id)

    def initialize_admin_from_env(self) -> Optional[str]:
        """Assign admin group to user with INITIAL_ADMIN_EMAIL."""
        return self._user_repo.initialize_admin_from_env()

    def get_available_providers(self) -> Set[str]:
        """Get the set of all providers in the system.

        Returns:
            Set of all provider names in enabled_models table.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT provider
                FROM enabled_models
            """)
            return {row[0] for row in cursor.fetchall()}

    def get_enabled_models(self) -> Dict[str, List[str]]:
        """Get all enabled models grouped by provider.

        Returns:
            Dict mapping provider name to list of enabled model names.
            Example: {'openai': ['gpt-4o', 'gpt-5-nano'], 'groq': ['llama-3.1-8b-instant']}
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT provider, model
                FROM enabled_models
                WHERE enabled = 1
                ORDER BY provider, sort_order
            """)
            result: Dict[str, List[str]] = {}
            for row in cursor.fetchall():
                provider = row['provider']
                if provider not in result:
                    result[provider] = []
                result[provider].append(row['model'])
            return result

    def get_all_enabled_models(self) -> List[Dict[str, Any]]:
        """Get all models with their enabled status.

        Returns:
            List of dicts with provider, model, enabled, user_enabled, display_name, etc.
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT id, provider, model, enabled, user_enabled, display_name, notes,
                       supports_reasoning, supports_json_mode, supports_image_gen,
                       sort_order, created_at, updated_at
                FROM enabled_models
                ORDER BY provider, sort_order
            """)
            return [dict(row) for row in cursor.fetchall()]

    def update_model_enabled(self, model_id: int, enabled: bool) -> bool:
        """Update the enabled status of a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (1 if enabled else 0, model_id))
            return cursor.rowcount > 0

    def update_model_details(self, model_id: int, display_name: str = None, notes: str = None) -> bool:
        """Update display name and notes for a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET display_name = COALESCE(?, display_name),
                    notes = COALESCE(?, notes),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (display_name, notes, model_id))
            return cursor.rowcount > 0

    def delete_game(self, game_id: str) -> None:
        """Delete a game and all associated data."""
        with self._get_connection() as conn:
            # Delete all associated data (order matters for foreign keys)
            conn.execute("DELETE FROM personality_snapshots WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM ai_player_state WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_messages WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
    
    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO game_messages (game_id, message_type, message_text)
                VALUES (?, ?, ?)
            """, (game_id, message_type, message_text))
    
    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        with self._get_connection() as conn:
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
                has_acted=player_data['has_acted'],
                last_action=player_data.get('last_action')
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
            awaiting_action=state_dict['awaiting_action'],
            run_it_out=state_dict.get('run_it_out', False)
        )
    
    # AI State Persistence Methods
    def save_ai_player_state(self, game_id: str, player_name: str, 
                            messages: List[Dict[str, str]], 
                            personality_state: Dict[str, Any]) -> None:
        """Save AI player conversation history and personality state."""
        with self._get_connection() as conn:
            conversation_history = json.dumps(messages)
            personality_json = json.dumps(personality_state)
            
            conn.execute("""
                INSERT OR REPLACE INTO ai_player_state
                (game_id, player_name, conversation_history, personality_state)
                VALUES (?, ?, ?, ?)
            """, (game_id, player_name, conversation_history, personality_json))
    
    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        return self._personality_repo.save_personality(name, config, source)

    def load_personality(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a personality configuration from the database."""
        return self._personality_repo.load_personality(name)

    def increment_personality_usage(self, name: str) -> None:
        """Increment the usage counter for a personality."""
        return self._personality_repo.increment_personality_usage(name)

    def list_personalities(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List all personalities with metadata."""
        return self._personality_repo.list_personalities(limit)

    def delete_personality(self, name: str) -> bool:
        """Delete a personality from the database."""
        return self._personality_repo.delete_personality(name)

    # Emotional State Persistence Methods
    def save_emotional_state(self, game_id: str, player_name: str,
                             emotional_state) -> None:
        """Save emotional state for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            emotional_state: EmotionalState object or dict from EmotionalState.to_dict()
        """
        # Convert to dict if it's an EmotionalState object
        if hasattr(emotional_state, 'to_dict'):
            state_dict = emotional_state.to_dict()
        else:
            state_dict = emotional_state

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO emotional_state
                (game_id, player_name, valence, arousal, control, focus,
                 narrative, inner_voice, generated_at_hand, source_events,
                 metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                player_name,
                state_dict.get('valence', 0.0),
                state_dict.get('arousal', 0.5),
                state_dict.get('control', 0.5),
                state_dict.get('focus', 0.5),
                state_dict.get('narrative', ''),
                state_dict.get('inner_voice', ''),
                state_dict.get('generated_at_hand', 0),
                json.dumps(state_dict.get('source_events', [])),
                json.dumps({
                    'created_at': state_dict.get('created_at'),
                    'used_fallback': state_dict.get('used_fallback', False)
                })
            ))

    def load_emotional_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a player.

        Returns:
            Dict suitable for EmotionalState.from_dict(), or None if not found
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM emotional_state
                WHERE game_id = ? AND player_name = ?
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}

            return {
                'valence': row['valence'],
                'arousal': row['arousal'],
                'control': row['control'],
                'focus': row['focus'],
                'narrative': row['narrative'] or '',
                'inner_voice': row['inner_voice'] or '',
                'generated_at_hand': row['generated_at_hand'],
                'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                'created_at': metadata.get('created_at'),
                'used_fallback': metadata.get('used_fallback', False)
            }

    def load_all_emotional_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game.

        Returns:
            Dict mapping player_name -> emotional_state dict
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM emotional_state
                WHERE game_id = ?
            """, (game_id,))

            states = {}
            for row in cursor.fetchall():
                metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}
                states[row['player_name']] = {
                    'valence': row['valence'],
                    'arousal': row['arousal'],
                    'control': row['control'],
                    'focus': row['focus'],
                    'narrative': row['narrative'] or '',
                    'inner_voice': row['inner_voice'] or '',
                    'generated_at_hand': row['generated_at_hand'],
                    'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                    'created_at': metadata.get('created_at'),
                    'used_fallback': metadata.get('used_fallback', False)
                }

            return states

    # Controller State Persistence Methods (Tilt + ElasticPersonality + PromptConfig)
    def save_controller_state(self, game_id: str, player_name: str,
                              psychology: Dict[str, Any],
                              prompt_config: Optional[Dict[str, Any]] = None) -> None:
        """Save unified psychology state and prompt config for a player.

        Args:
            game_id: The game identifier
            player_name: The player's name
            psychology: Dict from PlayerPsychology.to_dict()
            prompt_config: Dict from PromptConfig.to_dict() (optional)
        """
        # Extract components from unified psychology
        tilt_state = psychology.get('tilt')
        elastic_personality = psychology.get('elastic')

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO controller_state
                (game_id, player_name, tilt_state_json, elastic_personality_json, prompt_config_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                game_id,
                player_name,
                json.dumps(tilt_state) if tilt_state else None,
                json.dumps(elastic_personality) if elastic_personality else None,
                json.dumps(prompt_config) if prompt_config else None
            ))

    def load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player.

        Returns:
            Dict with 'tilt_state', 'elastic_personality', and 'prompt_config' keys, or None if not found
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT tilt_state_json, elastic_personality_json, prompt_config_json
                FROM controller_state
                WHERE game_id = ? AND player_name = ?
            """, (game_id, player_name))

            row = cursor.fetchone()
            if not row:
                return None

            # Handle prompt_config_json which may not exist in older databases
            prompt_config = None
            try:
                if row['prompt_config_json']:
                    prompt_config = json.loads(row['prompt_config_json'])
            except (KeyError, IndexError):
                # Column doesn't exist in older schema
                logger.warning(f"prompt_config_json column not found for {player_name}, using defaults")

            return {
                'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None,
                'prompt_config': prompt_config
            }

    def load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game.

        Returns:
            Dict mapping player_name -> controller state dict
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT player_name, tilt_state_json, elastic_personality_json, prompt_config_json
                FROM controller_state
                WHERE game_id = ?
            """, (game_id,))

            states = {}
            for row in cursor.fetchall():
                # Handle prompt_config_json which may not exist in older databases
                prompt_config = None
                try:
                    if row['prompt_config_json']:
                        prompt_config = json.loads(row['prompt_config_json'])
                except (KeyError, IndexError):
                    pass  # Column doesn't exist in older schema

                states[row['player_name']] = {
                    'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                    'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None,
                    'prompt_config': prompt_config
                }

            return states

    def delete_emotional_state_for_game(self, game_id: str) -> None:
        """Delete all emotional states for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM emotional_state WHERE game_id = ?", (game_id,))

    def delete_controller_state_for_game(self, game_id: str) -> None:
        """Delete all controller states for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM controller_state WHERE game_id = ?", (game_id,))

    # Opponent Model Persistence Methods
    def save_opponent_models(self, game_id: str, opponent_model_manager) -> None:
        """Save opponent models for a game.

        Args:
            game_id: The game identifier
            opponent_model_manager: OpponentModelManager instance or dict from to_dict()
        """
        # Convert to dict if it's an OpponentModelManager object
        if hasattr(opponent_model_manager, 'to_dict'):
            models_dict = opponent_model_manager.to_dict()
        else:
            models_dict = opponent_model_manager

        if not models_dict:
            return

        with self._get_connection() as conn:
            # Clear existing models for this game
            conn.execute("DELETE FROM opponent_models WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM memorable_hands WHERE game_id = ?", (game_id,))

            # Save each observer -> opponent -> model
            for observer_name, opponents in models_dict.items():
                for opponent_name, model_data in opponents.items():
                    tendencies = model_data.get('tendencies', {})

                    conn.execute("""
                        INSERT OR REPLACE INTO opponent_models
                        (game_id, observer_name, opponent_name, hands_observed,
                         vpip, pfr, aggression_factor, fold_to_cbet,
                         bluff_frequency, showdown_win_rate, recent_trend, notes, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        game_id,
                        observer_name,
                        opponent_name,
                        tendencies.get('hands_observed', 0),
                        tendencies.get('vpip', 0.5),
                        tendencies.get('pfr', 0.5),
                        tendencies.get('aggression_factor', 1.0),
                        tendencies.get('fold_to_cbet', 0.5),
                        tendencies.get('bluff_frequency', 0.3),
                        tendencies.get('showdown_win_rate', 0.5),
                        tendencies.get('recent_trend', 'stable'),
                        model_data.get('notes')
                    ))

                    # Save memorable hands
                    memorable_hands = model_data.get('memorable_hands', [])
                    for hand in memorable_hands:
                        conn.execute("""
                            INSERT INTO memorable_hands
                            (game_id, observer_name, opponent_name, hand_id,
                             memory_type, impact_score, narrative, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            game_id,
                            observer_name,
                            opponent_name,
                            hand.get('hand_id', 0),
                            hand.get('memory_type', ''),
                            hand.get('impact_score', 0.0),
                            hand.get('narrative', ''),
                            hand.get('timestamp', datetime.now().isoformat())
                        ))

            logger.debug(f"Saved opponent models for game {game_id}")

    def load_opponent_models(self, game_id: str) -> Dict[str, Any]:
        """Load opponent models for a game.

        Returns:
            Dict suitable for OpponentModelManager.from_dict(), or empty dict if not found
        """
        models_dict = {}

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Load all opponent models for this game
            cursor = conn.execute("""
                SELECT * FROM opponent_models WHERE game_id = ?
            """, (game_id,))

            for row in cursor.fetchall():
                observer_name = row['observer_name']
                opponent_name = row['opponent_name']

                if observer_name not in models_dict:
                    models_dict[observer_name] = {}

                # Build tendencies dict matching OpponentTendencies.to_dict() format
                tendencies = {
                    'hands_observed': row['hands_observed'],
                    'vpip': row['vpip'],
                    'pfr': row['pfr'],
                    'aggression_factor': row['aggression_factor'],
                    'fold_to_cbet': row['fold_to_cbet'],
                    'bluff_frequency': row['bluff_frequency'],
                    'showdown_win_rate': row['showdown_win_rate'],
                    'recent_trend': row['recent_trend'] or 'stable',
                    # Counters - we can't restore these perfectly, but we can estimate
                    '_vpip_count': int(row['vpip'] * row['hands_observed']),
                    '_pfr_count': int(row['pfr'] * row['hands_observed']),
                    '_bet_raise_count': 0,  # Can't restore
                    '_call_count': 0,  # Can't restore
                    '_fold_to_cbet_count': 0,  # Can't restore
                    '_cbet_faced_count': 0,  # Can't restore
                    '_showdowns': 0,  # Can't restore
                    '_showdowns_won': 0,  # Can't restore
                }

                models_dict[observer_name][opponent_name] = {
                    'observer': observer_name,
                    'opponent': opponent_name,
                    'tendencies': tendencies,
                    'memorable_hands': [],
                    'notes': row['notes'] if 'notes' in row.keys() else None
                }

            # Load memorable hands
            cursor = conn.execute("""
                SELECT * FROM memorable_hands WHERE game_id = ?
            """, (game_id,))

            for row in cursor.fetchall():
                observer_name = row['observer_name']
                opponent_name = row['opponent_name']

                if observer_name in models_dict and opponent_name in models_dict[observer_name]:
                    models_dict[observer_name][opponent_name]['memorable_hands'].append({
                        'hand_id': row['hand_id'],
                        'memory_type': row['memory_type'],
                        'opponent_name': opponent_name,
                        'impact_score': row['impact_score'],
                        'narrative': row['narrative'] or '',
                        'hand_summary': '',  # Not stored in DB
                        'timestamp': row['created_at'] or datetime.now().isoformat()
                    })

        if models_dict:
            logger.debug(f"Loaded opponent models for game {game_id}: {len(models_dict)} observers")

        return models_dict

    def delete_opponent_models_for_game(self, game_id: str) -> None:
        """Delete all opponent models for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM opponent_models WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM memorable_hands WHERE game_id = ?", (game_id,))

    # Hand History Persistence Methods
    def save_hand_history(self, recorded_hand) -> int:
        """Save a completed hand to the database.

        Args:
            recorded_hand: RecordedHand instance from hand_history.py

        Returns:
            The database ID of the saved hand
        """
        # Convert to dict if it's a RecordedHand object
        if hasattr(recorded_hand, 'to_dict'):
            hand_dict = recorded_hand.to_dict()
        else:
            hand_dict = recorded_hand

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT OR REPLACE INTO hand_history
                (game_id, hand_number, timestamp, players_json, hole_cards_json,
                 community_cards_json, actions_json, winners_json, pot_size, showdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hand_dict['game_id'],
                hand_dict['hand_number'],
                hand_dict.get('timestamp', datetime.now().isoformat()),
                json.dumps(hand_dict.get('players', [])),
                json.dumps(hand_dict.get('hole_cards', {})),
                json.dumps(hand_dict.get('community_cards', [])),
                json.dumps(hand_dict.get('actions', [])),
                json.dumps(hand_dict.get('winners', [])),
                hand_dict.get('pot_size', 0),
                hand_dict.get('was_showdown', False)
            ))

            hand_id = cursor.lastrowid
            logger.debug(f"Saved hand #{hand_dict['hand_number']} for game {hand_dict['game_id']}")
            return hand_id

    # Hand Commentary Persistence Methods
    def save_hand_commentary(self, game_id: str, hand_number: int, player_name: str,
                             commentary) -> None:
        """Save AI commentary for a completed hand.

        Args:
            game_id: The game identifier
            hand_number: The hand number
            player_name: The AI player's name
            commentary: HandCommentary instance or dict
        """
        if hasattr(commentary, 'to_dict'):
            c = commentary.to_dict()
        else:
            c = commentary

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO hand_commentary
                (game_id, hand_number, player_name, emotional_reaction,
                 strategic_reflection, opponent_observations, key_insight, decision_plans)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                hand_number,
                player_name,
                c.get('emotional_reaction'),
                c.get('strategic_reflection'),
                json.dumps(c.get('opponent_observations', [])),
                c.get('key_insight'),
                json.dumps(c.get('decision_plans', []))
            ))
            logger.debug(f"Saved commentary for {player_name} hand #{hand_number}")

    def get_recent_reflections(self, game_id: str, player_name: str,
                               limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent strategic reflections for a player.

        Args:
            game_id: The game identifier
            player_name: The AI player's name
            limit: Maximum number of reflections to return

        Returns:
            List of dicts with hand_number, strategic_reflection, key_insight
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT hand_number, strategic_reflection, key_insight,
                       opponent_observations
                FROM hand_commentary
                WHERE game_id = ? AND player_name = ?
                ORDER BY hand_number DESC
                LIMIT ?
            """, (game_id, player_name, limit))

            return [dict(row) for row in cursor.fetchall()]

    def get_hand_count(self, game_id: str) -> int:
        """Get the current hand count for a game.

        Args:
            game_id: The game identifier

        Returns:
            The maximum hand_number for this game, or 0 if no hands recorded
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT MAX(hand_number) FROM hand_history WHERE game_id = ?",
                (game_id,)
            )
            result = cursor.fetchone()[0]
            return result or 0

    def load_hand_history(self, game_id: str, limit: int = None) -> List[Dict[str, Any]]:
        """Load hand history for a game.

        Args:
            game_id: The game identifier
            limit: Optional limit on number of hands to load (most recent first)

        Returns:
            List of hand dicts suitable for RecordedHand.from_dict()
        """
        hands = []

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            query = """
                SELECT * FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number DESC
            """
            if limit:
                query += f" LIMIT {limit}"

            cursor = conn.execute(query, (game_id,))

            for row in cursor.fetchall():
                hand = {
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'hand_number': row['hand_number'],
                    'timestamp': row['timestamp'],
                    'players': json.loads(row['players_json'] or '[]'),
                    'hole_cards': json.loads(row['hole_cards_json'] or '{}'),
                    'community_cards': json.loads(row['community_cards_json'] or '[]'),
                    'actions': json.loads(row['actions_json'] or '[]'),
                    'winners': json.loads(row['winners_json'] or '[]'),
                    'pot_size': row['pot_size'] or 0,
                    'was_showdown': bool(row['showdown'])
                }
                hands.append(hand)

        # Return in chronological order (oldest first)
        hands.reverse()

        if hands:
            logger.debug(f"Loaded {len(hands)} hands for game {game_id}")

        return hands

    def delete_hand_history_for_game(self, game_id: str) -> None:
        """Delete all hand history for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM hand_history WHERE game_id = ?", (game_id,))

    def get_session_stats(self, game_id: str, player_name: str) -> Dict[str, Any]:
        """Compute session statistics for a player from hand history.

        Args:
            game_id: The game identifier
            player_name: The player to get stats for

        Returns:
            Dict with session statistics:
                - hands_played: Total hands where player participated
                - hands_won: Hands where player won
                - total_winnings: Net chip change (positive = up, negative = down)
                - biggest_pot_won: Largest pot won
                - biggest_pot_lost: Largest pot lost at showdown
                - current_streak: 'winning', 'losing', or 'neutral'
                - streak_count: Length of current streak
                - recent_hands: List of last N hand summaries
        """
        stats = {
            'hands_played': 0,
            'hands_won': 0,
            'total_winnings': 0,
            'biggest_pot_won': 0,
            'biggest_pot_lost': 0,
            'current_streak': 'neutral',
            'streak_count': 0,
            'recent_hands': []
        }

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Get all hands for this game, ordered by hand_number
            cursor = conn.execute("""
                SELECT hand_number, players_json, winners_json, actions_json, pot_size, showdown
                FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number ASC
            """, (game_id,))

            outcomes = []  # Track outcomes for streak calculation

            for row in cursor.fetchall():
                players = json.loads(row['players_json'] or '[]')
                winners = json.loads(row['winners_json'] or '[]')
                actions = json.loads(row['actions_json'] or '[]')
                pot_size = row['pot_size'] or 0

                # Check if player participated in this hand
                player_in_hand = any(p.get('name') == player_name for p in players)
                if not player_in_hand:
                    continue

                stats['hands_played'] += 1

                # Check if player won
                player_won = False
                amount_won = 0
                for winner in winners:
                    if winner.get('name') == player_name:
                        player_won = True
                        amount_won = winner.get('amount_won', 0)
                        break

                # Calculate amount lost (sum of player's bets)
                amount_bet = sum(
                    a.get('amount', 0)
                    for a in actions
                    if a.get('player_name') == player_name
                )

                # Determine outcome
                if player_won:
                    stats['hands_won'] += 1
                    stats['total_winnings'] += amount_won
                    outcomes.append('won')
                    if pot_size > stats['biggest_pot_won']:
                        stats['biggest_pot_won'] = pot_size
                else:
                    # Check if folded or lost at showdown
                    folded = any(
                        a.get('player_name') == player_name and a.get('action') == 'fold'
                        for a in actions
                    )
                    if folded:
                        outcomes.append('folded')
                        stats['total_winnings'] -= amount_bet
                    else:
                        # Lost at showdown
                        outcomes.append('lost')
                        stats['total_winnings'] -= amount_bet
                        if pot_size > stats['biggest_pot_lost']:
                            stats['biggest_pot_lost'] = pot_size

                # Build recent hand summary (keep last 5)
                if len(stats['recent_hands']) >= 5:
                    stats['recent_hands'].pop(0)

                outcome_str = outcomes[-1]
                if outcome_str == 'won':
                    summary = f"Hand {row['hand_number']}: Won ${amount_won}"
                elif outcome_str == 'folded':
                    summary = f"Hand {row['hand_number']}: Folded"
                else:
                    summary = f"Hand {row['hand_number']}: Lost ${amount_bet}"
                stats['recent_hands'].append(summary)

            # Calculate current streak from outcomes
            if outcomes:
                current = outcomes[-1]
                if current in ('won', 'lost'):
                    streak_type = 'winning' if current == 'won' else 'losing'
                    streak_count = 1
                    for outcome in reversed(outcomes[:-1]):
                        if (streak_type == 'winning' and outcome == 'won') or \
                           (streak_type == 'losing' and outcome == 'lost'):
                            streak_count += 1
                        else:
                            break
                    stats['current_streak'] = streak_type
                    stats['streak_count'] = streak_count

        return stats

    def get_session_context_for_prompt(self, game_id: str, player_name: str,
                                        max_recent: int = 3) -> str:
        """Get formatted session context string for AI prompts.

        Args:
            game_id: The game identifier
            player_name: The player to get context for
            max_recent: Maximum number of recent hands to include

        Returns:
            Formatted string suitable for injection into AI prompts
        """
        stats = self.get_session_stats(game_id, player_name)

        parts = []

        # Session overview
        if stats['hands_played'] > 0:
            win_rate = (stats['hands_won'] / stats['hands_played']) * 100
            parts.append(f"Session: {stats['hands_won']}/{stats['hands_played']} hands won ({win_rate:.0f}%)")

            # Net result
            if stats['total_winnings'] > 0:
                parts.append(f"Up ${stats['total_winnings']}")
            elif stats['total_winnings'] < 0:
                parts.append(f"Down ${abs(stats['total_winnings'])}")

            # Current streak (only show if 2+)
            if stats['streak_count'] >= 2:
                parts.append(f"On a {stats['streak_count']}-hand {stats['current_streak']} streak")

        # Recent hands
        recent = stats['recent_hands'][-max_recent:]
        if recent:
            parts.append("Recent: " + " | ".join(recent))

        return ". ".join(parts) if parts else ""

    # Tournament Results Persistence Methods
    def save_tournament_result(self, game_id: str, result: Dict[str, Any]) -> None:
        """Save tournament result when game completes.

        Args:
            game_id: The game identifier
            result: Dict with keys: winner_name, total_hands, biggest_pot,
                   starting_player_count, human_player_name, human_finishing_position,
                   started_at, standings (list of player standings),
                   owner_id (optional, human player's auth identity)
        """
        owner_id = result.get('owner_id')

        with self._get_connection() as conn:
            # Save main tournament result
            conn.execute("""
                INSERT OR REPLACE INTO tournament_results
                (game_id, winner_name, total_hands, biggest_pot, starting_player_count,
                 human_player_name, human_finishing_position, started_at, ended_at, human_owner_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """, (
                game_id,
                result.get('winner_name'),
                result.get('total_hands', 0),
                result.get('biggest_pot', 0),
                result.get('starting_player_count'),
                result.get('human_player_name'),
                result.get('human_finishing_position'),
                result.get('started_at'),
                owner_id
            ))

            # Save individual standings
            standings = result.get('standings', [])
            for standing in standings:
                # Set owner_id on the human player's standing row
                standing_owner_id = owner_id if standing.get('is_human') else None
                conn.execute("""
                    INSERT OR REPLACE INTO tournament_standings
                    (game_id, player_name, is_human, finishing_position,
                     eliminated_by, eliminated_at_hand, final_stack, hands_won, hands_played,
                     times_eliminated, all_in_wins, all_in_losses, owner_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    game_id,
                    standing.get('player_name'),
                    standing.get('is_human', False),
                    standing.get('finishing_position'),
                    standing.get('eliminated_by'),
                    standing.get('eliminated_at_hand'),
                    standing.get('final_stack'),
                    standing.get('hands_won'),
                    standing.get('hands_played'),
                    standing.get('times_eliminated', 0),
                    standing.get('all_in_wins', 0),
                    standing.get('all_in_losses', 0),
                    standing_owner_id,
                ))

    def get_tournament_result(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament result for a completed game."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Get main result
            cursor = conn.execute("""
                SELECT * FROM tournament_results WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()

            if not row:
                return None

            # Get standings
            standings_cursor = conn.execute("""
                SELECT * FROM tournament_standings
                WHERE game_id = ?
                ORDER BY finishing_position ASC
            """, (game_id,))

            standings = []
            for s_row in standings_cursor.fetchall():
                standings.append({
                    'player_name': s_row['player_name'],
                    'is_human': bool(s_row['is_human']),
                    'finishing_position': s_row['finishing_position'],
                    'eliminated_by': s_row['eliminated_by'],
                    'eliminated_at_hand': s_row['eliminated_at_hand']
                })

            return {
                'game_id': row['game_id'],
                'winner_name': row['winner_name'],
                'total_hands': row['total_hands'],
                'biggest_pot': row['biggest_pot'],
                'starting_player_count': row['starting_player_count'],
                'human_player_name': row['human_player_name'],
                'human_finishing_position': row['human_finishing_position'],
                'started_at': row['started_at'],
                'ended_at': row['ended_at'],
                'standings': standings
            }

    def update_career_stats(self, owner_id: str, player_name: str, tournament_result: Dict[str, Any]) -> None:
        """Update career stats for a player after a tournament.

        Args:
            owner_id: The user's auth identity (e.g., 'guest_jeff' or Google ID)
            player_name: The human player's display name
            tournament_result: Dict with tournament result data
        """
        # Find the player's standing in this tournament
        standings = tournament_result.get('standings', [])
        player_standing = next(
            (s for s in standings if s.get('player_name') == player_name),
            None
        )

        if not player_standing:
            logger.warning(f"Player {player_name} not found in tournament standings")
            return

        finishing_position = player_standing.get('finishing_position', 0)
        is_winner = finishing_position == 1

        # Count eliminations by this player
        eliminations_this_game = sum(
            1 for s in standings
            if s.get('eliminated_by') == player_name
        )

        biggest_pot = tournament_result.get('biggest_pot', 0)

        with self._get_connection() as conn:
            # Look up by owner_id first, fall back to player_name for legacy data
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE owner_id = ?
            """, (owner_id,))
            row = cursor.fetchone()

            if not row:
                # Try legacy lookup by player_name (for pre-migration data)
                cursor = conn.execute("""
                    SELECT * FROM player_career_stats WHERE player_name = ? AND owner_id IS NULL
                """, (player_name,))
                row = cursor.fetchone()

            if row:
                # Update existing stats
                games_played = row['games_played'] + 1
                games_won = row['games_won'] + (1 if is_winner else 0)
                total_eliminations = row['total_eliminations'] + eliminations_this_game

                # Update best/worst finish
                best_finish = row['best_finish']
                if best_finish is None or finishing_position < best_finish:
                    best_finish = finishing_position

                worst_finish = row['worst_finish']
                if worst_finish is None or finishing_position > worst_finish:
                    worst_finish = finishing_position

                # Calculate new average
                old_avg = row['avg_finish'] or finishing_position
                avg_finish = ((old_avg * (games_played - 1)) + finishing_position) / games_played

                # Update biggest pot
                biggest_pot_ever = max(row['biggest_pot_ever'] or 0, biggest_pot)

                conn.execute("""
                    UPDATE player_career_stats
                    SET games_played = ?,
                        games_won = ?,
                        total_eliminations = ?,
                        best_finish = ?,
                        worst_finish = ?,
                        avg_finish = ?,
                        biggest_pot_ever = ?,
                        owner_id = ?,
                        player_name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    games_played, games_won, total_eliminations,
                    best_finish, worst_finish, avg_finish, biggest_pot_ever,
                    owner_id, player_name,
                    row['id']
                ))
            else:
                # Insert new player
                conn.execute("""
                    INSERT INTO player_career_stats
                    (player_name, owner_id, games_played, games_won, total_eliminations,
                     best_finish, worst_finish, avg_finish, biggest_pot_ever)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                """, (
                    player_name,
                    owner_id,
                    1 if is_winner else 0,
                    eliminations_this_game,
                    finishing_position,
                    finishing_position,
                    float(finishing_position),
                    biggest_pot
                ))

    def get_career_stats(self, owner_id: str) -> Optional[Dict[str, Any]]:
        """Get career stats for a player by owner_id."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE owner_id = ?
            """, (owner_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'player_name': row['player_name'],
                'games_played': row['games_played'],
                'games_won': row['games_won'],
                'total_eliminations': row['total_eliminations'],
                'best_finish': row['best_finish'],
                'worst_finish': row['worst_finish'],
                'avg_finish': row['avg_finish'],
                'biggest_pot_ever': row['biggest_pot_ever'],
                'win_rate': row['games_won'] / row['games_played'] if row['games_played'] > 0 else 0
            }

    def get_tournament_history(self, owner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get tournament history for a player by owner_id."""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT tr.*, ts.finishing_position, ts.eliminated_by
                FROM tournament_results tr
                JOIN tournament_standings ts ON tr.game_id = ts.game_id
                WHERE ts.owner_id = ?
                ORDER BY tr.ended_at DESC
                LIMIT ?
            """, (owner_id, limit))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'game_id': row['game_id'],
                    'winner_name': row['winner_name'],
                    'total_hands': row['total_hands'],
                    'biggest_pot': row['biggest_pot'],
                    'player_count': row['starting_player_count'],
                    'your_position': row['finishing_position'],
                    'eliminated_by': row['eliminated_by'],
                    'ended_at': row['ended_at']
                })

            return history

    def get_eliminated_personalities(self, owner_id: str) -> List[Dict[str, Any]]:
        """Get all unique personalities eliminated by this player across all games.

        Uses owner_id to find the human player's names, then looks for AI players
        eliminated by any of those names.

        Returns a list of personalities with the first time they were eliminated.
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            # Get unique personalities eliminated by this player, with first elimination date.
            # The eliminated_by column stores player_name, so we find all names associated
            # with this owner_id via tournament_standings, then match.
            cursor = conn.execute("""
                SELECT
                    ts.player_name as personality_name,
                    MIN(tr.ended_at) as first_eliminated_at,
                    COUNT(*) as times_eliminated
                FROM tournament_standings ts
                JOIN tournament_results tr ON ts.game_id = tr.game_id
                WHERE ts.eliminated_by IN (
                    SELECT DISTINCT player_name FROM tournament_standings WHERE owner_id = ?
                ) AND ts.is_human = 0
                GROUP BY ts.player_name
                ORDER BY MIN(tr.ended_at) ASC
            """, (owner_id,))

            personalities = []
            for row in cursor.fetchall():
                personalities.append({
                    'name': row['personality_name'],
                    'first_eliminated_at': row['first_eliminated_at'],
                    'times_eliminated': row['times_eliminated']
                })

            return personalities

    # Guest Usage Tracking Methods
    def increment_hands_played(self, tracking_id: str) -> int:
        """Increment hands played for a guest tracking ID."""
        return self._guest_tracking_repo.increment_hands_played(tracking_id)

    def get_hands_played(self, tracking_id: str) -> int:
        """Get the number of hands played for a guest tracking ID."""
        return self._guest_tracking_repo.get_hands_played(tracking_id)

    # Avatar Image Persistence Methods
    def save_avatar_image(self, personality_name: str, emotion: str,
                          image_data: bytes, width: int = 256, height: int = 256,
                          content_type: str = 'image/png',
                          full_image_data: Optional[bytes] = None,
                          full_width: Optional[int] = None,
                          full_height: Optional[int] = None) -> None:
        """Save an avatar image to the database."""
        return self._personality_repo.save_avatar_image(
            personality_name, emotion, image_data, width, height,
            content_type, full_image_data, full_width, full_height)

    def load_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load avatar image data from database."""
        return self._personality_repo.load_avatar_image(personality_name, emotion)

    def load_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load avatar image with metadata from database."""
        return self._personality_repo.load_avatar_image_with_metadata(personality_name, emotion)

    def load_full_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load full uncropped avatar image from database."""
        return self._personality_repo.load_full_avatar_image(personality_name, emotion)

    def load_full_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load full avatar image with metadata from database."""
        return self._personality_repo.load_full_avatar_image_with_metadata(personality_name, emotion)

    def has_full_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if a full avatar image exists for the given personality and emotion."""
        return self._personality_repo.has_full_avatar_image(personality_name, emotion)

    def has_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the given personality and emotion."""
        return self._personality_repo.has_avatar_image(personality_name, emotion)

    def get_available_avatar_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have avatar images for a personality."""
        return self._personality_repo.get_available_avatar_emotions(personality_name)

    def has_all_avatar_emotions(self, personality_name: str) -> bool:
        """Check if a personality has all 6 emotion avatars."""
        return self._personality_repo.has_all_avatar_emotions(personality_name)

    def delete_avatar_images(self, personality_name: str) -> int:
        """Delete all avatar images for a personality."""
        return self._personality_repo.delete_avatar_images(personality_name)

    def list_personalities_with_avatars(self) -> List[Dict[str, Any]]:
        """Get list of all personalities that have at least one avatar image."""
        return self._personality_repo.list_personalities_with_avatars()

    def get_avatar_stats(self) -> Dict[str, Any]:
        """Get statistics about avatar images in the database."""
        return self._personality_repo.get_avatar_stats()

    def seed_personalities_from_json(self, json_path: str, overwrite: bool = False) -> Dict[str, int]:
        """Seed database with personalities from JSON file."""
        return self._personality_repo.seed_personalities_from_json(json_path, overwrite)

    # Prompt Capture Methods (for AI decision debugging)
    def save_prompt_capture(self, capture: Dict[str, Any]) -> int:
        """Save a prompt capture for debugging AI decisions.

        Args:
            capture: Dict containing capture data with keys:
                - game_id, player_name, hand_number, phase
                - system_prompt, user_message, ai_response
                - pot_total, cost_to_call, pot_odds, player_stack
                - community_cards, player_hand, valid_actions
                - action_taken, raise_amount
                - model, latency_ms, input_tokens, output_tokens

        Returns:
            The ID of the inserted capture.
        """
        return self._experiment_repo.save_prompt_capture(capture)

    def get_prompt_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get a single prompt capture by ID.

        Joins with api_usage to get cached_tokens, reasoning_tokens, and estimated_cost.
        """
        return self._experiment_repo.get_prompt_capture(capture_id)

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        tags: Optional[List[str]] = None,
        call_type: Optional[str] = None,
        error_type: Optional[str] = None,
        has_error: Optional[bool] = None,
        is_correction: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List prompt captures with optional filtering.

        Args:
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)

        Returns:
            Dict with 'captures' list and 'total' count.
        """
        return self._experiment_repo.list_prompt_captures(
            game_id=game_id, player_name=player_name, action=action,
            phase=phase, min_pot_odds=min_pot_odds, max_pot_odds=max_pot_odds,
            tags=tags, call_type=call_type, error_type=error_type,
            has_error=has_error, is_correction=is_correction,
            limit=limit, offset=offset
        )

    def get_prompt_capture_stats(
        self,
        game_id: Optional[str] = None,
        call_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get aggregate statistics for prompt captures."""
        return self._experiment_repo.get_prompt_capture_stats(game_id=game_id, call_type=call_type)

    def update_prompt_capture_tags(
        self,
        capture_id: int,
        tags: List[str],
        notes: Optional[str] = None
    ) -> bool:
        """Update tags and notes for a prompt capture."""
        return self._experiment_repo.update_prompt_capture_tags(capture_id, tags, notes)

    def delete_prompt_captures(self, game_id: Optional[str] = None, before_date: Optional[str] = None) -> int:
        """Delete prompt captures, optionally filtered by game or date.

        Args:
            game_id: Delete captures for a specific game
            before_date: Delete captures before this date (ISO format)

        Returns:
            Number of captures deleted.
        """
        return self._experiment_repo.delete_prompt_captures(game_id=game_id, before_date=before_date)

    # ========== Playground Capture Methods ==========

    def list_playground_captures(
        self,
        call_type: Optional[str] = None,
        provider: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List captures for the playground (filtered by call_type).

        This method is similar to list_prompt_captures but focuses on
        non-game captures identified by call_type.

        Args:
            call_type: Filter by call type (e.g., 'commentary', 'personality_generation')
            provider: Filter by LLM provider
            limit: Max results to return
            offset: Pagination offset
            date_from: Filter by start date (ISO format)
            date_to: Filter by end date (ISO format)

        Returns:
            Dict with 'captures' list and 'total' count
        """
        return self._experiment_repo.list_playground_captures(
            call_type=call_type, provider=provider, limit=limit,
            offset=offset, date_from=date_from, date_to=date_to
        )

    def get_playground_capture_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics for all prompt captures."""
        return self._experiment_repo.get_playground_capture_stats()

    def cleanup_old_captures(self, retention_days: int) -> int:
        """Delete captures older than the retention period.

        Args:
            retention_days: Delete captures older than this many days.
                           If 0, no deletion occurs (unlimited retention).

        Returns:
            Number of captures deleted.
        """
        return self._experiment_repo.cleanup_old_captures(retention_days)

    # ========== Decision Analysis Methods ==========

    def save_decision_analysis(self, analysis) -> int:
        """Save a decision analysis to the database.

        Args:
            analysis: DecisionAnalysis dataclass or dict with analysis data

        Returns:
            The ID of the inserted row.
        """
        return self._experiment_repo.save_decision_analysis(analysis)

    def get_decision_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        """Get a single decision analysis by ID."""
        return self._experiment_repo.get_decision_analysis(analysis_id)

    def get_decision_analysis_by_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get decision analysis by api_usage request_id."""
        return self._experiment_repo.get_decision_analysis_by_request(request_id)

    def get_decision_analysis_by_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get decision analysis linked to a prompt capture.

        Links via capture_id (preferred) or request_id (fallback).
        Note: request_id fallback only works when request_id is non-empty,
        as some providers (Google/Gemini) don't return request IDs.
        """
        return self._experiment_repo.get_decision_analysis_by_capture(capture_id)

    def list_decision_analyses(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        decision_quality: Optional[str] = None,
        min_ev_lost: Optional[float] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List decision analyses with optional filtering.

        Returns:
            Dict with 'analyses' list and 'total' count.
        """
        return self._experiment_repo.list_decision_analyses(
            game_id=game_id, player_name=player_name,
            decision_quality=decision_quality, min_ev_lost=min_ev_lost,
            limit=limit, offset=offset
        )

    def get_decision_analysis_stats(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregate statistics for decision analyses.

        Args:
            game_id: Optional filter by game

        Returns:
            Dict with aggregate stats including:
            - total: Total number of analyses
            - by_quality: Count by decision quality
            - by_action: Count by action taken
            - total_ev_lost: Sum of EV lost
            - avg_equity: Average equity across decisions
            - avg_processing_ms: Average processing time
        """
        return self._experiment_repo.get_decision_analysis_stats(game_id=game_id)

    # ==================== Experiment Methods ====================

    def create_experiment(self, config: Dict, parent_experiment_id: Optional[int] = None) -> int:
        """Create a new experiment record."""
        return self._experiment_repo.create_experiment(config, parent_experiment_id)

    def link_game_to_experiment(
        self,
        experiment_id: int,
        game_id: str,
        variant: Optional[str] = None,
        variant_config: Optional[Dict] = None,
        tournament_number: Optional[int] = None
    ) -> int:
        """Link a game to an experiment."""
        return self._experiment_repo.link_game_to_experiment(
            experiment_id, game_id, variant=variant,
            variant_config=variant_config, tournament_number=tournament_number
        )

    def complete_experiment(self, experiment_id: int, summary: Optional[Dict] = None) -> None:
        """Mark an experiment as completed and store summary."""
        return self._experiment_repo.complete_experiment(experiment_id, summary)

    def get_experiment(self, experiment_id: int) -> Optional[Dict]:
        """Get experiment details by ID."""
        return self._experiment_repo.get_experiment(experiment_id)

    def get_experiment_by_name(self, name: str) -> Optional[Dict]:
        """Get experiment details by name."""
        return self._experiment_repo.get_experiment_by_name(name)

    def get_experiment_games(self, experiment_id: int) -> List[Dict]:
        """Get all games linked to an experiment."""
        return self._experiment_repo.get_experiment_games(experiment_id)

    def update_experiment_game_heartbeat(
        self,
        game_id: str,
        state: str,
        api_call_started: bool = False,
        process_id: Optional[int] = None
    ) -> None:
        """Update heartbeat for an experiment game."""
        return self._experiment_repo.update_experiment_game_heartbeat(
            game_id, state, api_call_started=api_call_started, process_id=process_id
        )

    def get_stalled_variants(
        self,
        experiment_id: int,
        threshold_minutes: int = 5
    ) -> List[Dict]:
        """Get variants that appear to be stalled."""
        return self._experiment_repo.get_stalled_variants(experiment_id, threshold_minutes)

    def acquire_resume_lock(self, experiment_game_id: int) -> bool:
        """Attempt to acquire a resume lock on an experiment game."""
        return self._experiment_repo.acquire_resume_lock(experiment_game_id)

    def release_resume_lock(self, game_id: str) -> None:
        """Release the resume lock for a game."""
        return self._experiment_repo.release_resume_lock(game_id)

    def release_resume_lock_by_id(self, experiment_game_id: int) -> None:
        """Release the resume lock by experiment_games.id."""
        return self._experiment_repo.release_resume_lock_by_id(experiment_game_id)

    def check_resume_lock_superseded(self, game_id: str) -> bool:
        """Check if this process has been superseded by a resume."""
        return self._experiment_repo.check_resume_lock_superseded(game_id)

    def get_experiment_decision_stats(
        self,
        experiment_id: int,
        variant: Optional[str] = None
    ) -> Dict:
        """Get aggregated decision analysis stats for an experiment."""
        return self._experiment_repo.get_experiment_decision_stats(experiment_id, variant)

    def list_experiments(
        self,
        status: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """List experiments with optional status filter.

        Args:
            status: Optional status filter ('pending', 'running', 'completed', 'failed')
            include_archived: If False (default), filter out experiments with _archived tag
            limit: Maximum number of experiments to return
            offset: Number of experiments to skip for pagination

        Returns:
            List of experiment dictionaries with basic info and progress
        """
        return self._experiment_repo.list_experiments(status=status, include_archived=include_archived, limit=limit, offset=offset)

    def update_experiment_status(
        self,
        experiment_id: int,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """Update experiment status.

        Args:
            experiment_id: The experiment ID
            status: New status ('pending', 'running', 'completed', 'failed')
            error_message: Optional error message if status is 'failed'
        """
        return self._experiment_repo.update_experiment_status(experiment_id, status, error_message)

    def update_experiment_tags(self, experiment_id: int, tags: List[str]) -> None:
        """Update experiment tags.

        Args:
            experiment_id: The experiment ID
            tags: List of tags to set (replaces existing tags)
        """
        return self._experiment_repo.update_experiment_tags(experiment_id, tags)

    def mark_running_experiments_interrupted(self) -> int:
        """Mark all 'running' experiments as 'interrupted'.

        Called on startup to handle experiments that were running when the
        server was stopped. Users can manually resume these experiments.

        Returns:
            Number of experiments marked as interrupted.
        """
        return self._experiment_repo.mark_running_experiments_interrupted()

    def get_incomplete_tournaments(self, experiment_id: int) -> List[Dict]:
        """Get game_ids for tournaments that haven't completed (no tournament_results entry).

        Used when resuming a paused experiment to identify which tournaments need to continue.

        Args:
            experiment_id: The experiment ID to check

        Returns:
            List of dicts with game info for incomplete tournaments:
            [{'game_id': str, 'variant': str|None, 'variant_config': dict|None, 'tournament_number': int}]
        """
        return self._experiment_repo.get_incomplete_tournaments(experiment_id)

    def save_chat_session(
        self,
        session_id: str,
        owner_id: str,
        messages: List[Dict],
        config_snapshot: Dict,
        config_versions: Optional[List[Dict]] = None
    ) -> None:
        """Save or update a chat session.

        Args:
            session_id: Unique session identifier
            owner_id: User/owner identifier
            messages: List of chat messages [{role, content, configDiff?}]
            config_snapshot: Current config state
            config_versions: List of config version snapshots
        """
        return self._experiment_repo.save_chat_session(session_id, owner_id, messages, config_snapshot, config_versions)

    def get_chat_session(self, session_id: str) -> Optional[Dict]:
        """Get a chat session by its ID.

        Args:
            session_id: The session ID to retrieve

        Returns:
            Dict with session data or None if not found:
            {
                'session_id': str,
                'messages': List[Dict],
                'config': Dict,
                'config_versions': List[Dict] | None,
                'updated_at': str
            }
        """
        return self._experiment_repo.get_chat_session(session_id)

    def get_latest_chat_session(self, owner_id: str) -> Optional[Dict]:
        """Get the most recent non-archived chat session for an owner.

        Args:
            owner_id: User/owner identifier

        Returns:
            Dict with session data or None if no session exists:
            {
                'session_id': str,
                'messages': List[Dict],
                'config': Dict,
                'config_versions': List[Dict] | None,
                'updated_at': str
            }
        """
        return self._experiment_repo.get_latest_chat_session(owner_id)

    def archive_chat_session(self, session_id: str) -> None:
        """Archive a chat session so it won't be returned by get_latest_chat_session.

        Args:
            session_id: The session ID to archive
        """
        return self._experiment_repo.archive_chat_session(session_id)

    def delete_chat_session(self, session_id: str) -> None:
        """Delete a chat session entirely.

        Args:
            session_id: The session ID to delete
        """
        return self._experiment_repo.delete_chat_session(session_id)

    def save_experiment_design_chat(self, experiment_id: int, chat_history: List[Dict]) -> None:
        """Store the design chat history with an experiment.

        Called when an experiment is created to preserve the conversation that led to its design.

        Args:
            experiment_id: The experiment ID
            chat_history: List of chat messages from the design session
        """
        return self._experiment_repo.save_experiment_design_chat(experiment_id, chat_history)

    def get_experiment_design_chat(self, experiment_id: int) -> Optional[List[Dict]]:
        """Get the design chat history for an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of chat messages or None if no design chat stored
        """
        return self._experiment_repo.get_experiment_design_chat(experiment_id)

    def save_experiment_assistant_chat(self, experiment_id: int, chat_history: List[Dict]) -> None:
        """Store the ongoing assistant chat history for an experiment.

        Used for the experiment-scoped assistant that can query results and answer questions.

        Args:
            experiment_id: The experiment ID
            chat_history: List of chat messages from the assistant session
        """
        return self._experiment_repo.save_experiment_assistant_chat(experiment_id, chat_history)

    def get_experiment_assistant_chat(self, experiment_id: int) -> Optional[List[Dict]]:
        """Get the assistant chat history for an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of chat messages or None if no assistant chat stored
        """
        return self._experiment_repo.get_experiment_assistant_chat(experiment_id)

    def get_experiment_live_stats(self, experiment_id: int) -> Dict:
        """Get real-time unified stats per variant for running/completed experiments.

        Returns all metrics per variant in one call: latency, decision quality, and progress.
        This is designed to be called on every 5s refresh for running experiments.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dictionary with structure:
            {
                'by_variant': {
                    'Variant Label': {
                        'latency_metrics': { avg_ms, p50_ms, p95_ms, p99_ms, count },
                        'decision_quality': { total, correct, correct_pct, mistakes, avg_ev_lost },
                        'progress': { current_hands, max_hands, progress_pct }
                    },
                    ...
                },
                'overall': { ... same structure ... }
            }
        """
        return self._experiment_repo.get_experiment_live_stats(experiment_id)

    def get_experiment_game_snapshots(self, experiment_id: int) -> List[Dict]:
        """Load current game states for all running games in an experiment.

        This method provides live game snapshots for the monitoring view,
        including player states, community cards, pot, and psychology data.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of dictionaries with game snapshots:
            [
                {
                    'game_id': str,
                    'variant': str | None,
                    'phase': str,
                    'hand_number': int,
                    'pot': int,
                    'community_cards': [...],
                    'players': [
                        {
                            'name': str,
                            'stack': int,
                            'bet': int,
                            'hole_cards': [...],  # Always visible
                            'is_folded': bool,
                            'is_all_in': bool,
                            'is_current': bool,
                            'psychology': {...},
                            'llm_debug': {...}
                        }
                    ]
                }
            ]
        """
        return self._experiment_repo.get_experiment_game_snapshots(experiment_id)

    def get_experiment_player_detail(
        self,
        experiment_id: int,
        game_id: str,
        player_name: str
    ) -> Optional[Dict]:
        """Get detailed player info for the drill-down panel.

        Args:
            experiment_id: The experiment ID
            game_id: The game ID
            player_name: The player name

        Returns:
            Dictionary with detailed player info or None if not found:
            {
                'player': { name, stack, cards },
                'psychology': { narrative, inner_voice, tilt_level, tilt_category, tilt_source },
                'llm_debug': { provider, model, reasoning_effort, total_calls, avg_latency_ms, avg_cost_per_call },
                'play_style': { vpip, pfr, aggression_factor, summary },
                'recent_decisions': [ { hand_number, phase, action, decision_quality, ev_lost } ]
            }
        """
        return self._experiment_repo.get_experiment_player_detail(experiment_id, game_id, player_name)

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get an app setting by key, with optional default."""
        return self._settings_repo.get_setting(key, default)

    def set_setting(self, key: str, value: str, description: Optional[str] = None) -> bool:
        """Set an app setting."""
        return self._settings_repo.set_setting(key, value, description)

    def get_all_settings(self) -> Dict[str, Dict[str, Any]]:
        """Get all app settings."""
        return self._settings_repo.get_all_settings()

    def delete_setting(self, key: str) -> bool:
        """Delete an app setting."""
        return self._settings_repo.delete_setting(key)

    # ========================================
    # Prompt Preset Methods (v47)
    # ========================================

    def create_prompt_preset(
        self,
        name: str,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None,
        owner_id: Optional[str] = None
    ) -> int:
        """Create a new prompt preset.

        Args:
            name: Unique name for the preset
            description: Optional description of the preset
            prompt_config: PromptConfig toggles as dict
            guidance_injection: Extra guidance text to append to prompts
            owner_id: Optional owner ID for multi-tenant support

        Returns:
            The ID of the created preset

        Raises:
            ValueError: If a preset with the same name already exists
        """
        return self._experiment_repo.create_prompt_preset(
            name, description=description, prompt_config=prompt_config,
            guidance_injection=guidance_injection, owner_id=owner_id
        )

    def get_prompt_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        """Get a prompt preset by ID.

        Args:
            preset_id: The preset ID

        Returns:
            Preset data as dict, or None if not found
        """
        return self._experiment_repo.get_prompt_preset(preset_id)

    def get_prompt_preset_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a prompt preset by name.

        Args:
            name: The preset name

        Returns:
            Preset data as dict, or None if not found
        """
        return self._experiment_repo.get_prompt_preset_by_name(name)

    def list_prompt_presets(
        self,
        owner_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List all prompt presets.

        Args:
            owner_id: Optional filter by owner ID
            limit: Maximum number of results

        Returns:
            List of preset data dicts
        """
        return self._experiment_repo.list_prompt_presets(owner_id=owner_id, limit=limit)

    def update_prompt_preset(
        self,
        preset_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None
    ) -> bool:
        """Update a prompt preset.

        Args:
            preset_id: The preset ID to update
            name: Optional new name
            description: Optional new description
            prompt_config: Optional new prompt config
            guidance_injection: Optional new guidance text

        Returns:
            True if the preset was updated, False if not found

        Raises:
            ValueError: If the new name conflicts with an existing preset
        """
        return self._experiment_repo.update_prompt_preset(
            preset_id, name=name, description=description,
            prompt_config=prompt_config, guidance_injection=guidance_injection
        )

    def delete_prompt_preset(self, preset_id: int) -> bool:
        """Delete a prompt preset.

        Args:
            preset_id: The preset ID to delete

        Returns:
            True if the preset was deleted, False if not found
        """
        return self._experiment_repo.delete_prompt_preset(preset_id)

    # ==================== Capture Labels Methods ====================
    # These methods support labeling/tagging captured AI decisions for
    # filtering and selection in replay experiments.
    # ================================================================

    def add_capture_labels(
        self,
        capture_id: int,
        labels: List[str],
        label_type: str = 'user'
    ) -> List[str]:
        """Add labels to a captured AI decision.

        Args:
            capture_id: The prompt_captures ID
            labels: List of label strings to add
            label_type: Type of label ('user' for manual, 'smart' for auto-generated)

        Returns:
            List of labels that were actually added (excludes duplicates)
        """
        return self._experiment_repo.add_capture_labels(capture_id, labels, label_type)

    def compute_and_store_auto_labels(self, capture_id: int, capture_data: Dict[str, Any]) -> List[str]:
        """Compute auto-labels for a capture based on rules and store them.

        Labels are computed based on the capture data at capture time.
        Stored with label_type='auto' to distinguish from user-added labels.

        Args:
            capture_id: The prompt_captures ID
            capture_data: Dict containing capture fields (action_taken, pot_odds, stack_bb, already_bet_bb, etc.)

        Returns:
            List of auto-labels that were added
        """
        return self._experiment_repo.compute_and_store_auto_labels(capture_id, capture_data)

    def remove_capture_labels(
        self,
        capture_id: int,
        labels: List[str]
    ) -> int:
        """Remove labels from a captured AI decision.

        Args:
            capture_id: The prompt_captures ID
            labels: List of label strings to remove

        Returns:
            Number of labels that were removed
        """
        return self._experiment_repo.remove_capture_labels(capture_id, labels)

    def get_capture_labels(self, capture_id: int) -> List[Dict[str, Any]]:
        """Get all labels for a captured AI decision.

        Args:
            capture_id: The prompt_captures ID

        Returns:
            List of label dicts with 'label', 'label_type', 'created_at'
        """
        return self._experiment_repo.get_capture_labels(capture_id)

    def list_all_labels(self, label_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all unique labels with counts.

        Args:
            label_type: Optional filter by label type ('user' or 'smart')

        Returns:
            List of dicts with 'name', 'count', 'label_type'
        """
        return self._experiment_repo.list_all_labels(label_type)

    def get_label_stats(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        call_type: Optional[str] = None
    ) -> Dict[str, int]:
        """Get label counts filtered by game_id, player_name, and/or call_type.

        Args:
            game_id: Optional filter by game
            player_name: Optional filter by player
            call_type: Optional filter by call type

        Returns:
            Dict mapping label name to count
        """
        return self._experiment_repo.get_label_stats(game_id=game_id, player_name=player_name, call_type=call_type)

    def search_captures_with_labels(
        self,
        labels: List[str],
        match_all: bool = False,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        call_type: Optional[str] = None,
        min_pot_size: Optional[float] = None,
        max_pot_size: Optional[float] = None,
        min_big_blind: Optional[float] = None,
        max_big_blind: Optional[float] = None,
        error_type: Optional[str] = None,
        has_error: Optional[bool] = None,
        is_correction: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Search captures by labels and optional filters.

        Args:
            labels: List of labels to search for
            match_all: If True, captures must have ALL labels; if False, ANY label
            game_id: Optional filter by game
            player_name: Optional filter by player
            action: Optional filter by action taken
            phase: Optional filter by game phase
            min_pot_odds: Optional minimum pot odds filter
            max_pot_odds: Optional maximum pot odds filter
            call_type: Optional filter by call type (e.g., 'player_decision')
            min_pot_size: Optional minimum pot total filter
            max_pot_size: Optional maximum pot total filter
            min_big_blind: Optional minimum big blind filter (computed from stack_bb)
            max_big_blind: Optional maximum big blind filter (computed from stack_bb)
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'captures' list and 'total' count
        """
        return self._experiment_repo.search_captures_with_labels(
            labels, match_all=match_all, game_id=game_id, player_name=player_name,
            action=action, phase=phase, min_pot_odds=min_pot_odds, max_pot_odds=max_pot_odds,
            call_type=call_type, min_pot_size=min_pot_size, max_pot_size=max_pot_size,
            min_big_blind=min_big_blind, max_big_blind=max_big_blind,
            error_type=error_type, has_error=has_error, is_correction=is_correction,
            limit=limit, offset=offset
        )

    def bulk_add_capture_labels(
        self,
        capture_ids: List[int],
        labels: List[str],
        label_type: str = 'user'
    ) -> Dict[str, int]:
        """Add labels to multiple captures at once.

        Args:
            capture_ids: List of prompt_captures IDs
            labels: Labels to add to all captures
            label_type: Type of label

        Returns:
            Dict with 'captures_affected' and 'labels_added' counts
        """
        return self._experiment_repo.bulk_add_capture_labels(capture_ids, labels, label_type)

    def bulk_remove_capture_labels(
        self,
        capture_ids: List[int],
        labels: List[str]
    ) -> Dict[str, int]:
        """Remove labels from multiple captures at once.

        Args:
            capture_ids: List of prompt_captures IDs
            labels: Labels to remove from all captures

        Returns:
            Dict with 'captures_affected' and 'labels_removed' counts
        """
        return self._experiment_repo.bulk_remove_capture_labels(capture_ids, labels)

    # ==================== Replay Experiment Methods ====================
    # These methods support replay experiments that re-run captured AI
    # decisions with different variants (models, prompts, etc.).
    # ===================================================================

    def create_replay_experiment(
        self,
        name: str,
        capture_ids: List[int],
        variants: List[Dict[str, Any]],
        description: Optional[str] = None,
        hypothesis: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_experiment_id: Optional[int] = None
    ) -> int:
        """Create a new replay experiment.

        Args:
            name: Unique experiment name
            capture_ids: List of prompt_captures IDs to replay
            variants: List of variant configurations
            description: Optional experiment description
            hypothesis: Optional hypothesis being tested
            tags: Optional list of tags
            parent_experiment_id: Optional parent for lineage tracking

        Returns:
            The experiment_id of the created record
        """
        return self._experiment_repo.create_replay_experiment(name, capture_ids, variants, description=description, hypothesis=hypothesis, tags=tags, parent_experiment_id=parent_experiment_id)

    def add_replay_result(
        self,
        experiment_id: int,
        capture_id: int,
        variant: str,
        new_response: str,
        new_action: str,
        new_raise_amount: Optional[int] = None,
        new_quality: Optional[str] = None,
        new_ev_lost: Optional[float] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> int:
        """Add a result from replaying a capture with a variant.

        Args:
            experiment_id: The experiment ID
            capture_id: The prompt_captures ID
            variant: The variant label
            new_response: The new AI response
            new_action: The new action taken
            new_raise_amount: Optional new raise amount
            new_quality: Optional quality assessment
            new_ev_lost: Optional EV lost calculation
            provider: LLM provider used
            model: Model used
            reasoning_effort: Reasoning effort setting
            input_tokens: Input token count
            output_tokens: Output token count
            latency_ms: Response latency
            error_message: Error if the replay failed

        Returns:
            The replay_results record ID
        """
        return self._experiment_repo.add_replay_result(experiment_id, capture_id, variant, new_response, new_action, new_raise_amount=new_raise_amount, new_quality=new_quality, new_ev_lost=new_ev_lost, provider=provider, model=model, reasoning_effort=reasoning_effort, input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=latency_ms, error_message=error_message)

    def get_replay_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """Get a replay experiment with its captures and progress.

        Args:
            experiment_id: The experiment ID

        Returns:
            Experiment data with capture count and result progress, or None
        """
        return self._experiment_repo.get_replay_experiment(experiment_id)

    def get_replay_results(
        self,
        experiment_id: int,
        variant: Optional[str] = None,
        quality_change: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Get replay results for an experiment.

        Args:
            experiment_id: The experiment ID
            variant: Optional filter by variant
            quality_change: Optional filter by quality change ('improved', 'degraded', 'unchanged')
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'results' list and 'total' count
        """
        return self._experiment_repo.get_replay_results(experiment_id, variant=variant, quality_change=quality_change, limit=limit, offset=offset)

    def get_replay_results_summary(self, experiment_id: int) -> Dict[str, Any]:
        """Get summary statistics for replay experiment results.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dict with summary statistics by variant
        """
        return self._experiment_repo.get_replay_results_summary(experiment_id)

    def get_replay_experiment_captures(self, experiment_id: int) -> List[Dict[str, Any]]:
        """Get the captures linked to a replay experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of capture details with original info
        """
        return self._experiment_repo.get_replay_experiment_captures(experiment_id)

    def list_replay_experiments(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List replay experiments.

        Args:
            status: Optional filter by status
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'experiments' list and 'total' count
        """
        return self._experiment_repo.list_replay_experiments(status=status, limit=limit, offset=offset)
