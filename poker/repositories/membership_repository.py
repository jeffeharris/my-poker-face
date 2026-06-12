"""Game membership repository — multi-human seat ledger and invites.

Backs async-friends mode: who belongs to a shared game, which seat each human
owns, and the share codes that let a friend claim an open seat. Authorization
(`is_member`) and the "my async games" lobby read from here; the authoritative
seat->user identity still lives inside the game state (`Player.seat_id`), so
this table is the human-readable index + invite ledger, not the source of truth
for the engine.

See migration ``20260612_1200_async_friends`` for the schema.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from poker.repositories.base_repository import BaseRepository, retry_on_lock

logger = logging.getLogger(__name__)


@dataclass
class GameMember:
    """One human's membership in a game."""

    game_id: str
    user_id: str
    seat_index: Optional[int]
    role: str
    status: str
    display_name: Optional[str] = None


class MembershipRepository(BaseRepository):
    """CRUD for ``game_members`` and ``game_invites``."""

    # --- Members ---

    @retry_on_lock()
    def add_member(
        self,
        game_id: str,
        user_id: str,
        *,
        seat_index: Optional[int] = None,
        role: str = "member",
        status: str = "joined",
        display_name: Optional[str] = None,
    ) -> None:
        """Insert or update a membership row (idempotent on (game_id, user_id))."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO game_members
                    (game_id, user_id, seat_index, role, status, display_name, joined_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(game_id, user_id) DO UPDATE SET
                    seat_index = excluded.seat_index,
                    role = excluded.role,
                    status = excluded.status,
                    display_name = COALESCE(excluded.display_name, game_members.display_name)
                """,
                (game_id, user_id, seat_index, role, status, display_name),
            )

    @retry_on_lock()
    def claim_seat(
        self, game_id: str, user_id: str, seat_index: int, display_name: Optional[str] = None
    ) -> None:
        """Mark a member joined at a specific seat (used when a friend takes a seat)."""
        self.add_member(
            game_id,
            user_id,
            seat_index=seat_index,
            role="member",
            status="joined",
            display_name=display_name,
        )

    def get_member(self, game_id: str, user_id: str) -> Optional[GameMember]:
        """Return the membership row for a user in a game, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM game_members WHERE game_id = ? AND user_id = ?",
                (game_id, user_id),
            ).fetchone()
            return self._row_to_member(row) if row else None

    def is_member(self, game_id: str, user_id: str) -> bool:
        """True if the user has a non-left membership row in the game."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM game_members
                WHERE game_id = ? AND user_id = ? AND status != 'left'
                LIMIT 1
                """,
                (game_id, user_id),
            ).fetchone()
            return row is not None

    def list_members(self, game_id: str) -> List[GameMember]:
        """All membership rows for a game, ordered by seat then join time."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM game_members WHERE game_id = ?
                ORDER BY seat_index IS NULL, seat_index, joined_at
                """,
                (game_id,),
            ).fetchall()
            return [self._row_to_member(r) for r in rows]

    def list_user_games(self, user_id: str) -> List[str]:
        """Game ids the user belongs to (most recently active first)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT gm.game_id
                FROM game_members gm
                JOIN games g ON g.game_id = gm.game_id
                WHERE gm.user_id = ? AND gm.status != 'left'
                ORDER BY g.updated_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [r[0] for r in rows]

    def seat_taken(self, game_id: str, seat_index: int) -> bool:
        """True if a non-left member already occupies the seat."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM game_members
                WHERE game_id = ? AND seat_index = ? AND status != 'left'
                LIMIT 1
                """,
                (game_id, seat_index),
            ).fetchone()
            return row is not None

    # --- Invites ---

    @retry_on_lock()
    def create_invite(
        self,
        game_id: str,
        *,
        created_by: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        max_uses: int = 0,
    ) -> str:
        """Create a share code for the game and return it."""
        code = secrets.token_urlsafe(9)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO game_invites (code, game_id, created_by, expires_at, max_uses)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, game_id, created_by, expires_at, max_uses),
            )
        return code

    def get_invite(self, code: str) -> Optional[dict]:
        """Look up an invite by code, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM game_invites WHERE code = ?", (code,)
            ).fetchone()
            return dict(row) if row else None

    @retry_on_lock()
    def consume_invite(self, code: str) -> None:
        """Increment an invite's use count (call inside the seat-claim flow)."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE game_invites SET used_count = used_count + 1 WHERE code = ?",
                (code,),
            )

    @staticmethod
    def _row_to_member(row) -> GameMember:
        return GameMember(
            game_id=row["game_id"],
            user_id=row["user_id"],
            seat_index=row["seat_index"],
            role=row["role"],
            status=row["status"],
            display_name=row["display_name"],
        )
