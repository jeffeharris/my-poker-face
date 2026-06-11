"""Push/fold lookup for short-stack play (HU + multi-way).

When effective stack falls below ~15 BB, the deep-stack strategy tables
become mis-calibrated — their raise sizes commit too much of the stack
for non-jam plays to be coherent. This module routes short-stack preflop
decisions to separate Nash-style push/fold charts loaded from
`data/push_fold_hu.json` (heads-up) and `data/push_fold_6max.json`
(multi-way).

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

Two charts are served:
  - `lookup_push_fold_action` (HU, `push_fold_hu.json`) — SB open / BB
    call-vs-jam, gated to num_opponents == 1.
  - `lookup_push_fold_action_6max` (multi-way, `push_fold_6max.json`) —
    per-position unopened jams (UTG/HJ/CO/BTN/SB) + the two caller tables
    (bb_vs_sb, bb_vs_late), gated to num_players > 2. Reshove deferred to v2.

Multi-way short-stack spots that aren't covered by the 6max chart (e.g. a
non-blind hero facing a jam, num_players==2) fall through to the existing
`short_stack.py` heuristic.
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


# Loaded on first access; module-level caches.
_CHART: Optional[Dict] = None
_CHART_PATH = Path(__file__).parent / "data" / "push_fold_hu.json"

_CHART_6MAX: Optional[Dict] = None
_CHART_6MAX_PATH = Path(__file__).parent / "data" / "push_fold_6max.json"


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


def _load_chart_6max() -> Optional[Dict]:
    global _CHART_6MAX
    if _CHART_6MAX is not None:
        return _CHART_6MAX
    try:
        with _CHART_6MAX_PATH.open() as f:
            _CHART_6MAX = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"push_fold: failed to load 6max chart at {_CHART_6MAX_PATH}: {e}")
        _CHART_6MAX = {}
    return _CHART_6MAX


def _supported_depth_buckets() -> list:
    """Return the HU chart's depth buckets in sorted order, or empty list if
    the chart is unavailable."""
    chart = _load_chart() or {}
    return chart.get("meta", {}).get("depth_bb_buckets", [])


def _nearest_bucket_in(buckets: list, effective_stack_bb: float) -> Optional[int]:
    """Snap effective_stack_bb to the nearest bucket in `buckets`.

    Returns None when stack is above the top bucket (caller should fall
    through to the deep-stack path) or when `buckets` is empty. Below the
    bottom bucket, clamps to the smallest available depth.
    """
    if not buckets:
        return None
    if effective_stack_bb > max(buckets):
        return None
    if effective_stack_bb <= min(buckets):
        return min(buckets)
    # Pick the closest bucket (round to nearest, ties prefer lower).
    return min(buckets, key=lambda b: (abs(b - effective_stack_bb), b))


def _nearest_bucket(effective_stack_bb: float) -> Optional[int]:
    """Snap to the nearest HU chart bucket (see `_nearest_bucket_in`)."""
    return _nearest_bucket_in(_supported_depth_buckets(), effective_stack_bb)


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


_6MAX_UNOPENED_POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB")


def lookup_push_fold_action_6max(
    hand: str,
    position: str,
    effective_stack_bb: float,
    num_players: int,
    facing_jam: bool = False,
    opener_position: Optional[str] = None,
) -> Optional[str]:
    """Look up the multi-way (6-max) Nash push/fold action for a short-stack
    spot, using `data/push_fold_6max.json` (chip-EV, ICM off).

    Args:
        hand: Canonical hand string (e.g. 'AA', 'AKs', '72o').
        position: Hero's 6-max position (UTG/HJ/CO/BTN/SB/BB).
        effective_stack_bb: min(hero, largest active opponent) in BB.
        num_players: Players seated/active in the hand. Multi-way only
            (num_players > 2); HU (==2) returns None so the HU chart stays
            in charge.
        facing_jam: True when an opponent has already jammed all-in and
            hero is deciding whether to call.
        opener_position: When facing_jam, the jammer's 6-max position.
            'SB' routes to the bb_vs_sb caller table; anything else routes
            to bb_vs_late. Ignored when not facing a jam.

    Returns:
        'jam'/'fold' for an unopened (first-in) spot, 'call'/'fold' when
        facing a jam, or None when out of scope (HU, depth above the top
        bucket, position/hand not in the chart, BB unopened, etc.).

    Scope (v1): unopened jams (UTG/HJ/CO/BTN/SB) + the two caller tables.
    BB never open-shoves, so BB without `facing_jam` returns None. Reshove
    (jam over a min-raise) is deferred to v2.
    """
    if num_players <= 2:
        return None

    chart = _load_chart_6max()
    if not chart:
        return None

    buckets = chart.get("meta", {}).get("depth_bb_buckets", [])
    if effective_stack_bb > (max(buckets) if buckets else PUSH_FOLD_THRESHOLD_BB):
        return None

    if facing_jam:
        scenario, depth_data = _resolve_6max_call_scenario(
            chart, opener_position, effective_stack_bb, buckets
        )
    else:
        scenario, depth_data = _resolve_6max_unopened_scenario(
            chart, position, effective_stack_bb, buckets
        )

    if scenario is None:
        return None

    hand_actions = scenario.get(hand)
    if not hand_actions:
        return None

    action = max(hand_actions, key=hand_actions.get)

    # Audit low-confidence ([L]) routing per the README contract.
    conf = chart.get("meta", {}).get("confidence", {})
    tag = conf.get(depth_data[0], {}).get(str(depth_data[1])) if depth_data else None
    if tag == "L":
        logger.debug(
            "push_fold_6max: low-confidence [L] cell used " "(%s, %sBB) hand=%s -> %s",
            depth_data[0],
            depth_data[1],
            hand,
            action,
        )
    return action


def _resolve_6max_unopened_scenario(chart, position, effective_stack_bb, buckets):
    """Return (scenario_map, (conf_key, bucket)) for an unopened jam spot,
    or (None, None) when out of scope (BB, unknown position, no bucket)."""
    if position not in _6MAX_UNOPENED_POSITIONS:
        return None, None  # BB never open-shoves; unknown positions skip.
    bucket = _nearest_bucket_in(buckets, effective_stack_bb)
    if bucket is None:
        return None, None
    pos_data = chart.get("unopened", {}).get(position, {})
    scenario = pos_data.get(str(bucket))
    if not scenario:
        return None, None
    return scenario, (position, bucket)


def _resolve_6max_call_scenario(chart, opener_position, effective_stack_bb, buckets):
    """Return (scenario_map, (conf_key, bucket)) for a facing-jam call spot.

    SB jammer → bb_vs_sb; any other jammer → bb_vs_late. bb_vs_late has no
    4 BB row, so a sub-6 BB late jam clamps up to the table's own minimum
    bucket.
    """
    table_key = "bb_vs_sb" if opener_position == "SB" else "bb_vs_late"
    table = chart.get("call_vs_shove", {}).get(table_key, {})
    if not table:
        return None, None
    table_buckets = sorted(int(k) for k in table.keys())
    bucket = _nearest_bucket_in(table_buckets, effective_stack_bb)
    if bucket is None:
        return None, None
    scenario = table.get(str(bucket))
    if not scenario:
        return None, None
    return scenario, (table_key, bucket)


def reset_chart_cache() -> None:
    """Force both charts to reload on next access. Useful in tests that
    monkeypatch the chart files."""
    global _CHART, _CHART_6MAX
    _CHART = None
    _CHART_6MAX = None
