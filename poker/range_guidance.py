"""
Position-Aware Range Guidance for Poker-Native Psychology System.

Generates human-readable range guidance based on tightness trait and table position.
"""

from typing import Dict, Optional


# Base opening ranges by position (percentage of hands)
# These are typical opening ranges for a neutral (0.5) tightness player
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


def get_range_percentage(tightness: float, position: str) -> float:
    """
    Calculate the recommended opening range percentage.

    Tightness affects range width:
    - tightness = 0.0 (loose): 1.5x base range
    - tightness = 0.5 (neutral): 1.0x base range
    - tightness = 1.0 (tight): 0.5x base range

    Args:
        tightness: Tightness trait value (0.0 = loose, 1.0 = tight)
        position: Table position

    Returns:
        Recommended range as a percentage (0.0 to 1.0)
    """
    pos_key = normalize_position(position)
    base_range = POSITION_RANGES.get(pos_key, 0.20)

    # Tightness modifier: 1.5 (loose) to 0.5 (tight)
    # This creates a linear scale where:
    # - tightness 0.0 -> modifier 1.5 (50% wider ranges)
    # - tightness 0.5 -> modifier 1.0 (baseline)
    # - tightness 1.0 -> modifier 0.5 (50% tighter ranges)
    modifier = 1.5 - tightness

    adjusted_range = base_range * modifier

    # Clamp to reasonable bounds (5% minimum, 60% maximum)
    return max(0.05, min(0.60, adjusted_range))


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

    Args:
        tightness: Tightness trait value (0.0 = loose, 1.0 = tight)
        position: Table position
        include_archetype: If True and aggression provided, include archetype
        aggression: Aggression trait value (for archetype calculation)

    Returns:
        Guidance string like "Play top 22% of hands from button"
    """
    pos_key = normalize_position(position)
    range_pct = get_range_percentage(tightness, position)

    # Convert to percentage string
    pct_str = f"{range_pct * 100:.0f}%"

    # Describe the style
    if tightness > 0.7:
        style = 'tight'
    elif tightness > 0.4:
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
        archetype = get_player_archetype(tightness, aggression)
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
