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

    def list_open_due(self, *, now_iso: str) -> list[dict]:
        """All 'offered' invites whose `expires_at` is at/past `now_iso`
        (expiry sweep). NULL `expires_at` never expires."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tournament_invites
                WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (STATUS_OFFERED, now_iso),
            ).fetchall()
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
