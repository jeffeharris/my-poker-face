"""Per-archetype behavioral target ranges + scoring.

The tiered-bot archetypes (``deviation_profile_name``) are meant to be
*readable* opponents — a player should be able to get a reliable read on a
``nit`` vs a ``maniac``. To shape them we need (a) an explicit target range
for each behavioral stat per archetype and (b) a way to score the actual
measured behavior against it.

This module owns the targets. The review route (``archetype_review_routes``)
measures actuals from ``player_decision_analysis`` and calls :func:`score_stat`.

Targets are **cash-game** oriented (100bb 6-max). Tournament play distorts
these (ICM, short stacks) and gets separate handling later.

Numbers are starting points calibrated against standard poker stat ranges —
they are meant to be *tuned*. They can be overridden at runtime via the
``ARCHETYPE_TARGET_OVERRIDES`` app setting (JSON) without a code change; see
:func:`get_targets`.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Canonical production archetypes (mirror DEVIATION_PROFILES keys that are
# actually assigned in production — isolation/validation profiles excluded).
PRODUCTION_ARCHETYPES = (
    'nit',
    'rock',
    'tag',
    'lag',
    'maniac',
    'calling_station',
    'weak_fish',
)

# The stats we target, in display order, with human-facing labels and whether
# a higher value is "more aggressive" (used only for UI hinting).
STAT_LABELS: Dict[str, str] = {
    'vpip': 'VPIP %',
    'pfr': 'PFR %',
    'threebet': '3-bet %',
    'fourbet': '4-bet %',
    'fold_to_3bet': 'Fold-to-3bet %',
    'af': 'Aggression Factor',
    'all_in': 'All-in %',
    'afq': 'AFq %',
    'wtsd': 'WTSD %',
    'wsd': 'W$SD %',
    'flop_af': 'Flop AF',
    'turn_af': 'Turn AF',
    'river_af': 'River AF',
}

# (lo, hi) inclusive target band per archetype per stat. See module docstring.
# DENOMINATORS (must match archetype_review_routes computation):
#   vpip/pfr        = per preflop hand-instance (% of hands)
#   threebet        = raise AT a vs_open node / decisions facing an open
#                     ("3-bet when facing an open" — NOT % of all hands, which
#                     runs ~half this. A solid reg is ~15% here, not ~7%.)
#   fourbet         = raise at a vs_3bet node / decisions facing a 3-bet
#   fold_to_3bet    = fold at a vs_3bet node / decisions facing a 3-bet
#   af              = postflop (bet+raise)/call (a ratio, not a %)
#   all_in          = hand-instances with any all-in / hands
#   afq             = postflop (bet+raise)/(bet+raise+call+fold) % (folds in the
#                     denominator — the AF discriminator). Derived metric; the
#                     nit/rock bands are PROVISIONAL (tune from probe data).
#   wtsd            = went-to-showdown / saw-the-flop % (research §1B, 6-max)
#   wsd             = won-at-showdown / went-to-showdown % (research §1B, 6-max)
#   flop/turn/river_af = per-street (bet+raise)/call. NO target band by design
#                     (renders no_target, like c-bet) until sim data sets bands.
ARCHETYPE_TARGETS: Dict[str, Dict[str, Tuple[float, float]]] = {
    'nit': {
        'vpip': (10, 16),
        'pfr': (8, 13),
        'threebet': (2, 7),
        'fourbet': (3, 10),
        'fold_to_3bet': (60, 80),
        'af': (1.5, 2.8),
        'all_in': (0, 3),
        'afq': (20, 35),  # provisional
        'wtsd': (20, 24),
        'wsd': (52, 58),
    },
    # Rock: tight-PASSIVE (backlog #10, Option A) — tightest in the field, plays
    # those hands passively (low PFR/VPIP, low AF, high fold-to-3bet). Distinct
    # read from nit (tight-AGGRESSIVE). Bands tuned vs archetype_mixedfield_probe;
    # postflop passivity (AF < nit) carried by the `passive_postflop` spot tendency.
    'rock': {
        'vpip': (8, 15),
        'pfr': (5, 10),
        'threebet': (1, 5),
        'fourbet': (1, 9),
        'fold_to_3bet': (65, 85),
        'af': (0.8, 1.8),
        'all_in': (0, 2),
        'afq': (18, 32),  # provisional
        'wtsd': (20, 24),
        'wsd': (54, 60),
    },
    'tag': {
        'vpip': (20, 28),
        'pfr': (16, 23),
        'threebet': (10, 16),
        'fourbet': (5, 13),
        'fold_to_3bet': (40, 58),
        'af': (2.5, 3.8),
        'all_in': (0, 5),
        'afq': (35, 45),
        'wtsd': (26, 29),
        'wsd': (52, 56),
    },
    'lag': {
        'vpip': (28, 40),
        'pfr': (22, 32),
        'threebet': (16, 26),
        'fourbet': (10, 20),
        'fold_to_3bet': (30, 48),
        'af': (3.3, 5.5),
        'all_in': (0, 7),
        'afq': (40, 52),
        'wtsd': (27, 31),
        'wsd': (48, 52),
    },
    'maniac': {
        'vpip': (45, 70),
        'pfr': (35, 58),
        'threebet': (36, 52),
        'fourbet': (24, 40),
        'fold_to_3bet': (15, 35),
        'af': (5, 9),
        'all_in': (3, 14),
        'afq': (48, 65),
        'wtsd': (30, 40),
        'wsd': (40, 48),
    },
    'calling_station': {
        'vpip': (40, 58),
        'pfr': (4, 12),
        'threebet': (1, 5),
        'fourbet': (0, 5),
        'fold_to_3bet': (20, 40),
        'af': (0.4, 1.2),
        'all_in': (0, 4),
        'afq': (12, 25),
        'wtsd': (32, 45),
        'wsd': (44, 50),
    },
    'weak_fish': {
        'vpip': (33, 52),
        'pfr': (6, 15),
        'threebet': (2, 7),
        'fourbet': (1, 7),
        'fold_to_3bet': (25, 45),
        'af': (0.7, 1.6),
        'all_in': (0, 5),
        'afq': (14, 28),
        'wtsd': (30, 38),
        'wsd': (45, 50),
    },
}

# Below this many observations a stat is reported but flagged low-confidence.
MIN_SAMPLE = 20

# How far outside the band (as a fraction of the band width, min 1 unit) still
# counts as a "warn" rather than a hard "fail".
WARN_MARGIN_FRAC = 0.5


def get_targets(override_json: Optional[str] = None) -> Dict[str, Dict[str, Tuple[float, float]]]:
    """Return the target table, merged with an optional JSON override.

    ``override_json`` is the raw value of the ``ARCHETYPE_TARGET_OVERRIDES``
    app setting: ``{archetype: {stat: [lo, hi]}}``. Unknown archetypes/stats
    are ignored; a malformed blob falls back to the built-in defaults.
    """
    targets = {a: dict(s) for a, s in ARCHETYPE_TARGETS.items()}
    if not override_json:
        return targets
    try:
        overrides = json.loads(override_json)
    except (ValueError, TypeError):
        logger.warning('ARCHETYPE_TARGET_OVERRIDES is not valid JSON; using defaults')
        return targets
    for arch, stats in (overrides or {}).items():
        if arch not in targets or not isinstance(stats, dict):
            continue
        for stat, band in stats.items():
            if stat in targets[arch] and isinstance(band, (list | tuple)) and len(band) == 2:
                targets[arch][stat] = (float(band[0]), float(band[1]))
    return targets


def score_stat(actual: Optional[float], band: Tuple[float, float], sample: int) -> str:
    """Classify an actual value against its target band.

    Returns one of: ``no_data`` (no actual), ``low_n`` (below MIN_SAMPLE),
    ``pass`` (inside the band), ``warn`` (just outside, within WARN_MARGIN_FRAC
    of the band width), or ``fail`` (clearly outside).
    """
    if actual is None:
        return 'no_data'
    if sample < MIN_SAMPLE:
        return 'low_n'
    lo, hi = band
    if lo <= actual <= hi:
        return 'pass'
    margin = max((hi - lo) * WARN_MARGIN_FRAC, 1.0)
    if (lo - margin) <= actual <= (hi + margin):
        return 'warn'
    return 'fail'
