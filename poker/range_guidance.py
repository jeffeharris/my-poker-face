"""
Position-Aware Range Guidance for Poker-Native Psychology System v2.1.

Generates range guidance based on LOOSENESS (not tightness) and table position.

Key changes in v2.1:
- Uses looseness semantics (0=tight, 1=loose) instead of tightness
- Position clamps ensure realistic ranges regardless of emotional state
- Clamps apply to OUTPUT (range %), not INPUT (looseness value)
"""

from typing import Dict, Optional, Tuple

from poker.hand_tiers import PREMIUM_HANDS, is_hand_in_range


# Position-adjusted range clamps (PRD §18.4)
# These ensure no personality plays unrealistic ranges
POSITION_CLAMPS: Dict[str, Tuple[float, float]] = {
    'early': (0.08, 0.35),      # UTG: 8-35%
    'middle': (0.10, 0.45),     # MP: 10-45%
    'late': (0.15, 0.65),       # CO: 15-65%
    'button': (0.15, 0.65),     # BTN: 15-65%
    'small_blind': (0.12, 0.55),  # SB: 12-55%
    'big_blind': (0.12, 0.55),    # BB: 12-55%
    'blinds': (0.12, 0.55),     # Generic blinds reference
}

# Base opening ranges by position (for neutral 0.5 looseness)
# Used for get_range_guidance display
POSITION_RANGES = {
    'early': 0.12,      # UTG: ~12% (tight from early position)
    'middle': 0.18,     # MP: ~18%
    'late': 0.28,       # CO: ~28%
    'button': 0.35,     # BTN: ~35% (widest from button)
    'small_blind': 0.24,  # SB: ~24%
    'big_blind': 0.30,    # BB: ~30% (defending range)
    'blinds': 0.24,     # Generic blinds reference
}

# Position aliases for flexible matching
POSITION_ALIASES = {
    'utg': 'early',
    'utg+1': 'early',
    'utg+2': 'early',
    'ep': 'early',
    'mp': 'middle',
    'mp+1': 'middle',
    'mp+2': 'middle',
    'hj': 'middle',
    'hijack': 'middle',
    'co': 'late',
    'cutoff': 'late',
    'btn': 'button',
    'bu': 'button',
    'd': 'button',
    'dealer': 'button',
    'sb': 'small_blind',
    'bb': 'big_blind',
}


def normalize_position(position: str) -> str:
    """
    Normalize position string to standard format.

    Args:
        position: Raw position string (e.g., 'UTG', 'btn', 'CO')

    Returns:
        Normalized position key (e.g., 'early', 'button', 'late')
    """
    if not position:
        return 'middle'

    pos_lower = position.lower().strip()

    # Check aliases first
    if pos_lower in POSITION_ALIASES:
        return POSITION_ALIASES[pos_lower]

    # Check direct match
    if pos_lower in POSITION_RANGES:
        return pos_lower

    # Default to middle if unknown
    return 'middle'


def looseness_to_range_pct(effective_looseness: float, position: str) -> float:
    """
    Convert effective looseness to position-clamped range percentage.

    This is the core range calculation (PRD §18.4):
    1. Get position min/max clamps
    2. Map looseness linearly to [min, max]
    3. Clamp OUTPUT to position bounds

    Args:
        effective_looseness: Looseness value (0.0 = tight, 1.0 = loose)
                            This is baseline_looseness + emotional modifier
        position: Table position

    Returns:
        Range percentage clamped to position bounds (0.0 to 1.0)
    """
    pos_key = normalize_position(position)
    min_range, max_range = POSITION_CLAMPS.get(pos_key, (0.10, 0.50))

    # Linear mapping: looseness 0 -> min_range, looseness 1 -> max_range
    range_pct = min_range + (max_range - min_range) * effective_looseness

    # Clamp to position bounds (critical: clamp OUTPUT, not INPUT)
    return max(min_range, min(max_range, range_pct))


def get_range_percentage(tightness: float, position: str) -> float:
    """
    Calculate the recommended opening range percentage.

    BACKWARD COMPAT: Uses tightness (inverted looseness).
    Prefer looseness_to_range_pct for new code.

    Args:
        tightness: Tightness trait value (0.0 = loose, 1.0 = tight)
        position: Table position

    Returns:
        Recommended range as a percentage (0.0 to 1.0)
    """
    # Convert tightness to looseness
    looseness = 1.0 - tightness
    return looseness_to_range_pct(looseness, position)


