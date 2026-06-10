"""Depth-aware preflop reference strategy — the grading standard.

Mirrors the TieredBot's *base* chart selection (see
``tiered_bot_controller._select_preflop_table``) so the coach grades a human
decision against exactly what a competent bot would play at that depth and
seat count — the same standard the player's opponents use.

Deliberately the BASELINE competent reference: it skips the archetype
width-tier charts (loose / station / …) the bot layers on for personality —
those are opponent *flavor*, not the standard we grade against. It also skips
the personality/emotional distortion the bot applies downstream; we want the
raw solver frequencies at the node.

Depth selection:
  - 2-handed            → the HU chart
  - otherwise           → nearest depth bucket (25/50) or the 100bb base

Short-stack push/fold (≤15bb) is HU-only and needs live all-in context, so it
is resolved at capture time, not here; the grading layer marks ≤15bb multiway
as out-of-scope rather than mis-grading it against a deep chart.

The chart actions carry raise SIZES (``raise_3x``, ``raise_2.2x`` …); we fold
them into a single ``raise`` bucket so they line up with a human's plain
fold / call / raise.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .nodes import PreflopNode
from .strategy_table import (
    load_depth_strategy_tables,
    load_hu_strategy_table,
    load_strategy_table,
    nearest_depth_bucket,
)

# 6-max position labels, used to enumerate plausible openers when the exact
# opener is unknown (backfill). Invalid matchups simply miss the chart, so
# trying all and averaging the hits self-filters to legal (opener-before-hero)
# pairs.
_POSITIONS = ('UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB')


# ── Chart caching ───────────────────────────────────────────────────────
# Loaded once and reused; the tables are immutable.
_base = None
_depth: Dict[int, object] = {}
_hu = None
_loaded = False
_archetype_tables: Dict[str, object] = {}  # archetype key → table (or None)


def _ensure_loaded() -> None:
    global _base, _depth, _hu, _loaded
    if _loaded:
        return
    _base = load_strategy_table()
    _depth = load_depth_strategy_tables() or {}
    _hu = load_hu_strategy_table()
    _loaded = True


def _archetype_table(key: str):
    """The width-tier chart for an opponent archetype (nit / maniac / station …),
    or None if the key is unknown. Mirrors the bot's archetype table selection
    (deviation_profiles.ARCHETYPE_WIDTH_TABLE). Only used by drills that grade
    "what would a <archetype> do" — never by the baseline grading path."""
    if key in _archetype_tables:
        return _archetype_tables[key]
    # Lazy import avoids a module-load cycle (deviation_profiles ↔ strategy).
    from .deviation_profiles import ARCHETYPE_WIDTH_TABLE

    _ensure_loaded()
    fname = ARCHETYPE_WIDTH_TABLE.get(key, '__unknown__')
    if fname == '__unknown__':
        table = None
    elif fname is None:
        table = _base  # e.g. 'tag' → the base chart
    else:
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        table = load_strategy_table(json_path=os.path.join(data_dir, fname))
    _archetype_tables[key] = table
    return table


def _select_table(num_players: int, effective_stack_bb: float, archetype: Optional[str] = None):
    """Mirror the bot's base table selection. Returns a StrategyTable.

    When `archetype` names a known width tier, return that opponent's chart
    instead (for "what would a <archetype> do" drills); unknown archetypes fall
    through to the baseline selection."""
    _ensure_loaded()
    if archetype:
        table = _archetype_table(archetype)
        if table is not None:
            return table
    if num_players == 2 and _hu is not None:
        return _hu
    bucket = nearest_depth_bucket(effective_stack_bb)
    return _depth.get(bucket) or _base


def bucket_actions(action_probabilities: Dict[str, float]) -> Dict[str, float]:
    """Fold raise-size variants into a single ``raise`` bucket.

    ``{fold, call, raise_3x, raise_2.2x}`` → ``{fold, call, raise}``. Any
    non-fold/call action (jam, bet, all_in, raise_*) counts as ``raise``.
    """
    out = {'fold': 0.0, 'call': 0.0, 'raise': 0.0}
    for action, prob in action_probabilities.items():
        if action == 'fold':
            out['fold'] += prob
        elif action == 'call':
            out['call'] += prob
        else:  # raise_*, jam, bet, all_in, …
            out['raise'] += prob
    return out


def _lookup_bucketed(table, node: PreflopNode) -> Optional[Dict[str, float]]:
    profile = table.lookup_preflop(node)
    if profile is None:
        return None
    return bucket_actions(profile.action_probabilities)


def _average(profiles: List[Dict[str, float]]) -> Dict[str, float]:
    n = len(profiles)
    return {k: sum(p[k] for p in profiles) / n for k in ('fold', 'call', 'raise')}


def reference_strategy(
    hand: str,
    position: str,
    scenario: str,
    opener: Optional[str],
    effective_stack_bb: float,
    num_players: int,
    archetype: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """Bucketed reference frequencies ``{fold, call, raise}`` for a node.

    Returns ``None`` when the chart has no entry for the spot (caller should
    skip, not guess). When ``opener`` is unknown (backfill), averages across
    every opener the chart actually holds for this (scenario, position) — the
    legal-matchup self-filter means only opener-before-hero pairs contribute.

    ``archetype`` (nit / tag / lag / maniac / calling_station …) grades against
    that opponent's width-tier chart instead of the baseline — for "what would a
    <archetype> do" read drills. Default None = the baseline standard.
    """
    table = _select_table(num_players, effective_stack_bb, archetype)

    if scenario == 'rfi':
        return _lookup_bucketed(table, PreflopNode(hand, position, 'rfi', ''))

    # Faced-raise scenarios need an opener for the matchup key.
    if opener:
        return _lookup_bucketed(table, PreflopNode(hand, position, scenario, opener))

    # Opener unknown → average over every opener present in the chart.
    hits = [
        b
        for o in _POSITIONS
        if o != position
        for b in (_lookup_bucketed(table, PreflopNode(hand, position, scenario, o)),)
        if b is not None
    ]
    return _average(hits) if hits else None
