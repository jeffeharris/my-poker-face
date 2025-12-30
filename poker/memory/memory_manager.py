"""
AI Memory Manager - Central orchestrator for all memory systems.

Coordinates:
- Hand history recording
- Session memory per player
- Opponent modeling
- Commentary generation
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from .hand_history import HandHistoryRecorder, RecordedHand, WinnerInfo
from .session_memory import SessionMemory
from .opponent_model import OpponentModelManager
from .commentary_generator import CommentaryGenerator, HandCommentary
from ..config import COMMENTARY_ENABLED

logger = logging.getLogger(__name__)


class AIMemoryManager:
    """Orchestrates all memory systems for AI players in a game."""

    def __init__(self, game_id: str, db_path: Optional[str] = None):
        """Initialize the memory manager.

        Args:
            game_id: Unique identifier for the game
            db_path: Path to database for persistence (optional)
        """
        self.game_id = game_id
        self.db_path = db_path

        # Core systems
        self.hand_recorder = HandHistoryRecorder(game_id)
        self.opponent_model_manager = OpponentModelManager()
        self.commentary_generator = CommentaryGenerator()

        # Per-player session memories
        self.session_memories: Dict[str, SessionMemory] = {}

        # Tracking
        self.hand_count = 0
        self.initialized_players: set = set()

    def initialize_for_player(self, player_name: str) -> None:
        """Set up memory systems for an AI player.

        Args:
            player_name: Name of the AI player
        """
        if player_name in self.initialized_players:
            return

        # Create session memory
        self.session_memories[player_name] = SessionMemory(player_name)

        self.initialized_players.add(player_name)
        logger.info(f"Initialized memory systems for {player_name}")

    def on_hand_start(self, game_state: Any, hand_number: int) -> None:
        """Called when a new hand begins.

        Args:
            game_state: Current PokerGameState
            hand_number: The hand number in this game
        """
        self.hand_count = hand_number
        self.hand_recorder.start_hand(game_state, hand_number)
        logger.debug(f"Started recording hand #{hand_number}")

    def on_action(self, player_name: str, action: str, amount: int,
                  phase: str, pot_total: int) -> None:
        """Record an action and update opponent models.

        Args:
            player_name: Name of player who acted
            action: The action taken ('fold', 'check', 'call', 'raise', 'bet', 'all_in')
            amount: Amount added to pot
            phase: Current game phase ('PRE_FLOP', 'FLOP', 'TURN', 'RIVER')
            pot_total: Total pot after the action
        """
        # Record to hand history
        self.hand_recorder.record_action(player_name, action, amount, phase, pot_total)

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
            # Store for async commentary generation
            self._last_recorded_hand = recorded_hand
        except Exception as e:
            logger.error(f"Failed to complete hand recording: {e}")
            return {}

        # Update opponent models with showdown info
        if recorded_hand.was_showdown:
            for winner in recorded_hand.winners:
                for observer in self.initialized_players:
                    if observer != winner.name:
                        model = self.opponent_model_manager.get_model(observer, winner.name)
                        model.observe_showdown(won=True)

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

            # Extract notable events
            notable_events = self.commentary_generator.extract_notable_events(
                recorded_hand, player_name
            )

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

    def generate_commentary_for_hand(self, ai_players: Dict[str, Any]) -> Dict[str, HandCommentary]:
        """Generate commentary for the last completed hand.

        This can be called asynchronously after on_hand_complete(skip_commentary=True).
        All AI players' commentary is generated in parallel using ThreadPoolExecutor.

        Args:
            ai_players: Dict mapping player names to their AIPokerPlayer objects

        Returns:
            Dict mapping player names to their HandCommentary
        """
        if not hasattr(self, '_last_recorded_hand') or self._last_recorded_hand is None:
            logger.warning("No recorded hand available for commentary generation")
            return {}

        recorded_hand = self._last_recorded_hand
        commentaries: Dict[str, HandCommentary] = {}

        if not COMMENTARY_ENABLED:
            return commentaries

        # Build list of players to generate commentary for
        players_to_process = [
            player_name for player_name in ai_players
            if player_name in self.session_memories
        ]

        if not players_to_process:
            return commentaries

        def generate_single_commentary(player_name: str) -> Tuple[str, Optional[HandCommentary]]:
            """Generate commentary for a single player. Returns (player_name, commentary)."""
            ai_player = ai_players[player_name]
            session_memory = self.session_memories[player_name]
            outcome = recorded_hand.get_player_outcome(player_name)
            player_cards = recorded_hand.hole_cards.get(player_name, [])

            try:
                commentary = self.commentary_generator.generate_commentary(
                    player_name=player_name,
                    hand=recorded_hand,
                    player_outcome=outcome,
                    player_cards=player_cards,
                    session_memory=session_memory,
                    opponent_models=self.opponent_model_manager.get_all_models_for_observer(player_name),
                    confidence=getattr(ai_player, 'confidence', 'neutral'),
                    attitude=getattr(ai_player, 'attitude', 'neutral'),
                    chattiness=self._get_chattiness(ai_player),
                    assistant=getattr(ai_player, 'assistant', None)
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
