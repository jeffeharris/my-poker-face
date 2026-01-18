"""Repository pattern implementations for poker game persistence.

This package provides a clean repository abstraction over the persistence layer.
Use RepositoryFactory to get repository instances with proper dependency injection.

Example usage:
    from poker.repositories.factory import get_repository_factory

    factory = get_repository_factory(db_path="poker_games.db", initialize_schema=True)
    game_repo = factory.game
    message_repo = factory.messages
"""

from .factory import RepositoryFactory, get_repository_factory, reset_factory

from .protocols import (
    # Domain entities
    GameEntity,
    MessageEntity,
    AIPlayerStateEntity,
    PersonalityEntity,
    PersonalitySnapshotEntity,
    EmotionalStateEntity,
    ControllerStateEntity,
    HandHistoryEntity,
    HandCommentaryEntity,
    OpponentModelEntity,
    MemorableHandEntity,
    TournamentResultEntity,
    TournamentStandingEntity,
    TournamentTrackerEntity,
    CareerStatsEntity,
    AvatarImageEntity,
    ApiUsageEntity,
    ModelPricingEntity,
    EnabledModelEntity,
    PromptCaptureEntity,
    DecisionAnalysisEntity,
    ExperimentEntity,
    ExperimentGameEntity,
    AppSettingEntity,
    UserEntity,
    PressureEventEntity,
    # Repository protocols
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

__all__ = [
    # Factory
    'RepositoryFactory',
    'get_repository_factory',
    'reset_factory',
    # Domain entities
    'GameEntity',
    'MessageEntity',
    'AIPlayerStateEntity',
    'PersonalityEntity',
    'PersonalitySnapshotEntity',
    'EmotionalStateEntity',
    'ControllerStateEntity',
    'HandHistoryEntity',
    'HandCommentaryEntity',
    'OpponentModelEntity',
    'MemorableHandEntity',
    'TournamentResultEntity',
    'TournamentStandingEntity',
    'TournamentTrackerEntity',
    'CareerStatsEntity',
    'AvatarImageEntity',
    'ApiUsageEntity',
    'ModelPricingEntity',
    'EnabledModelEntity',
    'PromptCaptureEntity',
    'DecisionAnalysisEntity',
    'ExperimentEntity',
    'ExperimentGameEntity',
    'AppSettingEntity',
    'UserEntity',
    'PressureEventEntity',
    # Repository protocols
    'GameRepositoryProtocol',
    'MessageRepositoryProtocol',
    'AIMemoryRepositoryProtocol',
    'EmotionalStateRepositoryProtocol',
    'PersonalityRepositoryProtocol',
    'HandHistoryRepositoryProtocol',
    'TournamentRepositoryProtocol',
    'LLMTrackingRepositoryProtocol',
    'DebugRepositoryProtocol',
    'ExperimentRepositoryProtocol',
    'ConfigRepositoryProtocol',
]
