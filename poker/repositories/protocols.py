"""
Protocol interfaces for all repositories.
These define the contracts that repository implementations must follow.
"""
from datetime import datetime
from typing import Protocol, Optional, List, Dict, Any, runtime_checkable


# =============================================================================
# Domain Models
# =============================================================================

from dataclasses import dataclass, field
from poker.poker_state_machine import PokerStateMachine


@dataclass
class GameEntity:
    """Domain model representing a complete game."""
    id: str
    state_machine: PokerStateMachine
    created_at: datetime
    updated_at: datetime
    owner_id: Optional[str] = None
    owner_name: Optional[str] = None
    debug_capture_enabled: bool = False
    llm_configs: Optional[Dict[str, Any]] = None

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
class MessageEntity:
    """Domain model for game messages/chat."""
    game_id: str
    message_type: str
    message_text: str
    timestamp: datetime
    id: Optional[int] = None


@dataclass
class AIPlayerStateEntity:
    """Domain model for AI player state."""
    game_id: str
    player_name: str
    conversation_history: List[Dict[str, str]]
    personality_state: Dict[str, Any]
    last_updated: datetime


@dataclass
class PersonalitySnapshotEntity:
    """Domain model for personality evolution tracking."""
    player_name: str
    game_id: str
    hand_number: int
    personality_traits: Dict[str, Any]
    pressure_levels: Dict[str, Any]
    timestamp: datetime
    id: Optional[int] = None


@dataclass
class PersonalityEntity:
    """Domain model for AI personality configuration."""
    name: str
    config: Dict[str, Any]
    source: str  # 'ai_generated', 'manual', 'imported'
    created_at: datetime
    last_used: Optional[datetime] = None


@dataclass
class EmotionalStateEntity:
    """Domain model for tilt and emotional state."""
    game_id: str
    player_name: str
    tilt_level: float
    current_mood: str
    trigger_events: List[str]
    modifier_stack: List[Dict[str, Any]]
    last_updated: datetime


@dataclass
class ControllerStateEntity:
    """Domain model for AI controller state (tilt state, elastic personality)."""
    game_id: str
    player_name: str
    state_type: str  # 'tilt_state' or 'elastic_personality'
    state_data: Dict[str, Any]
    last_updated: datetime


@dataclass
class HandHistoryEntity:
    """Domain model for recorded hand history."""
    game_id: str
    hand_number: int
    phase: str
    community_cards: List[Dict[str, Any]]
    pot_size: float
    player_hands: Dict[str, Any]
    actions: List[Dict[str, Any]]
    winners: List[str]
    timestamp: datetime
    id: Optional[int] = None


@dataclass
class HandCommentaryEntity:
    """Domain model for AI reflections on hands."""
    game_id: str
    hand_number: int
    player_name: str
    commentary: str
    reflection_type: str
    created_at: datetime
    id: Optional[int] = None


@dataclass
class OpponentModelEntity:
    """Domain model for AI opponent modeling."""
    game_id: str
    observer_name: str
    opponent_name: str
    observations: Dict[str, Any]
    last_updated: datetime


@dataclass
class MemorableHandEntity:
    """Domain model for hands marked as memorable."""
    game_id: str
    hand_number: int
    player_name: str
    memorability_score: float
    reason: str
    details: Dict[str, Any]
    created_at: datetime
    id: Optional[int] = None


@dataclass
class TournamentResultEntity:
    """Domain model for tournament outcomes."""
    game_id: str
    tournament_type: str
    starting_players: int
    final_standings: List[Dict[str, Any]]
    total_hands: int
    started_at: datetime
    ended_at: datetime
    id: Optional[int] = None


@dataclass
class TournamentStandingEntity:
    """Domain model for player tournament standings."""
    game_id: str
    player_name: str
    final_position: int
    final_chips: int
    hands_played: int
    eliminations: int
    id: Optional[int] = None


@dataclass
class CareerStatsEntity:
    """Domain model for player career statistics."""
    player_name: str
    tournaments_played: int
    total_wins: int
    total_final_tables: int
    best_finish: int
    avg_finish: float
    total_eliminations: int
    total_hands_played: int
    last_updated: datetime


@dataclass
class AvatarImageEntity:
    """Domain model for character avatar images."""
    personality_name: str
    emotion: str
    image_data: bytes
    thumbnail_data: Optional[bytes]
    full_image_data: Optional[bytes]
    generation_prompt: Optional[str]
    created_at: datetime


@dataclass
class ApiUsageEntity:
    """Domain model for LLM API usage tracking."""
    game_id: Optional[str]
    owner_id: Optional[str]
    player_name: Optional[str]
    hand_number: Optional[int]
    call_type: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    latency_ms: int
    timestamp: datetime
    input_cost: Optional[float] = None
    output_cost: Optional[float] = None
    total_cost: Optional[float] = None
    id: Optional[int] = None


