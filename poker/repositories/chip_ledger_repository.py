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
        conn=None,
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

        `conn` (chip-custody atomicity): when given, the INSERT runs on
        the CALLER's open connection so the ledger row commits in the
        SAME transaction as the caller's bankroll-int write (no two-commit
        divergence window). The caller owns the commit. None → open + commit
        our own connection (the standalone default; every existing caller).
        """
        context_blob = json.dumps(context) if context is not None else None
        sql = """
            INSERT INTO chip_ledger_entries
                (source, sink, amount, reason, context_json, sandbox_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (source, sink, int(amount), reason, context_blob, sandbox_id)
        if conn is not None:
            # Join the caller's transaction — they commit (or roll back) both
            # the int write and this row together.
            rid = conn.execute(sql, params).lastrowid
        else:
            with self._get_connection() as own:
                rid = own.execute(sql, params).lastrowid
        return int(rid) if rid is not None else 0

    def balance_of(
        self,
        account: str,
        *,
        sandbox_id: Optional[str] = None,
        conn=None,
    ) -> int:
        """Ledger-derived balance for one account: Σ(amount where sink=account)
        − Σ(amount where source=account). This is the D2 substrate — bankroll
        as the sum of its ledger parcels rather than a bare mutable int.

        Scope (resolves the storage asymmetry — see CASH_MODE_CHIP_CUSTODY):
          * `sandbox_id` given  → sum only rows stamped that sandbox. Use for AI
            (`ai_bankroll_state` is per-(pid, sandbox)): the chips an AI holds in
            ONE save-file.
          * `sandbox_id=None`   → sum across ALL sandboxes. Use for humans
            (`player_bankroll_state` is GLOBAL, no sandbox_id): a player's
            bankroll is shared across their sandboxes by design (D6 — one human
            per sandbox, but the bankroll roams with them).

        `conn` (the seat-settle seam): when given, the aggregate runs on the
        CALLER's open connection so it sees rows written-but-not-yet-committed in
        the SAME transaction (e.g. the per-hand `hand_pnl` rows and the settle's
        own read inside `save_table`'s txn). Opening a fresh connection there
        would both miss those rows AND risk a SQLite writer-lock. None → open our
        own connection (the standalone default; every read-path caller).

        Single aggregate query so it's cheap enough to call on a read path or in
        a consistency check. Returns 0 for an unknown account.
        """
        # Param order matches the SQL: two CASE clauses (sink, source) then the
        # WHERE (source, sink) and an optional sandbox filter.
        params: List[Any] = [account, account, account, account]
        where = "(source = ? OR sink = ?)"
        if sandbox_id is not None:
            where += " AND sandbox_id = ?"
            params.append(sandbox_id)
        sql = f"""
            SELECT
              COALESCE(SUM(CASE WHEN sink = ? THEN amount ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN source = ? THEN amount ELSE 0 END), 0)
              AS bal
            FROM chip_ledger_entries
            WHERE {where}
            """
        if conn is not None:
            row = conn.execute(sql, params).fetchone()
        else:
            with self._get_connection() as own:
                row = own.execute(sql, params).fetchone()
        return int(row["bal"] or 0)

    def entries_for_stake(
        self,
        stake_id: str,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Every ledger row tagged with `context.stake_id == stake_id`.

        The substrate for per-contract conservation checks (the stake state
        machine's settle-time guard): a single stake's funding + settlement
        rows, so the caller can verify the funding reached the borrower's seat
        and that the contract's signed amounts net out.

        Matching is a `context_json LIKE` on the canonical JSON spelling
        (`"stake_id": "<id>"`) — every stake chip-flow writer stamps the id
        that way via `record_*(context={'stake_id': ...})`. Rows whose
        `context_json` is malformed JSON are skipped (they can't be a tagged
        stake row). `sandbox_id=None` spans every sandbox; an explicit id
        scopes to one save file. Returns rows oldest-first (funding before
        settlement) so a running signed-amount total reads naturally.
        """
        # The space after the colon matches json.dumps' default separators,
        # which every `record_transfer` context goes through. Escape any LIKE
        # wildcards in the id defensively (stake ids are hex, but don't trust).
        needle = stake_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f'%"stake_id": "{needle}"%'
        where = "context_json LIKE ? ESCAPE '\\'"
        params: List[Any] = [like]
        if sandbox_id is not None:
            where += " AND sandbox_id = ?"
            params.append(sandbox_id)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT entry_id, created_at, source, sink, amount, reason, context_json
                FROM chip_ledger_entries
                WHERE {where}
                ORDER BY created_at ASC, entry_id ASC
                """,
                params,
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                context = json.loads(row['context_json']) if row['context_json'] else {}
            except (TypeError, ValueError):
                continue
            # LIKE can theoretically match a substring in an unrelated field;
            # confirm the parsed context actually carries this exact stake_id.
            if context.get('stake_id') != stake_id:
                continue
            out.append(
                {
                    'entry_id': row['entry_id'],
                    'created_at': row['created_at'],
                    'source': row['source'],
                    'sink': row['sink'],
                    'amount': int(row['amount']),
                    'reason': row['reason'],
                    'context': context,
                }
            )
        return out

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

    def payouts_by_sink(
        self,
        source: str,
        *,
        reason: str,
        sandbox_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Σ(amount) grouped by sink for rows where `source = <source>` and
        `reason = <reason>`. The authoritative "what has this escrow already paid
        each recipient" view that drives payout reconciliation: a tournament's
        `tournament_payout` rows (source = `tournament(id)`) grouped by sink give
        the per-finisher amounts already distributed, so a stuck (`in_progress`)
        payout can be resumed by paying only the unpaid remainder per sink without
        double-crediting anyone. Returns {sink_account: total}; empty if none."""
        params: List[Any] = [source, reason]
        where = "source = ? AND reason = ?"
        if sandbox_id is not None:
            where += " AND sandbox_id = ?"
            params.append(sandbox_id)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT sink, SUM(amount) AS total
                FROM chip_ledger_entries
                WHERE {where}
                GROUP BY sink
                """,
                params,
            ).fetchall()
        return {row['sink']: int(row['total'] or 0) for row in rows}

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

    def entries_for_account(
        self,
        account: str,
        *,
        sandbox_id: Optional[str] = None,
        limit: int = 200,
        newest_first: bool = True,
    ) -> List[Dict[str, Any]]:
        """Itemized ledger entries where `account` is the source OR sink — one
        account's statement (the player-facing "My Ledger" view).

        Each row carries `signed_amount`: +amount when the account RECEIVES (it's
        the `sink`) and -amount when it PAYS (it's the `source`). The running total
        of `signed_amount` over the FULL history equals `balance_of(account)`.

        `sandbox_id=None` (default) spans every sandbox — the human's
        `player:<owner_id>` account is global (no sandbox_id on
        `player_bankroll_state`), so a complete statement must not scope by save
        file. `context_json` is parsed back to a dict (malformed → `context_raw`),
        mirroring `recent_entries`."""
        order = "DESC" if newest_first else "ASC"
        where = "(source = ? OR sink = ?)"
        params: List[Any] = [account, account]
        if sandbox_id is not None:
            where += " AND sandbox_id = ?"
            params.append(sandbox_id)
        params.append(int(limit))
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT entry_id, created_at, source, sink, amount, reason, context_json
                FROM chip_ledger_entries
                WHERE {where}
                ORDER BY created_at {order}, entry_id {order}
                LIMIT ?
                """,
                params,
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            amount = int(row['amount'])
            entry: Dict[str, Any] = {
                'entry_id': row['entry_id'],
                'created_at': row['created_at'],
                'source': row['source'],
                'sink': row['sink'],
                'amount': amount,
                'signed_amount': amount if row['sink'] == account else -amount,
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
