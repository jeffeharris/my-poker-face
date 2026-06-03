"""Repository for the v132 `cash_scalps` surface — attributed bust counts.

A sandbox-scoped cumulative counter of eliminations, keyed per
(eliminator, victim) pair so renown-weighting can read the *victim's* standing
(busting a legend ≫ a nobody) rather than just a flat per-eliminator count.

Ids are raw (no `player:`/`ai:` prefix), mirroring `cash_pair_stats`:
`owner_id` for the human, `personality_id` for AIs. AI-symmetric and
forward-only. Attribution itself lives in `cash_mode/scalps.py` (the pure
headline-winner rule); this class only persists.

Schema is created by `SchemaManager.ensure_schema()` (v132 migration); this
class only touches data. See docs/plans/CASH_MODE_SCALP_TRACKER.md.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Tuple

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class CashScalpsRepository(BaseRepository):
    """CRUD for `cash_scalps`."""

    def record(
        self,
        sandbox_id: str,
        eliminator_id: str,
        victim_id: str,
        *,
        now: Optional[str] = None,
    ) -> None:
        """Increment the scalp count for one (eliminator, victim) by 1.

        Upsert: first scalp inserts `count=1`; subsequent ones bump it.
        `now` is an ISO-8601 string (the caller's tick time); stored as
        `last_at`. Best-effort by convention — callers wrap this so a write
        failure never breaks hand resolution.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO cash_scalps (sandbox_id, eliminator_id, victim_id, count, last_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(sandbox_id, eliminator_id, victim_id)
                DO UPDATE SET count = count + 1, last_at = excluded.last_at
                """,
                (sandbox_id, eliminator_id, victim_id, now),
            )

    def record_many(
        self,
        sandbox_id: str,
        scalps: Iterable[Tuple[str, str]],
        *,
        now: Optional[str] = None,
    ) -> int:
        """Record a batch of (eliminator, victim) pairs (as produced by
        `cash_mode.scalps`). Returns the number recorded. One transaction."""
        n = 0
        with self._get_connection() as conn:
            for eliminator_id, victim_id in scalps:
                conn.execute(
                    """
                    INSERT INTO cash_scalps (sandbox_id, eliminator_id, victim_id, count, last_at)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(sandbox_id, eliminator_id, victim_id)
                    DO UPDATE SET count = count + 1, last_at = excluded.last_at
                    """,
                    (sandbox_id, eliminator_id, victim_id, now),
                )
                n += 1
        return n

    def total_for(self, sandbox_id: str, eliminator_id: str) -> int:
        """Total scalps (across all victims) for one eliminator. 0 if none."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) AS total FROM cash_scalps "
                "WHERE sandbox_id = ? AND eliminator_id = ?",
                (sandbox_id, eliminator_id),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def list_for_eliminator(self, sandbox_id: str, eliminator_id: str) -> List[Tuple[str, int]]:
        """Per-victim breakdown for one eliminator: [(victim_id, count), …]
        descending by count. This is what renown-weighting consumes (it joins
        each victim_id to that entity's renown)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT victim_id, count FROM cash_scalps "
                "WHERE sandbox_id = ? AND eliminator_id = ? ORDER BY count DESC",
                (sandbox_id, eliminator_id),
            ).fetchall()
        return [(r["victim_id"], int(r["count"])) for r in rows]

    def victims_of(self, sandbox_id: str, victim_id: str) -> List[Tuple[str, int]]:
        """Who has busted this entity, and how often: [(eliminator_id, count), …]
        — the "who's hunting me" view (the villain's rival cohort)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT eliminator_id, count FROM cash_scalps "
                "WHERE sandbox_id = ? AND victim_id = ? ORDER BY count DESC",
                (sandbox_id, victim_id),
            ).fetchall()
        return [(r["eliminator_id"], int(r["count"])) for r in rows]
