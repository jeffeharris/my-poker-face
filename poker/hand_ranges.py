"""
Position-based hand ranges for realistic equity calculations.

Defines standard poker opening ranges by table position, allowing
equity calculations to sample from position-appropriate hand ranges
rather than completely random hands.

Hand notation:
- Pairs: "AA", "KK", "JJ"
- Suited: "AKs", "QJs"  (same suit)
- Offsuit: "AKo", "KQo" (different suits)
"""

import random
from enum import Enum
from typing import Set, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class Position(Enum):
    """Normalized position groups for range selection."""
    EARLY = "early"    # UTG, UTG+1 - tight ranges (~15%)
    MIDDLE = "middle"  # MP1, MP2, MP3 - medium ranges (~22%)
    LATE = "late"      # Cutoff, Button - wide ranges (~32%)
    BLIND = "blind"    # SB, BB defending - wide but passive (~28%)


# Map game position names to Position enum
POSITION_MAPPING = {
    "under_the_gun": Position.EARLY,
    "middle_position_1": Position.MIDDLE,
    "middle_position_2": Position.MIDDLE,
    "middle_position_3": Position.MIDDLE,
    "cutoff": Position.LATE,
    "button": Position.LATE,
    "small_blind_player": Position.BLIND,
    "big_blind_player": Position.BLIND,
}

# All ranks in order (high to low)
RANKS = ['A', 'K', 'Q', 'J', 'T', '9', '8', '7', '6', '5', '4', '3', '2']
SUITS = ['h', 'd', 'c', 's']


def get_position_group(position_name: str) -> Position:
    """Convert game position name to Position enum.

    Args:
        position_name: Position name from game (e.g., "button", "under_the_gun")

    Returns:
        Position enum value, defaults to LATE if unknown
    """
    return POSITION_MAPPING.get(position_name, Position.LATE)


def _rank_index(rank: str) -> int:
    """Get index of rank (A=0, K=1, ..., 2=12)."""
    return RANKS.index(rank)


def _expand_pairs(start_rank: str, end_rank: str = '2') -> Set[str]:
    """Expand pair notation like '88+' or '99-55'.

    Args:
        start_rank: High end of range (e.g., 'A' for AA)
        end_rank: Low end of range (e.g., '8' for 88), default '2'

    Returns:
        Set of pair notations like {'AA', 'KK', 'QQ', ...}
    """
    start_idx = _rank_index(start_rank)
    end_idx = _rank_index(end_rank)
    return {f"{RANKS[i]}{RANKS[i]}" for i in range(start_idx, end_idx + 1)}


def _expand_broadway(high: str, low_start: str, low_end: str, suited: bool) -> Set[str]:
    """Expand broadway hands like AKs-ATs or KQo-KTo.

    Args:
        high: High card (e.g., 'A')
        low_start: Starting low card (e.g., 'K')
        low_end: Ending low card (e.g., 'T')
        suited: True for suited, False for offsuit

    Returns:
        Set of hand notations
    """
    suffix = 's' if suited else 'o'
    start_idx = _rank_index(low_start)
    end_idx = _rank_index(low_end)
    return {f"{high}{RANKS[i]}{suffix}" for i in range(start_idx, end_idx + 1)}


# Define opening ranges for each position
# These are standard TAG (tight-aggressive) ranges from poker theory

EARLY_POSITION_RANGE = (
    _expand_pairs('A', '8') |  # AA-88
    _expand_broadway('A', 'K', 'K', True) |   # AKs
    _expand_broadway('A', 'K', 'K', False) |  # AKo
    _expand_broadway('A', 'Q', 'Q', True) |   # AQs
    _expand_broadway('A', 'Q', 'Q', False) |  # AQo
    _expand_broadway('A', 'J', 'T', True) |   # AJs, ATs
    _expand_broadway('K', 'Q', 'J', True)     # KQs, KJs
)  # ~15% of hands

MIDDLE_POSITION_RANGE = (
    EARLY_POSITION_RANGE |
    _expand_pairs('7', '5') |  # 77-55
    _expand_broadway('A', 'J', 'T', False) |  # AJo, ATo
    _expand_broadway('A', '9', '8', True) |   # A9s, A8s
    _expand_broadway('K', 'Q', 'Q', False) |  # KQo
    _expand_broadway('K', 'T', 'T', True) |   # KTs
    _expand_broadway('Q', 'J', 'T', True) |   # QJs, QTs
    _expand_broadway('J', 'T', 'T', True)     # JTs
)  # ~22% of hands

LATE_POSITION_RANGE = (
    MIDDLE_POSITION_RANGE |
    _expand_pairs('4', '2') |  # 44-22
    _expand_broadway('A', '7', '2', True) |   # A7s-A2s
    _expand_broadway('K', '9', '8', True) |   # K9s, K8s
    _expand_broadway('Q', '9', '9', True) |   # Q9s
    _expand_broadway('J', '9', '9', True) |   # J9s
    {'T9s', '98s', '87s', '76s', '65s', '54s'} |  # Suited connectors
    _expand_broadway('K', 'J', 'J', False) |  # KJo
    _expand_broadway('Q', 'J', 'T', False) |  # QJo, QTo
    {'JTo'}  # JTo
)  # ~32% of hands

BLIND_DEFENSE_RANGE = (
    MIDDLE_POSITION_RANGE |
    _expand_pairs('4', '2') |  # 44-22
    _expand_broadway('A', '7', '2', True) |   # A7s-A2s
    _expand_broadway('K', '9', '7', True) |   # K9s-K7s
    _expand_broadway('Q', '9', '8', True) |   # Q9s, Q8s
    {'T9s', '98s', '87s', '76s'} |  # Suited connectors
    _expand_broadway('K', 'T', 'T', False) |  # KTo
    {'QTo', 'JTo'}  # Some broadway offsuit
)  # ~28% of hands


