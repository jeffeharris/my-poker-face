"""Preflop leak finder — your range vs a reference, the visual coaching signal.

Aggregates a player's OWN preflop decisions (`player_decision_analysis`) into a
VPIP-by-hand-by-position picture and diffs it against a reference opening range.
The deviations ARE the leaks: hands you play that the reference folds (too
loose), hands you fold that it opens (too tight). This is the post-session,
specific, quantified signal — not per-hand noise.

v1 reference = the position-aware TAG opening ranges in `poker.hand_ranges`
(what the coach already uses for `in_range`). A solver-chart reference (querying
the same lookup tables the bots play) is a planned upgrade — it needs synthetic
node construction (`build_preflop_node` wants a game_state), so it's deferred.

The core (`compute_preflop_leaks`) is pure: it takes decision records + a
reference predicate and returns ranked leaks, so it's unit-testable without a DB.
`load_owner_preflop_decisions` is the thin DB adapter.

Honest scope: this measures VPIP (voluntary play vs fold), position-grouped — it
catches the big, costly "too loose / too tight by position" leak (the #1 leak),
not raise-vs-call frequencies or sizing. It conflates RFI with calling raises
(the TAG range is an opening range). Needs sample to be meaningful.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# Actions that count as Voluntarily Putting $ In Pot.
_VOLUNTARY = {'call', 'raise', 'bet', 'all_in'}

# Minimum times a (position, hand) combo must appear before we call it a leak —
# one loose call isn't a leak, a pattern is.
DEFAULT_MIN_SAMPLE = 4


@dataclass(frozen=True)
class PreflopLeak:
    """One leak: a (position, hand) where the player diverges from the reference."""

    position_group: str  # 'early' | 'middle' | 'late' | 'blind'
    canon: str  # canonical hand, e.g. 'AJo', 'TT', '72o'
    leak_type: str  # 'too_loose' (plays a fold hand) | 'too_tight' (folds an open hand)
    n: int  # decisions observed for this (position, hand)
    vpip_pct: float  # how often the player voluntarily played it (0-100)
    reference_plays: bool  # does the reference range open/play this hand here?
    # Severity ≈ how often the player makes the wrong call here. For too_loose
    # it's the count of voluntary plays of a fold-hand; for too_tight it's the
    # count of folds of an open-hand. Higher = bleeds more / more habitual.
    severity: int


@dataclass(frozen=True)
class PreflopLeakReport:
    leaks: list[PreflopLeak]  # ranked, worst first
    total_decisions: int
    sampled_combos: int  # (position, hand) combos meeting the sample gate
    by_position_summary: dict[str, dict]  # position_group -> {too_loose, too_tight, decisions}


def reference_plays(canon: str, position_group: str) -> bool:
    """Does the TAG reference open/play `canon` from this position group?"""
    from poker.hand_ranges import OPENING_RANGES

    _ensure_position_groups()
    pos = _POSITION_GROUP_BY_NAME.get(position_group)
    if pos is None:
        return False
    return canon in OPENING_RANGES.get(pos, frozenset())


# position_group string -> Position enum (built lazily to avoid an import cycle
# at module load; the enum values are stable).
_POSITION_GROUP_BY_NAME: dict[str, object] = {}


def _ensure_position_groups() -> None:
    if _POSITION_GROUP_BY_NAME:
        return
    from poker.hand_ranges import Position

    _POSITION_GROUP_BY_NAME.update(
        {'early': Position.EARLY, 'middle': Position.MIDDLE, 'late': Position.LATE, 'blind': Position.BLIND}
    )


def position_to_group(position: Optional[str]) -> Optional[str]:
    """Normalize a stored position (key, 6-max label, or display) to a group.

    Returns 'early' | 'middle' | 'late' | 'blind', or None if unmappable.
    """
    if not position:
        return None
    _ensure_group_names()
    p = position.strip().lower().replace(' ', '_')
    # Blinds FIRST — get_position_group is an RFI mapper that buckets blinds as
    # LATE, so we must catch them before falling through to it.
    if 'blind' in p or p in ('sb', 'bb'):
        return 'blind'
    # Long game keys → reuse hand_ranges' own mapper for the openers.
    if p in ('button', 'cutoff') or p.startswith('under_the_gun') or p.startswith('middle_position'):
        try:
            from poker.hand_ranges import get_position_group

            return _GROUP_NAME_BY_ENUM.get(get_position_group(p))
        except Exception:
            pass
    # 6-max labels / display forms.
    if p in ('utg', 'utg+1', 'utg1', 'ep', 'early'):
        return 'early'
    if p in ('hj', 'mp', 'mp1', 'mp2', 'middle', 'hijack', 'lojack', 'lj'):
        return 'middle'
    if p in ('co', 'btn', 'button', 'cutoff', 'late'):
        return 'late'
    return None


_GROUP_NAME_BY_ENUM: dict = {}


def _ensure_group_names() -> None:
    if _GROUP_NAME_BY_ENUM:
        return
    from poker.hand_ranges import Position

    _GROUP_NAME_BY_ENUM.update(
        {Position.EARLY: 'early', Position.MIDDLE: 'middle', Position.LATE: 'late', Position.BLIND: 'blind'}
    )


def compute_preflop_leaks(
    decisions: Iterable[dict],
    reference: Callable[[str, str], bool] = reference_plays,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> PreflopLeakReport:
    """Diff a player's preflop decisions against a reference range.

    `decisions`: iterable of dicts with `canon`, `position` (any form), and
    `action` (the preflop action taken). Pure — no DB/IO.
    """
    _ensure_position_groups()
    _ensure_group_names()

    # (group, canon) -> [voluntary_count, total]
    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    total = 0
    for d in decisions:
        canon = d.get('canon')
        group = position_to_group(d.get('position'))
        action = (d.get('action') or '').lower()
        if not canon or not group:
            continue
        total += 1
        agg[(group, canon)][1] += 1
        if action in _VOLUNTARY:
            agg[(group, canon)][0] += 1

    leaks: list[PreflopLeak] = []
    by_pos: dict[str, dict] = defaultdict(lambda: {'too_loose': 0, 'too_tight': 0, 'decisions': 0})
    sampled = 0
    for (group, canon), (vol, n) in agg.items():
        by_pos[group]['decisions'] += n
        if n < min_sample:
            continue
        sampled += 1
        plays_ref = reference(canon, group)
        vpip_pct = 100.0 * vol / n
        if not plays_ref and vol > 0:
            # Voluntarily playing a hand the reference folds — too loose.
            leaks.append(
                PreflopLeak(group, canon, 'too_loose', n, round(vpip_pct, 1), plays_ref, vol)
            )
            by_pos[group]['too_loose'] += vol
        elif plays_ref and vol < n:
            # Folding a hand the reference opens — too tight.
            folds = n - vol
            leaks.append(
                PreflopLeak(group, canon, 'too_tight', n, round(vpip_pct, 1), plays_ref, folds)
            )
            by_pos[group]['too_tight'] += folds

    # Worst first: by how many wrong decisions, then by how extreme.
    leaks.sort(key=lambda lk: (lk.severity, lk.n), reverse=True)
    return PreflopLeakReport(
        leaks=leaks,
        total_decisions=total,
        sampled_combos=sampled,
        by_position_summary=dict(by_pos),
    )


def load_owner_preflop_decisions(db_path: str, owner_id: str) -> list[dict]:
    """Load an owner's HUMAN preflop decisions from player_decision_analysis.

    Scopes to games owned by `owner_id`, and to the human seat (player_name ==
    the game's owner_name / not an AI persona). Best-effort, read-only.
    """
    import sqlite3

    rows: list[dict] = []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        # owner_name identifies the human seat; join games for owner scoping.
        cur = conn.execute(
            """
            SELECT pda.player_hand_canonical AS canon,
                   pda.player_position       AS position,
                   pda.action_taken          AS action
            FROM player_decision_analysis pda
            JOIN games g ON g.game_id = pda.game_id
            WHERE pda.phase = 'PRE_FLOP'
              AND g.owner_id = ?
              AND pda.player_name = g.owner_name
              AND pda.player_hand_canonical IS NOT NULL
            """,
            (owner_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except Exception as e:
        logger.warning("load_owner_preflop_decisions failed for %s: %s", owner_id, e)
    return rows
