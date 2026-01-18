"""
SQLite implementation of debug repository.
This repository handles prompt_captures and player_decision_analysis tables.
IMPORTANT: These tables contain historical data that should be preserved during migration.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import PromptCaptureEntity, DecisionAnalysisEntity
from ..serialization import to_json, from_json


class SQLiteDebugRepository:
    """SQLite implementation of DebugRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save_prompt_capture(self, capture: PromptCaptureEntity) -> int:
        """Save a prompt capture. Returns the capture ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO prompt_captures (
                    game_id, hand_number, player_name, action_taken,
                    system_prompt, user_prompt, raw_response, parsed_response,
                    model_used, temperature, latency_ms, timestamp,
                    source, experiment_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture.game_id,
                    capture.hand_number,
                    capture.player_name,
                    capture.action_taken,
                    capture.system_prompt,
                    capture.user_prompt,
                    capture.raw_response,
                    to_json(capture.parsed_response) if capture.parsed_response else None,
                    capture.model_used,
                    capture.temperature,
                    capture.latency_ms,
                    capture.timestamp.isoformat(),
                    capture.source,
                    capture.experiment_id,
                ),
            )
            return cursor.lastrowid

    def get_prompt_capture(self, capture_id: int) -> Optional[PromptCaptureEntity]:
        """Get a prompt capture by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM prompt_captures WHERE id = ?",
            (capture_id,),
        )

        if not row:
            return None

        return self._row_to_capture_entity(row)

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PromptCaptureEntity]:
        """List prompt captures with optional filters."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)
        if source:
            conditions.append("source = ?")
            params.append(source)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        rows = self._db.fetch_all(
            f"""
            SELECT * FROM prompt_captures
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        )

        return [self._row_to_capture_entity(row) for row in rows]

    def get_prompt_capture_stats(
        self, game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get prompt capture statistics."""
        if game_id:
            row = self._db.fetch_one(
                """
                SELECT
                    COUNT(*) as total_captures,
                    COUNT(DISTINCT player_name) as unique_players,
                    COUNT(DISTINCT hand_number) as unique_hands,
                    AVG(latency_ms) as avg_latency_ms,
                    MIN(timestamp) as first_capture,
                    MAX(timestamp) as last_capture
                FROM prompt_captures
                WHERE game_id = ?
                """,
                (game_id,),
            )
        else:
            row = self._db.fetch_one(
                """
                SELECT
                    COUNT(*) as total_captures,
                    COUNT(DISTINCT player_name) as unique_players,
                    COUNT(DISTINCT game_id) as unique_games,
                    AVG(latency_ms) as avg_latency_ms,
                    MIN(timestamp) as first_capture,
                    MAX(timestamp) as last_capture
                FROM prompt_captures
                """
            )

        # Get breakdown by player
        if game_id:
            player_rows = self._db.fetch_all(
                """
                SELECT player_name, COUNT(*) as captures
                FROM prompt_captures
                WHERE game_id = ?
                GROUP BY player_name
                ORDER BY captures DESC
                """,
                (game_id,),
            )
        else:
            player_rows = self._db.fetch_all(
                """
                SELECT player_name, COUNT(*) as captures
                FROM prompt_captures
                GROUP BY player_name
                ORDER BY captures DESC
                LIMIT 20
                """
            )

        return {
            "total_captures": row["total_captures"] or 0,
            "unique_players": row["unique_players"] or 0,
            "unique_hands": row.get("unique_hands") or row.get("unique_games") or 0,
            "avg_latency_ms": row["avg_latency_ms"] or 0.0,
            "first_capture": row["first_capture"],
            "last_capture": row["last_capture"],
            "by_player": [
                {"player_name": r["player_name"], "captures": r["captures"]}
                for r in player_rows
            ],
        }

    def delete_prompt_captures(
        self,
        game_id: Optional[str] = None,
        before_date: Optional[datetime] = None,
    ) -> int:
        """Delete prompt captures. Returns count deleted."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if before_date:
            conditions.append("timestamp < ?")
            params.append(before_date.isoformat())

        if not conditions:
            # Safety: require at least one filter
            return 0

        where_clause = " AND ".join(conditions)

        with self._db.transaction() as conn:
            # First delete related decision analyses
            conn.execute(
                f"""
                DELETE FROM player_decision_analysis
                WHERE prompt_capture_id IN (
                    SELECT id FROM prompt_captures WHERE {where_clause}
                )
                """,
                tuple(params),
            )

            # Then delete the captures
            cursor = conn.execute(
                f"DELETE FROM prompt_captures WHERE {where_clause}",
                tuple(params),
            )
            return cursor.rowcount

    def save_decision_analysis(self, analysis: DecisionAnalysisEntity) -> int:
        """Save decision analysis. Returns the analysis ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO player_decision_analysis (
                    prompt_capture_id, game_id, player_name, request_id,
                    hand_number, ev_analysis, gto_deviation,
                    personality_alignment, decision_quality_score,
                    analysis_metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis.prompt_capture_id,
                    analysis.game_id,
                    analysis.player_name,
                    analysis.request_id,
                    analysis.hand_number,
                    to_json(analysis.ev_analysis),
                    to_json(analysis.gto_deviation) if analysis.gto_deviation else None,
                    to_json(analysis.personality_alignment),
                    analysis.decision_quality_score,
                    to_json(analysis.analysis_metadata),
                    analysis.created_at.isoformat(),
                ),
            )
            return cursor.lastrowid

    def get_decision_analysis(self, analysis_id: int) -> Optional[DecisionAnalysisEntity]:
        """Get decision analysis by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM player_decision_analysis WHERE id = ?",
            (analysis_id,),
        )

        if not row:
            return None

        return self._row_to_analysis_entity(row)

    def get_decision_analysis_by_capture(
        self, capture_id: int
    ) -> Optional[DecisionAnalysisEntity]:
        """Get decision analysis by prompt capture ID."""
        row = self._db.fetch_one(
            "SELECT * FROM player_decision_analysis WHERE prompt_capture_id = ?",
            (capture_id,),
        )

        if not row:
            return None

        return self._row_to_analysis_entity(row)

    def list_decision_analyses(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[DecisionAnalysisEntity]:
        """List decision analyses with optional filters."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        rows = self._db.fetch_all(
            f"""
            SELECT * FROM player_decision_analysis
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        )

        return [self._row_to_analysis_entity(row) for row in rows]

    def _row_to_capture_entity(self, row) -> PromptCaptureEntity:
        """Convert a database row to a PromptCaptureEntity."""
        return PromptCaptureEntity(
            id=row["id"],
            game_id=row["game_id"],
            hand_number=row["hand_number"],
            player_name=row["player_name"],
            action_taken=row["action_taken"],
            system_prompt=row["system_prompt"],
            user_prompt=row["user_prompt"],
            raw_response=row["raw_response"],
            parsed_response=from_json(row["parsed_response"])
            if row["parsed_response"]
            else None,
            model_used=row["model_used"],
            temperature=row["temperature"],
            latency_ms=row["latency_ms"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            source=row["source"],
            experiment_id=row["experiment_id"],
        )

    def _row_to_analysis_entity(self, row) -> DecisionAnalysisEntity:
        """Convert a database row to a DecisionAnalysisEntity."""
        return DecisionAnalysisEntity(
            id=row["id"],
            prompt_capture_id=row["prompt_capture_id"],
            game_id=row["game_id"],
            player_name=row["player_name"],
            request_id=row["request_id"],
            hand_number=row["hand_number"],
            ev_analysis=from_json(row["ev_analysis"]) or {},
            gto_deviation=from_json(row["gto_deviation"])
            if row["gto_deviation"]
            else None,
            personality_alignment=from_json(row["personality_alignment"]) or {},
            decision_quality_score=row["decision_quality_score"],
            analysis_metadata=from_json(row["analysis_metadata"]) or {},
            created_at=datetime.fromisoformat(row["created_at"]),
        )
