"""Dossier soft signals (Parts B3 + B4) — pure presentation helpers that turn
already-computed numbers into two small reads:

- `build_temperament` — the emotional read: how the opponent handles pressure
  (poise / tilt resistance), how readable they are (expressiveness), and
  whether they've a history of tilting (the pressure `tilt_score`). This is the
  "reading their emotions" surface — distinct from the live in-the-moment
  `emotion` wax-seal the dossier already shows.
- `field_position` — where the opponent sits in the real LLM field for the
  headline stats (VPIP, aggression): "Looser than 80% of the field."

Both are pure functions over values the dossier already has. No new data
sources, no poker logic.

(The handoff's B3 also mentioned a VPIP loosening/tightening *trend*. That rides
on `OpponentTendencies.recent_trend`, which is dormant — never computed, always
'stable' — and a real trend needs windowed snapshots the lifetime store doesn't
keep. So the trend is intentionally NOT surfaced here; tilt is the live signal.)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# A tilt_score needs a few pressure events behind it before it means anything.
MIN_TILT_EVENTS = 3


def build_temperament(
    pressure_summary: Optional[dict],
    anchors: Optional[dict],
) -> Optional[dict]:
    """The emotional read. Returns a block with the temperament gauge values +
    one-line advice, or None when there's nothing to say (no anchors and no
    tilt history).

    - `poise` (anchor): composure under pressure / tilt resistance.
    - `expressiveness` (anchor): how readable they are at the table.
    - `tilt_score` (pressure history): only surfaced once there are
      `MIN_TILT_EVENTS` events behind it, so it isn't noise.
    """
    anchors = anchors or {}
    poise = anchors.get('poise')
    expressiveness = anchors.get('expressiveness')

    tilt: Optional[float] = None
    if pressure_summary and (pressure_summary.get('total_events') or 0) >= MIN_TILT_EVENTS:
        tilt = pressure_summary.get('tilt_score')

    if poise is None and expressiveness is None and tilt is None:
        return None

    lines: List[str] = []
    if poise is not None:
        if poise <= 0.35:
            lines.append("Rattles easily — keep the pressure on and they'll crack.")
        elif poise >= 0.70:
            lines.append("Hard to rattle — pressure won't shake them off a hand.")
    if tilt is not None and tilt >= 0.60:
        lines.append(
            "Runs hot — a history of tilting after a bad beat; "
            "expect loose, sticky play when they're stung."
        )
    if expressiveness is not None:
        if expressiveness >= 0.70:
            lines.append(
                "Wears it on their sleeve — table talk and timing " "give away where they're at."
            )
        elif expressiveness <= 0.30:
            lines.append("Stone-faced — few tells; trust the math over reads.")

    tilt_label = None
    if tilt is not None:
        tilt_label = 'On tilt' if tilt >= 0.66 else 'Runs hot' if tilt >= 0.40 else 'Composed'

    return {
        'tilt_score': round(tilt, 2) if tilt is not None else None,
        'tilt_label': tilt_label,
        'poise': round(poise, 2) if poise is not None else None,
        'expressiveness': round(expressiveness, 2) if expressiveness is not None else None,
        'lines': lines,
    }


# ── B4: field-relative percentiles ──────────────────────────────────────────
#
# Baseline = the real LLM field characterized by EXP_004 (26 personalities,
# `docs/experiments/EXP_004_STICKY_MID_PASSIVE_POPULATION_AUDIT/llm_field.csv`).
# Baked here (sorted) so there's no runtime file dependency; refresh from a new
# population audit when the field shifts. VPIP is per-hand; AF is the global
# bet-raise/call ratio — same definitions as the dossier's observation stats,
# so the percentile is apples-to-apples.

_FIELD_VPIP = [
    0.1022,
    0.1080,
    0.1189,
    0.1647,
    0.1722,
    0.2106,
    0.2127,
    0.2216,
    0.2248,
    0.2266,
    0.2406,
    0.2510,
    0.2587,
    0.2911,
    0.3252,
    0.3380,
    0.3403,
    0.3753,
    0.3813,
    0.3908,
    0.4534,
    0.4563,
    0.4979,
    0.5734,
    0.6961,
    0.8138,
]
_FIELD_AF = [
    0.5486,
    0.5497,
    0.5820,
    0.6085,
    0.6405,
    0.6489,
    0.6642,
    0.6715,
    0.6724,
    0.6761,
    0.6893,
    0.6904,
    0.7078,
    0.7486,
    0.7542,
    0.7583,
    0.7983,
    0.8807,
    0.8929,
    0.9473,
    1.0339,
    1.0913,
    1.1673,
    1.2008,
    1.7565,
    1.9277,
]


def _percentile(sorted_vals: List[float], x: Optional[float]) -> Optional[int]:
    """Percent of the field strictly below `x` (0–100), or None when x is None."""
    if x is None:
        return None
    below = sum(1 for v in sorted_vals if v < x)
    return round(100 * below / len(sorted_vals))


def field_position(vpip: Optional[float], aggression_factor: Optional[float]) -> Optional[dict]:
    """Where the opponent sits in the LLM field for VPIP and aggression.

    Returns `{vpip_pct, vpip_label, af_pct, af_label}` (each pair present only
    when its input is given), or None when neither stat is available.
    """
    vp = _percentile(_FIELD_VPIP, vpip)
    ap = _percentile(_FIELD_AF, aggression_factor)
    if vp is None and ap is None:
        return None

    out: Dict[str, Any] = {}
    if vp is not None:
        out['vpip_pct'] = vp
        out['vpip_label'] = (
            f"Looser than {vp}% of the field"
            if vp >= 50
            else f"Tighter than {100 - vp}% of the field"
        )
    if ap is not None:
        out['af_pct'] = ap
        out['af_label'] = (
            f"More aggressive than {ap}% of the field"
            if ap >= 50
            else f"More passive than {100 - ap}% of the field"
        )
    return out
