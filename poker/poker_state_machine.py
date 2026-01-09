from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import List, Tuple, Optional

from .poker_game import PokerGameState, setup_hand, set_betting_round_start_player, reset_player_action_flags, \
    are_pot_contributions_valid, deal_community_cards, determine_winner, reset_game_state_for_new_hand, \
    award_pot_winnings


class PokerPhase(Enum):
    """
    An enum class that represents different phases of the poker game.
    """
    INITIALIZING_GAME = auto()
    INITIALIZING_HAND = auto()
    HAND_INITIALIZED = auto()
    INITIALIZING_BET_ROUND = auto()
    PRE_FLOP = auto()
    DEALING_CARDS = auto()
    FLOP = auto()
    TURN = auto()
    RIVER = auto()
    SHOWDOWN = auto()
    EVALUATING_HAND = auto()
    HAND_OVER = auto()
    GAME_OVER = auto()  # Terminal state for completed tournaments

    @classmethod
    def _to_string(cls, phase):
        phase_to_strings = {
            cls.INITIALIZING_GAME: "Initializing Game",
            cls.INITIALIZING_HAND: "Initializing Hand",
            cls.INITIALIZING_BET_ROUND: "Initializing Betting Round",
            cls.PRE_FLOP: "Pre-Flop",
            cls.DEALING_CARDS: "Dealing Cards",
            cls.FLOP: "Flop",
            cls.TURN: "Turn",
            cls.RIVER: "River",
            cls.SHOWDOWN: "Showdown",
            cls.EVALUATING_HAND: "Determining Winners",
            cls.HAND_OVER: "Hand Over",
            cls.GAME_OVER: "Game Over",
        }
        return phase_to_strings.get(phase, "Unknown Phase")

    def __str__(self):
        return self._to_string(self)


# ============================================================================
# New Immutable Data Structures
# ============================================================================

@dataclass(frozen=True)
class BlindConfig:
    """Immutable blind escalation configuration."""
    growth: float = 2.0  # multiplier for blind increase
    hands_per_level: int = 5  # hands between increases
    max_blind: int = 0  # 0 = no limit

@dataclass(frozen=True)
class StateMachineStats:
    """Immutable statistics tracking for the state machine."""
    hand_count: int = 0

    def increment_hand_count(self) -> 'StateMachineStats':
        """Return new stats with incremented hand count."""
        return replace(self, hand_count=self.hand_count + 1)


@dataclass(frozen=True)
class ImmutableStateMachine:
    """
    Immutable state machine state.
    All fields are read-only and updates create new instances.
    """
    game_state: PokerGameState
    phase: PokerPhase
    stats: StateMachineStats = field(default_factory=StateMachineStats)
    snapshots: Tuple[PokerGameState, ...] = field(default_factory=tuple)
    blind_config: BlindConfig = field(default_factory=BlindConfig)
    
    def with_game_state(self, game_state: PokerGameState) -> 'ImmutableStateMachine':
        """Return new state with updated game state."""
        return replace(self, game_state=game_state)
    
    def with_phase(self, phase: PokerPhase) -> 'ImmutableStateMachine':
        """Return new state with updated phase."""
        return replace(self, phase=phase)
    
    def with_stats(self, stats: StateMachineStats) -> 'ImmutableStateMachine':
        """Return new state with updated stats."""
        return replace(self, stats=stats)
    
    def add_snapshot(self) -> 'ImmutableStateMachine':
        """Return new state with current game state added to snapshots."""
        new_snapshots = self.snapshots + (self.game_state,)
        return replace(self, snapshots=new_snapshots)
    
    @property
    def current_phase(self) -> PokerPhase:
        """Get the current phase."""
        return self.phase
    
    @property
    def awaiting_action(self) -> bool:
        """Check if game is awaiting player action."""
        return self.game_state.awaiting_action


# ============================================================================
# Pure State Transition Functions
# ============================================================================

def get_next_phase(state: ImmutableStateMachine) -> PokerPhase:
    """
    Pure function to determine the next phase based on current state.
    """
    current_phase = state.phase
    
    def next_betting_round_phase() -> PokerPhase:
        num_cards_dealt = len(state.game_state.community_cards)
        # What is the next phase of the game based on the number of community cards currently dealt
        num_cards_dealt_to_next_phase = {
            0: PokerPhase.PRE_FLOP,
            3: PokerPhase.FLOP,
            4: PokerPhase.TURN,
            5: PokerPhase.RIVER
        }
        # Handle unexpected card counts (corrupted state) - default to HAND_OVER to reset
        if num_cards_dealt not in num_cards_dealt_to_next_phase:
            print(f"Warning: Unexpected community card count: {num_cards_dealt}, resetting to HAND_OVER")
            return PokerPhase.HAND_OVER
        return num_cards_dealt_to_next_phase[num_cards_dealt]
    
    next_phase_map = {
        PokerPhase.INITIALIZING_GAME: PokerPhase.INITIALIZING_HAND,
        PokerPhase.INITIALIZING_HAND: PokerPhase.PRE_FLOP,
        PokerPhase.INITIALIZING_BET_ROUND: next_betting_round_phase(),
        PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
        PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
        PokerPhase.TURN: PokerPhase.DEALING_CARDS,
        PokerPhase.DEALING_CARDS: PokerPhase.INITIALIZING_BET_ROUND,
        PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
        PokerPhase.SHOWDOWN: PokerPhase.EVALUATING_HAND,
        PokerPhase.EVALUATING_HAND: PokerPhase.HAND_OVER,
        PokerPhase.HAND_OVER: PokerPhase.INITIALIZING_HAND
    }
    
    return next_phase_map[current_phase]


