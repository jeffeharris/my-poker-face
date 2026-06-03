"""Repository pattern implementations for poker game persistence.

This package provides domain-specific repository classes that replace
the former GamePersistence facade.
"""

import os

from .bankroll_repository import BankrollRepository
from .base_repository import BaseRepository
from .capture_label_repository import CaptureLabelRepository
from .cash_scalps_repository import CashScalpsRepository
from .cash_session_repository import CashSessionRepository
from .cash_table_repository import CashTableRepository
from .chip_ledger_repository import ChipLedgerRepository
from .coach_repository import CoachRepository
from .decision_analysis_repository import DecisionAnalysisRepository
from .entity_presence_repository import EntityPresenceRepository
from .experiment_repository import ExperimentRepository
from .game_repository import GameRepository, SavedGame
from .guest_tracking_repository import GuestTrackingRepository
from .hand_history_repository import HandHistoryRepository
from .holdings_snapshots_repository import HoldingsSnapshotsRepository
from .llm_repository import LLMRepository
from .personality_repository import PersonalityRepository
from .prestige_snapshots_repository import PrestigeSnapshotsRepository
from .prompt_capture_repository import PromptCaptureRepository
from .prompt_preset_repository import PromptPresetRepository
from .relationship_repository import RelationshipRepository
from .renown_field_repository import RenownFieldRepository
from .replay_experiment_repository import ReplayExperimentRepository
from .sandbox_repository import SandboxRepository, SandboxState
from .schema_manager import SchemaManager
from .settings_repository import SettingsRepository
from .side_hustle_state_repository import SideHustleState, SideHustleStateRepository
from .sqlite_repositories import PressureEventRepository
from .stake_repository import StakeRepository
from .tournament_repository import TournamentRepository
from .user_avatar_repository import UserAvatarRepository
from .user_preferences_repository import UserPreferencesRepository
from .user_repository import UserRepository
from .vice_state_repository import ViceState, ViceStateRepository


def create_repos(db_path: str) -> dict:
    """Create all repositories for a given db_path, ensuring schema exists.

    Returns a dict of repository instances keyed by role name.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    SchemaManager(db_path).ensure_schema()
    game_repo = GameRepository(db_path)
    prompt_capture_repo = PromptCaptureRepository(db_path)
    capture_label_repo = CaptureLabelRepository(db_path, prompt_capture_repo=prompt_capture_repo)
    bankroll_repo = BankrollRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    # D2: give the bankroll repo a ledger handle so its reads can derive from
    # the ledger (int as cache) when CHIP_CUSTODY_DERIVE_READS is on. Best-effort
    # wiring — the attribute defaults None so a repo built directly still works.
    bankroll_repo.chip_ledger_repo = chip_ledger_repo
    return {
        'game_repo': game_repo,
        'user_repo': UserRepository(db_path),
        'settings_repo': SettingsRepository(db_path),
        'personality_repo': PersonalityRepository(db_path),
        'experiment_repo': ExperimentRepository(db_path, game_repo=game_repo),
        'prompt_capture_repo': prompt_capture_repo,
        'decision_analysis_repo': DecisionAnalysisRepository(db_path),
        'prompt_preset_repo': PromptPresetRepository(db_path),
        'capture_label_repo': capture_label_repo,
        'replay_experiment_repo': ReplayExperimentRepository(db_path),
        'hand_history_repo': HandHistoryRepository(db_path),
        'tournament_repo': TournamentRepository(db_path),
        'llm_repo': LLMRepository(db_path),
        'guest_tracking_repo': GuestTrackingRepository(db_path),
        'coach_repo': CoachRepository(db_path),
        'pressure_event_repo': PressureEventRepository(db_path),
        'relationship_repo': RelationshipRepository(db_path),
        'bankroll_repo': bankroll_repo,
        'cash_table_repo': CashTableRepository(db_path),
        'chip_ledger_repo': chip_ledger_repo,
        'stake_repo': StakeRepository(db_path),
        'cash_session_repo': CashSessionRepository(db_path),
        'sandbox_repo': SandboxRepository(db_path),
        'vice_state_repo': ViceStateRepository(db_path),
        'side_hustle_state_repo': SideHustleStateRepository(db_path),
        'user_prefs_repo': UserPreferencesRepository(db_path),
        'user_avatar_repo': UserAvatarRepository(db_path),
        'holdings_snapshots_repo': HoldingsSnapshotsRepository(db_path),
        'prestige_snapshots_repo': PrestigeSnapshotsRepository(db_path),
        'cash_scalps_repo': CashScalpsRepository(db_path),
        'renown_field_repo': RenownFieldRepository(db_path),
        'entity_presence_repo': EntityPresenceRepository(db_path),
        'db_path': db_path,
    }


__all__ = [
    'BankrollRepository',
    'BaseRepository',
    'CaptureLabelRepository',
    'CashSessionRepository',
    'CashTableRepository',
    'ChipLedgerRepository',
    'CoachRepository',
    'DecisionAnalysisRepository',
    'EntityPresenceRepository',
    'ExperimentRepository',
    'GameRepository',
    'GuestTrackingRepository',
    'HandHistoryRepository',
    'HoldingsSnapshotsRepository',
    'PrestigeSnapshotsRepository',
    'CashScalpsRepository',
    'RenownFieldRepository',
    'LLMRepository',
    'PersonalityRepository',
    'PressureEventRepository',
    'PromptCaptureRepository',
    'PromptPresetRepository',
    'RelationshipRepository',
    'ReplayExperimentRepository',
    'SandboxRepository',
    'SandboxState',
    'SavedGame',
    'SchemaManager',
    'SettingsRepository',
    'SideHustleState',
    'SideHustleStateRepository',
    'StakeRepository',
    'TournamentRepository',
    'UserAvatarRepository',
    'UserPreferencesRepository',
    'UserRepository',
    'ViceState',
    'ViceStateRepository',
    'create_repos',
]
