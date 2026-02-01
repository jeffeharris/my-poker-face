"""Repository for guest usage tracking persistence.

Manages the guest_usage_tracking table for tracking hands played by guests.
"""
import logging

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class GuestTrackingRepository(BaseRepository):
    """Handles guest usage tracking operations."""

    def increment_hands_played(self, tracking_id: str) -> int:
        """Increment hands played for a guest tracking ID.

        Upserts the row and returns the new count.
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO guest_usage_tracking (tracking_id, hands_played, last_hand_at)
                VALUES (?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(tracking_id) DO UPDATE SET
                    hands_played = hands_played + 1,
                    last_hand_at = CURRENT_TIMESTAMP
            """, (tracking_id,))
            cursor = conn.execute(
                "SELECT hands_played FROM guest_usage_tracking WHERE tracking_id = ?",
                (tracking_id,)
            )
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_hands_played(self, tracking_id: str) -> int:
        """Get the number of hands played for a guest tracking ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT hands_played FROM guest_usage_tracking WHERE tracking_id = ?",
                (tracking_id,)
            )
            row = cursor.fetchone()
            return row[0] if row else 0
