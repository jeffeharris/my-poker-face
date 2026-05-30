"""Persistence for multi-table tournament (MTT) meta-state.

One row per tournament in the `tournaments` table (schema v123): the serialized
`TournamentSession` (the source of truth for field/seating/standings), the
human's live `game_id` (NULL until they sit), `status` ('active'|'complete'),
and `resolver_kind` ('fake'|'engine', rebuilt on rehydrate — resolvers aren't
serialized).

This is the durable backing for the in-memory `tournament_registry`: the
registry stays the hot path, the repo makes a tournament survive navigation /
TTL eviction / server restart. The live per-table hand state lives in the
`games` row (saved by the game repo); these two are persisted together at the
hand boundary so a crash between them can't desync stacks. See
`docs/plans/TOURNAMENT_PERSISTENCE_HANDOFF.md`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from .base_repository import BaseRepository


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


class TournamentSessionRepository(BaseRepository):
    """CRUD for the `tournaments` table. BaseRepository's `_get_connection`
    auto-commits on success and rolls back on error."""

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            'tournament_id': row['tournament_id'],
            'owner_id': row['owner_id'],
            'game_id': row['game_id'],
            'status': row['status'],
            'resolver_kind': row['resolver_kind'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
            'session_json': row['session_json'],
        }

    def save(
        self,
        *,
        tournament_id: str,
        owner_id: str,
        status: str,
        resolver_kind: str,
        session_json: str,
        created_at: str,
        game_id: Optional[str] = None,
    ) -> None:
        """Insert or update a tournament row. `created_at` is set on first
        insert and preserved on update; `updated_at` is stamped every save."""
        now = _utcnow_iso()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tournaments
                    (tournament_id, owner_id, game_id, status, resolver_kind,
                     created_at, updated_at, session_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tournament_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    game_id=excluded.game_id,
                    status=excluded.status,
                    resolver_kind=excluded.resolver_kind,
                    updated_at=excluded.updated_at,
                    session_json=excluded.session_json
                """,
                (
                    tournament_id,
                    owner_id,
                    game_id,
                    status,
                    resolver_kind,
                    created_at,
                    now,
                    session_json,
                ),
            )

    def load(self, tournament_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_active_for_owner(self, owner_id: str) -> Optional[dict]:
        """The owner's most-recently-updated active *multi-table* tournament, if
        any. Excludes `resolver_kind='single'` envelope rows — those wrap an
        ordinary single-table game (still tracker-driven) and must never be
        rehydrated as an MTT session or shadow a real MTT in the lobby."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM tournaments
                WHERE owner_id = ? AND status = 'active'
                  AND resolver_kind != 'single'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (owner_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def find_by_game_id(self, game_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tournaments WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def set_status(self, tournament_id: str, status: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tournaments SET status = ?, updated_at = ? WHERE tournament_id = ?",
                (status, _utcnow_iso(), tournament_id),
            )

    def set_game_id(self, tournament_id: str, game_id: Optional[str]) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tournaments SET game_id = ?, updated_at = ? WHERE tournament_id = ?",
                (game_id, _utcnow_iso(), tournament_id),
            )

    def delete(self, tournament_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            )
