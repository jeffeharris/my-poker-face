"""Repository for per-user settings.

`user_preferences` (schema v115) holds one row per user, keyed by
`user_id`. The first concrete setting is `world_pace` — how fast the
realtime background ticker advances the unseated-table world for that
user's sandbox. A `preferences_json` blob is reserved for future scalar
prefs so the next setting doesn't need a migration.

See `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
"""

from __future__ import annotations

import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


# Valid world-pace values + the default. Kept here (not in the ticker
# service) so both the repo's validation and the route's input check
# share one source of truth without importing Flask-side modules.
WORLD_PACES = ("subtle", "lively", "bustling")
DEFAULT_WORLD_PACE = "lively"

# Cap on the human's self-description. Long enough for a sentence or two of
# flavor the AIs can riff on, short enough to keep it out of token budgets.
MAX_BIO_LENGTH = 500


class UserPreferencesRepository(BaseRepository):
    """CRUD for `user_preferences`."""

    def get_world_pace(self, user_id: str) -> str:
        """Return the user's world pace, or the default if unset.

        Never raises on a missing row — a user who has never touched the
        setting gets `DEFAULT_WORLD_PACE`. An unrecognized stored value
        (e.g. a future pace rolled back) also degrades to the default so
        the ticker always has a usable rate.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT world_pace FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return DEFAULT_WORLD_PACE
        pace = row[0]
        return pace if pace in WORLD_PACES else DEFAULT_WORLD_PACE

    def set_world_pace(self, user_id: str, pace: str) -> None:
        """Persist the user's world pace.

        Raises `ValueError` for an invalid pace so the route can return a
        400 rather than silently storing garbage. UPSERT keeps the call
        idempotent and avoids a read-before-write.
        """
        if pace not in WORLD_PACES:
            raise ValueError(f"invalid world_pace {pace!r}; expected one of {WORLD_PACES}")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, world_pace, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    world_pace = excluded.world_pace,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, pace),
            )

    def get_bio(self, user_id: str) -> str:
        """Return the user's self-description, or empty string if unset.

        Never raises on a missing row/column — a user who has never written a
        bio gets ``""``. The AIs read this to trash-talk or comment on the
        human, so an empty string simply means "no extra color to riff on."
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT bio FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row or row[0] is None:
            return ""
        return str(row[0])

    def set_bio(self, user_id: str, bio: str) -> str:
        """Persist the user's self-description; return the stored (trimmed) value.

        Trims and caps at ``MAX_BIO_LENGTH`` so an overlong paste degrades
        gracefully rather than 400-ing. UPSERT keeps it idempotent and avoids a
        read-before-write. Passing an empty/whitespace string clears the bio.
        """
        trimmed = (bio or "").strip()[:MAX_BIO_LENGTH]
        stored = trimmed or None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, bio, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    bio = excluded.bio,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, stored),
            )
        return trimmed
