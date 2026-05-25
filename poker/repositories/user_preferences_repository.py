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
from typing import Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


# Valid world-pace values + the default. Kept here (not in the ticker
# service) so both the repo's validation and the route's input check
# share one source of truth without importing Flask-side modules.
WORLD_PACES = ("subtle", "lively", "bustling")
DEFAULT_WORLD_PACE = "lively"


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
            raise ValueError(
                f"invalid world_pace {pace!r}; expected one of {WORLD_PACES}"
            )
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
