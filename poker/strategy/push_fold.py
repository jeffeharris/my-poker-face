"""HU push/fold lookup for short-stack play.

When effective stack falls below ~15 BB, the deep-stack strategy table
in `preflop_100bb_hu.json` becomes mis-calibrated — its raise sizes
commit too much of the stack for non-jam plays to be coherent. This
module routes short-stack preflop decisions to a separate Nash-style
push/fold chart loaded from `data/push_fold_hu.json`.

Lookup contract:
  - `lookup_push_fold_action(hand, position, effective_stack_bb, num_opponents)`
  - Returns `'jam'` or `'fold'` (or `'call'` for BB facing a jam), or
    `None` when the situation isn't in scope (multi-way, hand not
    recognized, depth above the chart's highest bucket, etc.).
  - Caller is responsible for routing the resulting abstract action
    through `resolve_preflop_sizing` to produce the final game action.

Stack-depth lookup snaps to the nearest published bucket. Above the
top bucket (15 BB), returns None so the deep-stack path stays in
charge. Below the bottom bucket (5 BB), clamps to the bottom bucket.

This is HU-only for v1. Multi-way short-stack decisions fall through
to the existing `short_stack.py` heuristic (which suppresses medium
raises rather than enforcing a Nash range).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional


logger = logging.getLogger(__name__)


PUSH_FOLD_THRESHOLD_BB = 15.0
"""Effective stack depth (BB) at or below which push/fold takes over from
the deep-stack table. Above this, the lookup returns None and the
existing strategy-table pipeline handles the decision."""


# Loaded on first access; module-level cache.
_CHART: Optional[Dict] = None
_CHART_PATH = Path(__file__).parent / "data" / "push_fold_hu.json"


def _load_chart() -> Optional[Dict]:
    global _CHART
    if _CHART is not None:
        return _CHART
    try:
        with _CHART_PATH.open() as f:
            _CHART = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"push_fold: failed to load chart at {_CHART_PATH}: {e}")
        _CHART = {}
    return _CHART


def _supported_depth_buckets() -> list:
    """Return the chart's depth buckets in sorted order, or empty list if
    the chart is unavailable."""
    chart = _load_chart() or {}
    return chart.get("meta", {}).get("depth_bb_buckets", [])


def _nearest_bucket(effective_stack_bb: float) -> Optional[int]:
    """Snap effective_stack_bb to the nearest published bucket.

    Returns None when stack is above the top bucket (caller should fall
    through to the deep-stack path) or when no chart is loaded. Below
    the bottom bucket, clamps to the smallest available depth.
    """
    buckets = _supported_depth_buckets()
    if not buckets:
        return None
    if effective_stack_bb > max(buckets):
        return None
    if effective_stack_bb <= min(buckets):
        return min(buckets)
    # Pick the closest bucket (round to nearest, ties prefer lower).
    return min(buckets, key=lambda b: (abs(b - effective_stack_bb), b))


def lookup_push_fold_action(
    hand: str,
    position: str,
    effective_stack_bb: float,
    num_opponents: int = 1,
    facing_jam: bool = False,
) -> Optional[str]:
    """Look up the Nash push/fold action for a short-stack HU spot.

    Args:
        hand: Canonical hand string (e.g. 'AA', 'AKs', '72o'). Same
            format as the deep-stack chart.
        position: Hero's position. Currently only 'SB' and 'BB' are in
            scope (HU-only v1).
        effective_stack_bb: Smaller of hero's stack and the active
            opponent's stack, in big blinds.
        num_opponents: Active non-hero opponents in the hand. v1 only
            supports HU (num_opponents == 1); other values return None.
        facing_jam: When True (BB facing an SB all-in), use the
            bb_vs_jam scenario instead of sb_open. Caller must determine
            this from the live game state.

    Returns:
        'jam' or 'fold' for SB open spots; 'call' or 'fold' for BB
        facing a jam; None when the situation isn't covered by this
        chart (multi-way, depth above threshold, missing hand, etc.).
    """
    if num_opponents != 1:
        return None
    if effective_stack_bb > PUSH_FOLD_THRESHOLD_BB:
        return None
    if position not in ('SB', 'BB'):
        return None

    chart = _load_chart()
    if not chart:
        return None

    bucket = _nearest_bucket(effective_stack_bb)
    if bucket is None:
        return None

    depth_key = f"{bucket}bb"
    depth_data = chart.get(depth_key)
    if not depth_data:
        return None

    # SB acts first; scenario depends on hero position + facing_jam.
    if position == 'SB' and not facing_jam:
        scenario = depth_data.get('sb_open', {})
    elif position == 'BB' and facing_jam:
        scenario = depth_data.get('bb_vs_jam', {})
    else:
        # Combinations we don't cover (BB without an SB jam to face;
        # SB facing a re-jam — too rare at <15 BB to warrant a row).
        return None

    hand_actions = scenario.get(hand)
    if not hand_actions:
        return None

    # Pick the action with highest probability. v1 is binary 100/0 per
    # hand so this is unambiguous; future mixed strategies would need
    # weighted sampling, in which case the caller should expose an RNG
    # rather than the lookup deciding.
    return max(hand_actions, key=hand_actions.get)


def reset_chart_cache() -> None:
    """Force the chart to reload on next access. Useful in tests that
    monkeypatch the chart file."""
    global _CHART
    _CHART = None
