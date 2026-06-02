"""Persistence for circuit Main Event invites (schema v135).

One row per offer. Status lifecycle: `offered` → `accepted` | `declined` |
`expired`. Durable so a scheduled window ("open until 8pm") survives navigation
/ TTL eviction / restart. The in-flight tournament it produces lives in the
`tournaments` table (`tournament_id` links them once accepted/declined/expired).

See `docs/plans/TOURNAMENT_CIRCUIT_SURFACING.md`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from .base_repository import BaseRepository

STATUS_OFFERED = 'offered'
STATUS_ACCEPTED = 'accepted'
STATUS_DECLINED = 'declined'
STATUS_EXPIRED = 'expired'


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


class TournamentInviteRepository(BaseRepository):
    """CRUD for `tournament_invites`."""

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            'invite_id': row['invite_id'],
            'owner_id': row['owner_id'],
            'sandbox_id': row['sandbox_id'],
            'status': row['status'],
            'buy_in': row['buy_in'],
            'field_size': row['field_size'],
            'table_size': row['table_size'],
            'starting_stack': row['starting_stack'],
            'seed': row['seed'],
            'expires_at': row['expires_at'],
            'tournament_id': row['tournament_id'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    def create(
        self,
        *,
        invite_id: str,
        owner_id: str,
        sandbox_id: str,
        buy_in: int,
        field_size: int,
        table_size: int,
        starting_stack: int,
        seed: int = 0,
        expires_at: Optional[str] = None,
    ) -> None:
        now = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tournament_invites
                    (invite_id, owner_id, sandbox_id, status, buy_in, field_size,
                     table_size, starting_stack, seed, expires_at, tournament_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    invite_id, owner_id, sandbox_id, STATUS_OFFERED, int(buy_in),
                    int(field_size), int(table_size), int(starting_stack), int(seed),
                    expires_at, now, now,
                ),
            )

    def load(self, invite_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tournament_invites WHERE invite_id = ?",
                (invite_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def active_for_owner(self, owner_id: str) -> Optional[dict]:
        """The owner's currently-open ('offered') invite, if any (newest)."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM tournament_invites
                WHERE owner_id = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (owner_id, STATUS_OFFERED),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def list_open_due(self, *, now_iso: str, sandbox_id: Optional[str] = None) -> list[dict]:
        """All 'offered' invites whose `expires_at` is at/past `now_iso`
        (expiry sweep). NULL `expires_at` never expires.

        When `sandbox_id` is given, only that sandbox's invites are returned —
        the expiry sweep spawns an autonomous tournament in each invite's OWN
        sandbox, and the caller holds only its sandbox's lock, so an unscoped
        sweep would mutate another sandbox's escrow without its lock. `None`
        keeps the global sweep (admin / reconcile)."""
        sql = """
            SELECT * FROM tournament_invites
            WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
        """
        params: list = [STATUS_OFFERED, now_iso]
        if sandbox_id is not None:
            sql += " AND sandbox_id = ?"
            params.append(sandbox_id)
        with self._get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def last_created_at(self, owner_id: str) -> Optional[str]:
        """The `created_at` of the owner's most recent invite of ANY status — the
        cooldown anchor for the offer policy. None if they've never had one."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT created_at FROM tournament_invites WHERE owner_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (owner_id,),
            ).fetchone()
            return row['created_at'] if row else None

    def claim(self, invite_id: str, *, to_status: str, owner_id: Optional[str] = None) -> bool:
        """Atomically claim an OFFERED invite (cross-worker compare-and-swap).

        `UPDATE ... SET status = to_status WHERE invite_id = ? AND status =
        'offered'` — returns True iff THIS call won the transition (rowcount 1),
        False if a concurrent worker (or process — the in-memory sandbox lock
        does not span gunicorn workers) already resolved it. The single point of
        mutual exclusion for accept/decline/expire that actually holds across
        workers; the caller must gate the irreversible work (build + buy-in /
        autonomous spawn) on a True return. `owner_id`, when given, is an extra
        guard so an accept can only claim its own owner's invite."""
        sql = "UPDATE tournament_invites SET status = ?, updated_at = ? WHERE invite_id = ? AND status = ?"
        params: list = [to_status, _utcnow_iso(), invite_id, STATUS_OFFERED]
        if owner_id is not None:
            sql += " AND owner_id = ?"
            params.append(owner_id)
        with self._get_connection() as conn:
            return conn.execute(sql, tuple(params)).rowcount == 1

    def revert_to_offered(self, invite_id: str) -> bool:
        """Undo a claim — re-open an invite the winner claimed but then failed to
        consume (e.g. accept hit `InsufficientFundsError` before any chips moved,
        or couldn't field a tournament). Guarded on `status='accepted' AND
        tournament_id IS NULL` so it can only revert a still-unlinked claim, never
        clobber a fully-accepted (linked) or terminally-resolved invite. Returns
        True iff it re-opened. Safe under concurrency: only the claim winner ever
        reaches a revert, and both accept revert triggers fire before chips move."""
        with self._get_connection() as conn:
            return conn.execute(
                "UPDATE tournament_invites SET status = ?, updated_at = ? "
                "WHERE invite_id = ? AND status = ? AND tournament_id IS NULL",
                (STATUS_OFFERED, _utcnow_iso(), invite_id, STATUS_ACCEPTED),
            ).rowcount == 1

    def resolve(
        self,
        invite_id: str,
        *,
        status: str,
        tournament_id: Optional[str] = None,
    ) -> None:
        """Terminal-transition the invite (accepted | declined | expired) and
        link the tournament it produced."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tournament_invites
                   SET status = ?, tournament_id = ?, updated_at = ?
                 WHERE invite_id = ?
                """,
                (status, tournament_id, _utcnow_iso(), invite_id),
            )

    def delete(self, invite_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM tournament_invites WHERE invite_id = ?",
                (invite_id,),
            )
