"""LLM model management repository — enabled models and provider queries."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
            cursor = conn.execute(
                """
                UPDATE enabled_models
                SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (1 if enabled else 0, model_id),
            )
            return cursor.rowcount > 0

    def update_model_details(
        self, model_id: int, display_name: str = None, notes: str = None
    ) -> bool:
        """Update display name and notes for a model.

        Returns:
            True if model was found and updated, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE enabled_models
                SET display_name = COALESCE(?, display_name),
                    notes = COALESCE(?, notes),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (display_name, notes, model_id),
            )
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
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as total_calls,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COALESCE(AVG(latency_ms), 0) as avg_latency,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0), 0) as error_rate
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
            """,
                (date_modifier,),
            )
            return dict(cursor.fetchone())

    # -------------------------------------------------------------------------
    # Cost analytics — owner / call-type / model / time-series breakdowns
    # -------------------------------------------------------------------------
    #
    # All methods read the pre-computed `estimated_cost` (USD) written at
    # insert time by UsageTracker. NULL costs (missing pricing SKU) collapse
    # to 0 via COALESCE — same convention as the budget gate. `owner_id` may
    # be NULL for system-initiated calls; we surface those as '(system)' so
    # they never silently vanish from the rollup.

    def get_cost_by_owner(self, date_modifier: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Cost + volume aggregated per owner for a time range.

        Args:
            date_modifier: SQLite date modifier, e.g. '-7 days'.
            limit: Max owners returned (highest spend first).

        Returns:
            List of dicts: owner_id, total_cost, total_calls, image_calls,
            error_calls, input_tokens, output_tokens.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(owner_id, ''), '(system)') as owner_id,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls,
                    COALESCE(SUM(CASE WHEN call_type = 'image_generation' THEN 1 ELSE 0 END), 0) as image_calls,
                    COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), 0) as error_calls,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY COALESCE(NULLIF(owner_id, ''), '(system)')
                ORDER BY total_cost DESC
                LIMIT ?
                """,
                (date_modifier, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_cost_by_call_type(
        self, date_modifier: str, owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Cost + volume aggregated per call_type, optionally scoped to one owner.

        Returns:
            List of dicts: call_type, total_cost, total_calls, avg_latency,
            input_tokens, output_tokens, cached_tokens, reasoning_tokens,
            image_count. Sorted by total_cost desc.
        """
        params: List[Any] = [date_modifier]
        owner_clause = ""
        if owner_id is not None:
            owner_clause = " AND COALESCE(NULLIF(owner_id, ''), '(system)') = ?"
            params.append(owner_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    call_type,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls,
                    COALESCE(AVG(latency_ms), 0) as avg_latency,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(cached_tokens), 0) as cached_tokens,
                    COALESCE(SUM(reasoning_tokens), 0) as reasoning_tokens,
                    COALESCE(SUM(image_count), 0) as image_count
                FROM api_usage
                WHERE created_at >= datetime('now', ?){owner_clause}
                GROUP BY call_type
                ORDER BY total_cost DESC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_cost_by_model(
        self, date_modifier: str, owner_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Cost + volume aggregated per provider/model, optionally scoped to one owner.

        Returns:
            List of dicts: provider, model, total_cost, total_calls,
            input_tokens, output_tokens. Sorted by total_cost desc.
        """
        params: List[Any] = [date_modifier]
        owner_clause = ""
        if owner_id is not None:
            owner_clause = " AND COALESCE(NULLIF(owner_id, ''), '(system)') = ?"
            params.append(owner_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    provider,
                    model,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens
                FROM api_usage
                WHERE created_at >= datetime('now', ?){owner_clause}
                GROUP BY provider, model
                ORDER BY total_cost DESC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_cost_by_game(
        self, date_modifier: str, owner_id: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Cost + volume aggregated per game, optionally scoped to one owner.

        Only rows tied to a game (`game_id IS NOT NULL`) — non-game work like
        personality generation has no game to attribute to. `owner_id` is the
        representative (non-system) owner for the game, so the all-owners view
        can show whose game it is.

        Returns:
            List of dicts: game_id, owner_id, total_cost, total_calls,
            max_hand (highest hand_number seen). Sorted by total_cost desc.
        """
        params: List[Any] = [date_modifier]
        owner_clause = ""
        if owner_id is not None:
            owner_clause = " AND COALESCE(NULLIF(owner_id, ''), '(system)') = ?"
            params.append(owner_id)
        params.append(limit)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    game_id,
                    COALESCE(MAX(NULLIF(owner_id, '')), '(system)') as owner_id,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls,
                    MAX(hand_number) as max_hand
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                  AND game_id IS NOT NULL{owner_clause}
                GROUP BY game_id
                ORDER BY total_cost DESC
                LIMIT ?
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_uncosted_calls(self, date_modifier: str) -> Dict[str, Any]:
        """Successful calls with NULL estimated_cost — i.e. silent pricing gaps.

        A non-error call with no cost means the model had no matching pricing
        SKU at insert time, so it counts as $0 in every rollup. Surfacing these
        lets a new/unpriced model be caught instead of silently undercounting.

        Returns:
            {'total': int, 'by_model': [{provider, model, calls, last_seen}]}.
        """
        with self._get_connection() as conn:
            total = conn.execute(
                """SELECT COUNT(*) FROM api_usage
                   WHERE status != 'error' AND estimated_cost IS NULL
                     AND created_at >= datetime('now', ?)""",
                (date_modifier,),
            ).fetchone()[0]
            by_model = conn.execute(
                """SELECT provider, model, COUNT(*) as calls,
                          MAX(date(created_at)) as last_seen
                   FROM api_usage
                   WHERE status != 'error' AND estimated_cost IS NULL
                     AND created_at >= datetime('now', ?)
                   GROUP BY provider, model ORDER BY calls DESC LIMIT 20""",
                (date_modifier,),
            ).fetchall()
            return {'total': total, 'by_model': [dict(r) for r in by_model]}

    # Upper bound on gap-filled buckets, so an 'all'-range query over a very
    # old dataset can't return a pathological time-series. Daily over ~5 years
    # or hourly over ~80 days both stay under this.
    _MAX_TIMESERIES_BUCKETS = 2000

    def get_cost_timeseries(
        self, date_modifier: str, owner_id: Optional[str] = None, bucket: str = "day"
    ) -> List[Dict[str, Any]]:
        """Cost + call volume bucketed over time for a trend chart.

        Empty buckets are gap-filled with zeros so the chart reads as a true
        timeline (no day with zero calls silently collapses, which otherwise
        makes the area chart connect across the gap and misstate the trend).

        Args:
            date_modifier: SQLite date modifier, e.g. '-7 days'.
            owner_id: Optional owner scope.
            bucket: 'hour' or 'day' — controls the strftime grouping.

        Returns:
            List of dicts: period (ISO-ish string), total_cost, total_calls.
            Ordered chronologically, with a row per bucket in the window.
        """
        fmt = "%Y-%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"
        params: List[Any] = [fmt, date_modifier]
        owner_clause = ""
        if owner_id is not None:
            owner_clause = " AND COALESCE(NULLIF(owner_id, ''), '(system)') = ?"
            params.append(owner_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    strftime(?, created_at) as period,
                    COALESCE(SUM(estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls
                FROM api_usage
                WHERE created_at >= datetime('now', ?){owner_clause}
                GROUP BY period
                ORDER BY period ASC
                """,
                params,
            )
            agg = {row['period']: dict(row) for row in cursor.fetchall()}
            if not agg:
                return []

            # Resolve the window bounds from SQLite itself so 'now' matches the
            # UTC clock the rows were stamped with — no Python-side timezone gap.
            bounds = conn.execute(
                "SELECT strftime(?, datetime('now', ?)) AS start, strftime(?, 'now') AS end",
                (fmt, date_modifier, fmt),
            ).fetchone()

        periods = self._fill_periods(bounds['start'], bounds['end'], min(agg), fmt, bucket)
        return [
            {
                'period': p,
                'total_cost': agg[p]['total_cost'] if p in agg else 0.0,
                'total_calls': agg[p]['total_calls'] if p in agg else 0,
            }
            for p in periods
        ]

    @classmethod
    def _fill_periods(
        cls, start_str: str, end_str: str, earliest: str, fmt: str, bucket: str
    ) -> List[str]:
        """Every bucket label from the window start to end, inclusive.

        Fixed windows (24h/7d/30d) are filled edge-to-edge so leading/trailing
        idle buckets show as zeros. The unbounded 'all' window (-100 years)
        would overflow the bucket cap, so in that case the start is pulled
        forward to the earliest real data point instead of filling a century of
        empties. Lexical compares are chronological (zero-padded ISO format).
        """
        step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
        start = datetime.strptime(start_str, fmt)
        end = datetime.strptime(end_str, fmt)
        if start > end:
            return []
        # Only clamp to the first data point when the full window won't fit —
        # i.e. the 'all' case. Fixed windows keep their full span (with zeros).
        if int((end - start) / step) + 1 > cls._MAX_TIMESERIES_BUCKETS:
            start = max(start, datetime.strptime(earliest, fmt))
        out: List[str] = []
        cur = start
        while cur <= end and len(out) < cls._MAX_TIMESERIES_BUCKETS:
            out.append(cur.strftime(fmt))
            cur += step
        return out

    def get_recent_calls(
        self,
        date_modifier: str,
        owner_id: Optional[str] = None,
        call_type: Optional[str] = None,
        game_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Raw individual api_usage rows for drill-down detail.

        Filters by time range and optional owner_id / call_type / game_id.
        Returns the most recent rows first, capped at `limit`.
        """
        params: List[Any] = [date_modifier]
        clauses = ""
        if owner_id is not None:
            clauses += " AND COALESCE(NULLIF(owner_id, ''), '(system)') = ?"
            params.append(owner_id)
        if call_type is not None:
            clauses += " AND call_type = ?"
            params.append(call_type)
        if game_id is not None:
            clauses += " AND game_id = ?"
            params.append(game_id)
        params.append(limit)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT
                    id, created_at, owner_id, player_name, game_id, hand_number,
                    call_type, provider, model, reasoning_effort,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    image_count, image_size, latency_ms, status, finish_reason,
                    error_code, estimated_cost
                FROM api_usage
                WHERE created_at >= datetime('now', ?){clauses}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

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
                "SELECT enabled, user_enabled FROM enabled_models WHERE id = ?", (model_id,)
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

            conn.execute(
                """
                UPDATE enabled_models
                SET enabled = ?, user_enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (new_enabled, new_user_enabled, model_id),
            )

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
                return {(row[0], row[1]): bool(row[2]) for row in cursor.fetchall()}
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
                (provider, model),
            )
            row = cursor.fetchone()
            return bool(row['supports_img2img']) if row else False

    # -------------------------------------------------------------------------
    # Pricing management
    # -------------------------------------------------------------------------

    def list_pricing(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        current_only: bool = False,
    ) -> List[Dict[str, Any]]:
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

    def add_pricing(
        self,
        provider: str,
        model: str,
        unit: str,
        cost: float,
        valid_from: str,
        notes: Optional[str] = None,
    ) -> None:
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
            conn.execute(
                """
                UPDATE model_pricing
                SET valid_until = ?
                WHERE provider = ? AND model = ? AND unit = ?
                  AND valid_until IS NULL
            """,
                (valid_from, provider, model, unit),
            )

            # Insert new pricing
            conn.execute(
                """
                INSERT INTO model_pricing (provider, model, unit, cost, valid_from, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (provider, model, unit, cost, valid_from, notes),
            )

    def bulk_add_pricing(
        self, entries: List[Dict[str, Any]], expire_existing: bool = True
    ) -> Tuple[int, List[Dict[str, Any]]]:
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
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"Invalid cost value '{entry.get('cost')}': must be a number"
                        ) from exc
                    valid_from = entry.get('valid_from') or now
                    notes = entry.get('notes')

                    if expire_existing:
                        conn.execute(
                            """
                            UPDATE model_pricing SET valid_until = ?
                            WHERE provider = ? AND model = ? AND unit = ? AND valid_until IS NULL
                        """,
                            (valid_from, provider, model, unit),
                        )

                    conn.execute(
                        """
                        INSERT INTO model_pricing (provider, model, unit, cost, valid_from, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (provider, model, unit, cost, valid_from, notes),
                    )
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
            cursor = conn.execute(
                """
                SELECT DISTINCT model FROM model_pricing
                WHERE provider = ? AND (valid_until IS NULL OR valid_until > datetime('now'))
                ORDER BY model
            """,
                (provider,),
            )
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
                            "SELECT SUM(pgsize) as size FROM dbstat WHERE name=?", (table,)
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
                        (category_stats[category]['rows'] / total_rows * 100)
                        if total_rows > 0
                        else 0,
                        1,
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