@dataclass
class ModelPricingEntity:
    """Domain model for LLM model pricing."""
    model: str
    provider: str
    input_price_per_1m: float
    output_price_per_1m: float
    cached_input_price_per_1m: float
    reasoning_price_per_1m: float
    effective_date: datetime
    id: Optional[int] = None


@dataclass
class EnabledModelEntity:
    """Domain model for enabled LLM models."""
    model_id: str
    provider: str
    display_name: str
    is_default: bool
    enabled_at: datetime
    id: Optional[int] = None


@dataclass
class PromptCaptureEntity:
    """Domain model for AI prompt debugging captures."""
    game_id: str
    hand_number: int
    player_name: str
    action_taken: Optional[str]
    system_prompt: str
    user_prompt: str
    raw_response: Optional[str]
    parsed_response: Optional[Dict[str, Any]]
    model_used: str
    temperature: float
    latency_ms: int
    timestamp: datetime
    source: str  # 'game' or 'playground'
    experiment_id: Optional[int] = None
    id: Optional[int] = None


@dataclass
class DecisionAnalysisEntity:
    """Domain model for AI decision quality analysis."""
    prompt_capture_id: int
    game_id: str
    player_name: str
    request_id: str
    hand_number: int
    ev_analysis: Dict[str, Any]
    gto_deviation: Optional[Dict[str, Any]]
    personality_alignment: Dict[str, Any]
    decision_quality_score: float
    analysis_metadata: Dict[str, Any]
    created_at: datetime
    id: Optional[int] = None


@dataclass
class ExperimentEntity:
    """Domain model for experiments."""
    name: str
    description: str
    config: Dict[str, Any]
    status: str  # 'pending', 'running', 'completed', 'failed'
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class ExperimentGameEntity:
    """Domain model for linking games to experiments."""
    experiment_id: int
    game_id: str
    game_number: int
    status: str  # 'pending', 'running', 'completed', 'failed'
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class AppSettingEntity:
    """Domain model for application settings."""
    key: str
    value: str
    updated_at: datetime


@dataclass
class UserEntity:
    """Domain model for users."""
    id: str
    email: str
    name: str
    picture: Optional[str]
    created_at: datetime
    last_login: datetime
    linked_guest_id: Optional[str] = None


@dataclass
class PressureEventEntity:
    """Domain model for pressure events."""
    game_id: str
    player_name: str
    event_type: str
    details: Optional[Dict[str, Any]]
    timestamp: datetime
    id: Optional[int] = None


@dataclass
class TournamentTrackerEntity:
    """Domain model for tournament elimination tracking."""
    game_id: str
    tracker_data: Dict[str, Any]
    last_updated: datetime


# =============================================================================
# Repository Protocols
# =============================================================================

