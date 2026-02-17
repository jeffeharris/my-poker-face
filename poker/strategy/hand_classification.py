"""Hand classification for postflop strategy decisions.

Classifies a player's hand into a made-hand tier and draw modifier,
then maps those to a simplified 6-class bucket used by the postflop
strategy table.
"""

from collections import Counter
from types import SimpleNamespace
from typing import List, Tuple

from poker.board_analyzer import analyze_board_texture
from poker.hand_evaluator import HandEvaluator, _has_straight_draw

RANK_VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8,
    '9': 9, 'T': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14,
}


def _parse_card(card_str: str) -> SimpleNamespace:
    """Parse a card string like 'Ah' into a Card-like object."""
    return SimpleNamespace(value=RANK_VALUES[card_str[0]], suit=card_str[1])


def _classify_made_tier(
    hand_rank: int,
    hole_ranks: List[int],
    board_ranks: List[int],
    community_cards: List[str],
) -> str:
    """Classify the made-hand strength tier."""
    # Flush/straight or better
    if hand_rank <= 6:
        return 'nuts'

    # Three of a kind — set vs trips
    if hand_rank == 7:
        if hole_ranks[0] == hole_ranks[1]:
            return 'nuts'       # Set (pocket pair hit the board)
        return 'strong_made'    # Trips (one hole card + board pair)

    # Two pair
    if hand_rank == 8:
        return 'strong_made'

    # One pair
    if hand_rank == 9:
        is_pocket_pair = hole_ranks[0] == hole_ranks[1]
        sorted_board = sorted(board_ranks, reverse=True)

        # Overpair: pocket pair > all board ranks
        if is_pocket_pair and hole_ranks[0] > sorted_board[0]:
            return 'strong_made'

        # Which pair did we make with the board?
        matching_ranks = [r for r in hole_ranks if r in board_ranks]
        if matching_ranks:
            pair_rank = matching_ranks[0]
            other_hole = [r for r in hole_ranks if r != pair_rank]
            kicker = other_hole[0] if other_hole else 0

            # Top pair
            if pair_rank == sorted_board[0]:
                if kicker >= 13:  # A or K kicker
                    return 'strong_made'
                return 'medium_made'

            # Second pair
            if len(sorted_board) >= 2 and pair_rank == sorted_board[1]:
                texture = analyze_board_texture(community_cards)
                category = texture.get('texture_category', 'dry')
                if category in ('dry', 'semi_wet'):
                    return 'medium_made'
                return 'weak_made'

        # Third pair, bottom pair, underpair, etc.
        return 'weak_made'

    # High card / no pair
    return 'air'


def _classify_straight_draw(all_ranks_sorted: List[int]) -> str:
    """Classify straight draw type.

    Returns 'oesd', 'gutshot', or None.
    """
    # Check OESD: 4 ranks spanning exactly 3 (4 consecutive)
    for i in range(len(all_ranks_sorted) - 3):
        if all_ranks_sorted[i + 3] - all_ranks_sorted[i] == 3:
            return 'oesd'

    # Wheel OESD: A-2-3-4
    if 14 in all_ranks_sorted:
        low_ranks = sorted(set([1] + [r for r in all_ranks_sorted if r <= 5]))
        for i in range(len(low_ranks) - 3):
            if low_ranks[i + 3] - low_ranks[i] == 3:
                return 'oesd'

    # Gutshot: 4 in a 5-rank window (from _has_straight_draw)
    if _has_straight_draw(all_ranks_sorted):
        return 'gutshot'

    return None


def _classify_draw_modifier(
    hand_rank: int,
    hole_cards: List[str],
    community_cards: List[str],
) -> str:
    """Classify the draw modifier for the hand."""
    # Completed hands don't have draw modifiers
    if hand_rank <= 6:
        return 'no_draw'

    all_cards = hole_cards + community_cards
    all_suits = [c[1] for c in all_cards]
    all_ranks = sorted(set(RANK_VALUES[c[0]] for c in all_cards))

    # Flush draw: 4+ of any suit
    suit_counts = Counter(all_suits)
    has_flush_draw = any(count >= 4 for count in suit_counts.values())

    # Straight draw classification
    straight_type = _classify_straight_draw(all_ranks)

    # Combo draw or flush draw or OESD → strong_draw
    if has_flush_draw:
        return 'strong_draw'
    if straight_type == 'oesd':
        return 'strong_draw'

    # Gutshot → weak_draw
    if straight_type == 'gutshot':
        return 'weak_draw'

    # Backdoor flush: 3 of any suit
    has_backdoor = any(count == 3 for count in suit_counts.values())
    if has_backdoor:
        return 'backdoor'

    return 'no_draw'


def classify_hand(
    hole_cards: List[str],
    community_cards: List[str],
) -> Tuple[str, str]:
    """Classify a hand into (made_tier, draw_modifier).

    Args:
        hole_cards: Two card strings like ['Ah', 'Kd']
        community_cards: Three to five card strings like ['Ks', '7d', '2c']

    Returns:
        Tuple of (made_tier, draw_modifier) where:
        - made_tier: 'nuts', 'strong_made', 'medium_made', 'weak_made', 'air'
        - draw_modifier: 'strong_draw', 'weak_draw', 'backdoor', 'no_draw'
    """
    all_card_objs = [_parse_card(c) for c in hole_cards + community_cards]
    result = HandEvaluator(all_card_objs).evaluate_hand()
    hand_rank = result['hand_rank']

    hole_ranks = [RANK_VALUES[c[0]] for c in hole_cards]
    board_ranks = [RANK_VALUES[c[0]] for c in community_cards]

    made_tier = _classify_made_tier(hand_rank, hole_ranks, board_ranks, community_cards)
    draw_modifier = _classify_draw_modifier(hand_rank, hole_cards, community_cards)

    return made_tier, draw_modifier


def simplify_hand_class(made_tier: str, draw_modifier: str) -> str:
    """Map (made_tier, draw_modifier) to one of 6 simplified classes.

    Returns one of: 'nuts', 'strong_made', 'medium_made', 'weak_made',
    'air_strong_draw', 'air_no_draw'.
    """
    if made_tier == 'nuts':
        return 'nuts'
    if made_tier == 'strong_made' and draw_modifier == 'strong_draw':
        return 'nuts'
    if made_tier == 'strong_made':
        return 'strong_made'
    if made_tier == 'medium_made' and draw_modifier == 'strong_draw':
        return 'strong_made'
    if made_tier == 'medium_made':
        return 'medium_made'
    if made_tier == 'weak_made' and draw_modifier == 'strong_draw':
        return 'medium_made'
    if made_tier == 'weak_made':
        return 'weak_made'
    if made_tier == 'air' and draw_modifier == 'strong_draw':
        return 'air_strong_draw'
    return 'air_no_draw'
