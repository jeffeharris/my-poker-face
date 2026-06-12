"""Dossier "the read" (Part B2) — turn an opponent's lifetime tendencies into
human-facing exploit advice + an archetype badge.

This is a thin PRESENTATION layer over the tiered-bot exploitation detectors
(`poker.strategy.exploitation`). It reimplements no poker logic: the patterns
are decided by `classify_detected_patterns` / `classify_opponent_archetype`
(the same calibrated detectors the bots use to actually exploit these
archetypes), and this module only maps each detected label to a one-line piece
of advice for the human and an intensity-aware qualifier.

Discipline (see `project_archetype_exploitation_goNoGo` / the exploitation-layer
eval in memory): only surface reads for patterns that correspond to a real,
exploitable tendency. The detector returns the labels; the presentation maps
below simply have no entry for anything we wouldn't stand behind, so an
unmapped label is silently dropped rather than dressed up as an edge. The
detectors also self-gate on sample size (returning [] / None on thin data), so
a read never fires on noise.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Archetype badge labels, keyed by what `classify_opponent_archetype` emits.
_ARCHETYPE_BADGES: Dict[str, Dict[str, str]] = {
    'pure_station': {'id': 'pure_station', 'label': 'Calling Station'},
    'loose_passive': {'id': 'loose_passive', 'label': 'Calling Station'},
    'sticky_jammer': {'id': 'sticky_jammer', 'label': 'Sticky Jammer'},
    'hyper_aggressive': {'id': 'hyper_aggressive', 'label': 'Maniac'},
}

# Detected pattern → the exploit line shown to the human. Keyed by the labels
# `classify_detected_patterns` returns.
_PATTERN_TIPS: Dict[str, str] = {
    'high_fold_to_cbet': 'Folds to c-bets — barrel relentlessly, ' 'give up far less than usual.',
    'hyper_passive': 'Calls too much and rarely raises — value-bet thin '
    'and stop bluffing into them.',
    'loose_passive': 'Plays loose and calls down passively — value-bet thin '
    'and stop bluffing into them.',
    'passive_with_jams': 'Passive but jam-happy — keep value-betting, but '
    "treat their raise as the real thing; don't bluff-raise.",
    'tight_nit': 'Folds most hands preflop — steal wider and fold to ' 'their aggression.',
    'hyper_aggressive': 'Over-aggressive — let them keep bluffing into you '
    'and call down lighter.',
}

# `compute_pattern_intensity` only returns ramps for these keys; others
# (passive_with_jams) just carry no intensity qualifier.
_INTENSITY_BANDS = (
    (0.66, 'strongly'),
    (0.33, 'clearly'),
    (0.0, 'somewhat'),
)


def _intensity_word(value: Optional[float]) -> Optional[str]:
    """Map a 0–1 intensity to a qualifier word, or None when no intensity."""
    if value is None:
        return None
    for floor, word in _INTENSITY_BANDS:
        if value >= floor:
            return word
    return None


def build_the_read(tendencies) -> Dict[str, Any]:
    """Build the dossier read from a reconstructed `OpponentTendencies`.

    Returns `{'tips': [...], 'archetype': {...} | None}`:
      - `tips`: list of `{pattern, text, intensity}` (intensity 0–1 or None),
        ordered by the detector. Empty when nothing fires (thin data / no
        exploitable tendency).
      - `archetype`: the single-label badge `{id, label}` or None.
    """
    from poker.memory.opponent_model import _build_aggregate_from_single
    from poker.strategy.exploitation import (
        classify_detected_patterns,
        classify_opponent_archetype,
        compute_pattern_intensity,
    )

    stats = _build_aggregate_from_single(tendencies)
    patterns = classify_detected_patterns(stats)
    intensities = compute_pattern_intensity(stats)

    tips: List[Dict[str, Any]] = []
    for pattern in patterns:
        text = _PATTERN_TIPS.get(pattern)
        if not text:
            continue  # detector fired but we don't surface this label as advice
        intensity = intensities.get(pattern)
        tips.append(
            {
                'pattern': pattern,
                'text': text,
                'intensity': round(intensity, 2) if intensity is not None else None,
            }
        )

    archetype_label = classify_opponent_archetype(stats)
    archetype = _ARCHETYPE_BADGES.get(archetype_label) if archetype_label else None

    return {'tips': tips, 'archetype': archetype}
