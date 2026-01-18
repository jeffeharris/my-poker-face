"""
SQLite implementation of LLM tracking repository.
This repository handles api_usage, model_pricing, and enabled_models tables.
IMPORTANT: These tables contain historical data that should be preserved during migration.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import ApiUsageEntity, ModelPricingEntity, EnabledModelEntity
from ..serialization import to_json, from_json


class SQLiteLLMTrackingRepository:
    """SQLite implementation of LLMTrackingRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save_usage(self, usage: ApiUsageEntity) -> int:
        """Save API usage record. Returns the record ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO api_usage (
                    game_id, owner_id, player_name, hand_number,
                    call_type, model, provider,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    latency_ms, input_cost, output_cost, total_cost, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    usage.game_id,
                    usage.owner_id,
                    usage.player_name,
                    usage.hand_number,
                    usage.call_type,
                    usage.model,
                    usage.provider,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_tokens,
                    usage.reasoning_tokens,
                    usage.latency_ms,
                    usage.input_cost,
                    usage.output_cost,
                    usage.total_cost,
                    usage.timestamp.isoformat(),
                ),
            )
            return cursor.lastrowid

    def get_usage_stats(
        self,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get aggregated usage statistics."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if owner_id:
            conditions.append("owner_id = ?")
            params.append(owner_id)
        if start_date:
            conditions.append("timestamp >= ?")
            params.append(start_date.isoformat())
        if end_date:
            conditions.append("timestamp <= ?")
            params.append(end_date.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        row = self._db.fetch_one(
            f"""
            SELECT
                COUNT(*) as total_calls,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(cached_tokens) as total_cached_tokens,
                SUM(reasoning_tokens) as total_reasoning_tokens,
                SUM(total_cost) as total_cost,
                AVG(latency_ms) as avg_latency_ms
            FROM api_usage
            WHERE {where_clause}
            """,
            tuple(params),
        )

        # Get breakdown by call_type
        breakdown_rows = self._db.fetch_all(
            f"""
            SELECT
                call_type,
                COUNT(*) as calls,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(total_cost) as cost
            FROM api_usage
            WHERE {where_clause}
            GROUP BY call_type
            ORDER BY cost DESC
            """,
            tuple(params),
        )

        # Get breakdown by model
        model_rows = self._db.fetch_all(
            f"""
            SELECT
                model,
                provider,
                COUNT(*) as calls,
                SUM(total_cost) as cost
            FROM api_usage
            WHERE {where_clause}
            GROUP BY model, provider
            ORDER BY cost DESC
            """,
            tuple(params),
        )

        return {
            "total_calls": row["total_calls"] or 0,
            "total_input_tokens": row["total_input_tokens"] or 0,
            "total_output_tokens": row["total_output_tokens"] or 0,
            "total_cached_tokens": row["total_cached_tokens"] or 0,
            "total_reasoning_tokens": row["total_reasoning_tokens"] or 0,
            "total_cost": row["total_cost"] or 0.0,
            "avg_latency_ms": row["avg_latency_ms"] or 0.0,
            "by_call_type": [
                {
                    "call_type": r["call_type"],
                    "calls": r["calls"],
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                    "cost": r["cost"] or 0.0,
                }
                for r in breakdown_rows
            ],
            "by_model": [
                {
                    "model": r["model"],
                    "provider": r["provider"],
                    "calls": r["calls"],
                    "cost": r["cost"] or 0.0,
                }
                for r in model_rows
            ],
        }

    def save_model_pricing(self, pricing: ModelPricingEntity) -> None:
        """Save or update model pricing."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO model_pricing (
                    model, provider, input_price_per_1m, output_price_per_1m,
                    cached_input_price_per_1m, reasoning_price_per_1m, effective_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model, provider) DO UPDATE SET
                    input_price_per_1m = excluded.input_price_per_1m,
                    output_price_per_1m = excluded.output_price_per_1m,
                    cached_input_price_per_1m = excluded.cached_input_price_per_1m,
                    reasoning_price_per_1m = excluded.reasoning_price_per_1m,
                    effective_date = excluded.effective_date
                """,
                (
                    pricing.model,
                    pricing.provider,
                    pricing.input_price_per_1m,
                    pricing.output_price_per_1m,
                    pricing.cached_input_price_per_1m,
                    pricing.reasoning_price_per_1m,
                    pricing.effective_date.isoformat(),
                ),
            )

    def get_model_pricing(
        self, model: str, provider: str
    ) -> Optional[ModelPricingEntity]:
        """Get pricing for a specific model."""
        row = self._db.fetch_one(
            "SELECT * FROM model_pricing WHERE model = ? AND provider = ?",
            (model, provider),
        )

        if not row:
            return None

        return ModelPricingEntity(
            id=row["id"],
            model=row["model"],
            provider=row["provider"],
            input_price_per_1m=row["input_price_per_1m"],
            output_price_per_1m=row["output_price_per_1m"],
            cached_input_price_per_1m=row["cached_input_price_per_1m"],
            reasoning_price_per_1m=row["reasoning_price_per_1m"],
            effective_date=datetime.fromisoformat(row["effective_date"]),
        )

    def get_all_model_pricing(self) -> List[ModelPricingEntity]:
        """Get all model pricing records."""
        rows = self._db.fetch_all(
            "SELECT * FROM model_pricing ORDER BY provider, model"
        )

        return [
            ModelPricingEntity(
                id=row["id"],
                model=row["model"],
                provider=row["provider"],
                input_price_per_1m=row["input_price_per_1m"],
                output_price_per_1m=row["output_price_per_1m"],
                cached_input_price_per_1m=row["cached_input_price_per_1m"],
                reasoning_price_per_1m=row["reasoning_price_per_1m"],
                effective_date=datetime.fromisoformat(row["effective_date"]),
            )
            for row in rows
        ]

    def save_enabled_model(self, model: EnabledModelEntity) -> None:
        """Save or update an enabled model."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO enabled_models (
                    model_id, provider, display_name, is_default, enabled_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model_id, provider) DO UPDATE SET
                    display_name = excluded.display_name,
                    is_default = excluded.is_default
                """,
                (
                    model.model_id,
                    model.provider,
                    model.display_name,
                    1 if model.is_default else 0,
                    model.enabled_at.isoformat(),
                ),
            )

    def get_enabled_models(self) -> List[EnabledModelEntity]:
        """Get all enabled models."""
        rows = self._db.fetch_all(
            "SELECT * FROM enabled_models ORDER BY provider, display_name"
        )

        return [
            EnabledModelEntity(
                id=row["id"],
                model_id=row["model_id"],
                provider=row["provider"],
                display_name=row["display_name"],
                is_default=bool(row["is_default"]),
                enabled_at=datetime.fromisoformat(row["enabled_at"]),
            )
            for row in rows
        ]

    def delete_enabled_model(self, model_id: str, provider: str) -> bool:
        """Delete an enabled model. Returns True if deleted."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM enabled_models WHERE model_id = ? AND provider = ?",
                (model_id, provider),
            )
            return cursor.rowcount > 0

    def get_enabled_models_by_provider(self) -> Dict[str, List[str]]:
        """Get enabled model IDs grouped by provider."""
        rows = self._db.fetch_all(
            "SELECT provider, model_id FROM enabled_models ORDER BY provider, display_name"
        )

        result: Dict[str, List[str]] = {}
        for row in rows:
            provider = row["provider"]
            if provider not in result:
                result[provider] = []
            result[provider].append(row["model_id"])

        return result