def get_player_archetype(tightness: float, aggression: float) -> str:
    """
    Determine the player archetype from tightness and aggression.

    The classic 2x2 matrix:
    - TAG (Tight-Aggressive): tight range, aggressive betting
    - LAG (Loose-Aggressive): wide range, aggressive betting
    - Rock (Tight-Passive): tight range, passive betting
    - Fish (Loose-Passive): wide range, passive betting

    Args:
        tightness: Tightness trait (0 = loose, 1 = tight)
                   Note: This is 1 - looseness for backward compat
        aggression: Aggression trait (0 = passive, 1 = aggressive)

    Returns:
        Archetype string: 'TAG', 'LAG', 'Rock', or 'Fish'
    """
    tight = tightness > 0.5
    aggressive = aggression > 0.5

    if tight and aggressive:
        return 'TAG'
    elif not tight and aggressive:
        return 'LAG'
    elif tight and not aggressive:
        return 'Rock'
    else:
        return 'Fish'


def get_player_archetype_from_looseness(looseness: float, aggression: float) -> str:
    """
    Determine the player archetype from looseness and aggression.

    Preferred version using looseness semantics.

    Args:
        looseness: Looseness value (0 = tight, 1 = loose)
        aggression: Aggression trait (0 = passive, 1 = aggressive)

    Returns:
        Archetype string: 'TAG', 'LAG', 'Rock', or 'Fish'
    """
    # Convert looseness to tightness for the archetype logic
    tightness = 1.0 - looseness
    return get_player_archetype(tightness, aggression)


def get_archetype_description(archetype: str) -> str:
    """
    Get a brief description of the player archetype.

    Args:
        archetype: Archetype code ('TAG', 'LAG', 'Rock', 'Fish')

    Returns:
        Human-readable description
    """
    descriptions = {
        'TAG': 'Tight-Aggressive: Selective with hands, aggressive with bets',
        'LAG': 'Loose-Aggressive: Plays many hands, applies pressure',
        'Rock': 'Tight-Passive: Very selective, checks/calls often',
        'Fish': 'Loose-Passive: Plays too many hands, calls too much',
    }
    return descriptions.get(archetype, 'Unknown style')


def get_range_guidance(
    tightness: float,
    position: str,
    include_archetype: bool = False,
    aggression: Optional[float] = None,
) -> str:
    """
    Generate human-readable range guidance for AI prompts.

    BACKWARD COMPAT: Uses tightness. For new code, use get_range_guidance_from_looseness.

    Args:
        tightness: Tightness trait value (0.0 = loose, 1.0 = tight)
        position: Table position
        include_archetype: If True and aggression provided, include archetype
        aggression: Aggression trait value (for archetype calculation)

    Returns:
        Guidance string like "Play top 22% of hands from button"
    """
    looseness = 1.0 - tightness
    return get_range_guidance_from_looseness(looseness, position, include_archetype, aggression)


def get_range_guidance_from_looseness(
    looseness: float,
    position: str,
    include_archetype: bool = False,
    aggression: Optional[float] = None,
) -> str:
    """
    Generate human-readable range guidance for AI prompts.

    Preferred version using looseness semantics.

    Args:
        looseness: Looseness value (0.0 = tight, 1.0 = loose)
        position: Table position
        include_archetype: If True and aggression provided, include archetype
        aggression: Aggression trait value (for archetype calculation)

    Returns:
        Guidance string like "Play top 22% of hands from button"
    """
    pos_key = normalize_position(position)
    range_pct = looseness_to_range_pct(looseness, position)

    # Convert to percentage string
    pct_str = f"{range_pct * 100:.0f}%"

    # Describe the style based on looseness
    if looseness < 0.3:
        style = 'tight'
    elif looseness < 0.6:
        style = 'standard'
    else:
        style = 'loose'

    # Position display name
    pos_display = pos_key.replace('_', ' ').title()
    if pos_key == 'button':
        pos_display = 'Button'

    guidance = f"Play the top {pct_str} of hands from {pos_display} ({style} range)"

    # Add archetype if requested
    if include_archetype and aggression is not None:
        archetype = get_player_archetype_from_looseness(looseness, aggression)
        guidance = f"[{archetype}] {guidance}"

    return guidance


def get_full_range_guidance(
    tightness: float,
    aggression: float,
    position: str,
) -> Dict[str, str]:
    """
    Generate comprehensive range guidance for prompt injection.

    Args:
        tightness: Tightness trait value
        aggression: Aggression trait value
        position: Table position

    Returns:
        Dictionary with 'archetype', 'range', 'description', 'advice'
    """
    archetype = get_player_archetype(tightness, aggression)
    range_pct = get_range_percentage(tightness, position)
    pos_key = normalize_position(position)

    # Style-specific advice
    advice_map = {
        'TAG': 'Be selective preflop, then bet/raise with your strong hands',
        'LAG': 'Apply pressure with a wide range, but know when to give up',
        'Rock': 'Wait for premium hands, then trap opponents',
        'Fish': 'Your range may be too wide - consider tightening up',
    }

    return {
        'archetype': archetype,
        'archetype_description': get_archetype_description(archetype),
        'range_percentage': f"{range_pct * 100:.0f}%",
        'position': pos_key,
        'range_guidance': get_range_guidance(tightness, position),
        'style_advice': advice_map.get(archetype, ''),
    }


