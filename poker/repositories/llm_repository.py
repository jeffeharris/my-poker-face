"""LLM model management repository — enabled models and provider queries.

Extracted from GamePersistence (T3-35-B6).
"""
import sqlite3
from typing import Dict, List, Any, Set

from .base_repository import BaseRepository


class LLMRepository(BaseRepository):
    """Manages LLM model configuration (enabled_models table)."""

    def get_available_providers(self) -> Set[str]:
        """Get the set of all providers in the system.

        Returns:
            Set of all provider names in enabled_models table.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT provider
                FROM enabled_models
            """)
            return {row[0] for row in cursor.fetchall()}

    def get_enabled_models(self) -> Dict[str, List[str]]:
        """Get all enabled models grouped by provider.

        Returns:
            Dict mapping provider name to list of enabled model names.
            Example: {'openai': ['gpt-4o', 'gpt-5-nano'], 'groq': ['llama-3.1-8b-instant']}
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT provider, model
                FROM enabled_models
                WHERE enabled = 1
                ORDER BY provider, sort_order
            """)
            result: Dict[str, List[str]] = {}
            for row in cursor.fetchall():
                provider = row['provider']
                if provider not in result:
                    result[provider] = []
                result[provider].append(row['model'])
            return result

    def get_all_enabled_models(self) -> List[Dict[str, Any]]:
        """Get all models with their enabled status.

        Returns:
            List of dicts with provider, model, enabled, user_enabled, display_name, etc.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, provider, model, enabled, user_enabled, display_name, notes,
                       supports_reasoning, supports_json_mode, supports_image_gen,
                       sort_order, created_at, updated_at
                FROM enabled_models
                ORDER BY provider, sort_order
            """)
            return [dict(row) for row in cursor.fetchall()]

    def update_model_enabled(self, model_id: int, enabled: bool) -> bool:
        """Update the enabled status of a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (1 if enabled else 0, model_id))
            return cursor.rowcount > 0

    def update_model_details(self, model_id: int, display_name: str = None, notes: str = None) -> bool:
        """Update display name and notes for a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE enabled_models
                SET display_name = COALESCE(?, display_name),
                    notes = COALESCE(?, notes),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (display_name, notes, model_id))
            return cursor.rowcount > 0