# Map position to range
OPENING_RANGES = {
    Position.EARLY: EARLY_POSITION_RANGE,
    Position.MIDDLE: MIDDLE_POSITION_RANGE,
    Position.LATE: LATE_POSITION_RANGE,
    Position.BLIND: BLIND_DEFENSE_RANGE,
}


def get_range_for_position(position: Position) -> Set[str]:
    """Get the set of hands in a position's range.

    Args:
        position: Position enum value

    Returns:
        Set of canonical hand notations
    """
    return OPENING_RANGES.get(position, LATE_POSITION_RANGE)


def hand_to_canonical(card1: str, card2: str) -> str:
    """Convert two cards to canonical hand notation.

    Args:
        card1: First card as string (e.g., 'Ah', 'Kd')
        card2: Second card as string

    Returns:
        Canonical notation like 'AKs', 'AKo', or 'AA'

    Examples:
        ('Ah', 'Kh') -> 'AKs'
        ('Ah', 'Kd') -> 'AKo'
        ('Ah', 'Ad') -> 'AA'
    """
    # Extract rank and suit
    rank1, suit1 = card1[0], card1[1]
    rank2, suit2 = card2[0], card2[1]

    # Handle 10 represented as 'T'
    if rank1 == '1' and len(card1) > 2:
        rank1 = 'T'
        suit1 = card1[2]
    if rank2 == '1' and len(card2) > 2:
        rank2 = 'T'
        suit2 = card2[2]

    # Order by rank (higher first)
    idx1, idx2 = _rank_index(rank1), _rank_index(rank2)
    if idx1 > idx2:
        rank1, rank2 = rank2, rank1
        suit1, suit2 = suit2, suit1

    # Determine hand type
    if rank1 == rank2:
        return f"{rank1}{rank2}"  # Pair
    elif suit1 == suit2:
        return f"{rank1}{rank2}s"  # Suited
    else:
        return f"{rank1}{rank2}o"  # Offsuit


def _get_all_combos_for_hand(canonical: str) -> List[Tuple[str, str]]:
    """Get all specific card combinations for a canonical hand.

    Args:
        canonical: Canonical hand notation like 'AKs', 'AA', 'AKo'

    Returns:
        List of (card1, card2) tuples

    Examples:
        'AA' -> [('Ah', 'Ad'), ('Ah', 'Ac'), ('Ah', 'As'), ...]  (6 combos)
        'AKs' -> [('Ah', 'Kh'), ('Ad', 'Kd'), ...]  (4 combos)
        'AKo' -> [('Ah', 'Kd'), ('Ah', 'Kc'), ...]  (12 combos)
    """
    combos = []

    if len(canonical) == 2:
        # Pair (e.g., 'AA')
        rank = canonical[0]
        for i, s1 in enumerate(SUITS):
            for s2 in SUITS[i+1:]:
                combos.append((f"{rank}{s1}", f"{rank}{s2}"))
    elif canonical.endswith('s'):
        # Suited (e.g., 'AKs')
        r1, r2 = canonical[0], canonical[1]
        for suit in SUITS:
            combos.append((f"{r1}{suit}", f"{r2}{suit}"))
    else:
        # Offsuit (e.g., 'AKo')
        r1, r2 = canonical[0], canonical[1]
        for s1 in SUITS:
            for s2 in SUITS:
                if s1 != s2:
                    combos.append((f"{r1}{s1}", f"{r2}{s2}"))

    return combos


def sample_hand_from_range(
    position: Position,
    excluded_cards: Set[str],
    rng: Optional[random.Random] = None
) -> Optional[Tuple[str, str]]:
    """Sample a random hand from a position's range.

    Args:
        position: Position enum for range selection
        excluded_cards: Set of cards already dealt (e.g., {'Ah', 'Kd'})
        rng: Random number generator (optional, uses global if not provided)

    Returns:
        Tuple of two card strings, or None if no valid hand available
    """
    if rng is None:
        rng = random.Random()

    hand_range = get_range_for_position(position)

    # Build list of all valid combos across the range
    valid_combos = []
    for canonical in hand_range:
        for combo in _get_all_combos_for_hand(canonical):
            if combo[0] not in excluded_cards and combo[1] not in excluded_cards:
                valid_combos.append(combo)

    if not valid_combos:
        logger.debug(f"No valid combos for position {position} with excluded {excluded_cards}")
        return None

    return rng.choice(valid_combos)


def sample_hands_for_opponents(
    opponent_positions: List[str],
    excluded_cards: Set[str],
    rng: Optional[random.Random] = None
) -> List[Optional[Tuple[str, str]]]:
    """Sample hands for multiple opponents based on their positions.

    Args:
        opponent_positions: List of position names for each opponent
        excluded_cards: Initial set of excluded cards (hero's hand, board)
        rng: Random number generator

    Returns:
        List of (card1, card2) tuples, one per opponent
    """
    if rng is None:
        rng = random.Random()

    hands = []
    current_excluded = set(excluded_cards)

    for pos_name in opponent_positions:
        position = get_position_group(pos_name)
        hand = sample_hand_from_range(position, current_excluded, rng)
        hands.append(hand)

        # Add dealt cards to excluded set
        if hand:
            current_excluded.add(hand[0])
            current_excluded.add(hand[1])

    return hands


# Utility function to check range sizes
def get_range_percentage(position: Position) -> float:
    """Get approximate percentage of hands in a range.

    There are 169 unique starting hands (13 pairs + 78 suited + 78 offsuit).
    """
    hand_range = get_range_for_position(position)
    return len(hand_range) / 169 * 100
