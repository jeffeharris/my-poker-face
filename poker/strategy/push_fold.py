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
    (bb_vs_sb, bb_vs_late) + the reshove table (jam over a single open, via
    `reshove_action_6max`, flag-gated). Gated to num_players > 2.

Multi-way short-stack spots that aren't covered by the 6max chart (e.g. a
non-blind hero facing a jam, num_players==2) fall through to the existing
`short_stack.py` heuristic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Dict, Optional

from .preflop_classifier import get_6max_position

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
    facing_open: bool = False,
    over_limper: bool = False,
) -> Optional[str]:
    """Look up the multi-way (6-max) Nash push/fold action for a short-stack
    spot, using `data/push_fold_6max.json` (chip-EV, ICM off).

    Args:
        hand: Canonical hand string (e.g. 'AA', 'AKs', '72o').
        position: Hero's 6-max position (UTG/HJ/CO/BTN/SB/BB).
        effective_stack_bb: min(hero, largest active opponent) in BB.
        num_players: Players at the table this hand. The chart is calibrated
            for 3-6 handed only (its position labels are 6-max). HU (==2)
            returns None so the HU chart stays in charge; 7+ handed also
            returns None — the engine can't label >8-handed seats (9+ collapse
            to blinds, so `get_6max_position` falls back to UTG) and the early
            positions have more players behind than any 6-max range models, so
            those spots fall through to the deep-stack / short_stack.py path.
        facing_jam: True when an opponent has already jammed all-in and
            hero is deciding whether to call.
        opener_position: When facing_jam, the jammer's 6-max position.
            'SB' routes to the bb_vs_sb caller table; anything else routes
            to bb_vs_late. Ignored when not facing a jam. The caller tables
            are BB-vs-jam only, so a non-BB hero facing a jam returns None.
        facing_open: True when hero faces a single non-all-in open and is
            deciding whether to reshove (jam-or-fold). Uses the depth-keyed
            `reshove` table (opener-position-agnostic in v1). Mutually
            exclusive with facing_jam; takes precedence if both are set.
        over_limper: True when hero is first-in-to-RAISE but a single limper
            sits in front (raises this street == 0, no all-in). A short-stack
            ISO-jam-or-fold spot. v1 has no dedicated `iso_over_limper` chart
            section, so it resolves to the `unopened` jam range as a conservative
            proxy (those ranges are tight at 10-15bb → low spew, a strict
            improvement over the deep-stack fallback the spot gets today). A
            future sim-tuned `iso_over_limper` section takes precedence and drops
            in with no caller change. Mutually exclusive with facing_jam/open.

    Returns:
        'jam'/'fold' for an unopened (first-in) spot OR a reshove, 'call'/'fold'
        when facing a jam, or None when out of scope (HU, depth above the top
        bucket, position/hand not in the chart, BB unopened, etc.).

    Scope (v1): unopened jams (UTG/HJ/CO/BTN/SB) + the two caller tables + the
    reshove table (facing a single open; [L] confidence, flag-gated at the call
    site). BB never open-shoves first-in, so BB without facing_jam/facing_open
    returns None.
    """
    # 3-6 handed only: the chart's position labels are 6-max, and >8-handed
    # tables can't even be labeled (9+ collapse to blinds-only → UTG fallback).
    if num_players <= 2 or num_players > 6:
        return None

    chart = _load_chart_6max()
    if not chart:
        return None

    buckets = chart.get("meta", {}).get("depth_bb_buckets", [])
    if effective_stack_bb > (max(buckets) if buckets else PUSH_FOLD_THRESHOLD_BB):
        return None

    if over_limper:
        # First-in to raise, but a single limper sits in front: a short-stack
        # ISO jam. Resolves to the unopened range (v1 proxy) or a dedicated
        # iso_over_limper section if one has been added.
        scenario, depth_data = _resolve_6max_over_limper_scenario(
            chart, position, effective_stack_bb, buckets
        )
    elif facing_open:
        # Reshove (jam-or-fold over a single non-all-in open). Depth-keyed only
        # (opener-position-agnostic in v1); any hero position is in scope, incl.
        # BB (a BB reshove over an open is standard — unlike the unopened chart,
        # which excludes BB).
        scenario, depth_data = _resolve_6max_reshove_scenario(chart, effective_stack_bb)
    elif facing_jam:
        # The caller tables (bb_vs_sb / bb_vs_late) are BB-vs-jam only. A non-BB
        # hero facing a jam — a CO/BTN facing an earlier jam, or the SB (there is
        # no sb_vs_* table) — is out of v1 scope, so fall through.
        if position != "BB":
            return None
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


