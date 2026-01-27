"""
Minimal Poker Prompt Utilities.

DEPRECATED: The full prompt functions (convert_game_to_minimal_prompt, render_minimal_prompt,
parse_minimal_response, convert_minimal_response_to_game_action) have been removed.
The unified prompt architecture in controllers.py now handles all prompt modes through
PromptConfig toggles (include_personality, use_simple_response_format).

This module retains utility functions used across the codebase:
- to_bb(): Convert chip amounts to big blinds
- get_position_abbrev(): Standard position abbreviations
- format_cards(): Format card lists to string notation
- POSITION_ABBREV / STREET_NAMES: Reference dictionaries
"""
import logging

from poker.card_utils import card_to_string

logger = logging.getLogger(__name__)


# Position abbreviation mapping (internal name -> standard poker abbreviation)
POSITION_ABBREV = {
    "button": "BTN",
    "small_blind_player": "SB",
    "big_blind_player": "BB",
    "under_the_gun": "UTG",
    "under_the_gun_1": "UTG+1",
    "under_the_gun_2": "UTG+2",
    "middle_position": "MP",
    "middle_position_1": "MP",
    "middle_position_2": "MP+1",
    "middle_position_3": "MP+2",
    "hijack": "HJ",
    "cutoff": "CO",
}

# Street name mapping
STREET_NAMES = {
    "PRE_FLOP": "Pre-flop",
    "FLOP": "Flop",
    "TURN": "Turn",
    "RIVER": "River",
}


def get_position_abbrev(position_name: str) -> str:
    """Convert internal position name to standard poker abbreviation."""
    return POSITION_ABBREV.get(position_name, position_name.upper())


def to_bb(amount: int, big_blind: int) -> float:
    """Convert chip amount to big blinds."""
    if big_blind <= 0:
        return float(amount)
    return round(amount / big_blind, 1)


def format_cards(cards) -> str:
    """Format a list of cards to standard notation."""
    if not cards:
        return "none"
    return " ".join(card_to_string(c) for c in cards)
