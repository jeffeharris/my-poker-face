"""
SQLite implementation of personality repository.
"""
from datetime import datetime
from typing import Optional, List

from ..database import DatabaseContext
from ..protocols import PersonalityEntity, AvatarImageEntity
from ..serialization import to_json, from_json


class SQLitePersonalityRepository:
    """SQLite implementation of PersonalityRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save(self, personality: PersonalityEntity) -> None:
        """Save or update a personality."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO personalities (name, config_json, source, created_at, last_used)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    config_json = excluded.config_json,
                    source = excluded.source,
                    last_used = excluded.last_used
                """,
                (
                    personality.name,
                    to_json(personality.config),
                    personality.source,
                    personality.created_at.isoformat(),
                    personality.last_used.isoformat() if personality.last_used else None,
                ),
            )

    def find_by_name(self, name: str) -> Optional[PersonalityEntity]:
        """Find a personality by name."""
        row = self._db.fetch_one(
            "SELECT * FROM personalities WHERE name = ?",
            (name,),
        )

        if not row:
            return None

        return self._row_to_entity(row)

    def find_all(self, limit: int = 50) -> List[PersonalityEntity]:
        """List all personalities."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM personalities
            ORDER BY last_used DESC NULLS LAST, created_at DESC
            LIMIT ?
            """,
            (limit,),
        )

        return [self._row_to_entity(row) for row in rows]

    def delete(self, name: str) -> bool:
        """Delete a personality. Returns True if deleted."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM personalities WHERE name = ?",
                (name,),
            )
            deleted = cursor.rowcount > 0

            if deleted:
                # Also delete associated avatars
                conn.execute(
                    "DELETE FROM avatar_images WHERE personality_name = ?",
                    (name,),
                )

        return deleted

    def save_avatar(self, avatar: AvatarImageEntity) -> None:
        """Save avatar image for a personality."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO avatar_images (
                    personality_name, emotion, image_data,
                    thumbnail_data, full_image_data,
                    generation_prompt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(personality_name, emotion) DO UPDATE SET
                    image_data = excluded.image_data,
                    thumbnail_data = excluded.thumbnail_data,
                    full_image_data = excluded.full_image_data,
                    generation_prompt = excluded.generation_prompt,
                    created_at = excluded.created_at
                """,
                (
                    avatar.personality_name,
                    avatar.emotion,
                    avatar.image_data,
                    avatar.thumbnail_data,
                    avatar.full_image_data,
                    avatar.generation_prompt,
                    avatar.created_at.isoformat(),
                ),
            )

    def load_avatar(
        self, personality_name: str, emotion: str
    ) -> Optional[AvatarImageEntity]:
        """Load avatar image."""
        row = self._db.fetch_one(
            """
            SELECT * FROM avatar_images
            WHERE personality_name = ? AND emotion = ?
            """,
            (personality_name, emotion),
        )

        if not row:
            return None

        return AvatarImageEntity(
            personality_name=row["personality_name"],
            emotion=row["emotion"],
            image_data=row["image_data"],
            thumbnail_data=row["thumbnail_data"],
            full_image_data=row["full_image_data"],
            generation_prompt=row["generation_prompt"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_available_emotions(self, personality_name: str) -> List[str]:
        """Get available avatar emotions for a personality."""
        rows = self._db.fetch_all(
            """
            SELECT emotion FROM avatar_images
            WHERE personality_name = ?
            ORDER BY emotion
            """,
            (personality_name,),
        )

        return [row["emotion"] for row in rows]

    def delete_avatars(self, personality_name: str) -> int:
        """Delete all avatars for a personality. Returns count deleted."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM avatar_images WHERE personality_name = ?",
                (personality_name,),
            )
            return cursor.rowcount

    def update_last_used(self, name: str) -> None:
        """Update the last_used timestamp for a personality."""
        self._db.execute(
            "UPDATE personalities SET last_used = ? WHERE name = ?",
            (datetime.now().isoformat(), name),
        )

    def get_avatar_stats(self) -> dict:
        """Get statistics about avatar images in the database."""
        # Total count
        row = self._db.fetch_one("SELECT COUNT(*) as count FROM avatar_images")
        total_count = row['count'] if row else 0

        # Total size (if file_size column exists)
        try:
            row = self._db.fetch_one("SELECT SUM(file_size) as total_size FROM avatar_images")
            total_size = row['total_size'] if row and row['total_size'] else 0
        except Exception:
            # file_size column may not exist
            total_size = 0

        # Unique personalities
        row = self._db.fetch_one("SELECT COUNT(DISTINCT personality_name) as count FROM avatar_images")
        personality_count = row['count'] if row else 0

        # Personalities with all 6 emotions
        row = self._db.fetch_one("""
            SELECT COUNT(*) as count FROM (
                SELECT personality_name FROM avatar_images
                GROUP BY personality_name
                HAVING COUNT(DISTINCT emotion) = 6
            )
        """)
        complete_count = row['count'] if row else 0

        return {
            'total_images': total_count,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2) if total_size else 0,
            'unique_personalities': personality_count,
            'complete_personalities': complete_count,
        }

    def _row_to_entity(self, row) -> PersonalityEntity:
        """Convert a database row to a PersonalityEntity."""
        return PersonalityEntity(
            name=row["name"],
            config=from_json(row["config_json"]) or {},
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
        )
