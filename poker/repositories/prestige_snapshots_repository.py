"""Repository for the v121 `prestige_snapshots` surface.

Per-(sandbox, owner) human-reputation points captured by the realtime
background ticker. Powers the cash lobby's reputation scoreboard and a
renown trajectory over time.

Two axes (see `cash_mode/prestige.py` for the formula):
  - `renown` ratchets — the recorder always stores the running peak, so a
    downswing can't erase the career record.
  - `regard` swings with behaviour and partially decays with heat.

Component columns (`renown_*`, `regard_*`) store the formula's
contributions so the panel and debugging can show WHY without recomputing.
Rows are append-only history; `prune` enforces retention.

Schema is created by `SchemaManager.ensure_schema()` (v121 migration);
this class only touches data. See
`docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 60  # reputation moves slowly; keep a longer tail than holdings


class PrestigeSnapshotsRepository(BaseRepository):
    """CRUD for `prestige_snapshots`."""

    def record(
        self,
        *,
        captured_at: str,
        sandbox_id: str,
        owner_id: str,
        score: Any,  # cash_mode.prestige.ReputationScore (duck-typed to avoid an import cycle)
    ) -> None:
        """Insert one prestige capture for (sandbox, owner).

        `score` is a `ReputationScore`; its `renown` is expected to already
        be the ratcheted value (the recorder reads `load_renown_peak` and
        passes the peak into `compute_prestige`, which takes the max). This
        method just persists — it does not enforce the ratchet itself.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO prestige_snapshots (
                    captured_at, sandbox_id, owner_id,
                    renown, regard, quadrant,
                    renown_breadth, renown_tenure, renown_stake_tier,
                    renown_beat_respected, renown_high_stakes,
                    regard_likability, regard_respect, regard_heat,
                    opponent_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at,
                    sandbox_id,
                    owner_id,
                    float(score.renown),
                    float(score.regard),
                    score.quadrant,
                    float(score.renown_breadth),
                    float(score.renown_tenure),
                    float(score.renown_stake_tier),
                    float(score.renown_beat_respected),
                    float(score.renown_high_stakes),
                    float(score.regard_likability),
                    float(score.regard_respect),
                    float(score.regard_heat),
                    int(score.opponent_count),
                ),
            )

    def load_latest(
        self,
        sandbox_id: str,
        owner_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent capture for (sandbox, owner) as a dict, or None.

        The lobby route reads this for the current scoreboard. Returns every
        column so the route can expose the component breakdown without a
        second query. None when no capture exists yet (a brand-new sandbox
        before the first ticker fire) — the lobby renders no panel.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ?
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (sandbox_id, owner_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def load_renown_peak(
        self,
        sandbox_id: str,
        owner_id: str,
    ) -> float:
        """Return the historical max renown for (sandbox, owner), or 0.0.

        The recorder reads this and passes it to `compute_prestige` so
        renown ratchets — a recompute can never drop it below the peak.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(renown) AS peak
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ?
                """,
                (sandbox_id, owner_id),
            ).fetchone()
        return float(row["peak"]) if row and row["peak"] is not None else 0.0

    def series_since(
        self,
        since_iso: str,
        *,
        sandbox_id: str,
        owner_id: str,
    ) -> List[Dict[str, Any]]:
        """Return (captured_at, renown, regard) points since `since_iso`, oldest → newest.

        For a future renown/regard trajectory sparkline. Hits
        `idx_prestige_snap_scope`. Lexical `captured_at >= since_iso`
        comparison (the recorder writes explicit ISO-8601 UTC).
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT captured_at, renown, regard, quadrant
                FROM prestige_snapshots
                WHERE sandbox_id = ? AND owner_id = ? AND captured_at >= ?
                ORDER BY captured_at ASC
                """,
                (sandbox_id, owner_id, since_iso),
            ).fetchall()
        return [
            {
                "captured_at": r["captured_at"],
                "renown": float(r["renown"]),
                "regard": float(r["regard"]),
                "quadrant": r["quadrant"],
            }
            for r in rows
        ]

    def prune(self, older_than_iso: str) -> int:
        """Delete captures older than `older_than_iso`. Returns row count.

        Enforces retention so the table can't grow unbounded over long
        uptime. Lexical comparison on the ISO `captured_at`.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM prestige_snapshots WHERE captured_at < ?",
                (older_than_iso,),
            )
            return cursor.rowcount
