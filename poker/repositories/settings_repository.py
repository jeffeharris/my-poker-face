"""Repository for app settings persistence.

Manages the app_settings table for key-value configuration storage.
"""
import sqlite3
import logging
from typing import Optional, Dict, Any

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
                cursor = conn.execute(
                    "SELECT value FROM app_settings WHERE key = ?",
                    (key,)
                )
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
                conn.execute("""
                    INSERT INTO app_settings (key, value, description, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        description = COALESCE(excluded.description, app_settings.description),
                        updated_at = CURRENT_TIMESTAMP
                """, (key, value, description))
                logger.info(f"Setting '{key}' updated to '{value}'")
                return True
        except Exception as e:
            logger.error(f"Failed to set setting '{key}': {e}")
            return False

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
                cursor = conn.execute(
                    "DELETE FROM app_settings WHERE key = ?",
                    (key,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to delete setting '{key}': {e}")
            return False
