"""Repository for personality and avatar persistence.

Manages the personalities and avatar_images tables.
"""
import json
import logging
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class PersonalityRepository(BaseRepository):
    """Handles CRUD operations for personalities and avatar images."""

    # --- Personality CRUD ---

    def save_personality(self, name: str, config: Dict[str, Any], source: str = 'ai_generated',
                         owner_id: Optional[str] = None, visibility: str = 'public') -> None:
        """Save a personality configuration to the database."""
        elasticity_config = config.get('elasticity_config', {})
        config_without_elasticity = {k: v for k, v in config.items() if k != 'elasticity_config'}

        with self._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]

            has_elasticity = 'elasticity_config' in columns
            has_ownership = 'owner_id' in columns

            if has_elasticity and has_ownership:
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, owner_id, visibility, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config_without_elasticity), json.dumps(elasticity_config),
                      source, owner_id, visibility))
            elif has_elasticity:
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config_without_elasticity), json.dumps(elasticity_config), source))
            else:
                conn.execute("""
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, source, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (name, json.dumps(config), source))

    def load_personality(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a personality configuration from the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'elasticity_config' in columns:
                cursor = conn.execute("""
                    SELECT config_json, elasticity_config FROM personalities
                    WHERE name = ?
                """, (name,))
            else:
                cursor = conn.execute("""
                    SELECT config_json FROM personalities
                    WHERE name = ?
                """, (name,))

            row = cursor.fetchone()
            if row:
                conn.execute("""
                    UPDATE personalities
                    SET times_used = times_used + 1
                    WHERE name = ?
                """, (name,))

                config = json.loads(row['config_json'])

                if 'elasticity_config' in columns and row['elasticity_config']:
                    config['elasticity_config'] = json.loads(row['elasticity_config'])

                return config

            return None

    def list_personalities(self, limit: int = 50, user_id: Optional[str] = None,
                           include_disabled: bool = False) -> List[Dict[str, Any]]:
        """List personalities with metadata, filtered by visibility.

        Args:
            limit: Max number of results
            user_id: If provided, include this user's private personalities
            include_disabled: If True (admin), include disabled and all private personalities
        """
        with self._get_connection() as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()]
            has_ownership = 'owner_id' in columns

            if has_ownership:
                conditions = ["visibility = 'public'"]
                params: list = []

                if user_id:
                    conditions.append("owner_id = ?")
                    params.append(user_id)

                if include_disabled:
                    conditions.append("visibility = 'disabled'")
                    conditions.append("visibility = 'private'")

                where_clause = "WHERE " + " OR ".join(conditions)

                cursor = conn.execute(f"""
                    SELECT name, source, created_at, updated_at, times_used, is_generated,
                           owner_id, visibility
                    FROM personalities
                    {where_clause}
                    ORDER BY times_used DESC, updated_at DESC
                    LIMIT ?
                """, params + [limit])
            else:
                cursor = conn.execute("""
                    SELECT name, source, created_at, updated_at, times_used, is_generated
                    FROM personalities
                    ORDER BY times_used DESC, updated_at DESC
                    LIMIT ?
                """, (limit,))

            personalities = []
            for row in cursor:
                entry = {
                    'name': row['name'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'times_used': row['times_used'],
                    'is_generated': bool(row['is_generated'])
                }
                if has_ownership:
                    entry['owner_id'] = row['owner_id']
                    entry['visibility'] = row['visibility']
                personalities.append(entry)

            return personalities

    def delete_personality(self, name: str) -> bool:
        """Delete a personality from the database."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM personalities WHERE name = ?",
                    (name,)
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting personality {name}: {e}")
            return False

    def update_personality_config(self, name: str, config: Dict[str, Any], source: str = 'user_edited') -> bool:
        """Update only the config for an existing personality, preserving ownership fields.

        Unlike save_personality (which uses INSERT OR REPLACE and can wipe owner_id/visibility),
        this method uses UPDATE to modify only config_json, elasticity_config, and source.

        Returns:
            True if the personality was found and updated, False otherwise.
        """
        elasticity_config = config.get('elasticity_config', {})
        config_without_elasticity = {k: v for k, v in config.items() if k != 'elasticity_config'}

        with self._get_connection() as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()]
            has_elasticity = 'elasticity_config' in columns

            if has_elasticity:
                cursor = conn.execute("""
                    UPDATE personalities
                    SET config_json = ?, elasticity_config = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """, (json.dumps(config_without_elasticity), json.dumps(elasticity_config), source, name))
            else:
                cursor = conn.execute("""
                    UPDATE personalities
                    SET config_json = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """, (json.dumps(config_without_elasticity), source, name))

            return cursor.rowcount > 0

    def set_visibility(self, name: str, visibility: str) -> bool:
        """Set visibility for a personality. Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE personalities SET visibility = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (visibility, name)
            )
            return cursor.rowcount > 0

    def set_owner(self, name: str, owner_id: str, visibility: str = 'private') -> bool:
        """Assign an owner to a personality. Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE personalities SET owner_id = ?, visibility = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (owner_id, visibility, name)
            )
            return cursor.rowcount > 0

    def assign_unowned_disabled_to_owner(self, owner_id: str) -> int:
        """Assign disabled personalities with no owner to the given user.

        Changes their visibility to 'private' so the owner can use them.
        Idempotent: no-op if all disabled personalities already have owners.

        Returns:
            Count of personalities assigned.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE personalities
                SET owner_id = ?, visibility = 'private', updated_at = CURRENT_TIMESTAMP
                WHERE visibility = 'disabled' AND owner_id IS NULL
            """, (owner_id,))
            return cursor.rowcount

    def get_personality_owner(self, name: str) -> Optional[str]:
        """Get the owner_id of a personality, or None if unowned/not found."""
        with self._get_connection() as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()]
            if 'owner_id' not in columns:
                return None
            cursor = conn.execute("SELECT owner_id FROM personalities WHERE name = ?", (name,))
            row = cursor.fetchone()
            return row['owner_id'] if row else None

    def seed_personalities_from_json(self, json_path: str, overwrite: bool = False) -> Dict[str, int]:
        """Seed database with personalities from JSON file.

        Args:
            json_path: Path to personalities.json file
            overwrite: If True, overwrite existing personalities

        Returns:
            Dict with counts: {'added': N, 'skipped': M, 'updated': P}
        """
        from pathlib import Path

        json_file = Path(json_path)
        if not json_file.exists():
            logger.warning(f"Personalities JSON file not found: {json_path}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': 'File not found'}

        try:
            with open(json_file, 'r') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading personalities JSON: {e}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': str(e)}

        personalities = data.get('personalities', {})
        added = 0
        skipped = 0
        updated = 0

        for name, config in personalities.items():
            existing = self.load_personality(name)

            if existing and not overwrite:
                skipped += 1
                continue

            if existing:
                # Use config-only update to preserve ownership fields
                self.update_personality_config(name, config, source='personalities.json')
                updated += 1
            else:
                self.save_personality(name, config, source='personalities.json')
                added += 1

        logger.info(f"Seeded personalities from JSON: {added} added, {updated} updated, {skipped} skipped")
        return {'added': added, 'skipped': skipped, 'updated': updated}

    # --- Avatar CRUD ---

    def save_avatar_image(self, personality_name: str, emotion: str,
                          image_data: bytes, width: int = 256, height: int = 256,
                          content_type: str = 'image/png',
                          full_image_data: Optional[bytes] = None,
                          full_width: Optional[int] = None,
                          full_height: Optional[int] = None) -> None:
        """Save an avatar image to the database."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO avatar_images
                (personality_name, emotion, image_data, content_type, width, height, file_size,
                 full_image_data, full_width, full_height, full_file_size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                personality_name,
                emotion,
                image_data,
                content_type,
                width,
                height,
                len(image_data),
                full_image_data,
                full_width,
                full_height,
                len(full_image_data) if full_image_data else None
            ))

    def load_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load avatar image data from database."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT image_data FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            return row[0] if row else None

    def load_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load avatar image with metadata from database."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT image_data, content_type, width, height, file_size
                FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'image_data': row['image_data'],
                'content_type': row['content_type'],
                'width': row['width'],
                'height': row['height'],
                'file_size': row['file_size']
            }

    def load_full_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load full uncropped avatar image from database."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT full_image_data FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def load_full_avatar_image_with_metadata(self, personality_name: str, emotion: str) -> Optional[Dict[str, Any]]:
        """Load full avatar image with metadata from database."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT full_image_data, content_type, full_width, full_height, full_file_size
                FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            row = cursor.fetchone()
            if not row or not row['full_image_data']:
                return None

            return {
                'image_data': row['full_image_data'],
                'content_type': row['content_type'],
                'width': row['full_width'],
                'height': row['full_height'],
                'file_size': row['full_file_size']
            }

    def has_full_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if a full avatar image exists for the given personality and emotion."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ? AND full_image_data IS NOT NULL
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def has_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the given personality and emotion."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 1 FROM avatar_images
                WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))
            return cursor.fetchone() is not None

    def get_available_avatar_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have avatar images for a personality."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT emotion FROM avatar_images
                WHERE personality_name = ?
                ORDER BY emotion
            """, (personality_name,))
            return [row[0] for row in cursor.fetchall()]

    def has_all_avatar_emotions(self, personality_name: str) -> bool:
        """Check if a personality has all 6 emotion avatars."""
        emotions = self.get_available_avatar_emotions(personality_name)
        required = {'confident', 'happy', 'thinking', 'nervous', 'angry', 'shocked'}
        return required.issubset(set(emotions))

    def delete_avatar_images(self, personality_name: str) -> int:
        """Delete all avatar images for a personality.

        Returns:
            Number of images deleted
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM avatar_images WHERE personality_name = ?
            """, (personality_name,))
            return cursor.rowcount

    def list_personalities_with_avatars(self) -> List[Dict[str, Any]]:
        """Get list of all personalities that have at least one avatar image."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT personality_name, COUNT(*) as emotion_count
                FROM avatar_images
                GROUP BY personality_name
                ORDER BY personality_name
            """)
            return [
                {'personality_name': row['personality_name'], 'emotion_count': row['emotion_count']}
                for row in cursor.fetchall()
            ]

    def get_avatar_stats(self) -> Dict[str, Any]:
        """Get statistics about avatar images in the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM avatar_images")
            total_count = cursor.fetchone()['count']

            cursor = conn.execute("SELECT SUM(file_size) as total_size FROM avatar_images")
            total_size = cursor.fetchone()['total_size'] or 0

            cursor = conn.execute("SELECT COUNT(DISTINCT personality_name) as count FROM avatar_images")
            personality_count = cursor.fetchone()['count']

            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM (
                    SELECT personality_name FROM avatar_images
                    GROUP BY personality_name
                    HAVING COUNT(DISTINCT emotion) = 6
                )
            """)
            complete_count = cursor.fetchone()['count']

            return {
                'total_images': total_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'personality_count': personality_count,
                'complete_personality_count': complete_count
            }

    # --- Reference Image CRUD ---

    def save_reference_image(self, reference_id: str, image_data: bytes,
                             width: int, height: int, content_type: str,
                             source: str, original_url: Optional[str] = None) -> None:
        """Save a reference image to the database."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO reference_images (id, image_data, width, height, content_type, source, original_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (reference_id, image_data, width, height, content_type, source, original_url))

    def get_reference_image(self, reference_id: str) -> Optional[Dict[str, Any]]:
        """Load a reference image by ID.

        Returns:
            Dict with image_data and content_type, or None if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT image_data, content_type FROM reference_images WHERE id = ?
            """, (reference_id,))

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'image_data': row['image_data'],
                'content_type': row['content_type']
            }

    def assign_avatar(self, personality_name: str, emotion: str, image_data: bytes) -> None:
        """Assign an avatar image to a personality, updating if one already exists."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id FROM avatar_images WHERE personality_name = ? AND emotion = ?
            """, (personality_name, emotion))

            existing = cursor.fetchone()
            if existing:
                conn.execute("""
                    UPDATE avatar_images
                    SET image_data = ?, content_type = 'image/png', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (image_data, existing['id']))
            else:
                conn.execute("""
                    INSERT INTO avatar_images (personality_name, emotion, image_data, content_type)
                    VALUES (?, ?, ?, 'image/png')
                """, (personality_name, emotion, image_data))
