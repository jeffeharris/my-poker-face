"""Hand tier constants for preflop hand classification.

Based on standard poker hand rankings (169 unique starting hands).
"""

from typing import Optional

PREMIUM_HANDS = {'AA', 'KK', 'QQ', 'JJ', 'AKs'}  # Top ~3%
TOP_10_HANDS = PREMIUM_HANDS | {'TT', 'AKo', 'AQs', 'AJs', 'KQs'}  # Top ~10%
TOP_20_HANDS = TOP_10_HANDS | {'99', '88', '77', 'ATs', 'AQo', 'AJo', 'KJs', 'KTs', 'QJs', 'QTs', 'JTs'}  # Top ~20%
TOP_35_HANDS = TOP_20_HANDS | {
    '66', '55', '44', '33', '22',  # Small pairs
    'A9s', 'A8s', 'A7s', 'A6s', 'A5s', 'A4s', 'A3s', 'A2s',  # Suited aces
    'KQo', 'K9s', 'K8s', 'Q9s', 'J9s', 'T9s', '98s', '87s', '76s', '65s', '54s',  # Suited connectors
}

# Extended tiers for more granular range checking
TOP_15_HANDS = TOP_10_HANDS | {'99', '88', 'ATs', 'AQo', 'KJs', 'QJs'}  # Top ~15%
TOP_25_HANDS = TOP_20_HANDS | {
    '66', '55', 'A9s', 'A8s', 'KQo', 'K9s', 'T9s', '98s',
}  # Top ~25%


def is_hand_in_range(canonical: str, range_percentage: float) -> bool:
    """Check if a hand qualifies under a target range percentage.

    Uses tiered lookup to approximate whether a hand falls within
    a given percentage of top starting hands.

    Args:
        canonical: Canonical hand string (e.g., 'AKs', 'QQ', 'T9o')
        range_percentage: Target percentage as decimal (0.10 = top 10%)

    Returns:
        True if hand is estimated to be within the target range
    """
    if not canonical:
        return False

    # 100% range means any hand is in range
    if range_percentage >= 1.0:
        return True

    # Map percentage to closest tier
    if range_percentage >= 0.35:
        return canonical in TOP_35_HANDS
    if range_percentage >= 0.25:
        return canonical in TOP_25_HANDS
    if range_percentage >= 0.20:
        return canonical in TOP_20_HANDS
    if range_percentage >= 0.15:
        return canonical in TOP_15_HANDS
    if range_percentage >= 0.10:
        return canonical in TOP_10_HANDS
    if range_percentage >= 0.03:
        return canonical in PREMIUM_HANDS

    # Very tight range (< 3%): only AA, KK
    return canonical in {'AA', 'KK'}


def get_hand_tier(canonical: str) -> Optional[str]:
    """Get the tier name for a hand.

    Args:
        canonical: Canonical hand string

    Returns:
        Tier name ('premium', 'top10', 'top20', 'top35', 'trash') or None
    """
    if not canonical:
        return None
    if canonical in PREMIUM_HANDS:
        return 'premium'
    if canonical in TOP_10_HANDS:
        return 'top10'
    if canonical in TOP_20_HANDS:
        return 'top20'
    if canonical in TOP_35_HANDS:
        return 'top35'
    return 'trash'
