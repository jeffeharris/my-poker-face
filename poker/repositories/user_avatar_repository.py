"""Repository for human-player avatar images.

`user_avatars` (schema v118) holds one row per user, keyed by `user_id`
(`guest_*` or `google_*`). There is deliberately **no FK** to `users` because
guests never get a `users` row — they exist only as a signed cookie — yet they
can still set an avatar, exactly like guest-owned games and api_usage rows.

Each row stores the processed circular icon and square "full" PNG blobs plus a
stable opaque `public_id` UUID. The `public_id` is the *only* identifier
exposed in the public serve URL (`/api/user-avatar/<public_id>`): the raw
`user_id` is never leaked to other players sharing a multiplayer room. The
`public_id` is generated once and preserved across re-uploads so URLs already
embedded in game state or chat history stay valid.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class UserAvatarRepository(BaseRepository):
    """CRUD for `user_avatars`."""

    def upsert_avatar(
        self,
        user_id: str,
        icon_data: bytes,
        full_data: bytes,
        content_type: str = 'image/png',
        source: str = 'upload',
    ) -> str:
        """Insert or replace the user's avatar; return its (stable) `public_id`.

        UPSERT on the `user_id` primary key. The candidate `public_id` is only
        used on first insert — the `ON CONFLICT` branch deliberately does not
        touch `public_id`, so a re-upload keeps the same opaque URL.
        """
        candidate_public_id = str(uuid.uuid4())
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_avatars
                    (user_id, public_id, icon_data, full_data, content_type, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    icon_data = excluded.icon_data,
                    full_data = excluded.full_data,
                    content_type = excluded.content_type,
                    source = excluded.source,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, candidate_public_id, icon_data, full_data, content_type, source),
            )
            row = conn.execute(
                "SELECT public_id FROM user_avatars WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            # The row was just upserted in the same connection/transaction, so
            # this should be unreachable; guard rather than crash on row[0].
            raise RuntimeError(f"upsert_avatar: row missing after write for {user_id!r}")
        return row[0]

    def get_public_id(self, user_id: str) -> Optional[str]:
        """Return the user's avatar `public_id`, or None. Lightweight (no blob)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT public_id FROM user_avatars WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def get_avatar_descriptor(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return ``{public_id, updated_at}`` for the user, or None.

        The ``updated_at`` lets callers build a cache-busting URL token without
        fetching the blob.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT public_id, updated_at FROM user_avatars WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {'public_id': row['public_id'], 'updated_at': row['updated_at']}

    def get_image_by_public_id(
        self, public_id: str, *, full: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Return ``{image_data, content_type}`` for a public id, or None.

        Serves the circular icon by default, or the square full image when
        ``full=True`` (mirrors the AI avatar `/full` endpoint).
        """
        column = 'full_data' if full else 'icon_data'
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {column} AS image_data, content_type "
                "FROM user_avatars WHERE public_id = ?",
                (public_id,),
            ).fetchone()
        if not row:
            return None
        return {'image_data': row['image_data'], 'content_type': row['content_type']}

    def delete(self, user_id: str) -> bool:
        """Remove the user's avatar. Returns True if a row was deleted."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM user_avatars WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0
