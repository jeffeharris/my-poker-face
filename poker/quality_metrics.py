"""Quality metrics for detecting suspicious play patterns.

This module provides shared logic for categorizing all-in decisions
and computing quality indicators across the codebase.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Stack depth thresholds (in big blinds)
SHORT_STACK_BB = 10   # <= 10 BB: Short stack all-ins are defensible
MARGINAL_STACK_BB = 15  # 11-15 BB: Marginal territory

# Bluff detection threshold
BLUFF_THRESHOLD = 50  # bluff_likelihood >= 50 is considered intentional bluff

# Hand quality threshold
TRASH_EQUITY = 0.25  # Equity below this is considered trash hand


def categorize_allin_row(
    stack_bb: Optional[float],
    ai_response: Optional[str],
    equity: Optional[float]
) -> Optional[str]:
    """Categorize a single all-in decision as suspicious, marginal, or defensible.

    Uses 3-tier stack depth detection:
    - Short (<=10BB): Filtered out as defensible
    - Marginal (11-15BB): Tracked as 'marginal'
    - Deep (>15BB): Tracked as 'suspicious'

    A "suspicious all-in" requires:
    - bluff_likelihood < 50 (AI thinks it has a real hand)
    - Trash hand: hand_strength contains "high card" OR equity < 0.25

    Args:
        stack_bb: Stack size in big blinds at time of all-in
        ai_response: JSON string of AI response (contains bluff_likelihood, hand_strength)
        equity: Pre-computed equity value from decision analysis

    Returns:
        'suspicious': Deep stack trash all-in (not a bluff)
        'marginal': Marginal stack trash all-in (not a bluff)
        None: Defensible (short stack, intentional bluff, or non-trash hand)
    """
    # Parse AI response
    try:
        resp = json.loads(ai_response) if ai_response else {}
    except (json.JSONDecodeError, TypeError):
        resp = {}

    # Extract bluff likelihood
    try:
        bluff = int(resp.get('bluff_likelihood', BLUFF_THRESHOLD))
    except (ValueError, TypeError):
        bluff = BLUFF_THRESHOLD  # Default to skip if not parseable

    hand_str = str(resp.get('hand_strength', '')).lower()

    # Skip intentional bluffs
    if bluff >= BLUFF_THRESHOLD:
        return None

    # Check if trash hand: "high card" in hand_strength OR equity < TRASH_EQUITY
    is_trash = 'high card' in hand_str or (equity is not None and equity < TRASH_EQUITY)
    if not is_trash:
        return None

    # Categorize by stack depth
    if stack_bb is not None and stack_bb <= SHORT_STACK_BB:
        return None  # Short stack - defensible
    elif stack_bb is not None and stack_bb <= MARGINAL_STACK_BB:
        return 'marginal'
    else:
        return 'suspicious'


def compute_allin_categorizations(
    cursor_results: List[Tuple[Any, ...]]
) -> Tuple[int, int]:
    """Compute suspicious and marginal all-in counts from cursor results.

    Args:
        cursor_results: List of tuples containing (stack_bb, ai_response, equity)

    Returns:
        Tuple of (suspicious_count, marginal_count)
    """
    suspicious_count = 0
    marginal_count = 0

    for row in cursor_results:
        stack_bb, ai_response, equity = row
        category = categorize_allin_row(stack_bb, ai_response, equity)

        if category == 'suspicious':
            suspicious_count += 1
        elif category == 'marginal':
            marginal_count += 1

    return suspicious_count, marginal_count


def build_quality_indicators(
    fold_mistakes: int,
    total_all_ins: int,
    total_folds: int,
    total_decisions: int,
    suspicious_allins: int,
    marginal_allins: int,
    # Optional survival metrics
    total_eliminations: Optional[int] = None,
    all_in_wins: Optional[int] = None,
    all_in_losses: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the quality indicators dictionary.

    Args:
        fold_mistakes: Number of fold decisions marked as mistakes
        total_all_ins: Total number of all-in decisions
        total_folds: Total number of fold decisions
        total_decisions: Total number of decisions analyzed
        suspicious_allins: Count of suspicious (deep stack trash) all-ins
        marginal_allins: Count of marginal (medium stack trash) all-ins
        total_eliminations: Optional - total player eliminations
        all_in_wins: Optional - count of all-in showdown wins
        all_in_losses: Optional - count of all-in showdown losses

    Returns:
        Dictionary with quality indicators
    """
    result = {
        'suspicious_allins': suspicious_allins,
        'marginal_allins': marginal_allins,
        'fold_mistakes': fold_mistakes,
        'fold_mistake_rate': round(fold_mistakes * 100 / total_folds, 1) if total_folds > 0 else 0,
        'total_all_ins': total_all_ins,
        'total_folds': total_folds,
        'total_decisions': total_decisions,
    }

    # Add survival metrics if provided
    if total_eliminations is not None:
        result['total_eliminations'] = total_eliminations

    if all_in_wins is not None:
        result['all_in_wins'] = all_in_wins

    if all_in_losses is not None:
        result['all_in_losses'] = all_in_losses

    if all_in_wins is not None and all_in_losses is not None:
        total_showdowns = all_in_wins + all_in_losses
        result['all_in_survival_rate'] = (
            round(all_in_wins * 100 / total_showdowns, 1)
            if total_showdowns > 0 else None
        )

    return result
