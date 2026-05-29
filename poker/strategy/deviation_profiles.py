"""
Deviation profiles: archetype-keyed limits on personality distortion.

Each profile controls how far a player archetype can deviate from the
solver baseline in logit space.
"""

from dataclasses import dataclass
from typing import Dict, Tuple

from ..archetypes import classify_from_anchors


@dataclass(frozen=True)
class DeviationProfile:
    """Controls how far an archetype can deviate from solver baseline."""

    max_kl: float  # Max KL divergence from base
    max_per_action_shift: float  # Max absolute shift per action
    aggression_scale: float  # Multiplier for aggression offsets
    looseness_scale: float  # Multiplier for looseness offsets
    risk_scale: float  # Multiplier for risk identity offsets
    ego_fold_penalty: float  # Penalty applied to fold when ego > 0
    # item 3: spot/line-specific tendencies as ((name, strength), ...) — a
    # hashable, frozen-safe map. Empty = none active (default). Each name is a
    # registered rule in spot_tendencies.py; strength in [0, 1] scales the
    # reshape (then bounded by max_per_action_shift). Priced + budgeted before
    # a profile turns one on. See PERSONALITY_PRICING_AND_VARIETY.md.
    spot_tendencies: Tuple[Tuple[str, float], ...] = ()


# Predefined profiles from architecture doc:
# | Archetype        | max_kl | max_per_action | aggression | looseness | risk  | ego_fold |
# |------------------|--------|----------------|------------|-----------|-------|----------|
# | Nit              | 0.2    | 0.10           | 0.3        | 0.3       | 0.2   | 0.05     |
# | Rock             | 0.3    | 0.15           | 0.5        | 0.4       | 0.3   | 0.10     |
# | TAG              | 0.3    | 0.15           | 0.7        | 0.4       | 0.4   | 0.10     |
# | Calling Station  | 0.4    | 0.20           | 0.3        | 0.8       | 0.3   | 0.25     |
# | LAG              | 0.5    | 0.25           | 0.8        | 0.7       | 0.6   | 0.20     |
# | Maniac           | 0.6    | 0.30           | 1.0        | 1.0       | 0.8   | 0.30     |

DEVIATION_PROFILES: Dict[str, DeviationProfile] = {
    'nit': DeviationProfile(
        max_kl=0.4,
        max_per_action_shift=0.20,
        aggression_scale=0.6,
        looseness_scale=0.6,
        risk_scale=0.4,
        ego_fold_penalty=0.10,
    ),
    'rock': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=1.0,
        looseness_scale=0.8,
        risk_scale=0.6,
        ego_fold_penalty=0.20,
    ),
    'tag': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=1.4,
        looseness_scale=0.8,
        risk_scale=0.8,
        ego_fold_penalty=0.20,
    ),
    'calling_station': DeviationProfile(
        max_kl=0.8,
        max_per_action_shift=0.40,
        aggression_scale=0.6,
        looseness_scale=1.6,
        risk_scale=0.6,
        ego_fold_penalty=0.50,
    ),
    'lag': DeviationProfile(
        max_kl=1.0,
        max_per_action_shift=0.50,
        aggression_scale=1.6,
        looseness_scale=1.4,
        risk_scale=1.2,
        ego_fold_penalty=0.40,
    ),
    'maniac': DeviationProfile(
        max_kl=1.2,
        # The binding lever for this profile: clamp_divergence applies the
        # per-action clip BEFORE the KL check, and that clip already pulls
        # realized KL under max_kl, so max_kl never engages. Priced re-cap
        # 0.60 -> 0.35 brings intrinsic self-play cost -15.7 -> -11.3 bb/100.
        max_per_action_shift=0.35,
        aggression_scale=2.0,
        looseness_scale=2.0,
        risk_scale=1.6,
        ego_fold_penalty=0.60,
    ),
}


def parse_spot_tendencies(raw) -> Tuple[Tuple[str, float], ...]:
    """Normalize a personality config's `spot_tendencies` to the canonical form.

    Accepts a list/tuple of ``[name, strength]`` pairs (JSON arrays from
    personalities.json) or ``((name, strength), ...)``; ``None``/empty -> ``()``.
    Strength is coerced to float. Used by the per-personality override hook so a
    specific character can carry its own tendencies independent of its archetype
    profile (see TieredBotController.deviation_profile).
    """
    if not raw:
        return ()
    return tuple((str(name), float(strength)) for name, strength in raw)


def select_deviation_profile(anchors) -> DeviationProfile:
    """Select deviation profile from personality anchors.

    Uses classify_from_anchors() to get base archetype, then extends:
    - Very low looseness (<0.25) AND very low aggression (<0.25) -> 'nit'
    - Very high looseness (>0.80) AND very high aggression (>0.80) -> 'maniac'
    - tight_passive -> 'rock'
    - tight_aggressive -> 'tag'
    - loose_passive -> 'calling_station'
    - loose_aggressive -> 'lag'
    - default (balanced) -> 'tag' (reasonable middle ground)
    """
    # Extreme checks first
    if anchors.baseline_looseness < 0.25 and anchors.baseline_aggression < 0.25:
        return DEVIATION_PROFILES['nit']
    if anchors.baseline_looseness > 0.80 and anchors.baseline_aggression > 0.80:
        return DEVIATION_PROFILES['maniac']

    archetype = classify_from_anchors(anchors.baseline_looseness, anchors.baseline_aggression)

    mapping = {
        'tight_passive': 'rock',
        'tight_aggressive': 'tag',
        'loose_passive': 'calling_station',
        'loose_aggressive': 'lag',
        'default': 'tag',
    }

    return DEVIATION_PROFILES[mapping[archetype]]