def initialize_game_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for INITIALIZING_GAME phase transition."""
    return state.with_phase(get_next_phase(state))


def initialize_hand_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for INITIALIZING_HAND phase transition."""
    new_game_state = setup_hand(state.game_state)
    new_game_state = set_betting_round_start_player(game_state=new_game_state)
    return (state
            .with_game_state(new_game_state)
            .with_phase(get_next_phase(state)))


def initialize_betting_round_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for INITIALIZING_BET_ROUND phase transition."""
    num_active_players = len([p.name for p in state.game_state.players if not p.is_folded])

    if num_active_players == 1:
        next_phase = PokerPhase.SHOWDOWN
    else:
        new_game_state = reset_player_action_flags(state.game_state)
        new_game_state = set_betting_round_start_player(new_game_state)
        # Reset minimum raise to big blind at the start of each betting round (standard poker rules)
        new_game_state = new_game_state.update(last_raise_amount=new_game_state.current_ante)
        return (state
                .with_game_state(new_game_state)
                .with_phase(get_next_phase(state)))

    return state.with_phase(next_phase)


def run_betting_round_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for betting round phases (PRE_FLOP, FLOP, TURN, RIVER)."""
    pot_is_settled = not (not are_pot_contributions_valid(state.game_state)
                          and len([p.name for p in state.game_state.players if not p.is_folded or not p.is_all_in]) > 1)
    
    if not are_pot_contributions_valid(state.game_state):
        # Set awaiting action flag
        new_game_state = state.game_state.update(awaiting_action=True)
        return state.with_game_state(new_game_state)
    elif pot_is_settled and state.phase != PokerPhase.EVALUATING_HAND:
        return state.with_phase(get_next_phase(state))
    
    return state


def deal_cards_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for DEALING_CARDS phase transition."""
    new_game_state = deal_community_cards(state.game_state)
    return (state
            .with_game_state(new_game_state)
            .with_phase(get_next_phase(state)))


def showdown_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for SHOWDOWN phase transition."""
    # Currently just advances to next phase
    return state.with_phase(get_next_phase(state))


