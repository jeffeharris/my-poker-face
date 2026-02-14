"""
Position-Aware Range Guidance for Poker-Native Psychology System v2.1.

Generates range guidance based on LOOSENESS (not tightness) and table position.

Key changes in v2.1:
- Uses looseness semantics (0=tight, 1=loose) instead of tightness
- Position clamps ensure realistic ranges regardless of emotional state
- Clamps apply to OUTPUT (range %), not INPUT (looseness value)
"""

from typing import Dict, Optional, Tuple

from .archetypes import archetype_label_from_anchors
from .hand_tiers import PREMIUM_HANDS, is_hand_in_range


# Position offsets applied to effective_looseness to get range_pct.
# Negative = tighter (early position), positive = looser (late position).
# Looseness IS the range — offsets just shift it by position.
POSITION_OFFSETS: Dict[str, float] = {
    'early': -0.15,       # UTG: play 15pp tighter than baseline
    'middle': -0.08,      # MP: play 8pp tighter
    'late': 0.00,         # CO: play at baseline
    'button': +0.05,      # BTN: play 5pp wider
    'small_blind': -0.05,   # SB: play 5pp tighter (out of position)
    'big_blind': -0.05,     # BB: play 5pp tighter
    'blinds': -0.05,      # Generic blinds reference
}

# Floor: minimum range % for any position (even the tightest rock plays something)
RANGE_FLOOR = 0.05

# Legacy: POSITION_CLAMPS kept for backward compat (tests reference it)
POSITION_CLAMPS: Dict[str, Tuple[float, float]] = {
    'early': (0.08, 0.35),
    'middle': (0.10, 0.45),
    'late': (0.15, 0.65),
    'button': (0.15, 0.65),
    'small_blind': (0.12, 0.55),
    'big_blind': (0.12, 0.55),
    'blinds': (0.12, 0.55),
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
    Convert effective looseness to position-adjusted range percentage.

    Looseness IS the range — position offsets shift it so players are
    tighter from early position and looser from late position.

    Args:
        effective_looseness: Looseness value (0.0 = tight, 1.0 = loose)
                            This is baseline_looseness + emotional modifier
        position: Table position

    Returns:
        Range percentage floored at RANGE_FLOOR (0.0 to 1.0)
    """
    pos_key = normalize_position(position)
    offset = POSITION_OFFSETS.get(pos_key, 0.0)

    range_pct = effective_looseness + offset

    # Floor only — no ceiling. Let loose players be loose.
    return max(RANGE_FLOOR, min(1.0, range_pct))


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

    Uses shared thresholds from poker.archetypes (3-zone model with
    'Balanced' middle zone for looseness 0.45-0.65).

    Args:
        tightness: Tightness trait (0 = loose, 1 = tight)
                   Note: This is 1 - looseness for backward compat
        aggression: Aggression trait (0 = passive, 1 = aggressive)

    Returns:
        Archetype string: 'TAG', 'LAG', 'Rock', 'Fish', or 'Balanced'
    """
    looseness = 1.0 - tightness
    return archetype_label_from_anchors(looseness, aggression)


def get_player_archetype_from_looseness(looseness: float, aggression: float) -> str:
    """
    Determine the player archetype from looseness and aggression.

    Preferred version using looseness semantics.

    Args:
        looseness: Looseness value (0 = tight, 1 = loose)
        aggression: Aggression trait (0 = passive, 1 = aggressive)

    Returns:
        Archetype string: 'TAG', 'LAG', 'Rock', 'Fish', or 'Balanced'
    """
    return archetype_label_from_anchors(looseness, aggression)


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
        'Balanced': 'Balanced: Moderate hand selection and betting',
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
    from .hand_ranges import get_position_group
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
        return f"{canonical} - premium hand, raise or re-raise from {pos_display}"

    in_range = is_hand_in_range(canonical, range_pct)
    range_pct_display = f"~{range_pct * 100:.0f}%"

    # Looseness-scaled directive language:
    # Tight players (< 0.4) get strong fold directives
    # Medium players (0.4-0.6) get moderate guidance
    # Loose players (> 0.6) get soft nudges that respect their wide range
    if in_range:
        tighter_pct = max(0.0, range_pct - 0.05)
        in_tighter = is_hand_in_range(canonical, tighter_pct)
        if not in_tighter:
            return (
                f"{canonical} - playable but marginal from {pos_display}, "
                f"proceed carefully (you play {range_pct_display} here)"
            )
        return f"{canonical} - solid hand from {pos_display}, raise-worthy"
    else:
        looser_pct = min(1.0, range_pct + 0.10)
        in_looser = is_hand_in_range(canonical, looser_pct)

        outside_msg, just_outside_msg = _get_outside_range_messages(
            effective_looseness, canonical, pos_display, range_pct_display,
        )
        return just_outside_msg if in_looser else outside_msg


def _get_outside_range_messages(
    looseness: float,
    canonical: str,
    pos_display: str,
    range_pct_display: str,
) -> tuple:
    """Return (well_outside_msg, just_outside_msg) scaled by looseness.

    Tight players get strong fold directives.
    Loose players get soft nudges that respect their wide-range style.
    """
    if looseness < 0.4:
        # Tight player — strong directive
        just_outside = (
            f"{canonical} - below your range from {pos_display}, "
            f"fold unless you have a strong read (you play {range_pct_display} here)"
        )
        well_outside = (
            f"{canonical} - well below your range from {pos_display}, "
            f"you should fold this (you play {range_pct_display} here)"
        )
    elif looseness < 0.65:
        # Medium player — moderate guidance
        just_outside = (
            f"{canonical} - just outside your range from {pos_display}, "
            f"usually a fold without a read (you play {range_pct_display} here)"
        )
        well_outside = (
            f"{canonical} - outside your range from {pos_display}, "
            f"fold from here (you play {range_pct_display} here)"
        )
    else:
        # Loose player — soft nudge, respect their style
        just_outside = (
            f"{canonical} - just past the edge of your range from {pos_display}, "
            f"not a standard open but playable with position (you play {range_pct_display} here)"
        )
        well_outside = (
            f"{canonical} - outside your range from {pos_display}, "
            f"speculative at best (you play {range_pct_display} here)"
        )
    return well_outside, just_outside
