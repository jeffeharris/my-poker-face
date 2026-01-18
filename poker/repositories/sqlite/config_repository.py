"""
SQLite implementation of config repository.
Handles app_settings and users tables.
"""
from datetime import datetime
from typing import Optional, Dict

from ..database import DatabaseContext
from ..protocols import AppSettingEntity, UserEntity


class SQLiteConfigRepository:
    """SQLite implementation of ConfigRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a setting value."""
        row = self._db.fetch_one(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        )
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, datetime.now().isoformat()),
            )

    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings."""
        rows = self._db.fetch_all("SELECT key, value FROM app_settings")
        return {row["key"]: row["value"] for row in rows}

    def delete_setting(self, key: str) -> bool:
        """Delete a setting. Returns True if deleted."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM app_settings WHERE key = ?",
                (key,),
            )
            return cursor.rowcount > 0

    def get_user(self, user_id: str) -> Optional[UserEntity]:
        """Get a user by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        )

        if not row:
            return None

        return self._row_to_user_entity(row)

    def get_user_by_email(self, email: str) -> Optional[UserEntity]:
        """Get a user by email."""
        row = self._db.fetch_one(
            "SELECT * FROM users WHERE email = ?",
            (email,),
        )

        if not row:
            return None

        return self._row_to_user_entity(row)

    def save_user(self, user: UserEntity) -> None:
        """Save or update a user."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    id, email, name, picture, created_at, last_login, linked_guest_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    email = excluded.email,
                    name = excluded.name,
                    picture = excluded.picture,
                    last_login = excluded.last_login,
                    linked_guest_id = excluded.linked_guest_id
                """,
                (
                    user.id,
                    user.email,
                    user.name,
                    user.picture,
                    user.created_at.isoformat(),
                    user.last_login.isoformat(),
                    user.linked_guest_id,
                ),
            )

    def link_guest_to_user(self, user_id: str, guest_id: str) -> None:
        """Link a guest session to a user account."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE users SET linked_guest_id = ? WHERE id = ?",
                (guest_id, user_id),
            )

    def get_user_by_linked_guest(self, guest_id: str) -> Optional[UserEntity]:
        """Get a user by their linked guest ID."""
        row = self._db.fetch_one(
            "SELECT * FROM users WHERE linked_guest_id = ?",
            (guest_id,),
        )

        if not row:
            return None

        return self._row_to_user_entity(row)

    def _row_to_user_entity(self, row) -> UserEntity:
        """Convert a database row to a UserEntity."""
        return UserEntity(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            picture=row["picture"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login=datetime.fromisoformat(row["last_login"]),
            linked_guest_id=row["linked_guest_id"],
        )
