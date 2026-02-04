"""
Trait Converter for Poker-Native Psychology System.

Converts between the old 4-trait model (bluff_tendency, aggression, chattiness, emoji_usage)
and the new 5-trait poker-native model (tightness, aggression, confidence, composure, table_talk).
"""

from typing import Dict, Any, Tuple


# Old trait names (4-trait model)
OLD_TRAIT_NAMES = ['bluff_tendency', 'aggression', 'chattiness', 'emoji_usage']

# New trait names (5-trait poker-native model)
NEW_TRAIT_NAMES = ['tightness', 'aggression', 'confidence', 'composure', 'table_talk']


def detect_trait_format(traits: Dict[str, Any]) -> str:
    """
    Detect whether traits are in old or new format.

    Args:
        traits: Dictionary of trait values

    Returns:
        'old' if 4-trait format, 'new' if 5-trait format, 'unknown' otherwise
    """
    if not traits:
        return 'unknown'

    trait_names = set(traits.keys())

    # Check for new format (has tightness, composure, or table_talk)
    new_indicators = {'tightness', 'composure', 'table_talk'}
    if trait_names & new_indicators:
        return 'new'

    # Check for old format (has bluff_tendency, chattiness, or emoji_usage)
    old_indicators = {'bluff_tendency', 'chattiness', 'emoji_usage'}
    if trait_names & old_indicators:
        return 'old'

    # If only has aggression (common to both), assume old for backward compat
    if 'aggression' in trait_names:
        return 'old'

    return 'unknown'


def _clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp value to range [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def convert_old_to_new_traits(old_traits: Dict[str, float]) -> Dict[str, float]:
    """
    Convert 4-trait model to 5-trait poker-native model.

    Mapping logic:
    - tightness: Derived from inverse of bluff_tendency + aggression
      (Loose-aggressive players bluff more and play more hands)
    - aggression: Kept as-is (same meaning in both models)
    - confidence: Derived from aggression + bluff_tendency
      (Aggressive bluffers tend to be confident)
    - composure: Defaults to 0.7 (focused) since old model had separate tilt system
    - table_talk: Merges chattiness (80%) + emoji_usage (20%)

    Args:
        old_traits: Dictionary with bluff_tendency, aggression, chattiness, emoji_usage

    Returns:
        Dictionary with tightness, aggression, confidence, composure, table_talk
    """
    # Extract old values with defaults
    bluff = old_traits.get('bluff_tendency', 0.5)
    agg = old_traits.get('aggression', 0.5)
    chat = old_traits.get('chattiness', 0.5)
    emoji = old_traits.get('emoji_usage', 0.3)

    # Derive tightness: inverse of "looseness"
    # High bluff tendency + high aggression = plays more hands = loose
    looseness = bluff * 0.5 + agg * 0.3 + 0.2
    tightness = 1.0 - looseness

    # Aggression: keep as-is
    new_aggression = agg

    # Confidence: derived from aggression and bluff tendency
    # Aggressive players who bluff tend to be confident
    confidence = 0.5 + agg * 0.2 + bluff * 0.1

    # Composure: default to focused (old model had separate tilt system)
    composure = 0.7

    # Table talk: merge chattiness and emoji usage
    table_talk = chat * 0.8 + emoji * 0.2

    return {
        'tightness': round(_clamp(tightness), 2),
        'aggression': round(_clamp(new_aggression), 2),
        'confidence': round(_clamp(confidence), 2),
        'composure': round(_clamp(composure), 2),
        'table_talk': round(_clamp(table_talk), 2),
    }


def convert_tilt_to_composure(tilt_level: float) -> float:
    """
    Convert tilt level to composure.

    Composure is the inverse of tilt:
    - tilt_level = 0.0 (no tilt) -> composure = 1.0 (fully focused)
    - tilt_level = 1.0 (max tilt) -> composure = 0.0 (completely tilted)

    Args:
        tilt_level: Tilt value from 0.0 to 1.0

    Returns:
        Composure value from 0.0 to 1.0
    """
    return _clamp(1.0 - tilt_level)


def convert_composure_to_tilt(composure: float) -> float:
    """
    Convert composure back to tilt level (for backward compatibility).

    Args:
        composure: Composure value from 0.0 to 1.0

    Returns:
        Tilt level from 0.0 to 1.0
    """
    return _clamp(1.0 - composure)


def convert_old_elasticity_config(old_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert old elasticity config to new format.

    Args:
        old_config: Dictionary with trait_elasticity for old traits

    Returns:
        Dictionary with trait_elasticity for new traits
    """
    if not old_config:
        return get_default_elasticity_config()

    old_elasticity = old_config.get('trait_elasticity', {})

    # Map old elasticity values to new traits
    # - tightness: use bluff_tendency elasticity (since they're inversely related)
    # - aggression: keep as-is
    # - confidence: average of aggression and bluff_tendency elasticity
    # - composure: moderate elasticity (was a separate system)
    # - table_talk: use chattiness elasticity

    bluff_e = old_elasticity.get('bluff_tendency', 0.3)
    agg_e = old_elasticity.get('aggression', 0.5)
    chat_e = old_elasticity.get('chattiness', 0.8)

    return {
        'trait_elasticity': {
            'tightness': round(bluff_e, 2),
            'aggression': round(agg_e, 2),
            'confidence': round((agg_e + bluff_e) / 2, 2),
            'composure': 0.4,  # Moderate - not too stable, not too volatile
            'table_talk': round(chat_e, 2),
        },
        'mood_elasticity': old_config.get('mood_elasticity', 0.4),
        'recovery_rate': old_config.get('recovery_rate', 0.1),
    }


def get_default_elasticity_config() -> Dict[str, Any]:
    """
    Get default elasticity configuration for new 5-trait model.

    Returns:
        Default elasticity config dictionary
    """
    return {
        'trait_elasticity': {
            'tightness': 0.3,      # Moderate - playing style shifts under pressure
            'aggression': 0.5,     # High - aggression shifts significantly
            'confidence': 0.4,     # Moderate - confidence varies with results
            'composure': 0.4,      # Moderate - composure affected by bad beats
            'table_talk': 0.6,     # High - chattiness varies with mood
        },
        'mood_elasticity': 0.4,
        'recovery_rate': 0.1,
    }


def auto_convert_personality_traits(
    personality_config: Dict[str, Any]
) -> Tuple[Dict[str, float], Dict[str, Any], bool]:
    """
    Auto-detect and convert personality traits if needed.

    Args:
        personality_config: Full personality configuration dictionary

    Returns:
        Tuple of (converted_traits, converted_elasticity_config, was_converted)
    """
    traits = personality_config.get('personality_traits', {})
    elasticity_config = personality_config.get('elasticity_config', {})

    format_type = detect_trait_format(traits)

    if format_type == 'new':
        # Already in new format
        return traits, elasticity_config, False

    if format_type == 'old':
        # Convert from old format
        new_traits = convert_old_to_new_traits(traits)
        new_elasticity = convert_old_elasticity_config(elasticity_config)
        return new_traits, new_elasticity, True

    # Unknown format - return defaults
    return {
        'tightness': 0.5,
        'aggression': 0.5,
        'confidence': 0.5,
        'composure': 0.7,
        'table_talk': 0.5,
    }, get_default_elasticity_config(), True
