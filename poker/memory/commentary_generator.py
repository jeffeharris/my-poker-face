"""
Commentary Generator for AI Players.

Generates end-of-hand commentary including reactions, reflections, and observations.
"""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

from core.llm import CallType, LLMClient
from ..prompt_manager import PromptManager
from ..config import COMMENTARY_ENABLED, is_development_mode
from .hand_history import RecordedHand
from .session_memory import SessionMemory

logger = logging.getLogger(__name__)


@dataclass
class HandCommentary:
    """AI-generated commentary about a completed hand."""
    player_name: str
    emotional_reaction: str
    strategic_reflection: str
    opponent_observations: List[str]
    table_comment: Optional[str]  # What they say out loud (if anything)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'player_name': self.player_name,
            'emotional_reaction': self.emotional_reaction,
            'strategic_reflection': self.strategic_reflection,
            'opponent_observations': self.opponent_observations,
            'table_comment': self.table_comment
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HandCommentary':
        return cls(
            player_name=data['player_name'],
            emotional_reaction=data['emotional_reaction'],
            strategic_reflection=data['strategic_reflection'],
            opponent_observations=data['opponent_observations'],
            table_comment=data.get('table_comment')
        )


class CommentaryGenerator:
    """Generates end-of-hand commentary for AI players."""

    def __init__(self, prompt_manager: Optional[PromptManager] = None,
                 game_id: Optional[str] = None, owner_id: Optional[str] = None):
        self.prompt_manager = prompt_manager or PromptManager(enable_hot_reload=is_development_mode())
        self.game_id = game_id
        self.owner_id = owner_id
        # Use dedicated LLM client with minimal reasoning for fast/cheap commentary
        self._llm_client = LLMClient(reasoning_effort="minimal")

    def _is_hand_interesting(
        self,
        hand: RecordedHand,
        player_name: str,
        big_blind: Optional[int] = None,
        chattiness: float = 0.5
    ) -> bool:
        """Determine if a hand is interesting enough to warrant commentary.

        Filters out mundane hands to reduce commentary spam.

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
            bool: True if the hand is worth commenting on
        """
        # === PRIORITY OVERRIDES (check first, bypass pot threshold) ===

        # All-ins are always interesting
        if any(a.action == 'all_in' for a in hand.actions):
            logger.debug("Hand interesting: all-in occurred")
            return True

        # Showdowns are interesting
        if hand.was_showdown:
            logger.debug("Hand interesting: showdown occurred")
            return True

        # Pressure situations: pot is >30% of any player's starting stack
        # Factor in chattiness - chatty players more likely to comment on pressure
        for player_info in hand.players:
            if player_info.starting_stack > 0:
                pressure_ratio = hand.pot_size / player_info.starting_stack
                # Use chattiness to modulate: high chattiness = lower threshold (0.3)
                # Low chattiness = higher threshold (0.6)
                pressure_threshold = 0.3 + (0.3 * (1.0 - chattiness))
                if pressure_ratio > pressure_threshold:
                    logger.debug(
                        f"Hand interesting: pressure situation "
                        f"(pot/stack={pressure_ratio:.2f} for {player_info.name})"
                    )
                    return True

        # === STANDARD FILTERS (only if no priority override) ===

        # Dynamic pot threshold: 5x big blind or fallback to $200
        min_pot_threshold = (big_blind * 5) if big_blind else 200

        # Small pots aren't interesting (unless overridden above)
        if hand.pot_size < min_pot_threshold:
            logger.debug(
                f"Hand not interesting: pot size {hand.pot_size} < {min_pot_threshold}"
            )
            return False

        # Simple fold-outs aren't interesting (player only folded, nothing else)
        player_actions = [a for a in hand.actions if a.player_name == player_name]
        if len(player_actions) == 1 and player_actions[0].action == 'fold':
            logger.debug(f"Hand not interesting: {player_name} only folded")
            return False

        # Pot is big enough and player was involved
        logger.debug(f"Hand interesting: pot {hand.pot_size} >= {min_pot_threshold}")
        return True

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

        # ALL players (including spectators) go through interest filtering
        # Spectators only heckle on hands worth heckling about
        if not self._is_hand_interesting(hand, player_name, big_blind, chattiness):
            logger.debug(f"Skipping commentary for {player_name}: hand not interesting")
            return None

        try:
            # Build context for the prompt
            hand_summary = self._build_hand_summary(hand, hand.was_showdown)
            winner_info = self._build_winner_info(hand)

            # Use override if provided (thread-safe path), otherwise compute from objects
            if session_context_override is not None:
                session_context = session_context_override
            else:
                session_context = session_memory.get_context_for_prompt(100) if session_memory else "First hand"

            # Handle spectator mode vs active player
            if is_eliminated:
                # Spectators weren't dealt cards, use spectator outcome
                cards_display = "(watching from the rail)"
                outcome_display = "spectating"
            else:
                # Active players always see their own cards
                cards_display = ", ".join(player_cards) if player_cards else "unknown"
                outcome_display = player_outcome

            # Render the prompt with spectator context if applicable
            prompt = self.prompt_manager.render_prompt(
                'end_of_hand_commentary',
                hand_summary=hand_summary,
                player_outcome=outcome_display,
                player_cards=cards_display,
                winner_info=winner_info,
                session_context=session_context,
                player_name=player_name,
                confidence=confidence,
                attitude=attitude,
                chattiness=chattiness,
                spectator_context=spectator_context or ""
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
            return HandCommentary(
                player_name=player_name,
                emotional_reaction=commentary_data.get('emotional_reaction', ''),
                strategic_reflection=commentary_data.get('strategic_reflection', ''),
                opponent_observations=commentary_data.get('opponent_observations', []),
                table_comment=commentary_data.get('would_say_aloud')
            )

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"Failed to generate commentary for {player_name}: {e}")
            # Return a simple fallback commentary
            return self._generate_fallback_commentary(
                player_name, player_outcome, hand, chattiness
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
                                     chattiness: float) -> HandCommentary:
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

        # Only include table comment if chatty
        table_comment = None
        if chattiness > 0.5:
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