def _resolve_6max_over_limper_scenario(chart, position, effective_stack_bb, buckets):
    """Return (scenario_map, (conf_key, bucket)) for a short-stack ISO jam over a
    single limper, or (None, None) when out of scope (BB, unknown position, no
    bucket).

    A dedicated `iso_over_limper` section (per position × depth) takes precedence
    if present. v1 ships without one, so this falls back to the `unopened` jam
    range — a deliberately conservative proxy: those ranges are tight at 10-15bb,
    so jamming them over a limper is low-spew and a strict improvement over the
    deep-stack chart the spot falls to today. The dead-money-aware widening is the
    sim-tuned follow-up.
    """
    if position not in _6MAX_UNOPENED_POSITIONS:
        return None, None  # BB never open-shoves; unknown positions skip.
    bucket = _nearest_bucket_in(buckets, effective_stack_bb)
    if bucket is None:
        return None, None
    iso = chart.get("iso_over_limper", {}).get(position, {}).get(str(bucket))
    if iso:
        return iso, (position + "_iso", bucket)
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


def reshove_action_6max(
    hand: str,
    game_state,
    player_idx: int,
    num_seated: int,
    big_blind: float,
    effective_stack_bb: float,
    opener_fold_equity_ok: Optional[Callable[[int], bool]] = None,
) -> Optional[str]:
    """Controller-agnostic reshove decision: jam-or-fold over a SINGLE
    non-all-in open. Returns 'jam'/'fold' when in scope, else None.

    A pure read of `game_state` so any controller can reuse it — the sharp bot
    wires it behind PUSH_FOLD_6MAX_RESHOVE_ENABLED; other bot types may opt in
    independently (the 6-max push/fold *charts* are sharp-only, but reshoving a
    short stack over an open is a generally useful skill). Callers pass the
    effective stack (stack_utils.effective_stack_bb) and big blind they already
    computed.

    Fail-closed — fires only on a clean hero-vs-one-opener spot: exactly one
    live raiser, a single raise this round (no 3-bet+ war), no all-in opponent
    (that is the caller-table spot, not a reshove), no cold-caller between, and
    hero is not the opener. Anything else returns None (defer to the caller's
    fallback path).

    `opener_fold_equity_ok`, when given, is called with the opener's seat index
    once the spot is identified; if it returns False the whole reshove is
    declined (None → fall through). This is the fold-equity gate — reshoving has
    no value vs an opener who won't fold, so the caller injects a read-based
    predicate (see exploitation.reshove_fold_equity_ok). Omitted → no gate
    (chart always applies; used by the chart-routing unit tests).
    """
    if num_seated <= 2 or num_seated > 6:
        return None
    # Exactly one raise this round — excludes limped pots (0) and 3-bet+ wars.
    if getattr(game_state, "raises_this_round", 0) != 1:
        return None

    active_opps = [
        (i, p)
        for i, p in enumerate(game_state.players)
        if i != player_idx and not getattr(p, "is_folded", False)
    ]
    # An all-in opponent makes this a facing-jam (caller-table) spot, not a
    # reshove — the caller must have already routed those; bail to be safe.
    if any(
        getattr(p, "stack", 1) == 0 and getattr(p, "bet", 0) > big_blind for _, p in active_opps
    ):
        return None

    # The lone opener = the single non-BB opponent in for more than a blind. A
    # second voluntary contributor (cold-caller) is a multiway reshove the v1
    # single-opener table doesn't model → fall through.
    raisers = [
        (i, p)
        for i, p in active_opps
        if get_6max_position(game_state, i) != "BB" and getattr(p, "bet", 0) > big_blind
    ]
    if len(raisers) != 1:
        return None
    opener_idx, opener = raisers[0]
    highest = max((getattr(p, "bet", 0) for _, p in active_opps), default=0)
    if getattr(opener, "bet", 0) != highest:
        return None  # someone outbid the opener without a recorded raise → bail

    # Fold-equity gate: reshoving has no value vs an opener who won't fold, so
    # decline the whole spot (defer to the caller's fallback) when the injected
    # read-based predicate says there's no fold equity.
    if opener_fold_equity_ok is not None and not opener_fold_equity_ok(opener_idx):
        return None

    return lookup_push_fold_action_6max(
        hand=hand,
        position=get_6max_position(game_state, player_idx),
        effective_stack_bb=effective_stack_bb,
        num_players=num_seated,
        facing_open=True,
        opener_position=get_6max_position(game_state, opener_idx),
    )


def _resolve_6max_reshove_scenario(chart, effective_stack_bb):
    """Return (scenario_map, (conf_key, bucket)) for a reshove spot, or
    (None, None) when out of scope. Depth-keyed only (no position dimension);
    a sub-8 BB reshove clamps up to the table's own minimum bucket."""
    table = chart.get("reshove", {})
    if not table:
        return None, None
    table_buckets = sorted(int(k) for k in table.keys())
    bucket = _nearest_bucket_in(table_buckets, effective_stack_bb)
    if bucket is None:
        return None, None
    scenario = table.get(str(bucket))
    if not scenario:
        return None, None
    return scenario, ("reshove", bucket)


def reset_chart_cache() -> None:
    """Force both charts to reload on next access. Useful in tests that
    monkeypatch the chart files."""
    global _CHART, _CHART_6MAX
    _CHART = None
    _CHART_6MAX = None
