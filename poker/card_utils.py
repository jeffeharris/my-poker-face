"""Shared card conversion utilities.

This module consolidates card string conversion logic that was previously
duplicated across controllers.py, decision_analyzer.py, hand_ranges.py,
and minimal_prompt.py.
"""
from typing import Union

# Unicode suit symbols to letter mapping for eval7 compatibility
SUIT_MAP = {'♠': 's', '♥': 'h', '♦': 'd', '♣': 'c'}

# Extended suit map for various input formats
SUIT_MAP_EXTENDED = {
    'Spades': 's', 'spades': 's', 'S': 's', 's': 's', '♠': 's',
    'Hearts': 'h', 'hearts': 'h', 'H': 'h', 'h': 'h', '♥': 'h',
    'Diamonds': 'd', 'diamonds': 'd', 'D': 'd', 'd': 'd', '♦': 'd',
    'Clubs': 'c', 'clubs': 'c', 'C': 'c', 'c': 'c', '♣': 'c',
}


def normalize_card_string(card_str: str) -> str:
    """Convert card string to eval7 format.

    Handles Unicode suit symbols and 10 -> T conversion.
    Examples: '7♣' -> '7c', 'A♠' -> 'As', '10♥' -> 'Th'
    """
    # Handle unicode suit symbols
    for unicode_suit, letter_suit in SUIT_MAP.items():
        if unicode_suit in card_str:
            card_str = card_str.replace(unicode_suit, letter_suit)
            break
    # Handle '10' -> 'T'
    if card_str.startswith('10'):
        card_str = 'T' + card_str[2:]
    elif '10' in card_str:
        card_str = card_str.replace('10', 'T')
    return card_str


def card_to_string(card: Union[dict, object]) -> str:
    """Convert a Card object or dict to standard notation (e.g., 'Ah', 'Td').

    Handles:
    - Dict with 'rank'/'value' and 'suit' keys
    - Card objects with rank and suit attributes
    - Falls back to str() for unknown types
    """
    if isinstance(card, dict):
        rank = card.get('rank', card.get('value', '?'))
        suit = card.get('suit', '?')
    elif hasattr(card, 'rank') and hasattr(card, 'suit'):
        rank = card.rank
        suit = card.suit
    else:
        # Card object - use str() which gives "8♥" format, then normalize
        return normalize_card_string(str(card))

    # Normalize rank (10 -> T)
    rank_str = 'T' if rank == '10' or rank == 10 else str(rank)

    # Convert suit to single lowercase letter
    suit_char = SUIT_MAP_EXTENDED.get(suit, suit[0].lower() if suit else '?')

    return f"{rank_str}{suit_char}"