def evaluating_hand_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for EVALUATING_HAND phase transition."""
    winner_info = determine_winner(state.game_state)
    new_game_state = award_pot_winnings(state.game_state, winner_info)
    
    if winner_info:
        return (state
                .with_game_state(new_game_state)
                .with_phase(get_next_phase(state)))
    
    return state.with_game_state(new_game_state)


def hand_over_transition(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """Pure function for HAND_OVER phase transition."""
    new_game_state = reset_game_state_for_new_hand(state.game_state)

    # Increment hand count
    new_stats = state.stats.increment_hand_count()

    # Increase blinds based on config
    blind_cfg = state.blind_config
    if new_stats.hand_count % blind_cfg.hands_per_level == 0:
        new_ante = int(new_game_state.current_ante * blind_cfg.growth)
        # Apply max blind cap if set (0 = no limit)
        if blind_cfg.max_blind > 0:
            new_ante = min(new_ante, blind_cfg.max_blind)
        new_game_state = new_game_state.update(current_ante=new_ante, last_raise_amount=new_ante)

    return (state
            .with_game_state(new_game_state)
            .with_stats(new_stats)
            .with_phase(get_next_phase(state)))


def advance_state_pure(state: ImmutableStateMachine) -> ImmutableStateMachine:
    """
    Pure function that advances the state machine by one step.
    Returns a new state with appropriate transitions applied.
    """
    # Add snapshot
    state = state.add_snapshot()
    
    # Map phases to their transition functions
    phase_transitions = {
        PokerPhase.INITIALIZING_GAME: initialize_game_transition,
        PokerPhase.INITIALIZING_HAND: initialize_hand_transition,
        PokerPhase.INITIALIZING_BET_ROUND: initialize_betting_round_transition,
        PokerPhase.PRE_FLOP: run_betting_round_transition,
        PokerPhase.FLOP: run_betting_round_transition,
        PokerPhase.TURN: run_betting_round_transition,
        PokerPhase.RIVER: run_betting_round_transition,
        PokerPhase.DEALING_CARDS: deal_cards_transition,
        PokerPhase.SHOWDOWN: showdown_transition,
        PokerPhase.EVALUATING_HAND: evaluating_hand_transition,
        PokerPhase.HAND_OVER: hand_over_transition,
    }
    
    transition_fn = phase_transitions.get(state.phase)
    if not transition_fn:
        raise Exception(f"Invalid game phase: {state.phase}")
    
    return transition_fn(state)


# ============================================================================
# Existing PokerStateMachine (will be refactored to use immutable internals)
# ============================================================================

class PokerStateMachine:
    """
    Immutable poker state machine.
    All methods that modify state return a new instance.
    """

    def __init__(self, game_state: PokerGameState,
                 _internal_state: Optional[ImmutableStateMachine] = None,
                 blind_config: Optional[dict] = None):
        """
        Initialize state machine.

        Args:
            game_state: Initial game state (used for new games)
            _internal_state: Internal state (used for creating new instances)
            blind_config: Optional dict with 'growth', 'hands_per_level', 'max_blind'
        """
        if _internal_state is not None:
            self._state = _internal_state
        else:
            # Create BlindConfig from dict if provided
            if blind_config:
                bc = BlindConfig(
                    growth=blind_config.get('growth', 2.0),
                    hands_per_level=blind_config.get('hands_per_level', 5),
                    max_blind=blind_config.get('max_blind', 0)
                )
            else:
                bc = BlindConfig()

            self._state = ImmutableStateMachine(
                game_state=game_state,
                phase=PokerPhase.INITIALIZING_GAME,
                blind_config=bc
            )

    @classmethod
    def from_saved_state(cls, game_state: PokerGameState, phase: PokerPhase,
                         blind_config: Optional[dict] = None) -> 'PokerStateMachine':
        """Create a state machine from a saved game state with a specific phase."""
        if blind_config:
            bc = BlindConfig(
                growth=blind_config.get('growth', 2.0),
                hands_per_level=blind_config.get('hands_per_level', 5),
                max_blind=blind_config.get('max_blind', 0)
            )
        else:
            bc = BlindConfig()
        internal = ImmutableStateMachine(game_state=game_state, phase=phase, blind_config=bc)
        return cls(game_state, _internal_state=internal)

    # ========================================================================
    # Read-only properties
    # ========================================================================
    
    @property
    def game_state(self) -> PokerGameState:
        """Get current game state (read-only)."""
        return self._state.game_state
    
    @property
    def phase(self) -> PokerPhase:
        """Get current phase (read-only)."""
        return self._state.phase
    
    @property
    def current_phase(self) -> PokerPhase:
        """Alias for phase property."""
        return self._state.phase
    
    @property
    def snapshots(self) -> List[PokerGameState]:
        """Get snapshots as list (read-only)."""
        return list(self._state.snapshots)
    
    @property
    def stats(self) -> dict:
        """Get stats as dict (read-only)."""
        return {'hand_count': self._state.stats.hand_count}
    
    @property
    def next_phase(self) -> PokerPhase:
        """Get next phase."""
        return get_next_phase(self._state)
    
    @property
    def awaiting_action(self) -> bool:
        """Check if awaiting player action."""
        return self._state.game_state.awaiting_action
    
    # ========================================================================
    # Immutable update methods (return new instances)
    # ========================================================================
    
    def advance(self) -> 'PokerStateMachine':
        """
        Advance state machine by one step.
        Returns a new PokerStateMachine instance.
        """
        new_internal_state = advance_state_pure(self._state)
        return PokerStateMachine(
            game_state=None,  # Not used when _internal_state is provided
            _internal_state=new_internal_state
        )
    
    def with_game_state(self, game_state: PokerGameState) -> 'PokerStateMachine':
        """
        Create new state machine with updated game state.
        Returns a new PokerStateMachine instance.
        """
        new_internal_state = self._state.with_game_state(game_state)
        return PokerStateMachine(
            game_state=None,
            _internal_state=new_internal_state
        )
    
    def with_phase(self, phase: PokerPhase) -> 'PokerStateMachine':
        """
        Create new state machine with updated phase.
        Returns a new PokerStateMachine instance.
        """
        new_internal_state = self._state.with_phase(phase)
        return PokerStateMachine(
            game_state=None,
            _internal_state=new_internal_state
        )
    
    def run_until_player_action(self) -> 'PokerStateMachine':
        """
        Run until player action needed.
        Returns a new PokerStateMachine instance.
        """
        current = self
        while not current.awaiting_action:
            current = current.advance()
        return current
    
    def run_until(self, phases: List[PokerPhase]) -> 'PokerStateMachine':
        """
        Run until one of the specified phases or player action.
        Returns a new PokerStateMachine instance.
        """
        current = self
        while current.phase not in phases:
            current = current.advance()
            if current.awaiting_action:
                break
        return current
    
    # ========================================================================
    # Serialization support for persistence
    # ========================================================================
    
    def to_dict(self) -> dict:
        """Convert to dictionary for persistence."""
        return {
            'phase': self.phase.name,
            'game_state': self.game_state.to_dict(),
            'stats': self.stats
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'PokerStateMachine':
        """Create from dictionary (for loading from persistence)."""
        # This would need proper implementation based on persistence needs
        raise NotImplementedError("from_dict not yet implemented")
