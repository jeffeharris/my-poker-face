"""Commentary filtering logic - determines who can comment when."""
import logging
from typing import Set

from .hand_history import RecordedHand

logger = logging.getLogger(__name__)


def should_player_comment(
    player_name: str,
    recorded_hand: RecordedHand,
    is_eliminated: bool = False
) -> bool:
    """Determine if player should generate post-hand commentary.

    Filtering rules:
    1. Preflop with all-in: Everyone can comment (dramatic moment)
    2. Preflop fold-out (no all-in): Only winner can comment
    3. Post-flop hands: Standard interest filtering applies later

    Args:
        player_name: Name of the player considering commentary
        recorded_hand: The completed hand record
        is_eliminated: Whether player is eliminated from tournament (spectator)

    Returns:
        bool: True if player should proceed to commentary generation
    """
    # Detect preflop fold-out: no community cards dealt
    ended_preflop = len(recorded_hand.community_cards) == 0

    if ended_preflop:
        # Check if there was an all-in - that's always worth commenting on
        had_all_in = any(a.action == 'all_in' for a in recorded_hand.actions)

        if had_all_in:
            # All-in preflop is dramatic - everyone can comment
            logger.debug(f"{player_name} can comment: preflop all-in occurred")
            return True

        # Simple preflop fold - only winner can comment
        # (others already spoke when folding, spectators have nothing to heckle)
        winner_names: Set[str] = {w.name for w in recorded_hand.winners}
        if player_name not in winner_names:
            logger.debug(
                f"Skipping {player_name}: preflop fold-out, no all-in, not winner"
            )
            return False
        logger.debug(f"{player_name} won preflop - allowing commentary")

    # Post-flop hands or preflop winners proceed to interest filtering
    return True
