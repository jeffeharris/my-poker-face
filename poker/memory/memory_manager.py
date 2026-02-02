"""
AI Memory Manager - Central orchestrator for all memory systems.

Coordinates:
- Hand history recording
- Session memory per player
- Opponent modeling
- Commentary generation
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Any, Tuple

from .hand_history import HandHistoryRecorder, RecordedHand
from .session_memory import SessionMemory
from .opponent_model import OpponentModelManager
from .commentary_generator import CommentaryGenerator, HandCommentary
from .commentary_filter import should_player_comment
from ..hand_narrator import narrate_key_moments
from ..config import COMMENTARY_ENABLED

logger = logging.getLogger(__name__)


class AIMemoryManager:
    """Orchestrates all memory systems for AI players in a game."""

    def __init__(self, game_id: str, db_path: Optional[str] = None, owner_id: Optional[str] = None,
                 commentary_enabled: Optional[bool] = None):
        """Initialize the memory manager.

        Args:
            game_id: Unique identifier for the game
            db_path: Path to database for persistence (optional)
            owner_id: Owner/user ID for tracking (optional)
            commentary_enabled: Override for COMMENTARY_ENABLED config (optional).
                                If None, uses global COMMENTARY_ENABLED setting.
        """
        self.game_id = game_id
        self.db_path = db_path
        self.owner_id = owner_id
        # Allow instance-level override for commentary generation
        self._commentary_enabled_override = commentary_enabled

        # Core systems
        self.hand_recorder = HandHistoryRecorder(game_id)
        self.opponent_model_manager = OpponentModelManager()
        self.commentary_generator = CommentaryGenerator(game_id=game_id, owner_id=owner_id)

        # Per-player session memories
        self.session_memories: Dict[str, SessionMemory] = {}

        # Tracking
        self.hand_count = 0
        self.initialized_players: set = set()

        # C-bet tracking (reset each hand)
        self._preflop_raiser: Optional[str] = None  # Who raised preflop
        self._cbet_made: bool = False  # Has a c-bet been made this hand
        self._players_facing_cbet: set = set()  # Players who need to respond to c-bet

        # Thread safety for parallel commentary generation
        self._lock = threading.Lock()
        self._last_recorded_hand: Optional[RecordedHand] = None

        # Persistence layer (set externally to avoid circular imports)
        self._persistence = None

    def set_hand_history_repo(self, hand_history_repo) -> None:
        """Set the hand history repository for saving hand history.

        Args:
            hand_history_repo: HandHistoryRepository instance
        """
        self._persistence = hand_history_repo

    def initialize_for_player(self, player_name: str) -> None:
        """Set up memory systems for an AI player.

        Args:
            player_name: Name of the AI player
        """
        if player_name in self.initialized_players:
            return

        # Create session memory with DB backing if persistence is available
        session_memory = SessionMemory(player_name)
        if self._persistence:
            session_memory.set_hand_history_repo(self._persistence, self.game_id)
        self.session_memories[player_name] = session_memory

        self.initialized_players.add(player_name)
        logger.info(f"Initialized memory systems for {player_name}")

    def initialize_human_observer(self, player_name: str) -> None:
        """Add human player as an observer for opponent modeling.

        Unlike AI players, humans don't need session memory or other AI systems,
        but they should still track observations about opponents.

        Args:
            player_name: Name of the human player
        """
        if player_name in self.initialized_players:
            return

        self.initialized_players.add(player_name)
        logger.info(f"Initialized human observer: {player_name}")

    def on_hand_start(self, game_state: Any, hand_number: int) -> None:
        """Called when a new hand begins.

        Args:
            game_state: Current PokerGameState
            hand_number: The hand number in this game
        """
        self.hand_count = hand_number
        self.hand_recorder.start_hand(game_state, hand_number)

        # Reset c-bet tracking for new hand
        self._preflop_raiser = None
        self._cbet_made = False
        self._players_facing_cbet = set()

        logger.debug(f"Started recording hand #{hand_number}")

    def on_action(self, player_name: str, action: str, amount: int,
                  phase: str, pot_total: int, active_players: List[str] = None) -> None:
        """Record an action and update opponent models.

        Args:
            player_name: Name of player who acted
            action: The action taken ('fold', 'check', 'call', 'raise', 'bet', 'all_in')
            amount: Amount added to pot
            phase: Current game phase ('PRE_FLOP', 'FLOP', 'TURN', 'RIVER')
            pot_total: Total pot after the action
            active_players: List of players still in the hand (for c-bet tracking)
        """
        # Record to hand history
        self.hand_recorder.record_action(player_name, action, amount, phase, pot_total)

        # Track preflop raiser for c-bet detection
        if phase == 'PRE_FLOP' and action == 'raise':
            self._preflop_raiser = player_name

        # Detect c-bet: preflop raiser bets on flop
        if (phase == 'FLOP' and
            action in ('bet', 'raise') and
            player_name == self._preflop_raiser and
            not self._cbet_made):
            self._cbet_made = True
            # All other active players are facing the c-bet
            if active_players:
                self._players_facing_cbet = {p for p in active_players if p != player_name}
            logger.debug(f"C-bet detected from {player_name}, facing: {self._players_facing_cbet}")

        # Track response to c-bet
        if self._cbet_made and player_name in self._players_facing_cbet:
            folded = (action == 'fold')
            # Update fold_to_cbet for all AI observers
            for observer in self.initialized_players:
                if observer != player_name:
                    model = self.opponent_model_manager.get_model(observer, player_name)
                    model.tendencies.update_fold_to_cbet(folded)
            # Remove from facing set (they've responded)
            self._players_facing_cbet.discard(player_name)
            logger.debug(f"{player_name} {'folded to' if folded else 'called/raised'} c-bet")

        # Update opponent models for all AI observers
        for observer in self.initialized_players:
            if observer != player_name:
                self.opponent_model_manager.observe_action(
                    observer=observer,
                    opponent=player_name,
                    action=action,
                    phase=phase,
                    is_voluntary=(action not in ('sb', 'bb')),
                    hand_number=self.hand_count
                )

    def on_community_cards(self, phase: str, cards: List[str]) -> None:
        """Record community cards dealt.

        Args:
            phase: Game phase ('FLOP', 'TURN', 'RIVER')
            cards: List of card strings
        """
        self.hand_recorder.record_community_cards(phase, cards)

    def on_hand_complete(self, winner_info: Dict[str, Any],
                        game_state: Any,
                        ai_players: Dict[str, Any] = None,
                        skip_commentary: bool = False) -> Dict[str, HandCommentary]:
        """Process end of hand - record history, update models, optionally generate commentary.

        Args:
            winner_info: Dict with 'winnings', 'hand_name', 'hand_rank'
            game_state: Current game state
            ai_players: Dict mapping player names to their AIPokerPlayer objects
            skip_commentary: If True, skip commentary generation (for async flow)

        Returns:
            Dict mapping player names to their HandCommentary (or None if skip_commentary)
        """
        ai_players = ai_players or {}

        # Complete hand recording
        try:
            recorded_hand = self.hand_recorder.complete_hand(winner_info, game_state)
            # Store for async commentary generation (thread-safe)
            with self._lock:
                self._last_recorded_hand = recorded_hand

            # Persist hand to database
            if self._persistence:
                try:
                    self._persistence.save_hand_history(recorded_hand)
                except Exception as e:
                    logger.warning(f"Failed to persist hand history: {e}")
        except Exception as e:
            logger.error(f"Failed to complete hand recording: {e}")
            return {}

        # Update opponent models with showdown info
        if recorded_hand.was_showdown:
            # Track all players at showdown (winners and losers)
            for player in recorded_hand.players:
                outcome = recorded_hand.get_player_outcome(player.name)

                # Skip players who folded - they weren't at showdown
                if outcome == 'folded':
                    continue

                # Update opponent models for all observers
                for observer in self.initialized_players:
                    if observer != player.name:
                        model = self.opponent_model_manager.get_model(observer, player.name)
                        model.observe_showdown(won=(outcome == 'won'))

        # Update session memories
        for player_name, session_memory in self.session_memories.items():
            # Determine player's outcome
            outcome = recorded_hand.get_player_outcome(player_name)

            # Calculate amount won/lost
            amount = 0
            for winner in recorded_hand.winners:
                if winner.name == player_name:
                    amount = winner.amount_won
                    break
            if outcome == 'lost':
                # Estimate loss from actions
                player_actions = recorded_hand.get_player_actions(player_name)
                amount = -sum(a.amount for a in player_actions)

            # Extract notable events (use hand narrator for richer key moments)
            notable_events = self.commentary_generator.extract_notable_events(
                recorded_hand, player_name
            )
            try:
                key_moment = narrate_key_moments(recorded_hand, player_name, big_blind=game_state.current_ante)
                if key_moment:
                    notable_events = [key_moment] + notable_events
            except Exception as e:
                logger.debug(f"Key moment narration failed for {player_name}: {e}")

            # Update session memory
            session_memory.record_hand_outcome(
                hand_number=recorded_hand.hand_number,
                outcome=outcome,
                pot_size=recorded_hand.pot_size,
                amount_won_or_lost=amount,
                notable_events=notable_events
            )

        # Skip commentary generation if requested (for async flow)
        if skip_commentary:
            return {}

        # Generate commentary synchronously (legacy flow)
        return self.generate_commentary_for_hand(ai_players)

    def generate_commentary_for_hand(
        self,
        ai_players: Dict[str, Any],
        on_commentary_ready: Optional[Callable] = None,
        big_blind: Optional[int] = None
    ) -> Dict[str, HandCommentary]:
        """Generate commentary for the last completed hand.

        This can be called asynchronously after on_hand_complete(skip_commentary=True).
        All AI players' commentary is generated in parallel using ThreadPoolExecutor.

        Thread Safety:
            - Acquires lock to get snapshot of recorded hand
            - Creates immutable snapshots of session context before spawning threads
            - Each thread only reads from its own snapshot

        Args:
            ai_players: Dict mapping player names to context dicts:
                        {'ai_player': AIPokerPlayer, 'is_eliminated': bool,
                         'spectator_context': Optional[str]}
            on_commentary_ready: Optional callback called immediately when each commentary
                                 is ready. Signature: (player_name, commentary) -> None
            big_blind: Current big blind for dynamic interest thresholds

        Returns:
            Dict mapping player names to their HandCommentary
        """
        # Thread-safe access to recorded hand - acquire lock, get snapshot, release
        with self._lock:
            if self._last_recorded_hand is None:
                logger.warning("No recorded hand available for commentary generation")
                return {}
            # RecordedHand is immutable (frozen dataclass), safe to share
            recorded_hand = self._last_recorded_hand
            # Clear to prevent memory leak - we have our reference now
            self._last_recorded_hand = None

        commentaries: Dict[str, HandCommentary] = {}

        # Check instance override first, then fall back to global setting
        commentary_enabled = self._commentary_enabled_override if self._commentary_enabled_override is not None else COMMENTARY_ENABLED
        if not commentary_enabled:
            return commentaries

        # Build list of players to generate commentary for
        # Apply filtering rules (preflop folds, eliminated players, etc.)
        players_to_process = []
        for player_name, context in ai_players.items():
            # Skip if no session memory
            if player_name not in self.session_memories:
                continue

            # Extract context (support both old format and new dict format)
            if isinstance(context, dict):
                is_eliminated = context.get('is_eliminated', False)
            else:
                is_eliminated = False

            # Apply commentary filter rules
            if should_player_comment(player_name, recorded_hand, is_eliminated):
                players_to_process.append(player_name)
            else:
                logger.debug(f"Filtering out {player_name} from commentary")

        if not players_to_process:
            return commentaries

        # Create thread-safe snapshots BEFORE spawning threads
        # This prevents race conditions if session memory or opponent models
        # are modified by another thread (e.g., new hand starting)
        player_snapshots: Dict[str, Dict[str, Any]] = {}
        for player_name in players_to_process:
            context = ai_players[player_name]

            # Extract context (support both old format and new dict format)
            if isinstance(context, dict):
                ai_player = context.get('ai_player')
                is_eliminated = context.get('is_eliminated', False)
                spectator_context = context.get('spectator_context')
            else:
                ai_player = context
                is_eliminated = False
                spectator_context = None

            session_memory = self.session_memories[player_name]

            # Capture all data needed for commentary generation
            player_snapshots[player_name] = {
                'outcome': recorded_hand.get_player_outcome(player_name),
                'player_cards': list(recorded_hand.hole_cards.get(player_name, [])),
                'session_context': session_memory.get_context_for_prompt(100),
                'opponent_summaries': self.opponent_model_manager.get_table_summary(
                    player_name,
                    [p for p in ai_players if p != player_name],
                    200
                ),
                'confidence': getattr(ai_player, 'confidence', 'neutral'),
                'attitude': getattr(ai_player, 'attitude', 'neutral'),
                'chattiness': self._get_chattiness(ai_player),
                'assistant': getattr(ai_player, 'assistant', None),
                'is_eliminated': is_eliminated,
                'spectator_context': spectator_context,
            }

        def generate_single_commentary(player_name: str) -> Tuple[str, Optional[HandCommentary]]:
            """Generate commentary for a single player. Returns (player_name, commentary).

            Uses pre-captured snapshot data to avoid accessing shared mutable state.
            """
            snapshot = player_snapshots[player_name]

            try:
                commentary = self.commentary_generator.generate_commentary(
                    player_name=player_name,
                    hand=recorded_hand,  # Immutable, safe to share
                    player_outcome=snapshot['outcome'],
                    player_cards=snapshot['player_cards'],
                    session_memory=None,  # Pass context string instead
                    opponent_models=None,  # Pass summary string instead
                    confidence=snapshot['confidence'],
                    attitude=snapshot['attitude'],
                    chattiness=snapshot['chattiness'],
                    assistant=snapshot['assistant'],
                    # Pre-computed context for thread safety
                    session_context_override=snapshot['session_context'],
                    opponent_context_override=snapshot['opponent_summaries'],
                    # New params for filtering and spectator mode
                    big_blind=big_blind,
                    is_eliminated=snapshot['is_eliminated'],
                    spectator_context=snapshot['spectator_context'],
                )
                return (player_name, commentary)
            except Exception as e:
                logger.warning(f"Failed to generate commentary for {player_name}: {e}")
                return (player_name, None)

        # Generate all commentaries in parallel
        with ThreadPoolExecutor(max_workers=len(players_to_process)) as executor:
            futures = {
                executor.submit(generate_single_commentary, player_name): player_name
                for player_name in players_to_process
            }

            for future in as_completed(futures):
                player_name, commentary = future.result()
                if commentary:
                    commentaries[player_name] = commentary
                    # Call callback immediately so commentary can be emitted right away
                    if on_commentary_ready:
                        try:
                            on_commentary_ready(player_name, commentary)
                        except Exception as e:
                            logger.warning(f"Commentary callback failed for {player_name}: {e}")

        return commentaries

    def get_decision_context(self, player_name: str, opponents: List[str]) -> str:
        """Get memory context for a decision prompt.

        Args:
            player_name: Name of the AI player making the decision
            opponents: List of opponent names at the table

        Returns:
            Formatted string with session and opponent context
        """
        parts = []

        # Session context
        if player_name in self.session_memories:
            session_ctx = self.session_memories[player_name].get_context_for_prompt()
            if session_ctx:
                parts.append(f"=== Your Session ===\n{session_ctx}")

        # Opponent summaries
        opponent_ctx = self.opponent_model_manager.get_table_summary(player_name, opponents)
        if opponent_ctx:
            parts.append(f"=== Opponent Intel ===\n{opponent_ctx}")

        return "\n\n".join(parts)

    def get_session_memory(self, player_name: str) -> Optional[SessionMemory]:
        """Get session memory for a player."""
        return self.session_memories.get(player_name)

    def get_opponent_model_manager(self) -> OpponentModelManager:
        """Get the opponent model manager."""
        return self.opponent_model_manager

    def apply_learned_adjustments(self, player_name: str,
                                  elastic_personality: Any) -> None:
        """Apply learned opponent patterns to adjust AI personality.

        Args:
            player_name: Name of the AI player
            elastic_personality: The player's ElasticPersonality object
        """
        if not elastic_personality:
            return

        # Get all opponent models for this player
        opponent_models = self.opponent_model_manager.get_all_models_for_observer(player_name)

        if not opponent_models:
            return

        # Average the tendencies of all opponents
        avg_tendencies = {
            'aggression_factor': 0,
            'bluff_frequency': 0,
            'vpip': 0,
            'fold_to_cbet': 0
        }
        count = 0

        for model in opponent_models.values():
            if model.tendencies.hands_observed >= 5:  # Need enough data
                avg_tendencies['aggression_factor'] += model.tendencies.aggression_factor
                avg_tendencies['bluff_frequency'] += model.tendencies.bluff_frequency
                avg_tendencies['vpip'] += model.tendencies.vpip
                avg_tendencies['fold_to_cbet'] += model.tendencies.fold_to_cbet
                count += 1

        if count > 0:
            for key in avg_tendencies:
                avg_tendencies[key] /= count

            # Apply adjustment to elastic personality
            elastic_personality.apply_learned_adjustment(avg_tendencies)

    def _get_chattiness(self, ai_player: Any) -> float:
        """Extract chattiness from AI player."""
        if hasattr(ai_player, 'elastic_personality'):
            return ai_player.elastic_personality.get_trait_value('chattiness')
        if hasattr(ai_player, 'personality_config'):
            traits = ai_player.personality_config.get('personality_traits', {})
            return traits.get('chattiness', 0.5)
        return 0.5

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            'game_id': self.game_id,
            'hand_count': self.hand_count,
            'initialized_players': list(self.initialized_players),
            'session_memories': {
                name: memory.to_dict()
                for name, memory in self.session_memories.items()
            },
            'opponent_models': self.opponent_model_manager.to_dict(),
            'completed_hands': [
                hand.to_dict() for hand in self.hand_recorder.completed_hands
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AIMemoryManager':
        """Deserialize from persistence."""
        manager = cls(game_id=data['game_id'])
        manager.hand_count = data.get('hand_count', 0)
        manager.initialized_players = set(data.get('initialized_players', []))

        # Restore session memories
        for name, memory_data in data.get('session_memories', {}).items():
            manager.session_memories[name] = SessionMemory.from_dict(memory_data)

        # Restore opponent models
        if 'opponent_models' in data:
            manager.opponent_model_manager = OpponentModelManager.from_dict(
                data['opponent_models']
            )

        # Restore completed hands
        for hand_data in data.get('completed_hands', []):
            hand = RecordedHand.from_dict(hand_data)
            manager.hand_recorder.completed_hands.append(hand)

        return manager
