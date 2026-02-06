"""Repository for prompt preset persistence.

Covers CRUD operations on the prompt_presets table.
"""
from __future__ import annotations

import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class PromptPresetRepository(BaseRepository):
    """Handles prompt preset CRUD operations."""

    @staticmethod
    def _preset_row_to_dict(row) -> dict:
        """Convert a prompt_presets row to a dictionary."""
        return {
            'id': row['id'],
            'name': row['name'],
            'description': row['description'],
            'prompt_config': json.loads(row['prompt_config']) if row['prompt_config'] else None,
            'guidance_injection': row['guidance_injection'],
            'owner_id': row['owner_id'],
            'is_system': bool(row['is_system']) if row['is_system'] is not None else False,
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    def create_prompt_preset(
        self,
        name: str,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None,
        owner_id: Optional[str] = None
    ) -> int:
        """Create a new prompt preset.

        Args:
            name: Unique name for the preset
            description: Optional description of the preset
            prompt_config: PromptConfig toggles as dict
            guidance_injection: Extra guidance text to append to prompts
            owner_id: Optional owner ID for multi-tenant support

        Returns:
            The ID of the created preset

        Raises:
            ValueError: If a preset with the same name already exists
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    INSERT INTO prompt_presets (name, description, prompt_config, guidance_injection, owner_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    name,
                    description,
                    json.dumps(prompt_config) if prompt_config else None,
                    guidance_injection,
                    owner_id
                ))
                preset_id = cursor.lastrowid
                logger.info(f"Created prompt preset '{name}' with ID {preset_id}")
                return preset_id
        except sqlite3.IntegrityError:
            raise ValueError(f"Prompt preset with name '{name}' already exists")

    def get_prompt_preset(self, preset_id: int) -> Optional[Dict[str, Any]]:
        """Get a prompt preset by ID.

        Args:
            preset_id: The preset ID

        Returns:
            Preset data as dict, or None if not found
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT id, name, description, prompt_config, guidance_injection,
                       owner_id, is_system, created_at, updated_at
                FROM prompt_presets
                WHERE id = ?
            """, (preset_id,))
            row = cursor.fetchone()
            return self._preset_row_to_dict(row) if row else None

    def get_prompt_preset_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a prompt preset by name.

        Args:
            name: The preset name

        Returns:
            Preset data as dict, or None if not found
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT id, name, description, prompt_config, guidance_injection,
                       owner_id, is_system, created_at, updated_at
                FROM prompt_presets
                WHERE name = ?
            """, (name,))
            row = cursor.fetchone()
            return self._preset_row_to_dict(row) if row else None

    def list_prompt_presets(
        self,
        owner_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List all prompt presets.

        Args:
            owner_id: Optional filter by owner ID
            limit: Maximum number of results

        Returns:
            List of preset data dicts
        """
        with self._get_connection() as conn:

            if owner_id:
                # Include system presets for all users, plus user's own presets
                cursor = conn.execute("""
                    SELECT id, name, description, prompt_config, guidance_injection,
                           owner_id, is_system, created_at, updated_at
                    FROM prompt_presets
                    WHERE owner_id = ? OR owner_id IS NULL OR is_system = TRUE
                    ORDER BY is_system DESC, updated_at DESC
                    LIMIT ?
                """, (owner_id, limit))
            else:
                cursor = conn.execute("""
                    SELECT id, name, description, prompt_config, guidance_injection,
                           owner_id, is_system, created_at, updated_at
                    FROM prompt_presets
                    ORDER BY is_system DESC, updated_at DESC
                    LIMIT ?
                """, (limit,))

            return [self._preset_row_to_dict(row) for row in cursor.fetchall()]

    def update_prompt_preset(
        self,
        preset_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None
    ) -> bool:
        """Update a prompt preset.

        Args:
            preset_id: The preset ID to update
            name: Optional new name
            description: Optional new description
            prompt_config: Optional new prompt config
            guidance_injection: Optional new guidance text

        Returns:
            True if the preset was updated, False if not found

        Raises:
            ValueError: If the new name conflicts with an existing preset
        """
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if prompt_config is not None:
            updates.append("prompt_config = ?")
            params.append(json.dumps(prompt_config))
        if guidance_injection is not None:
            updates.append("guidance_injection = ?")
            params.append(guidance_injection)

        if not updates:
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(preset_id)

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    f"UPDATE prompt_presets SET {', '.join(updates)} WHERE id = ?",
                    params
                )
                if cursor.rowcount > 0:
                    logger.info(f"Updated prompt preset ID {preset_id}")
                    return True
                return False
        except sqlite3.IntegrityError:
            raise ValueError(f"Prompt preset with name '{name}' already exists")

    def update_prompt_preset_for_owner(
        self,
        preset_id: int,
        owner_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        guidance_injection: Optional[str] = None
    ) -> bool:
        """Update a prompt preset owned by a specific user.

        Args:
            preset_id: The preset ID to update
            owner_id: Required owner ID for ownership enforcement
            name: Optional new name
            description: Optional new description
            prompt_config: Optional new prompt config
            guidance_injection: Optional new guidance text

        Returns:
            True if the preset was updated, False if not found or not owned by owner_id

        Raises:
            ValueError: If the new name conflicts with an existing preset
        """
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if prompt_config is not None:
            updates.append("prompt_config = ?")
            params.append(json.dumps(prompt_config))
        if guidance_injection is not None:
            updates.append("guidance_injection = ?")
            params.append(guidance_injection)

        if not updates:
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([preset_id, owner_id])

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    f"UPDATE prompt_presets SET {', '.join(updates)} WHERE id = ? AND owner_id = ?",
                    params
                )
                if cursor.rowcount > 0:
                    logger.info(f"Updated prompt preset ID {preset_id} for owner {owner_id}")
                    return True
                return False
        except sqlite3.IntegrityError:
            raise ValueError(f"Prompt preset with name '{name}' already exists")

    def delete_prompt_preset(self, preset_id: int) -> bool:
        """Delete a prompt preset.

        Args:
            preset_id: The preset ID to delete

        Returns:
            True if the preset was deleted, False if not found
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM prompt_presets WHERE id = ?",
                    (preset_id,)
                )
                if cursor.rowcount > 0:
                    logger.info(f"Deleted prompt preset ID {preset_id}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to delete prompt preset {preset_id}: {e}")
            return False

    def delete_prompt_preset_for_owner(self, preset_id: int, owner_id: str) -> bool:
        """Delete a prompt preset owned by a specific user.

        Args:
            preset_id: The preset ID to delete
            owner_id: Required owner ID for ownership enforcement

        Returns:
            True if the preset was deleted, False if not found or not owned by owner_id
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM prompt_presets WHERE id = ? AND owner_id = ?",
                    (preset_id, owner_id)
                )
                if cursor.rowcount > 0:
                    logger.info(f"Deleted prompt preset ID {preset_id} for owner {owner_id}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to delete prompt preset {preset_id} for owner {owner_id}: {e}")
            return False
