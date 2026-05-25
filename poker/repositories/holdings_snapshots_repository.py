"""Repository for the v116 `holdings_snapshots` surface.

Per-entity net-worth points captured by the realtime background ticker
(~10 min per active sandbox). Powers the admin Chip Economy "Player
Holdings" chart: real net worth over time, which the chip ledger can't
reconstruct because seat-to-seat chip flows never hit it.

`net_worth = chips + receivable - outstanding`; the components are stored
alongside so the curve stays explainable. Rows are append-only history;
`prune` enforces retention.

Schema is created by `SchemaManager.ensure_schema()` (v116 migration);
this class only touches data. See
`docs/plans/CASH_MODE_NET_WORTH_HOLDINGS.md`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class HoldingsSnapshotsRepository(BaseRepository):
    """CRUD for `holdings_snapshots`."""

    def record(self, rows: List[Dict[str, Any]], *, captured_at: str) -> int:
        """Insert one capture's worth of per-entity net-worth points.

        `rows` is a list of dicts with `entity_id`, `kind`, `net_worth`,
        `chips`, `receivable`, `outstanding` (and a `sandbox_id`). All
        share the single `captured_at` ISO-8601 timestamp so they form one
        coherent column on the chart. Returns the number of rows written.
        Empty input is a no-op (returns 0) — a sandbox with no entities in
        scope shouldn't write a phantom column.
        """
        if not rows:
            return 0
        params = [
            (
                captured_at,
                row['sandbox_id'],
                row['entity_id'],
                row['kind'],
                int(row['net_worth']),
                int(row['chips']),
                int(row.get('receivable', 0)),
                int(row.get('outstanding', 0)),
            )
            for row in rows
        ]
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO holdings_snapshots (
                    captured_at, sandbox_id, entity_id, kind,
                    net_worth, chips, receivable, outstanding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
        return len(params)

    def series_since(
        self,
        since_iso: str,
        *,
        sandbox_id: str,
    ) -> List[Dict[str, Any]]:
        """Return all snapshot points for a sandbox since `since_iso`.

        Rows come back ordered by `entity_id`, then `captured_at` ASC, so
        the caller can group into per-entity series in a single forward
        walk. `captured_at` is compared lexically; the recorder writes
        explicit ISO-8601 UTC so the comparison is format-consistent (no
        space-vs-`T` mismatch). The caller is responsible for
        downsampling / truncation for display.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT entity_id, kind, captured_at,
                       net_worth, chips, receivable, outstanding
                FROM holdings_snapshots
                WHERE sandbox_id = ? AND captured_at >= ?
                ORDER BY entity_id ASC, captured_at ASC
                """,
                (sandbox_id, since_iso),
            ).fetchall()
        return [
            {
                'entity_id': r['entity_id'],
                'kind': r['kind'],
                'captured_at': r['captured_at'],
                'net_worth': int(r['net_worth']),
                'chips': int(r['chips']),
                'receivable': int(r['receivable']),
                'outstanding': int(r['outstanding']),
            }
            for r in rows
        ]

    def latest_captured_at(self, sandbox_id: str) -> Optional[str]:
        """Return the most recent `captured_at` for a sandbox, or None.

        Used by the recorder's rate-limit / first-view seed check — if no
        snapshot exists yet the history endpoint can seed one so the chart
        isn't blank before the first tick fires.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT MAX(captured_at) AS latest
                FROM holdings_snapshots
                WHERE sandbox_id = ?
                """,
                (sandbox_id,),
            ).fetchone()
        return row['latest'] if row and row['latest'] else None

    def prune(self, older_than_iso: str) -> int:
        """Delete snapshots older than `older_than_iso`. Returns row count.

        Enforces the 30-day retention so the table can't grow unbounded
        over a long uptime. Lexical comparison on the ISO `captured_at`.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM holdings_snapshots WHERE captured_at < ?",
                (older_than_iso,),
            )
            return cursor.rowcount
