"""
Position-based hand ranges for realistic equity calculations.

Defines standard poker opening ranges by table position, allowing
equity calculations to sample from position-appropriate hand ranges
rather than completely random hands.

Fallback hierarchy for estimating opponent ranges:
1. In-game observed stats (VPIP-based, if enough hands observed)
2. Position-based static ranges (universal fallback)

Hand notation:
- Pairs: "AA", "KK", "JJ"
- Suited: "AKs", "QJs"  (same suit)
- Offsuit: "AKo", "KQo" (different suits)
"""

import random
from dataclasses import dataclass
from enum import Enum
from typing import Set, List, Tuple, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class EquityConfig:
    """Configuration for equity calculation behavior."""
    use_in_game_stats: bool = True       # Use observed stats from current game
    min_hands_for_stats: int = 5         # Minimum hands before using observed stats


@dataclass
class OpponentInfo:
    """Information about an opponent for range estimation."""
    name: str
    position: str  # Table position name

    # Observed stats (from opponent model)
    hands_observed: int = 0
    vpip: Optional[float] = None         # Voluntarily Put $ In Pot (0-1)
    pfr: Optional[float] = None          # Pre-Flop Raise % (0-1)
    aggression: Optional[float] = None   # Aggression factor


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


# ============================================================================
# Range Adjustment Functions (for observed stats and personality)
# ============================================================================

# Define range tiers from tightest to widest
RANGE_TIERS = [
    EARLY_POSITION_RANGE,   # ~15%
    MIDDLE_POSITION_RANGE,  # ~22%
    BLIND_DEFENSE_RANGE,    # ~28%
    LATE_POSITION_RANGE,    # ~32%
]


def _generate_all_starting_hands() -> Set[str]:
    """Generate all 169 unique starting hands."""
    hands = set()
    for i, r1 in enumerate(RANKS):
        # Pairs
        hands.add(f"{r1}{r1}")
        # Non-pairs
        for r2 in RANKS[i+1:]:
            hands.add(f"{r1}{r2}s")  # Suited
            hands.add(f"{r1}{r2}o")  # Offsuit
    return hands


# All 169 starting hands for maximum range
ALL_STARTING_HANDS = _generate_all_starting_hands()


def estimate_range_from_vpip(vpip: float) -> Set[str]:
    """Estimate a hand range based on VPIP (voluntarily put $ in pot).

    Args:
        vpip: VPIP as a decimal (0.0 - 1.0)

    Returns:
        Set of canonical hand notations
    """
    if vpip <= 0.15:
        return EARLY_POSITION_RANGE
    elif vpip <= 0.22:
        return MIDDLE_POSITION_RANGE
    elif vpip <= 0.30:
        return BLIND_DEFENSE_RANGE
    elif vpip <= 0.40:
        return LATE_POSITION_RANGE
    else:
        # Very loose player - expand beyond standard ranges
        # Add more speculative hands
        expanded = LATE_POSITION_RANGE.copy()
        # Add suited gappers and more offsuit hands
        expanded.update({
            'A9o', 'A8o', 'A7o', 'A6o', 'A5o', 'A4o', 'A3o', 'A2o',
            'K7s', 'K6s', 'K5s', 'K4s', 'K3s', 'K2s',
            '97s', '86s', '75s', '64s', '53s', '42s',
            'T8s', 'T7s', '96s', '85s', '74s', '63s',
        })
        return expanded


def adjust_range_for_position(base_range: Set[str], position: Position) -> Set[str]:
    """Adjust a range based on table position.

    Earlier positions should be tighter, later positions can be wider.

    Args:
        base_range: The estimated base range
        position: Table position

    Returns:
        Adjusted range
    """
    position_range = get_range_for_position(position)

    # If the base range is wider than the position allows, tighten it
    # If the base range is tighter, keep it (player is tighter than position suggests)
    if len(base_range) > len(position_range):
        # Intersect to get hands that are in both ranges
        # This ensures we don't give a UTG player a button range
        return base_range & position_range

    return base_range


