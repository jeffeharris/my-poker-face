"""Repository for app settings persistence.

Manages the app_settings table for key-value configuration storage.
"""

import logging
import sqlite3
from typing import Any, Dict, Optional

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class SettingsRepository(BaseRepository):
    """Handles CRUD operations for app settings."""

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get an app setting by key, with optional default.

        Args:
            key: The setting key (e.g., 'LLM_PROMPT_CAPTURE')
            default: Default value if setting doesn't exist

        Returns:
            The setting value, or default if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
                row = cursor.fetchone()
                return row[0] if row else default
        except sqlite3.OperationalError:
            # Table doesn't exist yet (e.g., during startup)
            return default

    def set_setting(self, key: str, value: str, description: Optional[str] = None) -> bool:
        """Set an app setting.

        Args:
            key: The setting key
            value: The setting value (stored as string)
            description: Optional description for the setting

        Returns:
            True if successful
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, description, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        description = COALESCE(excluded.description, app_settings.description),
                        updated_at = CURRENT_TIMESTAMP
                """,
                    (key, value, description),
                )
                logger.info(f"Setting '{key}' updated to '{value}'")
                return True
        except Exception as e:
            logger.error(f"Failed to set setting '{key}': {e}")
            return False

    def increment_counter(self, key: str, *, by: int = 1, description: Optional[str] = None) -> int:
        """Atomically increment an integer-valued setting and return the new total.

        A missing or non-integer stored value is treated as 0, so the first
        call lands at `by`. The increment is a single UPSERT so concurrent
        callers can't lose a tick to a read-modify-write race. Returns the new
        value, or 0 on error (e.g. table missing during startup).
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO app_settings (key, value, description, updated_at)
                    VALUES (?, CAST(? AS TEXT), ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = CAST(
                            CAST(COALESCE(NULLIF(app_settings.value, ''), '0') AS INTEGER) + ?
                            AS TEXT
                        ),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, str(by), description, by),
                )
                cursor = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
                row = cursor.fetchone()
                return int(row[0]) if row and str(row[0]).lstrip("-").isdigit() else 0
        except Exception as e:  # noqa: BLE001 — counter is best-effort telemetry
            logger.warning("Failed to increment counter '%s': %s", key, e)
            return 0

    def get_counter(self, key: str) -> int:
        """Read an integer-valued setting, returning 0 if missing/non-integer."""
        raw = self.get_setting(key)
        return int(raw) if raw is not None and str(raw).lstrip("-").isdigit() else 0

    def sum_counters_with_prefix(self, prefix: str) -> int:
        """Sum every integer-valued setting whose key starts with `prefix`.

        Used for cross-sandbox roll-ups of per-sandbox counters. Non-integer
        values cast to 0 in SQLite, so they're ignored harmlessly.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT COALESCE(SUM(CAST(value AS INTEGER)), 0) "
                    "FROM app_settings WHERE key LIKE ?",
                    (prefix + "%",),
                )
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:  # noqa: BLE001 — best-effort roll-up
            logger.warning("Failed to sum counters with prefix '%s': %s", prefix, e)
            return 0

    def get_all_settings(self) -> Dict[str, Dict[str, Any]]:
        """Get all app settings.

        Returns:
            Dict mapping setting keys to their values and metadata
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT key, value, description, updated_at
                    FROM app_settings
                    ORDER BY key
                """)
                return {
                    row['key']: {
                        'value': row['value'],
                        'description': row['description'],
                        'updated_at': row['updated_at'],
                    }
                    for row in cursor.fetchall()
                }
        except sqlite3.OperationalError:
            return {}

    def delete_setting(self, key: str) -> bool:
        """Delete an app setting.

        Args:
            key: The setting key to delete

        Returns:
            True if the setting was deleted, False if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete setting '{key}': {e}")
            return False
