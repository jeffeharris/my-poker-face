"""Device repository — push-notification delivery targets.

Stores the device tokens a player has registered (APNs today; FCM/web/email
later) so the notification dispatcher can reach them when it's their turn and
the app is closed. Keyed ``(user_id, token)`` so multiple devices per user each
get a row and a re-registered token upserts in place.

See migration ``20260612_1200_async_friends`` for the schema.
"""

import logging
from dataclasses import dataclass
from typing import List

from poker.repositories.base_repository import BaseRepository, retry_on_lock

logger = logging.getLogger(__name__)


@dataclass
class Device:
    """A registered push target."""

    user_id: str
    platform: str
    token: str


class DeviceRepository(BaseRepository):
    """CRUD for ``user_devices``."""

    @retry_on_lock()
    def register(self, user_id: str, platform: str, token: str) -> None:
        """Upsert a device token, refreshing ``last_seen`` on re-registration."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_devices (user_id, platform, token, created_at, last_seen)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, token) DO UPDATE SET
                    platform = excluded.platform,
                    last_seen = CURRENT_TIMESTAMP
                """,
                (user_id, platform, token),
            )

    def list_devices(self, user_id: str) -> List[Device]:
        """All registered devices for a user."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT user_id, platform, token FROM user_devices WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            return [Device(r["user_id"], r["platform"], r["token"]) for r in rows]

    @retry_on_lock()
    def remove(self, user_id: str, token: str) -> None:
        """Drop a token — call when APNs reports it Unregistered (410)."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM user_devices WHERE user_id = ? AND token = ?",
                (user_id, token),
            )
