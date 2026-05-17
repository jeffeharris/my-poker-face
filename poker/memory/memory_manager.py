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
from .cbet_detector import CbetDetector
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

        # C-bet tracking — delegated to a CbetDetector so simulator
        # paths that bypass MemoryManager can drive the same state
        # machine. Reset on hand start.
        self._cbet_detector = CbetDetector()

        # Phase 6.7a: per-street live aggressor for spot-aware exploitation.
        # Reset on hand start AND each street transition. Updated only on
        # accepted postflop bet/raise/all_in. Preflop aggressor still lives
        # in _preflop_raiser; this field is the post-flop counterpart.
        self._recent_aggressor_name: Optional[str] = None
        self._current_street: Optional[str] = None

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

    @property
    def last_preflop_aggressor(self) -> Optional[str]:
        """Name of the last player to make an accepted preflop raise/all-in.

        Phase 6.6: surfaces the c-bet detector's preflop-aggressor state
        for HU c-bet exploitation gating. Resets at hand start. Reads
        from accepted-action recording, not controller intent.
        """
        return self._cbet_detector.preflop_aggressor

    @property
    def recent_aggressor_name(self) -> Optional[str]:
        """Name of the most recent postflop aggressor on the current street.

        Phase 6.7a: surfaces the per-street live aggressor for
        select_primary_aggressor() tie-break in multiway facing-bet
        spots. Reset on hand start and on each street transition.
        Updated only on accepted postflop bet/raise/all_in.

        Returns None on preflop streets (preflop aggression uses
        `last_preflop_aggressor` instead) and whenever no postflop
        aggression has occurred on the current street yet.
        """
        return self._recent_aggressor_name

    def record_preflop_aggression(self, player_name: str) -> None:
        """Manually record a preflop aggressor (test / sim-path hook).

        Production paths reach this state via `on_action()` and shouldn't
        call this directly. Simulators that bypass MemoryManager (e.g.
        simulate_bb100, analyze_6max_vs_rules) drive their own
        CbetDetector instead; this method exists for tests that hold a
        MemoryManager and want to seed the aggressor without firing a
        full on_action.
        """
        self._cbet_detector.record_preflop_aggression(player_name)

    def record_postflop_aggression(self, player_name: str, phase: str) -> None:
        """Manually record a postflop aggressor (sim-path hook).

        Production paths reach this state via `on_action()`; sims that
        bypass MemoryManager (analyze_6max_vs_rules, simulate_bb100) use
        this to feed the same signal. Caller is responsible for street
        transition (passing the correct phase) — this method does not
        reset on its own.
        """
        if phase in ('FLOP', 'TURN', 'RIVER'):
            self._recent_aggressor_name = player_name
            self._current_street = phase

    def initialize_for_player(self, player_name: str, personality_id: Optional[str] = None) -> None:
        """Set up memory systems for an AI player.

        Args:
            player_name: Name of the AI player
            personality_id: Stable personality_id (slug) for this player.
                Passed through to the opponent_model_manager so cross-session
                callers (relationship layer, AI bankrolls) can key on the
                id rather than the display name. None for AI players whose
                personality predates v85 or wasn't resolved at startup.
        """
        if player_name in self.initialized_players:
            return

        # Create session memory with DB backing if persistence is available
        session_memory = SessionMemory(player_name)
        if self._persistence:
            session_memory.set_hand_history_repo(self._persistence, self.game_id)
        self.session_memories[player_name] = session_memory

        # Register the player's stable personality_id with the opponent
        # model manager so newly-created OpponentModels carry it and
        # save_opponent_models persists it. Always call even when None,
        # so the registry distinguishes "known no id" (human guests,
        # pre-v85 personalities) from "never registered."
        self.opponent_model_manager.register_player_id(player_name, personality_id)

        self.initialized_players.add(player_name)
        logger.info(
            f"Initialized memory systems for {player_name} "
            f"(personality_id={personality_id!r})"
        )

    def initialize_human_observer(self, player_name: str, personality_id: Optional[str] = None) -> None:
        """Add human player as an observer for opponent modeling.

        Unlike AI players, humans don't need session memory or other AI systems,
        but they should still track observations about opponents.

        Args:
            player_name: Name of the human player
            personality_id: Almost always None for humans (they aren't
                personalities). Plumbed through anyway so callers can
                use a single uniform path for both player types.
        """
        if player_name in self.initialized_players:
            return

        # Register with the opponent model manager. Most human players
        # have personality_id=None; explicitly recording that prevents
        # repeated name-lookup attempts.
        self.opponent_model_manager.register_player_id(player_name, personality_id)

        self.initialized_players.add(player_name)
        logger.info(
            f"Initialized human observer: {player_name} "
            f"(personality_id={personality_id!r})"
        )

    def on_hand_start(self, game_state: Any, hand_number: int, deck_seed: Optional[int] = None) -> None:
        """Called when a new hand begins.

        Args:
            game_state: Current PokerGameState
            hand_number: The hand number in this game
            deck_seed: Optional deterministic deck seed used for this hand
        """
        self.hand_count = hand_number
        self.hand_recorder.start_hand(game_state, hand_number, deck_seed=deck_seed)

        # Reset c-bet tracking for new hand.
        self._cbet_detector.reset_for_new_hand()

        # Phase 6.7a: reset per-street aggressor state.
        self._recent_aggressor_name = None
        self._current_street = None

        # Phase 6/6.5: record that each opponent was dealt this hand. This
        # is the correct denominator for VPIP/PFR/all_in_frequency — opponents
        # who fold before action reaches them never trigger observe_action,
        # so hands_dealt has to be incremented independently.
        all_player_names = [p.name for p in game_state.players]
        for observer in self.initialized_players:
            opponents = [n for n in all_player_names if n != observer]
            if opponents:
                self.opponent_model_manager.record_hand_dealt(
                    observer=observer,
                    opponents=opponents,
                    hand_number=hand_number,
                )

        logger.debug(f"Started recording hand #{hand_number}")

    def record_blinds(self, game_state: Any) -> None:
        """Record blind posts as actions at the start of a hand.

        Args:
            game_state: Current PokerGameState with table_positions and current_ante
        """
        if not self.hand_recorder.current_hand:
            logger.warning("record_blinds called but no hand in progress")
            return

        table_positions = getattr(game_state, 'table_positions', {})
        bb_amount = game_state.current_ante
        sb_amount = bb_amount // 2

        pot_running = 0

        # Record SB post
        sb_player = table_positions.get('SB')
        if sb_player:
            pot_running += sb_amount
            self.hand_recorder.record_action(
                player_name=sb_player,
                action='post_blind',
                amount=sb_amount,
                phase='PRE_FLOP',
                pot_total=pot_running
            )

        # Record BB post
        bb_player = table_positions.get('BB')
        if bb_player:
            pot_running += bb_amount
            self.hand_recorder.record_action(
                player_name=bb_player,
                action='post_blind',
                amount=bb_amount,
                phase='PRE_FLOP',
                pot_total=pot_running
            )

        logger.debug(f"Recorded blinds: SB={sb_player}(${sb_amount}), BB={bb_player}(${bb_amount})")

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

        # Phase 6.7a: per-street live aggressor reset. When the phase
        # changes between actions, the previous street's aggression no
        # longer applies. Detect the transition off the prior action's
        # phase rather than off a separate street-transition hook so
        # all callers go through one path.
        if self._current_street != phase:
            self._recent_aggressor_name = None
            self._current_street = phase

        # Phase 7.5 Step 0: capture was_facing_bet BEFORE the current
        # action updates _recent_aggressor_name. The snapshot reflects
        # the state the actor SAW at decision time. On postflop streets,
        # facing a bet = there's a prior aggressor on this street whose
        # bet hasn't been folded out. The actor's own previous action
        # on this street (if they were the prior aggressor) is treated
        # as "not facing a bet" — they were free to act when they bet,
        # and someone re-raising them is what would make them face a
        # bet next.
        #
        # Preflop extension (for opportunity-normalized VPIP/PFR):
        # facing a bet preflop = a live RAISE above the blind has been
        # made by someone other than the current player. Forced blind
        # posts ('sb'/'bb') are not raises and don't count. The
        # preflop aggressor lives on the cbet detector and is read
        # BEFORE cbet_detector.record_action updates it below.
        if phase in ('FLOP', 'TURN', 'RIVER'):
            was_facing_bet = (
                self._recent_aggressor_name is not None
                and self._recent_aggressor_name != player_name
            )
        elif phase == 'PRE_FLOP':
            prior_raiser = self._cbet_detector.preflop_aggressor
            was_facing_bet = (
                prior_raiser is not None and prior_raiser != player_name
            )
        else:
            was_facing_bet = False

        # Phase 6.7a: postflop live aggressor — last accepted bet/raise/
        # all_in on flop/turn/river. Used by select_primary_aggressor()
        # to disambiguate tied-bet spots when multiple opponents called.
        if phase in ('FLOP', 'TURN', 'RIVER') and action in ('bet', 'raise', 'all_in'):
            self._recent_aggressor_name = player_name

        # C-bet detection (preflop aggressor → flop bet → response). The
        # detector also owns the preflop-aggressor field that Phase 6.6's
        # `last_preflop_aggressor` property surfaces.
        cbet_responses = self._cbet_detector.record_action(
            player_name=player_name, action=action, phase=phase,
            active_players=active_players,
        )
        for opp_name, folded in cbet_responses:
            logger.debug(
                f"{opp_name} {'folded to' if folded else 'called/raised'} c-bet"
            )
            for observer in self.initialized_players:
                if observer != opp_name:
                    model = self.opponent_model_manager.get_model(observer, opp_name)
                    model.tendencies.update_fold_to_cbet(folded)

        # Phase 8.1a: drain PFR-attempt events (typically zero or one
        # per call). When the preflop aggressor takes their first
        # flop action with a clean c-bet decision (bet/raise/check
        # without anyone donk-betting ahead of them), apply the
        # attempt to every observer's model of that player.
        for pfr_name, attempted in self._cbet_detector.consume_pfr_attempt_events():
            logger.debug(
                f"{pfr_name} {'attempted' if attempted else 'declined'} c-bet"
            )
            for observer in self.initialized_players:
                if observer != pfr_name:
                    model = self.opponent_model_manager.get_model(observer, pfr_name)
                    model.tendencies.update_cbet_attempt(attempted)

        # Update opponent models for all observers (including self-observation
        # for coaching stats like VPIP/PFR)
        for observer in self.initialized_players:
            self.opponent_model_manager.observe_action(
                observer=observer,
                opponent=player_name,
                action=action,
                phase=phase,
                is_voluntary=(action not in ('sb', 'bb')),
                hand_number=self.hand_count,
                was_facing_bet=was_facing_bet,
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

            # Skip RuleBots - they don't use LLM commentary
            if getattr(ai_player, 'is_rule_based', False):
                logger.debug(f"Skipping commentary for {player_name}: RuleBot (no LLM)")
                continue

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
            elastic_personality: The player's personality object (unused, pass None)
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
            if model.tendencies.hands_observed >= 15:  # Need enough data (sync with poker/config.py)
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
        """Extract chattiness/table_talk from AI player.

        Checks for new 'table_talk' trait first, falls back to 'chattiness'.
        """
        if hasattr(ai_player, 'elastic_personality'):
            # Try new trait name first, fall back to old
            value = ai_player.elastic_personality.get_trait_value('table_talk')
            if value is None:
                value = ai_player.elastic_personality.get_trait_value('chattiness')
            return value if value is not None else 0.5
        if hasattr(ai_player, 'personality_config'):
            traits = ai_player.personality_config.get('personality_traits', {})
            return traits.get('table_talk', traits.get('chattiness', 0.5))
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
