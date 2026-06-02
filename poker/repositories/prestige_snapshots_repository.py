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

import json
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
        formula_version: str = "v1",
        renown_v2: Optional[float] = None,
        victim_percentile: Optional[float] = None,
        high_cut: Optional[float] = None,
        renown_v2_components: Optional[Dict[str, float]] = None,
        field_size: Optional[int] = None,
    ) -> None:
        """Insert one prestige capture for (sandbox, owner).

        `score` is a `ReputationScore`; its `renown` is expected to already
        be the ratcheted value (the recorder reads `load_renown_peak` and
        passes the peak into `compute_prestige`, which takes the max). This
        method just persists — it does not enforce the ratchet itself.

        The v2 fields (``formula_version`` … ``field_size``) are the v133
        ADDITIVE columns. They default to a v1 row (``formula_version='v1'``,
        the rest NULL) so every existing caller is unchanged. When the ticker
        computes the field-relative v2 layer (behind ``RENOWN_V2_ENABLED``) it
        passes ``formula_version='v2'`` plus the uncapped ``renown_v2``, the
        field ``high_cut``, the human's ``victim_percentile``, the v2 component
        breakdown (JSON-serialised here), and the ``field_size``. The CONSUMED
        ``score.quadrant`` is whichever formula's quadrant the caller chose —
        ``formula_version`` only records which one, for the panel's gauge.
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
                    opponent_count,
                    formula_version, renown_v2, victim_percentile,
                    high_cut, renown_v2_components, field_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    formula_version,
                    None if renown_v2 is None else float(renown_v2),
                    None if victim_percentile is None else float(victim_percentile),
                    None if high_cut is None else float(high_cut),
                    None if renown_v2_components is None
                    else json.dumps(renown_v2_components, sort_keys=True),
                    None if field_size is None else int(field_size),
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

    def load_renown_v2_peak(
        self,
        sandbox_id: str,
        owner_id: str,
    ) -> float:
        """Return the historical max **v2** renown for (sandbox, owner), or 0.0.

        The v2 layer is uncapped and on a different scale than v1, so it keeps
        its OWN ratchet — the recorder reads this and passes it into the v2
        compute, which takes the max, so a downswing (or a field that inflated
        around the human) can't erase the v2 career record. Rows written before
        v133 (or any v1-only row) have NULL `renown_v2` and are ignored by the
        MAX. Independent of `load_renown_peak` (the v1 ratchet); the two never
        mix scales.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(renown_v2) AS peak
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
