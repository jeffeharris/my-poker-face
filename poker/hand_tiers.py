"""Hand tier constants for preflop hand classification.

Based on standard poker hand rankings (169 unique starting hands).
"""


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

# Wider tiers for loose archetypes (LAG/LAP)
TOP_45_HANDS = TOP_35_HANDS | {
    'ATo',  # Offsuit ace
    'KJo', 'KTo', 'QJo', 'QTo', 'JTo',  # Offsuit broadway
    'K7s', 'K6s', 'K5s', 'K4s', 'K3s', 'K2s',  # Remaining suited kings
    'Q8s', 'J8s', 'T8s',  # Suited one-gappers
    '97s', '86s', '75s', '64s', '53s', '43s',  # Suited connectors
    'A9o', 'A8o', 'A7o', 'A6o', 'A5o', 'A4o', 'A3o', 'A2o',  # Offsuit aces
    'J9o', 'T9o',  # Offsuit one-gap connectors
}  # ~45% (76 hands)

TOP_55_HANDS = TOP_45_HANDS | {
    'K9o', 'K8o', 'K7o', 'K6o', 'K5o',  # Mid offsuit kings
    'Q9o', 'Q8o',  # Offsuit queens
    'Q7s', 'Q6s', 'Q5s', 'Q4s', 'Q3s', 'Q2s',  # Remaining suited queens
    'J7s', 'T7s',  # Suited mid gappers
    '98o', '87o',  # Offsuit connectors
}  # ~55% (93 hands)

TOP_65_HANDS = TOP_55_HANDS | {
    'K4o', 'K3o', 'K2o',  # Weak offsuit kings
    'Q7o', 'Q6o', 'Q5o',  # Weak offsuit queens
    'J8o', 'T8o',  # Offsuit mid gappers
    'J6s', 'J5s', 'J4s', 'J3s', 'J2s',  # Remaining suited jacks
    '96s', '85s', '74s', '63s',  # Suited gappers
}  # ~65% (110 hands)

TOP_75_HANDS = TOP_65_HANDS | {
    'Q4o', 'Q3o', 'Q2o',  # Remaining offsuit queens
    'J7o', 'J6o', 'J5o',  # Weak offsuit jacks
    'T6s', 'T5s', 'T4s', 'T3s', 'T2s',  # Remaining suited tens
    '76o', '65o', '54o',  # Offsuit connectors
    '52s', '42s', '32s',  # Smallest suited connectors
}  # ~75% (127 hands)


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
    if range_percentage >= 0.75:
        return canonical in TOP_75_HANDS
    if range_percentage >= 0.65:
        return canonical in TOP_65_HANDS
    if range_percentage >= 0.55:
        return canonical in TOP_55_HANDS
    if range_percentage >= 0.45:
        return canonical in TOP_45_HANDS
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