def get_opponent_range(
    opponent: OpponentInfo,
    config: EquityConfig = None
) -> Set[str]:
    """Get estimated hand range for an opponent using fallback hierarchy.

    Priority:
    1. In-game observed stats (if enough hands observed)
    2. Position-based static ranges (fallback)

    Args:
        opponent: OpponentInfo with available data
        config: EquityConfig for calculation options

    Returns:
        Set of canonical hand notations
    """
    if config is None:
        config = EquityConfig()

    position = get_position_group(opponent.position)
    base_range = None

    # Priority 1: In-game observed stats
    if (config.use_in_game_stats and
        opponent.hands_observed >= config.min_hands_for_stats and
        opponent.vpip is not None):

        base_range = estimate_range_from_vpip(opponent.vpip)
        logger.debug(
            f"Using observed stats for {opponent.name}: "
            f"VPIP={opponent.vpip:.2f}, range={len(base_range)} hands"
        )

    # Priority 2: Position-based static ranges (fallback)
    if base_range is None:
        base_range = get_range_for_position(position)
        logger.debug(
            f"Using position-based range for {opponent.name}: "
            f"position={position.value}, range={len(base_range)} hands"
        )

    # Adjust for position (don't let UTG player have button range)
    return adjust_range_for_position(base_range, position)


def sample_hand_for_opponent(
    opponent: OpponentInfo,
    excluded_cards: Set[str],
    config: EquityConfig = None,
    rng: Optional[random.Random] = None
) -> Optional[Tuple[str, str]]:
    """Sample a hand from an opponent's estimated range.

    Args:
        opponent: OpponentInfo with available data
        excluded_cards: Cards already dealt
        config: EquityConfig for calculation options
        rng: Random number generator

    Returns:
        Tuple of (card1, card2) or None if no valid hand
    """
    if rng is None:
        rng = random.Random()

    hand_range = get_opponent_range(opponent, config)

    # Build list of valid combos
    valid_combos = []
    for canonical in hand_range:
        for combo in _get_all_combos_for_hand(canonical):
            if combo[0] not in excluded_cards and combo[1] not in excluded_cards:
                valid_combos.append(combo)

    if not valid_combos:
        logger.debug(f"No valid combos for {opponent.name} with excluded {len(excluded_cards)} cards")
        return None

    return rng.choice(valid_combos)


def sample_hands_for_opponent_infos(
    opponents: List[OpponentInfo],
    excluded_cards: Set[str],
    config: EquityConfig = None,
    rng: Optional[random.Random] = None
) -> List[Optional[Tuple[str, str]]]:
    """Sample hands for multiple opponents using the fallback hierarchy.

    Args:
        opponents: List of OpponentInfo objects
        excluded_cards: Initial excluded cards (hero's hand, board)
        config: EquityConfig for calculation options
        rng: Random number generator

    Returns:
        List of (card1, card2) tuples, one per opponent
    """
    if rng is None:
        rng = random.Random()
    if config is None:
        config = EquityConfig()

    hands = []
    current_excluded = set(excluded_cards)

    for opponent in opponents:
        hand = sample_hand_for_opponent(opponent, current_excluded, config, rng)
        hands.append(hand)

        if hand:
            current_excluded.add(hand[0])
            current_excluded.add(hand[1])

    return hands


def build_opponent_info(
    name: str,
    position: str,
    opponent_model: Optional[Dict[str, Any]] = None,
) -> OpponentInfo:
    """Build OpponentInfo from available data sources.

    Args:
        name: Player name
        position: Table position name
        opponent_model: Dict with observed stats (vpip, pfr, aggression, hands_observed)

    Returns:
        OpponentInfo with all available data populated
    """
    info = OpponentInfo(
        name=name,
        position=position,
    )

    # Load observed stats from opponent model
    if opponent_model:
        info.hands_observed = opponent_model.get('hands_observed', 0)
        info.vpip = opponent_model.get('vpip')
        info.pfr = opponent_model.get('pfr')
        info.aggression = opponent_model.get('aggression_factor')

    return info


