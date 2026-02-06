"""Range target definitions and expansion logic for dynamic coaching.

Provides default ranges, position normalization, and gate-based expansion
for the personalized range coaching system.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Default ranges for complete beginners (Gate 1)
# These are intentionally tight to teach discipline first
DEFAULT_RANGE_TARGETS: Dict[str, float] = {
    'UTG': 0.08,       # Top 8% - very tight early position
    'UTG+1': 0.10,     # Top 10%
    'MP': 0.12,        # Top 12%
    'CO': 0.18,        # Top 18% - opening up in late position
    'BTN': 0.25,       # Top 25% - widest from button
    'BB': 0.20,        # Top 20% - defending big blind
}

# Range expansions when gates unlock
# Each gate defines complete ranges that replace the previous
GATE_EXPANSIONS: Dict[int, Dict[str, float]] = {
    2: {
        # Gate 2 (postflop basics): modest expansion
        'UTG': 0.10,
        'UTG+1': 0.12,
        'MP': 0.15,
        'CO': 0.22,
        'BTN': 0.30,
        'BB': 0.30,
    },
    3: {
        # Gate 3 (pressure recognition): further expansion
        'UTG': 0.12,
        'UTG+1': 0.15,
        'MP': 0.18,
        'CO': 0.28,
        'BTN': 0.35,
        'BB': 0.35,
    },
    4: {
        # Gate 4 (multi-street thinking): near-standard ranges
        'UTG': 0.15,
        'UTG+1': 0.18,
        'MP': 0.22,
        'CO': 0.32,
        'BTN': 0.40,
        'BB': 0.40,
    },
}


def normalize_position(position: str) -> str:
    """Normalize a position label to a standard key.

    Handles various position naming formats from game state.

    Args:
        position: Raw position string (e.g., "Under The Gun", "button", "big_blind_player")

    Returns:
        Normalized key: UTG, UTG+1, MP, CO, BTN, or BB
    """
    if not position:
        logger.info("normalize_position: empty position, falling back to 'MP'")
        return 'MP'  # Conservative fallback

    pos_lower = position.lower().replace('_', ' ').replace('-', ' ')

    # Early positions
    if 'under the gun' in pos_lower or pos_lower == 'utg':
        return 'UTG'
    if 'utg+1' in pos_lower or 'utg 1' in pos_lower or 'utg1' in pos_lower:
        return 'UTG+1'

    # Middle position (covers middle_position_1, middle_position_2, etc.)
    if 'middle' in pos_lower or pos_lower == 'mp':
        return 'MP'

    # Late positions
    if 'cutoff' in pos_lower or pos_lower == 'co':
        return 'CO'
    if 'button' in pos_lower or 'dealer' in pos_lower or pos_lower == 'btn':
        return 'BTN'

    # Blinds - treat small blind like BB for simplicity
    if 'blind' in pos_lower or pos_lower == 'bb' or pos_lower == 'sb':
        return 'BB'

    # Fallback to middle position (conservative)
    logger.info("normalize_position: unrecognized position '%s', falling back to 'MP'", position)
    return 'MP'


def get_range_target(targets: Dict[str, float], position: str) -> float:
    """Get the range target percentage for a position.

    Args:
        targets: Dict of position -> percentage (0.0-1.0)
        position: Raw position string

    Returns:
        Target percentage (e.g., 0.10 for top 10%)
    """
    normalized = normalize_position(position)
    return targets.get(normalized, 0.15)  # Default to 15% if position unknown


def get_expanded_ranges(current_gate: int) -> Dict[str, float]:
    """Get the range targets for a given gate level.

    Args:
        current_gate: Highest unlocked gate number (1-4)

    Returns:
        Range targets dict appropriate for that gate level
    """
    if current_gate <= 1:
        return DEFAULT_RANGE_TARGETS.copy()

    # Find the highest expansion that applies
    for gate in sorted(GATE_EXPANSIONS.keys(), reverse=True):
        if current_gate >= gate:
            return GATE_EXPANSIONS[gate].copy()

    return DEFAULT_RANGE_TARGETS.copy()


def expand_ranges_for_gate(current_targets: Dict[str, float], gate_num: int) -> Dict[str, float]:
    """Apply range expansion when a specific gate unlocks.

    Args:
        current_targets: Player's current range targets
        gate_num: The gate that just unlocked

    Returns:
        Updated range targets (new dict, doesn't mutate input)
    """
    if gate_num in GATE_EXPANSIONS:
        return GATE_EXPANSIONS[gate_num].copy()

    # No expansion defined for this gate, return current
    return current_targets.copy()