def derive_bluff_propensity(tightness: float, aggression: float) -> float:
    """
    Derive bluff tendency from tightness and aggression traits.

    This replaces the explicit bluff_tendency trait from the old model.
    Logic: Loose-aggressive players bluff more, tight-passive players bluff less.

    Args:
        tightness: Tightness trait (0 = loose, 1 = tight)
        aggression: Aggression trait (0 = passive, 1 = aggressive)

    Returns:
        Derived bluff propensity (0.0 to 1.0)
    """
    # Looseness is inverse of tightness
    looseness = 1.0 - tightness

    # Bluff propensity = 60% aggression + 40% looseness
    # This means aggressive players bluff more, and loose players bluff more
    bluff_propensity = aggression * 0.6 + looseness * 0.4

    return max(0.0, min(1.0, bluff_propensity))


# ============================================================================
# Per-Hand Range-Relative Classification
# ============================================================================

# Bridge from hand_ranges.Position enum values to range_guidance position keys
_POSITION_GROUP_TO_RANGE_KEY = {
    'early': 'early',
    'middle': 'middle',
    'late': 'late',
    'blind': 'big_blind',
}

# Special-case game position names that need direct mapping
_GAME_POSITION_OVERRIDES = {
    'button': 'button',
    'small_blind_player': 'small_blind',
}


def _game_position_to_range_key(game_position: str) -> str:
    """Convert a game position name to a range_guidance position key.

    Bridges between hand_ranges.get_position_group() output and
    the keys used by looseness_to_range_pct().

    Args:
        game_position: Game position name (e.g., 'under_the_gun', 'button')

    Returns:
        Range key for looseness_to_range_pct (e.g., 'early', 'button')
    """
    # Check direct overrides first
    if game_position in _GAME_POSITION_OVERRIDES:
        return _GAME_POSITION_OVERRIDES[game_position]

    # Use hand_ranges position grouping
    from poker.hand_ranges import get_position_group
    pos_group = get_position_group(game_position)
    return _POSITION_GROUP_TO_RANGE_KEY.get(pos_group.value, 'middle')


def _position_display_name(range_key: str) -> str:
    """Human-readable position name for prompt output.

    Args:
        range_key: Range key (e.g., 'early', 'button', 'small_blind')

    Returns:
        Display string (e.g., 'early position', 'the button')
    """
    display_map = {
        'early': 'early position',
        'middle': 'middle position',
        'late': 'late position',
        'button': 'the button',
        'small_blind': 'the small blind',
        'big_blind': 'the big blind',
    }
    return display_map.get(range_key, range_key)


def classify_preflop_hand_for_player(
    canonical: str,
    effective_looseness: float,
    game_position: str,
) -> str:
    """Classify a preflop hand relative to the player's current range.

    Composes existing range/tier utilities to produce a one-line description
    that tells the AI whether this hand fits their personality+emotional state.

    Args:
        canonical: Canonical hand string (e.g., 'AKs', 'T8o', 'QQ')
        effective_looseness: Player's current looseness (0-1), includes emotional modifier
        game_position: Game position name (e.g., 'under_the_gun', 'button')

    Returns:
        One-line string like 'AKs - premium hand, always in range from early position'
        or empty string if canonical is empty
    """
    if not canonical:
        return ''

    range_key = _game_position_to_range_key(game_position)
    range_pct = looseness_to_range_pct(effective_looseness, range_key)
    pos_display = _position_display_name(range_key)

    # Premium hands are always in range
    if canonical in PREMIUM_HANDS:
        return f"{canonical} - premium hand, always in range from {pos_display}"

    in_range = is_hand_in_range(canonical, range_pct)
    range_pct_display = f"~{range_pct * 100:.0f}%"

    if in_range:
        # Check if hand is on the edge (would drop out at range_pct - 0.05)
        tighter_pct = max(0.0, range_pct - 0.05)
        in_tighter = is_hand_in_range(canonical, tighter_pct)
        if not in_tighter:
            return (
                f"{canonical} - on the edge of your range from {pos_display} "
                f"(you play {range_pct_display} here)"
            )
        return f"{canonical} - well within your range from {pos_display}"
    else:
        # Check if hand is just outside (would be included at range_pct + 0.10)
        looser_pct = min(1.0, range_pct + 0.10)
        in_looser = is_hand_in_range(canonical, looser_pct)
        if in_looser:
            return (
                f"{canonical} - just outside your range from {pos_display} "
                f"(you play {range_pct_display} here)"
            )
        return (
            f"{canonical} - outside your range from {pos_display} "
            f"(you play {range_pct_display} here)"
        )