def calculate_equity_vs_ranges(
    player_hand: List[str],
    community_cards: List[str],
    opponent_infos: List[OpponentInfo],
    iterations: int = 500,
) -> Optional[float]:
    """Calculate equity vs opponent hand ranges using fallback hierarchy.

    Uses the following priority for range estimation:
    1. In-game observed stats (VPIP-based, if enough hands observed)
    2. Position-based static ranges (fallback)

    Args:
        player_hand: Hero's hole cards as strings ['Ah', 'Kd']
        community_cards: Board cards as strings
        opponent_infos: List of OpponentInfo objects with position/stats
        iterations: Monte Carlo iterations (default 500 for speed)

    Returns:
        Win probability (0.0-1.0) or None if calculation fails
    """
    try:
        import eval7

        def _convert_card(card_str: str) -> str:
            """Convert card string to eval7 format (e.g., 'Ah' -> 'Ah')."""
            if len(card_str) == 2:
                return card_str
            elif len(card_str) == 3 and card_str[0] == '1':
                # Handle '10h' -> 'Th'
                return 'T' + card_str[2]
            return card_str

        # Parse hero's hand
        hero_hand = [eval7.Card(_convert_card(c)) for c in player_hand]
        board = [eval7.Card(_convert_card(c)) for c in community_cards] if community_cards else []

        # Build set of excluded cards (hero's hand + board)
        excluded_cards = set(player_hand + (community_cards or []))

        # Build deck excluding known cards
        all_known = set(hero_hand + board)
        deck = [c for c in eval7.Deck().cards if c not in all_known]

        wins = 0
        valid_iterations = 0
        rng = random.Random()
        config = EquityConfig()

        for _ in range(iterations):
            # Sample opponent hands from ranges
            opponent_hands_raw = sample_hands_for_opponent_infos(
                opponent_infos, excluded_cards, config, rng
            )

            # Skip iteration if we couldn't sample valid hands
            if None in opponent_hands_raw:
                continue

            valid_iterations += 1

            # Convert to eval7 cards
            opponent_hands = []
            opp_cards_set = set()
            for hand in opponent_hands_raw:
                opp_hand = [eval7.Card(_convert_card(hand[0])), eval7.Card(_convert_card(hand[1]))]
                opponent_hands.append(opp_hand)
                opp_cards_set.add(opp_hand[0])
                opp_cards_set.add(opp_hand[1])

            # Build deck excluding all known cards for this iteration
            iter_deck = [c for c in deck if c not in opp_cards_set]
            rng.shuffle(iter_deck)

            # Deal remaining board cards
            cards_needed = 5 - len(board)
            sim_board = board + iter_deck[:cards_needed]

            # Evaluate hands
            hero_score = eval7.evaluate(hero_hand + sim_board)

            # Check if hero beats all opponents
            hero_wins = True
            for opp_hand in opponent_hands:
                opp_score = eval7.evaluate(opp_hand + sim_board)
                if opp_score > hero_score:  # Higher is better in eval7
                    hero_wins = False
                    break

            if hero_wins:
                wins += 1

        return wins / valid_iterations if valid_iterations > 0 else None

    except Exception as e:
        logger.debug(f"Equity vs ranges calculation failed: {e}")
        return None


def format_opponent_stats(opponent_infos: List[OpponentInfo]) -> str:
    """Format opponent stats for display in prompt.

    Args:
        opponent_infos: List of OpponentInfo objects

    Returns:
        Formatted string like "  BTN: loose (VPIP=35%, PFR=28%)\n  SB: tight (VPIP=18%)"
    """
    lines = []
    for opp in opponent_infos:
        # Get position abbreviation
        pos_abbrev = opp.position.upper()[:3] if opp.position else "???"

        # Determine tightness label
        if opp.vpip is not None:
            vpip_pct = int(opp.vpip * 100)
            if vpip_pct >= 35:
                tightness = "loose"
            elif vpip_pct <= 20:
                tightness = "tight"
            else:
                tightness = "average"

            # Format stats
            stats_parts = [f"VPIP={vpip_pct}%"]
            if opp.pfr is not None:
                stats_parts.append(f"PFR={int(opp.pfr * 100)}%")

            lines.append(f"  {pos_abbrev}: {tightness} ({', '.join(stats_parts)})")
        else:
            # No observed stats - use position-based defaults
            from .hand_ranges import get_position_group, get_range_percentage
            pos_group = get_position_group(opp.position)
            range_pct = int(get_range_percentage(pos_group) * 100)
            lines.append(f"  {pos_abbrev}: position-based (~{range_pct}% range)")

    return "\n".join(lines) if lines else ""
