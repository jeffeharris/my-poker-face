"""Repository pattern implementations for poker game persistence.

This package provides domain-specific repository classes that replace
the former GamePersistence facade.
"""

import os

from .sqlite_repositories import PressureEventRepository
from .base_repository import BaseRepository
from .schema_manager import SchemaManager
from .settings_repository import SettingsRepository
from .guest_tracking_repository import GuestTrackingRepository
from .personality_repository import PersonalityRepository
from .user_repository import UserRepository
from .experiment_repository import ExperimentRepository
from .game_repository import GameRepository, SavedGame
from .hand_history_repository import HandHistoryRepository
from .tournament_repository import TournamentRepository
from .llm_repository import LLMRepository


def create_repos(db_path: str) -> dict:
    """Create all repositories for a given db_path, ensuring schema exists.

    Returns a dict of repository instances keyed by role name.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    SchemaManager(db_path).ensure_schema()
    game_repo = GameRepository(db_path)
    return {
        'game_repo': game_repo,
        'user_repo': UserRepository(db_path),
        'settings_repo': SettingsRepository(db_path),
        'personality_repo': PersonalityRepository(db_path),
        'experiment_repo': ExperimentRepository(db_path, game_repo=game_repo),
        'hand_history_repo': HandHistoryRepository(db_path),
        'tournament_repo': TournamentRepository(db_path),
        'llm_repo': LLMRepository(db_path),
        'guest_tracking_repo': GuestTrackingRepository(db_path),
        'db_path': db_path,
    }


__all__ = [
    'BaseRepository',
    'ExperimentRepository',
    'GameRepository',
    'GuestTrackingRepository',
    'HandHistoryRepository',
    'LLMRepository',
    'PersonalityRepository',
    'PressureEventRepository',
    'SavedGame',
    'SchemaManager',
    'SettingsRepository',
    'TournamentRepository',
    'UserRepository',
    'create_repos',
]
