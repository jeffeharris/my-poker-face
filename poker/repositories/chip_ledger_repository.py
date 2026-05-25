"""Repository for the v93 `chip_ledger_entries` observability surface.

Append-only ledger. Each row is one chip creation or destruction event
— a transfer where `central_bank` is on one side. v0 ships with no
enforcement: writes here describe what happened, they don't gate
anything. The audit endpoint (commit 4) reads back the aggregates.

Spec: `docs/plans/CASH_MODE_CHIP_LEDGER_HANDOFF.md`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)

CENTRAL_BANK = 'central_bank'


class ChipLedgerRepository(BaseRepository):
    """CRUD for `chip_ledger_entries`.

    Insert-and-read only; no updates, no deletes (the ledger is
    append-only by design — drift between the ledger and reality is
    the signal we're trying to surface, not something to paper over).
    """

    def record(
        self,
        source: str,
        sink: str,
        amount: int,
        reason: str,
        context: Optional[Dict[str, Any]] = None,
        *,
        sandbox_id: Optional[str] = None,
    ) -> int:
        """Append one ledger entry. Returns the row id.

        Annotation rows (`amount == 0`) are valid — they exist so the
        audit endpoint can reconcile forgive-balance events without
        actually moving chips. The CHECK constraint enforces
        `amount >= 0`; negative amounts indicate a bug at the call
        site (use source/sink direction instead).

        `sandbox_id` is a Phase 2.5 v103 addition — when provided, the
        write stamps the dedicated column so per-sandbox audits can
        filter. Pre-v103 callers that omit it write NULL (the legacy
        bucket the audit aggregates under `_pre_v103`). Production
        callers should always pass it; legacy migration helpers
        (`_migrate_v94_seed_pre_ledger_universe`) leave it NULL on
        purpose.
        """
        context_blob = json.dumps(context) if context is not None else None
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chip_ledger_entries
                    (source, sink, amount, reason, context_json, sandbox_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, sink, int(amount), reason, context_blob, sandbox_id),
            )
            return cursor.lastrowid

    def sum_creations_by_reason(
        self,
        since_iso: Optional[str] = None,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Sum chip-creation amounts grouped by reason.

        A creation is any row with `source = 'central_bank'`. Pass
        `since_iso` (ISO 8601 UTC string) to restrict to a window
        (e.g. last 24h); omit to sum the full history.

        `sandbox_id=None` (default) is the cross-sandbox / admin view
        — includes every row regardless of `sandbox_id`, including the
        pre-v103 NULL bucket. Passing an explicit sandbox_id scopes
        to that one save-file (and naturally excludes the NULL bucket).
        """
        return self._sum_by_reason('source', CENTRAL_BANK, since_iso, sandbox_id)

    def sum_destructions_by_reason(
        self,
        since_iso: Optional[str] = None,
        *,
        sandbox_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Sum chip-destruction amounts grouped by reason.

        A destruction is any row with `sink = 'central_bank'`.
        Same sandbox semantics as `sum_creations_by_reason`.
        """
        return self._sum_by_reason('sink', CENTRAL_BANK, since_iso, sandbox_id)

    def _sum_by_reason(
        self,
        side_column: str,
        side_value: str,
        since_iso: Optional[str],
        sandbox_id: Optional[str] = None,
    ) -> Dict[str, int]:
        # side_column is a hard-coded literal in callers ('source' /
        # 'sink'); never a user-supplied string. Safe to interpolate.
        params: List[Any] = [side_value]
        where = f"{side_column} = ?"
        if since_iso is not None:
            where += " AND created_at >= ?"
            params.append(since_iso)
        if sandbox_id is not None:
            where += " AND sandbox_id = ?"
            params.append(sandbox_id)

        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT reason, SUM(amount) AS total
                FROM chip_ledger_entries
                WHERE {where}
                GROUP BY reason
                """,
                params,
            ).fetchall()
        return {row['reason']: int(row['total'] or 0) for row in rows}

    def non_bank_entries_since(
        self,
        since_iso: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return entries since `since_iso` where one side is not the bank, oldest first.

        Drives the admin "Player Holdings over time" graph: each ledger
        row is a signed chip flow into or out of a non-bank entity
        (`player:<id>` or `ai:<id>`), so the caller can compute running
        cumulative balance per entity by walking the result in order.

        Returns plain dicts with `entry_id`, `created_at`, `source`,
        `sink`, `amount`, `reason`. Annotation rows (`amount == 0`)
        are excluded — they don't change the curve.
        """
        params: List[Any] = [CENTRAL_BANK, CENTRAL_BANK, since_iso]
        where = "amount > 0" " AND (source = ? OR sink = ?)" " AND created_at >= ?"
        if sandbox_id is not None:
            where += " AND sandbox_id = ?"
            params.append(sandbox_id)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT entry_id, created_at, source, sink, amount, reason
                FROM chip_ledger_entries
                WHERE {where}
                ORDER BY created_at ASC, entry_id ASC
                """,
                params,
            ).fetchall()
        return [
            {
                'entry_id': row['entry_id'],
                'created_at': row['created_at'],
                'source': row['source'],
                'sink': row['sink'],
                'amount': int(row['amount']),
                'reason': row['reason'],
            }
            for row in rows
        ]

    def recent_entries(
        self,
        limit: int = 100,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the most recent ledger entries, newest first.

        Returns plain dicts (not row objects) so the audit endpoint
        can serialise them straight to JSON. `context_json` is
        parsed back into a dict; malformed JSON is returned as the
        raw string under `context_raw` for forensics.

        `sandbox_id=None` (default) returns rows across every sandbox
        — the admin / cross-sandbox view. Passing an explicit id
        scopes to that one save-file; the pre-v103 NULL-sandbox
        bucket is naturally excluded by the WHERE filter.
        """
        with self._get_connection() as conn:
            if sandbox_id is None:
                rows = conn.execute(
                    """
                    SELECT entry_id, created_at, source, sink, amount, reason, context_json
                    FROM chip_ledger_entries
                    ORDER BY created_at DESC, entry_id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT entry_id, created_at, source, sink, amount, reason, context_json
                    FROM chip_ledger_entries
                    WHERE sandbox_id = ?
                    ORDER BY created_at DESC, entry_id DESC
                    LIMIT ?
                    """,
                    (sandbox_id, int(limit)),
                ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            entry: Dict[str, Any] = {
                'entry_id': row['entry_id'],
                'created_at': row['created_at'],
                'source': row['source'],
                'sink': row['sink'],
                'amount': int(row['amount']),
                'reason': row['reason'],
            }
            raw = row['context_json']
            if raw is None:
                entry['context'] = None
            else:
                try:
                    entry['context'] = json.loads(raw)
                except (TypeError, ValueError):
                    entry['context'] = None
                    entry['context_raw'] = raw
            out.append(entry)
        return out
