"""LLM model management repository — enabled models and provider queries."""
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

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

    # -------------------------------------------------------------------------
    # Usage summary
    # -------------------------------------------------------------------------

    def get_usage_summary(self, date_modifier: str) -> dict:
        """Get aggregated API usage stats for a time range.

        Args:
            date_modifier: SQLite date modifier, e.g. '-7 days'.

        Returns:
            Dict with total_calls, total_cost, avg_latency, error_rate.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_calls,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COALESCE(AVG(latency_ms), 0) as avg_latency,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
            """, (date_modifier,))
            return dict(cursor.fetchone())

    # -------------------------------------------------------------------------
    # Model toggle with cascade
    # -------------------------------------------------------------------------

    def toggle_model(self, model_id: int, field: str, enabled: bool) -> dict:
        """Toggle a model's enabled or user_enabled field with cascade logic.

        Cascade rules:
        - If field='enabled' and enabled=False: also set user_enabled=0.
        - If field='user_enabled' and enabled=True: also set enabled=1.

        Returns:
            Dict with 'enabled' and 'user_enabled' bools.

        Raises:
            ValueError: If field is invalid or model not found.
        """
        if field not in ('enabled', 'user_enabled'):
            raise ValueError('Invalid field. Must be "enabled" or "user_enabled"')

        with self._get_connection() as conn:
            current = conn.execute(
                "SELECT enabled, user_enabled FROM enabled_models WHERE id = ?",
                (model_id,)
            ).fetchone()

            if not current:
                raise ValueError('Model not found')

            new_enabled = current['enabled']
            new_user_enabled = current['user_enabled']

            if field == 'enabled':
                new_enabled = 1 if enabled else 0
                if not enabled:
                    new_user_enabled = 0
            else:  # field == 'user_enabled'
                new_user_enabled = 1 if enabled else 0
                if enabled:
                    new_enabled = 1

            conn.execute("""
                UPDATE enabled_models
                SET enabled = ?, user_enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_enabled, new_user_enabled, model_id))

            return {
                'enabled': bool(new_enabled),
                'user_enabled': bool(new_user_enabled),
            }

    # -------------------------------------------------------------------------
    # Model listing variants
    # -------------------------------------------------------------------------

    def list_all_models_full(self) -> List[Dict[str, Any]]:
        """List all models including supports_img2img column.

        Returns:
            List of model dicts. Empty list if table doesn't exist.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='enabled_models'
            """)
            if not cursor.fetchone():
                return []

            cursor = conn.execute("""
                SELECT id, provider, model, enabled, user_enabled, display_name, notes,
                       supports_reasoning, supports_json_mode, supports_image_gen,
                       supports_img2img, sort_order, updated_at
                FROM enabled_models
                ORDER BY provider, sort_order
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_enabled_image_models(self) -> List[Dict[str, Any]]:
        """Get enabled models that support image generation.

        Returns:
            List of dicts with provider, model, display_name, supports_img2img.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT provider, model, display_name, supports_img2img
                FROM enabled_models
                WHERE enabled = 1 AND supports_image_gen = 1
                ORDER BY provider, sort_order
            """)
            return [dict(row) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Cost tiers
    # -------------------------------------------------------------------------

    def get_model_cost_tiers(self) -> Dict[str, Dict[str, str]]:
        """Calculate cost tier labels from model_pricing table.

        Tiers based on output token cost per 1M:
        - free: <= $0.10
        - $: < $1.00
        - $$: <= $5.00
        - $$$: <= $20.00
        - $$$$: > $20.00

        Returns:
            Dict mapping provider -> model -> tier string.
        """
        tiers: Dict[str, Dict[str, str]] = {}

        model_aliases = {
            'xai': {
                'grok-4-fast': 'grok-4-fast-reasoning',
            }
        }

        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT provider, model, cost FROM model_pricing
                    WHERE unit = 'output_tokens_1m'
                      AND (valid_from IS NULL OR valid_from <= datetime('now'))
                      AND (valid_until IS NULL OR valid_until > datetime('now'))
                """)

                for provider, model, cost in cursor:
                    if provider not in tiers:
                        tiers[provider] = {}

                    if cost <= 0.10:
                        tier = "free"
                    elif cost < 1.00:
                        tier = "$"
                    elif cost <= 5.00:
                        tier = "$$"
                    elif cost <= 20.00:
                        tier = "$$$"
                    else:
                        tier = "$$$$"

                    tiers[provider][model] = tier

                # Apply model aliases
                for provider, aliases in model_aliases.items():
                    if provider in tiers:
                        for ui_model, pricing_model in aliases.items():
                            if pricing_model in tiers[provider]:
                                tiers[provider][ui_model] = tiers[provider][pricing_model]

        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            pass

        return tiers

    # -------------------------------------------------------------------------
    # Enabled models maps
    # -------------------------------------------------------------------------

    def get_enabled_models_map(self) -> Dict[Tuple[str, str], bool]:
        """Get map of (provider, model) -> enabled for user-facing features.

        Both enabled and user_enabled must be True.
        Returns empty dict if table doesn't exist.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='enabled_models'
                """)
                if not cursor.fetchone():
                    return {}

                cursor = conn.execute("""
                    SELECT provider, model, enabled, user_enabled FROM enabled_models
                """)
                return {
                    (row[0], row[1]): bool(row[2]) and bool(row[3] if row[3] is not None else True)
                    for row in cursor.fetchall()
                }
        except sqlite3.Error:
            return {}

    def get_system_enabled_models_map(self) -> Dict[Tuple[str, str], bool]:
        """Get map of (provider, model) -> enabled for system/admin features.

        Only checks system enabled status (ignores user_enabled).
        Returns empty dict if table doesn't exist.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='enabled_models'
                """)
                if not cursor.fetchone():
                    return {}

                cursor = conn.execute("""
                    SELECT provider, model, enabled FROM enabled_models
                """)
                return {
                    (row[0], row[1]): bool(row[2])
                    for row in cursor.fetchall()
                }
        except sqlite3.Error:
            return {}

    def get_model_capabilities_map(self) -> Dict[Tuple[str, str], Dict[str, bool]]:
        """Get map of (provider, model) -> capability flags.

        Returns model-level capabilities from enabled_models table.
        Checks for supports_img2img column existence via PRAGMA.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("PRAGMA table_info(enabled_models)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'supports_img2img' not in columns:
                    return {}

                cursor = conn.execute("""
                    SELECT provider, model, supports_reasoning, supports_json_mode,
                           supports_image_gen, supports_img2img
                    FROM enabled_models
                """)
                return {
                    (row[0], row[1]): {
                        'supports_reasoning': bool(row[2]),
                        'supports_json_mode': bool(row[3]),
                        'supports_image_generation': bool(row[4]),
                        'supports_img2img': bool(row[5]) if row[5] is not None else False,
                    }
                    for row in cursor.fetchall()
                }
        except sqlite3.Error:
            return {}

    def check_model_supports_img2img(self, provider: str, model: str) -> bool:
        """Check if a specific model supports image-to-image generation."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT supports_img2img FROM enabled_models WHERE provider = ? AND model = ?",
                (provider, model)
            )
            row = cursor.fetchone()
            return bool(row['supports_img2img']) if row else False

    # -------------------------------------------------------------------------
    # Pricing management
    # -------------------------------------------------------------------------

    def list_pricing(self, provider: Optional[str] = None, model: Optional[str] = None,
                     current_only: bool = False) -> List[Dict[str, Any]]:
        """List pricing entries with optional filters.

        Args:
            provider: Filter by provider name.
            model: Filter by model name.
            current_only: If True, only return currently valid prices.

        Returns:
            List of pricing row dicts.
        """
        with self._get_connection() as conn:
            query = "SELECT * FROM model_pricing WHERE 1=1"
            params: List[Any] = []

            if provider:
                query += " AND provider = ?"
                params.append(provider)
            if model:
                query += " AND model = ?"
                params.append(model)
            if current_only:
                query += " AND (valid_from IS NULL OR valid_from <= datetime('now'))"
                query += " AND (valid_until IS NULL OR valid_until > datetime('now'))"

            query += " ORDER BY provider, model, unit, valid_from DESC"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def add_pricing(self, provider: str, model: str, unit: str, cost: float,
                    valid_from: str, notes: Optional[str] = None) -> None:
        """Add a pricing entry, expiring any current price for the same SKU.

        Args:
            provider: Provider name.
            model: Model name.
            unit: Pricing unit (e.g. 'input_tokens_1m').
            cost: Cost in USD.
            valid_from: Effective date string.
            notes: Optional notes.
        """
        with self._get_connection() as conn:
            # Expire any current pricing for this SKU
            conn.execute("""
                UPDATE model_pricing
                SET valid_until = ?
                WHERE provider = ? AND model = ? AND unit = ?
                  AND valid_until IS NULL
            """, (valid_from, provider, model, unit))

            # Insert new pricing
            conn.execute("""
                INSERT INTO model_pricing (provider, model, unit, cost, valid_from, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (provider, model, unit, cost, valid_from, notes))

    def bulk_add_pricing(self, entries: List[Dict[str, Any]],
                         expire_existing: bool = True) -> Tuple[int, List[Dict[str, Any]]]:
        """Add multiple pricing entries at once.

        Args:
            entries: List of dicts with provider, model, unit, cost, and optional valid_from/notes.
            expire_existing: If True, expire existing prices for matching SKUs.

        Returns:
            Tuple of (added_count, list of error dicts).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        added = 0
        errors: List[Dict[str, Any]] = []

        with self._get_connection() as conn:
            for entry in entries:
                try:
                    provider = entry['provider']
                    model = entry['model']
                    unit = entry['unit']
                    try:
                        cost = float(entry['cost'])
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid cost value '{entry.get('cost')}': must be a number")
                    valid_from = entry.get('valid_from') or now
                    notes = entry.get('notes')

                    if expire_existing:
                        conn.execute("""
                            UPDATE model_pricing SET valid_until = ?
                            WHERE provider = ? AND model = ? AND unit = ? AND valid_until IS NULL
                        """, (valid_from, provider, model, unit))

                    conn.execute("""
                        INSERT INTO model_pricing (provider, model, unit, cost, valid_from, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (provider, model, unit, cost, valid_from, notes))
                    added += 1
                except Exception as e:
                    errors.append({'entry': entry, 'error': str(e)})

        return added, errors

    def delete_pricing(self, pricing_id: int) -> bool:
        """Delete a pricing entry by ID.

        Returns:
            True if the entry was found and deleted, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM model_pricing WHERE id = ?", (pricing_id,))
            return cursor.rowcount > 0

    def list_providers_with_counts(self) -> List[Dict[str, Any]]:
        """List all providers with their model and SKU counts."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT provider, COUNT(DISTINCT model) as model_count, COUNT(*) as sku_count
                FROM model_pricing
                WHERE valid_until IS NULL OR valid_until > datetime('now')
                GROUP BY provider
                ORDER BY provider
            """)
            return [dict(row) for row in cursor.fetchall()]

    def list_models_for_provider(self, provider: str) -> List[str]:
        """List all current model names for a provider.

        Args:
            provider: Provider name.

        Returns:
            Sorted list of model name strings.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT model FROM model_pricing
                WHERE provider = ? AND (valid_until IS NULL OR valid_until > datetime('now'))
                ORDER BY model
            """, (provider,))
            return [row['model'] for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Storage statistics
    # -------------------------------------------------------------------------

    def get_storage_stats(self, categories: Dict[str, List[str]]) -> dict:
        """Calculate database storage statistics by table and category.

        Args:
            categories: Dict mapping category name to list of table names.

        Returns:
            Dict with 'total_bytes', 'total_mb', 'categories' (with rows/bytes/percentage),
            and 'tables' (with rows/bytes per table).
        """
        total_bytes = Path(self.db_path).stat().st_size

        # Build whitelist from known categories for defensive SQL
        allowed_tables: set = set()
        for table_list in categories.values():
            allowed_tables.update(table_list)
        allowed_tables.add('experiments')

        with self._get_connection() as conn:
            # Get row counts and estimate sizes for each table
            table_stats: Dict[str, Dict[str, int]] = {}
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """)
            tables = [row['name'] for row in cursor.fetchall()]

            for table in tables:
                if table not in allowed_tables:
                    continue

                try:
                    cursor = conn.execute(f'SELECT COUNT(*) as cnt FROM "{table}"')
                    count = cursor.fetchone()['cnt']

                    try:
                        cursor = conn.execute(
                            "SELECT SUM(pgsize) as size FROM dbstat WHERE name=?",
                            (table,)
                        )
                        size_row = cursor.fetchone()
                        size = size_row['size'] if size_row and size_row['size'] else 0
                    except sqlite3.OperationalError:
                        size = 0

                    table_stats[table] = {'rows': count, 'bytes': size}
                except sqlite3.OperationalError:
                    table_stats[table] = {'rows': 0, 'bytes': 0}

            # Aggregate by category
            category_stats: Dict[str, Dict[str, Any]] = {}
            categorized_tables: set = set()

            for category, table_list in categories.items():
                rows = 0
                bytes_est = 0
                for table in table_list:
                    if table in table_stats:
                        rows += table_stats[table]['rows']
                        bytes_est += table_stats[table]['bytes']
                        categorized_tables.add(table)
                category_stats[category] = {'rows': rows, 'bytes': bytes_est}

            # Add 'other' category for uncategorized tables
            other_rows = 0
            other_bytes = 0
            for table, stats in table_stats.items():
                if table not in categorized_tables:
                    other_rows += stats['rows']
                    other_bytes += stats['bytes']
            if other_rows > 0 or other_bytes > 0:
                category_stats['other'] = {'rows': other_rows, 'bytes': other_bytes}

            # Calculate percentages
            total_tracked_bytes = sum(cat['bytes'] for cat in category_stats.values())
            if total_tracked_bytes == 0:
                total_rows = sum(cat['rows'] for cat in category_stats.values())
                for category in category_stats:
                    if total_rows > 0:
                        pct = (category_stats[category]['rows'] / total_rows) * 100
                        category_stats[category]['bytes'] = int(total_bytes * pct / 100)
                    category_stats[category]['percentage'] = round(
                        (category_stats[category]['rows'] / total_rows * 100) if total_rows > 0 else 0, 1
                    )
            else:
                for category in category_stats:
                    category_stats[category]['percentage'] = round(
                        (category_stats[category]['bytes'] / total_bytes * 100), 1
                    )

            return {
                'total_bytes': total_bytes,
                'total_mb': round(total_bytes / 1024 / 1024, 2),
                'categories': category_stats,
                'tables': table_stats,
            }
