"""
Persistence layer for poker game using SQLite.
Handles saving and loading game states.
"""
import os
from typing import Optional, List, Dict, Any, Set
from poker.poker_game import PokerGameState
from poker.poker_state_machine import PokerStateMachine, PokerPhase
import logging

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.settings_repository import SettingsRepository
from poker.repositories.guest_tracking_repository import GuestTrackingRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.user_repository import UserRepository
from poker.repositories.experiment_repository import ExperimentRepository
from poker.repositories.game_repository import GameRepository, SavedGame
from poker.repositories.hand_history_repository import HandHistoryRepository
from poker.repositories.tournament_repository import TournamentRepository
from poker.repositories.llm_repository import LLMRepository

logger = logging.getLogger(__name__)

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

        # Initialize repositories
        self._settings_repo = SettingsRepository(self.db_path)
        self._guest_tracking_repo = GuestTrackingRepository(self.db_path)
        self._personality_repo = PersonalityRepository(self.db_path)
        self._user_repo = UserRepository(self.db_path)
        self._experiment_repo = ExperimentRepository(self.db_path)
        self._game_repo = GameRepository(self.db_path)
        self._hand_history_repo = HandHistoryRepository(self.db_path)
        self._tournament_repo = TournamentRepository(self.db_path)
        self._llm_repo = LLMRepository(self.db_path)

    def close(self):
        """Close all repository connections."""
        for repo in (self._settings_repo, self._guest_tracking_repo,
                     self._personality_repo, self._user_repo,
                     self._experiment_repo, self._game_repo,
                     self._hand_history_repo, self._tournament_repo,
                     self._llm_repo):
            repo.close()

    def save_coach_mode(self, game_id: str, mode: str) -> None:
        """Persist coach mode preference for a game."""
        return self._game_repo.save_coach_mode(game_id, mode)

    def load_coach_mode(self, game_id: str) -> str:
        """Load coach mode preference for a game. Defaults to 'off'."""
        return self._game_repo.load_coach_mode(game_id)

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
        return self._game_repo.save_game(game_id, state_machine, owner_id, owner_name, llm_configs)
    
    def load_game(self, game_id: str) -> Optional[PokerStateMachine]:
        """Load a game state from the database."""
        return self._game_repo.load_game(game_id)

    def load_llm_configs(self, game_id: str) -> Optional[Dict]:
        """Load LLM configs for a game.

        Args:
            game_id: The game identifier

        Returns:
            Dict with 'player_llm_configs' and 'default_llm_config', or None if not found
        """
        return self._game_repo.load_llm_configs(game_id)

    def save_tournament_tracker(self, game_id: str, tracker) -> None:
        """Save tournament tracker state to the database.

        Args:
            game_id: The game identifier
            tracker: TournamentTracker instance or dict from to_dict()
        """
        return self._game_repo.save_tournament_tracker(game_id, tracker)

    def load_tournament_tracker(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament tracker state from the database.

        Args:
            game_id: The game identifier

        Returns:
            Dict that can be passed to TournamentTracker.from_dict(), or None if not found
        """
        return self._game_repo.load_tournament_tracker(game_id)

    def list_games(self, owner_id: Optional[str] = None, limit: int = 20) -> List[SavedGame]:
        """List saved games, most recently updated first. Filter by owner_id if provided."""
        return self._game_repo.list_games(owner_id, limit)
    
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
        """Get the set of all providers in the system."""
        return self._llm_repo.get_available_providers()

    def get_enabled_models(self) -> Dict[str, List[str]]:
        """Get all enabled models grouped by provider."""
        return self._llm_repo.get_enabled_models()

    def get_all_enabled_models(self) -> List[Dict[str, Any]]:
        """Get all models with their enabled status."""
        return self._llm_repo.get_all_enabled_models()

    def update_model_enabled(self, model_id: int, enabled: bool) -> bool:
        """Update the enabled status of a model."""
        return self._llm_repo.update_model_enabled(model_id, enabled)

    def update_model_details(self, model_id: int, display_name: str = None, notes: str = None) -> bool:
        """Update display name and notes for a model."""
        return self._llm_repo.update_model_details(model_id, display_name, notes)

    def delete_game(self, game_id: str) -> None:
        """Delete a game and all associated data."""
        return self._game_repo.delete_game(game_id)

    def save_message(self, game_id: str, message_type: str, message_text: str) -> None:
        """Save a game message/event."""
        return self._game_repo.save_message(game_id, message_type, message_text)

    def load_messages(self, game_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Load recent messages for a game."""
        return self._game_repo.load_messages(game_id, limit)

    # AI State Persistence Methods
    def save_ai_player_state(self, game_id: str, player_name: str,
                            messages: List[Dict[str, str]],
                            personality_state: Dict[str, Any]) -> None:
        """Save AI player conversation history and personality state."""
        return self._game_repo.save_ai_player_state(game_id, player_name, messages, personality_state)

    def load_ai_player_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all AI player states for a game."""
        return self._game_repo.load_ai_player_states(game_id)

    def save_personality_snapshot(self, game_id: str, player_name: str,
                                 hand_number: int, traits: Dict[str, Any],
                                 pressure_levels: Optional[Dict[str, float]] = None) -> None:
        """Save a snapshot of personality state for elasticity tracking."""
        return self._game_repo.save_personality_snapshot(game_id, player_name, hand_number, traits, pressure_levels)
    
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
        return self._game_repo.save_emotional_state(game_id, player_name, emotional_state)

    def load_emotional_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a player.

        Returns:
            Dict suitable for EmotionalState.from_dict(), or None if not found
        """
        return self._game_repo.load_emotional_state(game_id, player_name)

    def load_all_emotional_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game.

        Returns:
            Dict mapping player_name -> emotional_state dict
        """
        return self._game_repo.load_all_emotional_states(game_id)

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
        return self._game_repo.save_controller_state(game_id, player_name, psychology, prompt_config)

    def load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player.

        Returns:
            Dict with 'tilt_state', 'elastic_personality', and 'prompt_config' keys, or None if not found
        """
        return self._game_repo.load_controller_state(game_id, player_name)

    def load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game.

        Returns:
            Dict mapping player_name -> controller state dict
        """
        return self._game_repo.load_all_controller_states(game_id)

    def delete_emotional_state_for_game(self, game_id: str) -> None:
        """Delete all emotional states for a game."""
        return self._game_repo.delete_emotional_state_for_game(game_id)

    def delete_controller_state_for_game(self, game_id: str) -> None:
        """Delete all controller states for a game."""
        return self._game_repo.delete_controller_state_for_game(game_id)

    # Opponent Model Persistence Methods
    def save_opponent_models(self, game_id: str, opponent_model_manager) -> None:
        """Save opponent models for a game.

        Args:
            game_id: The game identifier
            opponent_model_manager: OpponentModelManager instance or dict from to_dict()
        """
        return self._game_repo.save_opponent_models(game_id, opponent_model_manager)

    def load_opponent_models(self, game_id: str) -> Dict[str, Any]:
        """Load opponent models for a game.

        Returns:
            Dict suitable for OpponentModelManager.from_dict(), or empty dict if not found
        """
        return self._game_repo.load_opponent_models(game_id)

    def delete_opponent_models_for_game(self, game_id: str) -> None:
        """Delete all opponent models for a game."""
        return self._game_repo.delete_opponent_models_for_game(game_id)

    # Hand History Persistence Methods
    def save_hand_history(self, recorded_hand) -> int:
        """Save a completed hand to the database."""
        return self._hand_history_repo.save_hand_history(recorded_hand)

    def save_hand_commentary(self, game_id: str, hand_number: int, player_name: str,
                             commentary) -> None:
        """Save AI commentary for a completed hand."""
        return self._hand_history_repo.save_hand_commentary(game_id, hand_number, player_name, commentary)

    def get_recent_reflections(self, game_id: str, player_name: str,
                               limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent strategic reflections for a player."""
        return self._hand_history_repo.get_recent_reflections(game_id, player_name, limit)

    def get_hand_count(self, game_id: str) -> int:
        """Get the current hand count for a game."""
        return self._hand_history_repo.get_hand_count(game_id)

    def load_hand_history(self, game_id: str, limit: int = None) -> List[Dict[str, Any]]:
        """Load hand history for a game."""
        return self._hand_history_repo.load_hand_history(game_id, limit)

    def delete_hand_history_for_game(self, game_id: str) -> None:
        """Delete all hand history for a game."""
        return self._hand_history_repo.delete_hand_history_for_game(game_id)

    def get_session_stats(self, game_id: str, player_name: str) -> Dict[str, Any]:
        """Compute session statistics for a player from hand history."""
        return self._hand_history_repo.get_session_stats(game_id, player_name)

    def get_session_context_for_prompt(self, game_id: str, player_name: str,
                                        max_recent: int = 3) -> str:
        """Get formatted session context string for AI prompts."""
        return self._hand_history_repo.get_session_context_for_prompt(game_id, player_name, max_recent)

    # Tournament Results Persistence Methods
    def save_tournament_result(self, game_id: str, result: Dict[str, Any]) -> None:
        """Save tournament result when game completes."""
        return self._tournament_repo.save_tournament_result(game_id, result)

    def get_tournament_result(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament result for a completed game."""
        return self._tournament_repo.get_tournament_result(game_id)

    def update_career_stats(self, owner_id: str, player_name: str, tournament_result: Dict[str, Any]) -> None:
        """Update career stats for a player after a tournament."""
        return self._tournament_repo.update_career_stats(owner_id, player_name, tournament_result)

    def get_career_stats(self, owner_id: str) -> Optional[Dict[str, Any]]:
        """Get career stats for a player by owner_id."""
        return self._tournament_repo.get_career_stats(owner_id)

    def get_tournament_history(self, owner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get tournament history for a player by owner_id."""
        return self._tournament_repo.get_tournament_history(owner_id, limit)

    def get_eliminated_personalities(self, owner_id: str) -> List[Dict[str, Any]]:
        """Get all unique personalities eliminated by this player across all games."""
        return self._tournament_repo.get_eliminated_personalities(owner_id)

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
