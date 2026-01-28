"""
Commentary Generator for AI Players.

Generates end-of-hand commentary including reactions, reflections, and observations.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from core.llm import CallType, LLMClient
from flask_app.config import get_default_model, get_default_provider
from ..moment_analyzer import MomentAnalyzer
from ..prompt_manager import PromptManager, DRAMA_CONTEXTS, TONE_MODIFIERS
from ..config import COMMENTARY_ENABLED, is_development_mode
from .hand_history import RecordedHand
from .session_memory import SessionMemory

logger = logging.getLogger(__name__)


@dataclass
class DecisionPlan:
    """Captured AI decision reasoning from a single action.

    Stores the AI's strategy and inner thoughts at the time of a decision,
    enabling post-hand reflection on whether the plan worked.
    """
    hand_number: int
    phase: str                        # PRE_FLOP, FLOP, TURN, RIVER
    player_name: str
    hand_strategy: Optional[str]      # "Check-raise to trap"
    inner_monologue: str
    action: str
    amount: int
    pot_size: int
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hand_number': self.hand_number,
            'phase': self.phase,
            'player_name': self.player_name,
            'hand_strategy': self.hand_strategy,
            'inner_monologue': self.inner_monologue,
            'action': self.action,
            'amount': self.amount,
            'pot_size': self.pot_size,
            'timestamp': self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DecisionPlan':
        timestamp = data.get('timestamp')
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now()
        return cls(
            hand_number=data['hand_number'],
            phase=data['phase'],
            player_name=data['player_name'],
            hand_strategy=data.get('hand_strategy'),
            inner_monologue=data.get('inner_monologue', ''),
            action=data['action'],
            amount=data.get('amount', 0),
            pot_size=data.get('pot_size', 0),
            timestamp=timestamp
        )


@dataclass
class HandCommentary:
    """AI-generated commentary about a completed hand.

    Extended to support strategic reflection persistence and feedback loop.
    """
    player_name: str
    emotional_reaction: str
    strategic_reflection: str
    opponent_observations: List[str]
    table_comment: Optional[str]  # What they say out loud (if anything)

    # NEW FIELDS for reflection persistence
    decision_plans: List[DecisionPlan] = field(default_factory=list)
    key_insight: Optional[str] = None   # One-liner for session context
    hand_number: Optional[int] = None   # For persistence lookup

    def to_dict(self) -> Dict[str, Any]:
        return {
            'player_name': self.player_name,
            'emotional_reaction': self.emotional_reaction,
            'strategic_reflection': self.strategic_reflection,
            'opponent_observations': self.opponent_observations,
            'table_comment': self.table_comment,
            'decision_plans': [p.to_dict() for p in self.decision_plans],
            'key_insight': self.key_insight,
            'hand_number': self.hand_number
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HandCommentary':
        decision_plans = [
            DecisionPlan.from_dict(p) for p in data.get('decision_plans', [])
        ]
        return cls(
            player_name=data['player_name'],
            emotional_reaction=data['emotional_reaction'],
            strategic_reflection=data['strategic_reflection'],
            opponent_observations=data['opponent_observations'],
            table_comment=data.get('table_comment'),
            decision_plans=decision_plans,
            key_insight=data.get('key_insight'),
            hand_number=data.get('hand_number')
        )


class CommentaryGenerator:
    """Generates end-of-hand commentary for AI players."""

    def __init__(self, prompt_manager: Optional[PromptManager] = None,
                 game_id: Optional[str] = None, owner_id: Optional[str] = None):
        self.prompt_manager = prompt_manager or PromptManager(enable_hot_reload=is_development_mode())
        self.game_id = game_id
        self.owner_id = owner_id
        # Use dedicated LLM client with minimal reasoning for fast/cheap commentary
        self._llm_client = LLMClient(model=get_default_model(), provider=get_default_provider(), reasoning_effort="minimal")

    def _should_reflect(
        self,
        hand: RecordedHand,
        player_name: str,
        is_eliminated: bool = False
    ) -> bool:
        """Determine if a hand warrants reflection (internal learning).

        Lower threshold than speaking - we want to learn from routine hands too.
        Reflects on any hand where the player was meaningfully involved.

        Args:
            hand: The completed hand record
            player_name: Name of the player considering reflection
            is_eliminated: Whether player is eliminated (spectators always reflect on drama)

        Returns:
            bool: True if worth reflecting on
        """
        # Eliminated players (spectators) only reflect on dramatic hands
        if is_eliminated:
            return self._should_speak(hand, player_name)

        # Get player's actions in this hand
        player_actions = [a for a in hand.actions if a.player_name == player_name]

        # No actions = wasn't in the hand (shouldn't happen, but safety check)
        if not player_actions:
            return False

        # Pure preflop fold with no other action = skip reflection
        # (Nothing interesting to learn from folding 72o preflop)
        if len(player_actions) == 1 and player_actions[0].action == 'fold':
            if player_actions[0].phase == 'PRE_FLOP':
                logger.debug(f"Skipping reflection for {player_name}: preflop fold only")
                return False

        # Player saw the flop or made multiple decisions = worth reflecting
        saw_flop = any(a.phase in ('FLOP', 'TURN', 'RIVER') for a in player_actions)
        multiple_actions = len(player_actions) > 1

        if saw_flop or multiple_actions:
            logger.debug(f"Reflecting for {player_name}: saw_flop={saw_flop}, actions={len(player_actions)}")
            return True

        # Won the hand (even preflop) = worth noting
        if hand.winners and any(w.name == player_name for w in hand.winners):
            logger.debug(f"Reflecting for {player_name}: won the hand")
            return True

        # Default: reflect if involved at all (called, raised preflop)
        non_fold_actions = [a for a in player_actions if a.action != 'fold']
        if non_fold_actions:
            logger.debug(f"Reflecting for {player_name}: made non-fold action preflop")
            return True

        return False

    def _should_speak(
        self,
        hand: RecordedHand,
        player_name: str,
        big_blind: Optional[int] = None,
        chattiness: float = 0.5
    ) -> bool:
        """Determine if a hand is dramatic enough to warrant table talk.

        Higher threshold than reflection - only speak on interesting hands.

        Priority (checked first, override pot size):
        1. All-ins - always dramatic
        2. Showdowns - cards revealed
        3. Pressure situations - significant % of someone's stack

        Args:
            hand: The completed hand record
            player_name: Name of the player considering commentary
            big_blind: Current big blind for dynamic thresholds (fallback to $200)
            chattiness: Player's chattiness level (affects pressure situation decisions)

        Returns:
            bool: True if the hand is worth speaking about
        """
        # === PRIORITY OVERRIDES (check first, bypass pot threshold) ===

        # All-ins are always dramatic
        if any(a.action == 'all_in' for a in hand.actions):
            logger.debug("Should speak: all-in occurred")
            return True

        # Showdowns are dramatic
        if hand.was_showdown:
            logger.debug("Should speak: showdown occurred")
            return True

        # Pressure situations: pot is >30% of any player's starting stack
        for player_info in hand.players:
            if player_info.starting_stack > 0:
                pressure_ratio = hand.pot_size / player_info.starting_stack
                pressure_threshold = 0.3 + (0.3 * (1.0 - chattiness))
                if pressure_ratio > pressure_threshold:
                    logger.debug(
                        f"Should speak: pressure situation "
                        f"(pot/stack={pressure_ratio:.2f} for {player_info.name})"
                    )
                    return True

        # === STANDARD FILTERS ===

        # Dynamic pot threshold: 5x big blind or fallback to $200
        min_pot_threshold = (big_blind * 5) if big_blind else 200

        # Small pots aren't worth talking about
        if hand.pot_size < min_pot_threshold:
            logger.debug(f"Should not speak: pot {hand.pot_size} < {min_pot_threshold}")
            return False

        # Pot is big enough
        logger.debug(f"Should speak: pot {hand.pot_size} >= {min_pot_threshold}")
        return True

    def _analyze_hand_drama(
        self,
        hand: RecordedHand,
        player_outcome: str,
        big_blind: Optional[int] = None
    ) -> dict:
        """Derive drama level and tone from a completed hand.

        Detects drama factors from post-hand RecordedHand data (all_in,
        showdown, big_pot, heads_up) and delegates level determination to
        MomentAnalyzer._determine_level(). The live analyzer detects
        additional factors (big_bet, huge_raise, late_stage) from game state.
        """
        factors = []

        # All-in occurred
        if any(a.action == 'all_in' for a in hand.actions):
            factors.append('all_in')

        # Showdown
        if hand.was_showdown:
            factors.append('showdown')

        # Big pot detection uses BB-relative threshold (20+ BB) rather than
        # stack-relative like MomentAnalyzer.is_big_pot(). This is intentional:
        # - Live analysis (MomentAnalyzer): "Is this pot significant to ME right now?"
        # - Post-hand narrative: "Was this objectively a big pot?" (standard poker metric)
        # Note: _should_speak() separately uses stack-relative pressure ratio.
        if big_blind and big_blind > 0:
            pot_bb = hand.pot_size / big_blind
            if pot_bb >= 20:
                factors.append('big_pot')

        # Heads-up (only 2 players involved in actions)
        active_players = set(a.player_name for a in hand.actions)
        if len(active_players) == 2:
            factors.append('heads_up')

        level = MomentAnalyzer._determine_level(factors)

        # Determine post-hand tone based on outcome and drama
        if level == 'climactic' and player_outcome == 'won':
            tone = 'triumphant'
        elif level in ('high_stakes', 'climactic') and player_outcome in ('lost', 'folded'):
            tone = 'desperate'
        elif player_outcome == 'won' and level in ('notable', 'high_stakes'):
            tone = 'confident'
        else:
            tone = 'neutral'

        return {'level': level, 'tone': tone}

    @staticmethod
    def _format_beats_for_chat(stage_direction) -> Optional[str]:
        """Convert stage_direction to chat-ready string.

        Frontend's parseBeats() splits on newlines and detects *action* syntax.
        """
        if isinstance(stage_direction, list):
            beats = [b.strip() for b in stage_direction if isinstance(b, str) and b.strip()]
            return "\n".join(beats) if beats else None
        if isinstance(stage_direction, str):
            return stage_direction.strip() or None
        return None

    def generate_commentary(self,
                           player_name: str,
                           hand: RecordedHand,
                           player_outcome: str,
                           player_cards: List[str],
                           session_memory: Optional[SessionMemory],
                           opponent_models: Optional[Dict[str, Any]],
                           confidence: str,
                           attitude: str,
                           chattiness: float,
                           assistant: Any,
                           session_context_override: Optional[str] = None,
                           opponent_context_override: Optional[str] = None,
                           big_blind: Optional[int] = None,
                           is_eliminated: bool = False,
                           spectator_context: Optional[str] = None) -> Optional[HandCommentary]:
        """Generate personalized commentary for a player about a hand.

        Args:
            player_name: Name of the AI player
            hand: The completed hand record
            player_outcome: 'won', 'lost', or 'folded' (or 'spectating' if eliminated)
            player_cards: The player's hole cards (empty if spectator)
            session_memory: Player's session memory (optional, ignored if override provided)
            opponent_models: Dict of opponent models (optional, ignored if override provided)
            confidence: Player's current confidence level
            attitude: Player's current attitude
            chattiness: 0-1 chattiness level
            assistant: The AI assistant to use for generation
            session_context_override: Pre-computed session context string (for thread safety)
            opponent_context_override: Pre-computed opponent summary string (for thread safety)
            big_blind: Current big blind for dynamic thresholds
            is_eliminated: Whether player is eliminated (spectator mode)
            spectator_context: Context for spectators (who eliminated them, position, etc.)

        Returns:
            HandCommentary or None if commentary generation is disabled/fails
        """
        if not COMMENTARY_ENABLED:
            return None

        # Check if hand warrants reflection (lower bar than speaking)
        if not self._should_reflect(hand, player_name, is_eliminated):
            logger.debug(f"Skipping reflection for {player_name}: not involved enough")
            return None

        # Determine if hand is dramatic enough to speak about (higher bar)
        should_speak = self._should_speak(hand, player_name, big_blind, chattiness)

        try:
            # Build context for the prompt
            hand_summary = self._build_hand_summary(hand, hand.was_showdown)
            winner_info = self._build_winner_info(hand)

            # Use override if provided (thread-safe path), otherwise compute from objects
            if session_context_override is not None:
                session_context = session_context_override
            else:
                session_context = session_memory.get_context_for_prompt(100) if session_memory else "First hand"

            # Use override if provided, otherwise build from opponent_models
            if opponent_context_override is not None:
                opponent_context = opponent_context_override
            else:
                opponent_context = ""  # Will be empty if not provided

            # Format opponent context for prompt (add newline prefix if non-empty)
            if opponent_context:
                opponent_context = f"\nYour reads on opponents:\n{opponent_context}"

            # Handle spectator mode vs active player
            if is_eliminated:
                # Spectators weren't dealt cards, use spectator outcome
                cards_display = "(watching from the rail)"
                outcome_display = "spectating"
            else:
                # Active players always see their own cards
                cards_display = ", ".join(player_cards) if player_cards else "unknown"
                outcome_display = player_outcome

            # Compute drama context for intensity calibration
            drama = self._analyze_hand_drama(hand, outcome_display, big_blind)
            drama_level = drama['level']
            drama_tone = drama['tone']
            drama_text = DRAMA_CONTEXTS.get(drama_level, '')
            tone_modifier = TONE_MODIFIERS.get(drama_tone, '')
            drama_guidance = f"{drama_text}{tone_modifier}" if drama_text else ""

            # Render the prompt with spectator context if applicable
            prompt = self.prompt_manager.render_prompt(
                'end_of_hand_commentary',
                hand_summary=hand_summary,
                player_outcome=outcome_display,
                player_cards=cards_display,
                winner_info=winner_info,
                session_context=session_context,
                opponent_context=opponent_context,
                player_name=player_name,
                confidence=confidence,
                attitude=attitude,
                chattiness=chattiness,
                spectator_context=spectator_context or "",
                drama_guidance=drama_guidance
            )

            # Use lightweight commentary-specific system prompt (not the decision-making one)
            system_prompt = self.prompt_manager.render_prompt(
                'poker_player_commentary',
                name=player_name,
                attitude=attitude,
                confidence=confidence
            )

            # Build messages for LLM call
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]

            # Use internal LLM client with minimal reasoning for fast/cheap commentary
            llm_response = self._llm_client.complete(
                messages=messages,
                json_format=True,
                call_type=CallType.COMMENTARY,
                game_id=self.game_id,
                owner_id=self.owner_id,
                player_name=player_name,
                hand_number=hand.hand_number,
                prompt_template='end_of_hand_commentary'
            )

            # Parse response
            commentary_data = json.loads(llm_response.content)

            # Build commentary object
            # Suppress table_comment if hand isn't dramatic enough to speak about
            # Handle stage_direction as list of beats (new) or plain string (legacy)
            raw_stage_direction = commentary_data.get('stage_direction') if should_speak else None
            table_comment = self._format_beats_for_chat(raw_stage_direction)

            return HandCommentary(
                player_name=player_name,
                emotional_reaction=commentary_data.get('emotional_reaction', ''),
                strategic_reflection=commentary_data.get('strategic_reflection', ''),
                opponent_observations=commentary_data.get('opponent_observations', []),
                table_comment=table_comment
            )

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to generate commentary for {player_name}: {e}")
            # Return a simple fallback commentary
            return self._generate_fallback_commentary(
                player_name, player_outcome, hand, chattiness, should_speak
            )

    def generate_quick_reaction(self,
                               player_name: str,
                               player_outcome: str,
                               pot_size: int,
                               chattiness: float) -> Optional[str]:
        """Generate a quick one-liner reaction without using the LLM.

        This is faster and cheaper for simple reactions.
        """
        if chattiness < 0.3:
            return None

        # Template-based reactions
        if player_outcome == 'won':
            reactions = [
                "Nice hand!",
                "I'll take that.",
                "Finally!",
                "That's more like it.",
            ]
            if pot_size > 1000:
                reactions.extend([
                    "Now that's a pot worth winning!",
                    "Big one there!",
                ])
        elif player_outcome == 'folded':
            if chattiness < 0.5:
                return None  # Don't comment on folds usually
            reactions = [
                "Had to let that one go.",
                "Not my hand.",
                "I'll pick my spots.",
            ]
        else:  # lost
            reactions = [
                "Nice hand.",
                "You got me.",
                "Next one...",
            ]
            if pot_size > 1000:
                reactions.extend([
                    "That one hurt.",
                    "Ouch.",
                ])

        # Simple random selection (avoid importing random just for this)
        import random
        return random.choice(reactions) if reactions else None

    def _build_hand_summary(
        self,
        hand: RecordedHand,
        include_showdown_cards: bool = False
    ) -> str:
        """Build a summary of the hand for the prompt.

        Args:
            hand: The completed hand record
            include_showdown_cards: If True and was showdown, include shown hole cards

        Returns:
            String summary of the hand
        """
        parts = []

        # Community cards
        if hand.community_cards:
            parts.append(f"Board: {', '.join(hand.community_cards)}")

        # Action summary
        action_counts = {}
        for action in hand.actions:
            key = action.player_name
            if key not in action_counts:
                action_counts[key] = []
            action_counts[key].append(action.action)

        for player, actions in action_counts.items():
            parts.append(f"{player}: {', '.join(actions)}")

        # Pot size
        parts.append(f"Final pot: ${hand.pot_size}")

        # Include showdown cards if applicable
        if include_showdown_cards and hand.was_showdown and hand.hole_cards:
            shown_cards = []
            for winner in hand.winners:
                if winner.name in hand.hole_cards:
                    cards = hand.hole_cards[winner.name]
                    shown_cards.append(f"{winner.name} showed {', '.join(cards)}")
            if shown_cards:
                parts.append("Showdown: " + "; ".join(shown_cards))

        return "\n".join(parts)

    def _build_winner_info(self, hand: RecordedHand) -> str:
        """Build winner info string."""
        if not hand.winners:
            return "No winner"

        winner_parts = []
        for winner in hand.winners:
            if winner.hand_name:
                winner_parts.append(f"{winner.name} won ${winner.amount_won} with {winner.hand_name}")
            else:
                winner_parts.append(f"{winner.name} won ${winner.amount_won}")

        return ", ".join(winner_parts)

    def _generate_fallback_commentary(self,
                                     player_name: str,
                                     player_outcome: str,
                                     hand: RecordedHand,
                                     chattiness: float,
                                     should_speak: bool = True) -> HandCommentary:
        """Generate simple fallback commentary without LLM."""
        if player_outcome == 'won':
            emotional = "Feeling good about that one."
            strategic = "Played it well."
        elif player_outcome == 'folded':
            emotional = "Had to make the smart play."
            strategic = "Saved my chips for a better spot."
        else:
            emotional = "That's poker."
            strategic = "Sometimes the cards don't go your way."

        # Only include table comment if chatty AND hand is dramatic enough
        table_comment = None
        if should_speak and chattiness > 0.5:
            table_comment = self.generate_quick_reaction(
                player_name, player_outcome, hand.pot_size, chattiness
            )

        return HandCommentary(
            player_name=player_name,
            emotional_reaction=emotional,
            strategic_reflection=strategic,
            opponent_observations=[],
            table_comment=table_comment
        )

    def should_comment(self, chattiness: float, emotional_impact: float) -> bool:
        """Determine if the AI should speak aloud based on chattiness and impact."""
        # Higher emotional impact = more likely to speak
        # Higher chattiness = more likely to speak
        threshold = 0.5 - (emotional_impact * 0.3) - (chattiness * 0.2)
        return chattiness > threshold

    def extract_notable_events(self, hand: RecordedHand, player_name: str) -> List[str]:
        """Extract notable events from a hand for a specific player's perspective."""
        events = []

        # Check for all-ins
        for action in hand.actions:
            if action.action == 'all_in':
                if action.player_name == player_name:
                    events.append("Went all-in")
                else:
                    events.append(f"{action.player_name} went all-in")

        # Check for big pots
        if hand.pot_size > 1000:
            events.append(f"Big pot (${hand.pot_size})")

        # Check for showdown
        if hand.was_showdown:
            # Find if any bluffs were caught
            for winner in hand.winners:
                if winner.hand_rank and winner.hand_rank >= 8:  # Weak hand
                    events.append(f"Potential bluff by {winner.name}")

        # Check player's outcome
        outcome = hand.get_player_outcome(player_name)
        if outcome == 'won' and hand.pot_size > 500:
            events.append("Won a nice pot")
        elif outcome == 'lost' and hand.pot_size > 500:
            events.append("Lost a significant pot")

        return events
