"""
Deviation profiles: archetype-keyed limits on personality distortion.

Each profile controls how far a player archetype can deviate from the
solver baseline in logit space.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

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

# Division of labour (post-width-tier architecture, 2026-05-29): the per-archetype
# preflop TABLE (ARCHETYPE_WIDTH_TABLE) now carries the coarse VPIP *envelope*
# (tight ~21% / std ~25% / loose ~50% / station 43%/19%); these distortion scales
# carry the *flavor within* it — the aggression/passivity character and the
# tight-end separation distortion CAN reach (it can always boost fold). The
# binding lever is max_per_action_shift (the per-action clip in clamp_divergence
# runs before the KL check and pulls realized KL under max_kl, so for the
# aggressive profiles max_kl is inert). Variety is *expected* to cost EV — the
# bleed IS the skill gradient; weak characters are budgeted generously, not ~0.
DEVIATION_PROFILES: Dict[str, DeviationProfile] = {
    # Nit: very tight, very passive. The old 0.20 cap throttled nit's tighter
    # anchors down to rock's realized play (the two measured byte-identical);
    # a 0.30 cap + stronger looseness_scale lets the fold boost express, so nit
    # sits clearly below rock on the same tight table.
    'nit': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=0.6,
        looseness_scale=1.2,
        risk_scale=0.3,
        ego_fold_penalty=0.05,
    ),
    # Rock: tight, mildly passive — a touch looser + more willing than nit.
    'rock': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=0.9,
        looseness_scale=0.7,
        risk_scale=0.5,
        ego_fold_penalty=0.15,
    ),
    # TAG: tight-aggressive — the competent-reg anchor, so it sits at the LOWER
    # edge of the TAG band (~22/19), not over the ceiling. High aggression_scale
    # gives the AF character but also boosts preflop opens (raise-or-fold RFI),
    # nudging VPIP up; the higher looseness_scale boosts fold to pull entry back
    # down without touching the aggression flavor.
    'tag': DeviationProfile(
        max_kl=0.6,
        max_per_action_shift=0.30,
        aggression_scale=1.6,
        looseness_scale=1.6,
        risk_scale=0.9,
        ego_fold_penalty=0.20,
    ),
    # Calling Station: loose-passive. The station TABLE creates the high VPIP /
    # low PFR via wide flat-calling; this distortion reinforces passivity
    # (aggression_scale amplifies the negative agg_dev -> shifts raise->call)
    # and stickiness (high ego_fold_penalty -> pays off, doesn't fold).
    'calling_station': DeviationProfile(
        max_kl=0.8,
        max_per_action_shift=0.40,
        aggression_scale=1.2,
        looseness_scale=0.8,
        risk_scale=0.4,
        ego_fold_penalty=0.55,
    ),
    # LAG: loose-aggressive. Loose table + strong aggression.
    'lag': DeviationProfile(
        max_kl=1.0,
        max_per_action_shift=0.50,
        aggression_scale=1.8,
        looseness_scale=1.0,
        risk_scale=1.2,
        ego_fold_penalty=0.40,
    ),
    # Maniac: the wildest — loose table + the highest aggression so its AF tops
    # the field (its VPIP shares the loose envelope with LAG; the wildness shows
    # in aggression). Cap held at 0.35 (the priced ceiling); aggression_scale
    # does the work.
    'maniac': DeviationProfile(
        max_kl=1.2,
        max_per_action_shift=0.35,
        aggression_scale=2.2,
        looseness_scale=1.2,
        risk_scale=1.6,
        ego_fold_penalty=0.60,
    ),
}


# Width-tier preflop table per archetype profile (filename in
# poker/strategy/data/). None = the standard base chart. The personality
# *distortion* layer can TIGHTEN a chart (boost fold mass) but CANNOT open a
# hand the base chart folds ~100% (no mass to amplify; the per-action cap pins
# it near 0), so the loose/station archetypes need a wider base TABLE — the
# table carries the coarse VPIP envelope, distortion carries the flavor within
# it. Selected in TieredBotController._select_preflop_table. Measured envelopes
# (Baseline hero, no distortion, vs a Baseline roster): tight 21% VPIP, std 25%,
# loose 50%, station 43% VPIP / 19% PFR (a real caller). See
# docs/plans/PERSONALITY_PRICING_AND_VARIETY.md.
ARCHETYPE_WIDTH_TABLE: Dict[str, Optional[str]] = {
    'nit': 'preflop_100bb_6max_tight_rfi.json',
    'rock': 'preflop_100bb_6max_tight_rfi.json',
    'tag': None,  # standard base chart
    'calling_station': 'preflop_100bb_6max_station.json',
    'lag': 'preflop_100bb_6max_loose_mid.json',  # between TAG and Maniac
    'maniac': 'preflop_100bb_6max_loose.json',
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


def select_deviation_profile_key(anchors) -> str:
    """Resolve the DEVIATION_PROFILES key from personality anchors.

    Uses classify_from_anchors() to get base archetype, then extends:
    - Very low looseness (<0.25) AND very low aggression (<0.25) -> 'nit'
    - Very high looseness (>0.80) AND very high aggression (>0.80) -> 'maniac'
    - tight_passive -> 'rock'
    - tight_aggressive -> 'tag'
    - loose_passive -> 'calling_station'
    - loose_aggressive -> 'lag'
    - default (balanced) -> 'tag' (reasonable middle ground)

    Returning the key (not just the profile) lets the caller also look up the
    archetype's width-tier preflop table (ARCHETYPE_WIDTH_TABLE).
    """
    # Extreme checks first
    if anchors.baseline_looseness < 0.25 and anchors.baseline_aggression < 0.25:
        return 'nit'
    if anchors.baseline_looseness > 0.80 and anchors.baseline_aggression > 0.80:
        return 'maniac'

    archetype = classify_from_anchors(anchors.baseline_looseness, anchors.baseline_aggression)

    mapping = {
        'tight_passive': 'rock',
        'tight_aggressive': 'tag',
        'loose_passive': 'calling_station',
        'loose_aggressive': 'lag',
        'default': 'tag',
    }
    return mapping[archetype]


def select_deviation_profile(anchors) -> DeviationProfile:
    """Select deviation profile from personality anchors (see
    select_deviation_profile_key for the classification rules)."""
    return DEVIATION_PROFILES[select_deviation_profile_key(anchors)]
