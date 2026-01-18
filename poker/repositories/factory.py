"""
Repository factory for dependency injection.
"""
from typing import Optional
from .database import DatabaseContext
from .protocols import (
    GameRepositoryProtocol,
    MessageRepositoryProtocol,
    AIMemoryRepositoryProtocol,
    EmotionalStateRepositoryProtocol,
    PersonalityRepositoryProtocol,
    HandHistoryRepositoryProtocol,
    TournamentRepositoryProtocol,
    LLMTrackingRepositoryProtocol,
    DebugRepositoryProtocol,
    ExperimentRepositoryProtocol,
    ConfigRepositoryProtocol,
)


class RepositoryFactory:
    """Factory for creating repository instances with shared database context."""

    def __init__(self, db_path: str, initialize_schema: bool = False):
        """
        Initialize the factory with a database path.

        Args:
            db_path: Path to the SQLite database file.
            initialize_schema: If True, create tables from schema files.
        """
        self._db = DatabaseContext(db_path)
        self._db_path = db_path

        if initialize_schema:
            self._db.initialize_schema()

        # Cache repository instances
        self._game_repo: Optional[GameRepositoryProtocol] = None
        self._message_repo: Optional[MessageRepositoryProtocol] = None
        self._ai_memory_repo: Optional[AIMemoryRepositoryProtocol] = None
        self._emotional_state_repo: Optional[EmotionalStateRepositoryProtocol] = None
        self._personality_repo: Optional[PersonalityRepositoryProtocol] = None
        self._hand_history_repo: Optional[HandHistoryRepositoryProtocol] = None
        self._tournament_repo: Optional[TournamentRepositoryProtocol] = None
        self._llm_tracking_repo: Optional[LLMTrackingRepositoryProtocol] = None
        self._debug_repo: Optional[DebugRepositoryProtocol] = None
        self._experiment_repo: Optional[ExperimentRepositoryProtocol] = None
        self._config_repo: Optional[ConfigRepositoryProtocol] = None

    @property
    def db(self) -> DatabaseContext:
        """Get the database context."""
        return self._db

    @property
    def db_path(self) -> str:
        """Get the database path."""
        return self._db_path

    @property
    def game(self) -> GameRepositoryProtocol:
        """Get the game repository."""
        if self._game_repo is None:
            from .sqlite.game_repository import SQLiteGameRepository
            self._game_repo = SQLiteGameRepository(self._db)
        return self._game_repo

    @property
    def messages(self) -> MessageRepositoryProtocol:
        """Get the message repository."""
        if self._message_repo is None:
            from .sqlite.game_repository import SQLiteMessageRepository
            self._message_repo = SQLiteMessageRepository(self._db)
        return self._message_repo

    @property
    def ai_memory(self) -> AIMemoryRepositoryProtocol:
        """Get the AI memory repository."""
        if self._ai_memory_repo is None:
            from .sqlite.ai_memory_repository import SQLiteAIMemoryRepository
            self._ai_memory_repo = SQLiteAIMemoryRepository(self._db)
        return self._ai_memory_repo

    @property
    def emotional_state(self) -> EmotionalStateRepositoryProtocol:
        """Get the emotional state repository."""
        if self._emotional_state_repo is None:
            from .sqlite.emotional_state_repository import SQLiteEmotionalStateRepository
            self._emotional_state_repo = SQLiteEmotionalStateRepository(self._db)
        return self._emotional_state_repo

    @property
    def personality(self) -> PersonalityRepositoryProtocol:
        """Get the personality repository."""
        if self._personality_repo is None:
            from .sqlite.personality_repository import SQLitePersonalityRepository
            self._personality_repo = SQLitePersonalityRepository(self._db)
        return self._personality_repo

    @property
    def hand_history(self) -> HandHistoryRepositoryProtocol:
        """Get the hand history repository."""
        if self._hand_history_repo is None:
            from .sqlite.hand_history_repository import SQLiteHandHistoryRepository
            self._hand_history_repo = SQLiteHandHistoryRepository(self._db)
        return self._hand_history_repo

    @property
    def tournament(self) -> TournamentRepositoryProtocol:
        """Get the tournament repository."""
        if self._tournament_repo is None:
            from .sqlite.tournament_repository import SQLiteTournamentRepository
            self._tournament_repo = SQLiteTournamentRepository(self._db)
        return self._tournament_repo

    @property
    def llm_tracking(self) -> LLMTrackingRepositoryProtocol:
        """Get the LLM tracking repository."""
        if self._llm_tracking_repo is None:
            from .sqlite.llm_tracking_repository import SQLiteLLMTrackingRepository
            self._llm_tracking_repo = SQLiteLLMTrackingRepository(self._db)
        return self._llm_tracking_repo

    @property
    def debug(self) -> DebugRepositoryProtocol:
        """Get the debug repository."""
        if self._debug_repo is None:
            from .sqlite.debug_repository import SQLiteDebugRepository
            self._debug_repo = SQLiteDebugRepository(self._db)
        return self._debug_repo

    @property
    def experiment(self) -> ExperimentRepositoryProtocol:
        """Get the experiment repository."""
        if self._experiment_repo is None:
            from .sqlite.experiment_repository import SQLiteExperimentRepository
            self._experiment_repo = SQLiteExperimentRepository(self._db)
        return self._experiment_repo

    @property
    def config(self) -> ConfigRepositoryProtocol:
        """Get the config repository."""
        if self._config_repo is None:
            from .sqlite.config_repository import SQLiteConfigRepository
            self._config_repo = SQLiteConfigRepository(self._db)
        return self._config_repo


# Singleton instance for the default database
_default_factory: Optional[RepositoryFactory] = None


def get_repository_factory(
    db_path: str = "poker_games.db", initialize_schema: bool = False
) -> RepositoryFactory:
    """
    Get or create the default repository factory.

    Args:
        db_path: Path to the SQLite database file.
        initialize_schema: If True, create tables from schema files.

    Returns:
        The repository factory instance.
    """
    global _default_factory
    if _default_factory is None or _default_factory.db_path != db_path:
        _default_factory = RepositoryFactory(db_path, initialize_schema)
    return _default_factory


def reset_factory() -> None:
    """Reset the default factory (useful for testing)."""
    global _default_factory
    _default_factory = None
