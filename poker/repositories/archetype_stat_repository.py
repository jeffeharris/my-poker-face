"""Repository for `archetype_stat_counts` — per-archetype behavioral tallies.

Durable, sandbox-scoped counters for the background AI-vs-AI cash sim. The lean
lobby sim (`cash_mode/full_sim.py`) discards its per-decision stream; the
`ArchetypeStatRecorder` accumulates tallies in memory and flushes them here as
DELTAS (add-to-existing), so the Archetype Review tool can read AI-only sim
behavior without bloating `player_decision_analysis` with unbounded rows.

Schema is created by the v156 migration; this class only touches data.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)

# The additive counter columns (everything except the PK + updated_at).
COUNTER_COLUMNS = (
    'hands',
    'pf_decisions',
    'vpip',
    'pfr',
    'vs_open',
    'vs_open_agg',
    'vs_3bet',
    'vs_3bet_agg',
    'vs_3bet_fold',
    'postflop_agg',
    'postflop_call',
    'allin_hands',
    # Backlog #11 — showdown reach/win + per-street aggression. Aggregate
    # postflop fold = flop_fold + turn_fold + river_fold (not stored separately).
    'saw_flop',
    'showdowns',
    'showdowns_won',
    'flop_agg',
    'flop_call',
    'flop_fold',
    'turn_agg',
    'turn_call',
    'turn_fold',
    'river_agg',
    'river_call',
    'river_fold',
    # Backlog #6 — flop continuation betting. cbet = the preflop aggressor's
    # first-in flop bet; fold_to_cbet = a fold when facing one.
    'cbet_opportunity',
    'cbet_made',
    'cbet_faced',
    'fold_to_cbet',
)


class ArchetypeStatRepository(BaseRepository):
    """CRUD for `archetype_stat_counts` (delta-accumulating counters)."""

    def add_stats(
        self,
        sandbox_id: str,
        deltas_by_archetype: Dict[str, Dict[str, int]],
        *,
        now: Optional[str] = None,
    ) -> None:
        """Add a batch of per-archetype counter deltas (one upsert per archetype).

        ``deltas_by_archetype`` maps ``archetype -> {column: delta}``. Missing
        columns default to 0. First write for a (sandbox, archetype) inserts the
        deltas; later writes add to the running totals. One transaction.
        """
        cols = COUNTER_COLUMNS
        col_list = ', '.join(cols)
        placeholders = ', '.join('?' for _ in cols)
        # ON CONFLICT: col = col + excluded.col for every counter.
        updates = ', '.join(f'{c} = {c} + excluded.{c}' for c in cols)
        sql = (
            f"INSERT INTO archetype_stat_counts "
            f"(sandbox_id, archetype, {col_list}, updated_at) "
            f"VALUES (?, ?, {placeholders}, ?) "
            f"ON CONFLICT(sandbox_id, archetype) DO UPDATE SET "
            f"{updates}, updated_at = excluded.updated_at"
        )
        with self._get_connection() as conn:
            for archetype, deltas in deltas_by_archetype.items():
                if not deltas:
                    continue
                values: list = [sandbox_id, archetype]
                values.extend(int(deltas.get(c, 0)) for c in cols)
                values.append(now)
                conn.execute(sql, values)

    def get_stats(self, sandbox_id: Optional[str] = None) -> List[dict]:
        """Return per-archetype totals as a list of plain dicts.

        With ``sandbox_id`` → that sandbox only. Without → summed across ALL
        sandboxes (the global AI-only behavioral picture). Counter columns are
        named exactly as in COUNTER_COLUMNS.
        """
        col_sums = ', '.join(f'SUM({c}) AS {c}' for c in COUNTER_COLUMNS)
        with self._get_connection() as conn:
            if sandbox_id is not None:
                rows = conn.execute(
                    f"SELECT archetype, {col_sums} FROM archetype_stat_counts "
                    "WHERE sandbox_id = ? GROUP BY archetype",
                    (sandbox_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT archetype, {col_sums} FROM archetype_stat_counts " "GROUP BY archetype"
                ).fetchall()
        out = []
        for r in rows:
            d = {'archetype': r['archetype']}
            for c in COUNTER_COLUMNS:
                d[c] = int(r[c] or 0)
            out.append(d)
        return out