@runtime_checkable
class GameRepositoryProtocol(Protocol):
    """Repository interface for game persistence."""

    def save(self, game: GameEntity) -> None:
        """Save or update a game."""
        ...

    def find_by_id(self, game_id: str) -> Optional[GameEntity]:
        """Find a game by ID."""
        ...

    def find_recent(self, owner_id: Optional[str] = None, limit: int = 20) -> List[GameEntity]:
        """Find recent games, optionally filtered by owner."""
        ...

    def delete(self, game_id: str) -> None:
        """Delete a game and all related data."""
        ...

    def exists(self, game_id: str) -> bool:
        """Check if a game exists."""
        ...

    def count_by_owner(self, owner_id: str) -> int:
        """Count games owned by a specific user."""
        ...

    def save_llm_configs(self, game_id: str, configs: Dict[str, Any]) -> None:
        """Save LLM configurations for a game."""
        ...

    def load_llm_configs(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load LLM configurations for a game."""
        ...


@runtime_checkable
class MessageRepositoryProtocol(Protocol):
    """Repository interface for game messages."""

    def save(self, message: MessageEntity) -> MessageEntity:
        """Save a message and return it with ID."""
        ...

    def find_by_game_id(self, game_id: str, limit: int = 100) -> List[MessageEntity]:
        """Find messages for a game."""
        ...

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all messages for a game."""
        ...


@runtime_checkable
class AIMemoryRepositoryProtocol(Protocol):
    """Repository interface for AI player state and memory."""

    def save_player_state(self, state: AIPlayerStateEntity) -> None:
        """Save or update AI player state."""
        ...

    def load_player_states(self, game_id: str) -> Dict[str, AIPlayerStateEntity]:
        """Load all AI player states for a game."""
        ...

    def save_personality_snapshot(self, snapshot: PersonalitySnapshotEntity) -> None:
        """Save a personality snapshot."""
        ...

    def save_opponent_model(self, model: OpponentModelEntity) -> None:
        """Save opponent model observations."""
        ...

    def load_opponent_models(self, game_id: str) -> List[OpponentModelEntity]:
        """Load all opponent models for a game."""
        ...

    def save_memorable_hand(self, hand: MemorableHandEntity) -> None:
        """Save a memorable hand."""
        ...

    def save_hand_commentary(self, commentary: HandCommentaryEntity) -> None:
        """Save hand commentary/reflection."""
        ...

    def get_recent_reflections(
        self, game_id: str, player_name: str, limit: int = 5
    ) -> List[HandCommentaryEntity]:
        """Get recent reflections for a player."""
        ...

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all AI memory data for a game."""
        ...


@runtime_checkable
class EmotionalStateRepositoryProtocol(Protocol):
    """Repository interface for emotional state persistence."""

    def save_emotional_state(self, state: EmotionalStateEntity) -> None:
        """Save or update emotional state."""
        ...

    def load_emotional_state(
        self, game_id: str, player_name: str
    ) -> Optional[EmotionalStateEntity]:
        """Load emotional state for a player."""
        ...

    def load_all_emotional_states(self, game_id: str) -> Dict[str, EmotionalStateEntity]:
        """Load all emotional states for a game."""
        ...

    def save_controller_state(self, state: ControllerStateEntity) -> None:
        """Save or update controller state."""
        ...

    def load_controller_state(
        self, game_id: str, player_name: str
    ) -> Optional[ControllerStateEntity]:
        """Load controller state for a player."""
        ...

    def load_all_controller_states(self, game_id: str) -> Dict[str, ControllerStateEntity]:
        """Load all controller states for a game."""
        ...

    def save_pressure_event(self, event: PressureEventEntity) -> None:
        """Save a pressure event."""
        ...

    def get_pressure_events(self, game_id: str) -> List[PressureEventEntity]:
        """Get all pressure events for a game."""
        ...

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all emotional state data for a game."""
        ...


@runtime_checkable
class PersonalityRepositoryProtocol(Protocol):
    """Repository interface for personality persistence."""

    def save(self, personality: PersonalityEntity) -> None:
        """Save or update a personality."""
        ...

    def find_by_name(self, name: str) -> Optional[PersonalityEntity]:
        """Find a personality by name."""
        ...

    def find_all(self, limit: int = 50) -> List[PersonalityEntity]:
        """List all personalities."""
        ...

    def delete(self, name: str) -> bool:
        """Delete a personality. Returns True if deleted."""
        ...

    def save_avatar(self, avatar: AvatarImageEntity) -> None:
        """Save avatar image for a personality."""
        ...

    def load_avatar(
        self, personality_name: str, emotion: str
    ) -> Optional[AvatarImageEntity]:
        """Load avatar image."""
        ...

    def get_available_emotions(self, personality_name: str) -> List[str]:
        """Get available avatar emotions for a personality."""
        ...

    def delete_avatars(self, personality_name: str) -> int:
        """Delete all avatars for a personality. Returns count deleted."""
        ...


@runtime_checkable
class HandHistoryRepositoryProtocol(Protocol):
    """Repository interface for hand history persistence."""

    def save(self, hand: HandHistoryEntity) -> int:
        """Save a hand record. Returns the hand ID."""
        ...

    def find_by_game_id(self, game_id: str, limit: Optional[int] = None) -> List[HandHistoryEntity]:
        """Find hand history for a game."""
        ...

    def get_hand_count(self, game_id: str) -> int:
        """Get the number of hands played in a game."""
        ...

    def get_session_stats(self, game_id: str, player_name: str) -> Dict[str, Any]:
        """Get session statistics for a player."""
        ...

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all hand history for a game."""
        ...


@runtime_checkable
class TournamentRepositoryProtocol(Protocol):
    """Repository interface for tournament persistence."""

    def save_result(self, result: TournamentResultEntity) -> int:
        """Save tournament result. Returns the result ID."""
        ...

    def get_result(self, game_id: str) -> Optional[TournamentResultEntity]:
        """Get tournament result for a game."""
        ...

    def save_standing(self, standing: TournamentStandingEntity) -> None:
        """Save a player's tournament standing."""
        ...

    def get_standings(self, game_id: str) -> List[TournamentStandingEntity]:
        """Get all standings for a tournament."""
        ...

    def save_career_stats(self, stats: CareerStatsEntity) -> None:
        """Save or update career stats."""
        ...

    def get_career_stats(self, player_name: str) -> Optional[CareerStatsEntity]:
        """Get career stats for a player."""
        ...

    def get_tournament_history(
        self, player_name: str, limit: int = 20
    ) -> List[TournamentResultEntity]:
        """Get tournament history for a player."""
        ...

    def save_tracker(self, tracker: TournamentTrackerEntity) -> None:
        """Save tournament tracker state."""
        ...

    def load_tracker(self, game_id: str) -> Optional[TournamentTrackerEntity]:
        """Load tournament tracker state."""
        ...


@runtime_checkable
class LLMTrackingRepositoryProtocol(Protocol):
    """Repository interface for LLM usage tracking."""

    def save_usage(self, usage: ApiUsageEntity) -> int:
        """Save API usage record. Returns the record ID."""
        ...

    def get_usage_stats(
        self,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get aggregated usage statistics."""
        ...

    def save_model_pricing(self, pricing: ModelPricingEntity) -> None:
        """Save or update model pricing."""
        ...

    def get_model_pricing(self, model: str, provider: str) -> Optional[ModelPricingEntity]:
        """Get pricing for a specific model."""
        ...

    def get_all_model_pricing(self) -> List[ModelPricingEntity]:
        """Get all model pricing records."""
        ...

    def save_enabled_model(self, model: EnabledModelEntity) -> None:
        """Save or update an enabled model."""
        ...

    def get_enabled_models(self) -> List[EnabledModelEntity]:
        """Get all enabled models."""
        ...

    def delete_enabled_model(self, model_id: str, provider: str) -> bool:
        """Delete an enabled model. Returns True if deleted."""
        ...


@runtime_checkable
class DebugRepositoryProtocol(Protocol):
    """Repository interface for debugging and analysis."""

    def save_prompt_capture(self, capture: PromptCaptureEntity) -> int:
        """Save a prompt capture. Returns the capture ID."""
        ...

    def get_prompt_capture(self, capture_id: int) -> Optional[PromptCaptureEntity]:
        """Get a prompt capture by ID."""
        ...

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PromptCaptureEntity]:
        """List prompt captures with optional filters."""
        ...

    def get_prompt_capture_stats(
        self, game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get prompt capture statistics."""
        ...

    def delete_prompt_captures(
        self,
        game_id: Optional[str] = None,
        before_date: Optional[datetime] = None,
    ) -> int:
        """Delete prompt captures. Returns count deleted."""
        ...

    def save_decision_analysis(self, analysis: DecisionAnalysisEntity) -> int:
        """Save decision analysis. Returns the analysis ID."""
        ...

    def get_decision_analysis(self, analysis_id: int) -> Optional[DecisionAnalysisEntity]:
        """Get decision analysis by ID."""
        ...

    def get_decision_analysis_by_capture(
        self, capture_id: int
    ) -> Optional[DecisionAnalysisEntity]:
        """Get decision analysis by prompt capture ID."""
        ...

    def list_decision_analyses(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[DecisionAnalysisEntity]:
        """List decision analyses with optional filters."""
        ...


@runtime_checkable
class ExperimentRepositoryProtocol(Protocol):
    """Repository interface for experiment tracking."""

    def create_experiment(self, experiment: ExperimentEntity) -> int:
        """Create a new experiment. Returns the experiment ID."""
        ...

    def update_experiment(self, experiment: ExperimentEntity) -> None:
        """Update an existing experiment."""
        ...

    def get_experiment(self, experiment_id: int) -> Optional[ExperimentEntity]:
        """Get an experiment by ID."""
        ...

    def get_experiment_by_name(self, name: str) -> Optional[ExperimentEntity]:
        """Get an experiment by name."""
        ...

    def list_experiments(
        self, status: Optional[str] = None, limit: int = 50
    ) -> List[ExperimentEntity]:
        """List experiments with optional status filter."""
        ...

    def add_game_to_experiment(self, game: ExperimentGameEntity) -> int:
        """Add a game to an experiment. Returns the link ID."""
        ...

    def get_experiment_games(self, experiment_id: int) -> List[ExperimentGameEntity]:
        """Get all games for an experiment."""
        ...

    def update_experiment_game(self, game: ExperimentGameEntity) -> None:
        """Update an experiment game record."""
        ...

    def get_experiment_stats(self, experiment_id: int) -> Dict[str, Any]:
        """Get aggregated statistics for an experiment."""
        ...


@runtime_checkable
class ConfigRepositoryProtocol(Protocol):
    """Repository interface for application configuration."""

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a setting value."""
        ...

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value."""
        ...

    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings."""
        ...

    def delete_setting(self, key: str) -> bool:
        """Delete a setting. Returns True if deleted."""
        ...

    def get_user(self, user_id: str) -> Optional[UserEntity]:
        """Get a user by ID."""
        ...

    def get_user_by_email(self, email: str) -> Optional[UserEntity]:
        """Get a user by email."""
        ...

    def save_user(self, user: UserEntity) -> None:
        """Save or update a user."""
        ...

    def link_guest_to_user(self, user_id: str, guest_id: str) -> None:
        """Link a guest session to a user account."""
        ...

    def get_user_by_linked_guest(self, guest_id: str) -> Optional[UserEntity]:
        """Get a user by their linked guest ID."""
        ...
