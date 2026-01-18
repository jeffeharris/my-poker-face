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

    def get_prompt_capture(self, capture_id: int) -> Optional[dict]:
        """Get a prompt capture by ID. Returns dict for JSON compatibility."""
        row = self._db.fetch_one(
            "SELECT * FROM prompt_captures WHERE id = ?",
            (capture_id,),
        )

        if not row:
            return None

        return self._entity_to_dict(self._row_to_capture_entity(row))

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        source: Optional[str] = None,
        call_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        **kwargs,  # Accept extra filters for compatibility
    ) -> Dict[str, Any]:
        """List prompt captures with optional filters. Returns dict with 'captures' and 'total'."""
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
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) as count FROM prompt_captures WHERE {where_clause}",
            tuple(params),
        )
        total = count_row["count"] if count_row else 0

        # Get paginated results
        rows = self._db.fetch_all(
            f"""
            SELECT * FROM prompt_captures
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        )

        captures = [self._entity_to_dict(self._row_to_capture_entity(row)) for row in rows]
        return {'captures': captures, 'total': total}

    def get_prompt_capture_stats(
        self, game_id: Optional[str] = None, call_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get prompt capture statistics."""
        conditions = []
        params = []
        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        row = self._db.fetch_one(
            f"""
            SELECT
                COUNT(*) as total_captures,
                COUNT(DISTINCT player_name) as unique_players,
                COUNT(DISTINCT game_id) as unique_games,
                COUNT(DISTINCT hand_number) as unique_hands,
                AVG(latency_ms) as avg_latency_ms,
                MIN(timestamp) as first_capture,
                MAX(timestamp) as last_capture
            FROM prompt_captures
            {where_clause}
            """,
            tuple(params),
        )

        # Get breakdown by player
        player_rows = self._db.fetch_all(
            f"""
            SELECT player_name, COUNT(*) as captures
            FROM prompt_captures
            {where_clause}
            GROUP BY player_name
            ORDER BY captures DESC
            LIMIT 20
            """,
            tuple(params),
        )

        return {
            "total_captures": row["total_captures"] or 0,
            "unique_players": row["unique_players"] or 0,
            "unique_hands": row["unique_hands"] or 0,
            "unique_games": row["unique_games"] or 0,
            "avg_latency_ms": row["avg_latency_ms"] or 0.0,
            "first_capture": row["first_capture"],
            "last_capture": row["last_capture"],
            "by_player": [
                {"player_name": r["player_name"], "captures": r["captures"]}
                for r in player_rows
            ],
        }

    def update_prompt_capture_tags(
        self,
        capture_id: int,
        tags: List[str],
        notes: Optional[str] = None,
    ) -> bool:
        """Update tags and notes for a prompt capture."""
        with self._db.transaction() as conn:
            if notes is not None:
                cursor = conn.execute(
                    "UPDATE prompt_captures SET tags = ?, notes = ? WHERE id = ?",
                    (to_json(tags), notes, capture_id)
                )
            else:
                cursor = conn.execute(
                    "UPDATE prompt_captures SET tags = ? WHERE id = ?",
                    (to_json(tags), capture_id)
                )
            return cursor.rowcount > 0

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

    def get_decision_analysis(self, analysis_id: int) -> Optional[dict]:
        """Get decision analysis by ID. Returns dict for JSON compatibility."""
        row = self._db.fetch_one(
            "SELECT * FROM player_decision_analysis WHERE id = ?",
            (analysis_id,),
        )

        if not row:
            return None

        return self._entity_to_dict(self._row_to_analysis_entity(row))

    def get_decision_analysis_by_capture(
        self, capture_id: int
    ) -> Optional[dict]:
        """Get decision analysis by prompt capture ID. Returns dict for JSON compatibility."""
        row = self._db.fetch_one(
            "SELECT * FROM player_decision_analysis WHERE prompt_capture_id = ?",
            (capture_id,),
        )

        if not row:
            return None

        return self._entity_to_dict(self._row_to_analysis_entity(row))

    def list_decision_analyses(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        **kwargs,  # Accept extra filters for compatibility
    ) -> Dict[str, Any]:
        """List decision analyses with optional filters. Returns dict with 'analyses' and 'total'."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # Get total count
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) as count FROM player_decision_analysis WHERE {where_clause}",
            tuple(params),
        )
        total = count_row["count"] if count_row else 0

        rows = self._db.fetch_all(
            f"""
            SELECT * FROM player_decision_analysis
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        )

        analyses = [self._entity_to_dict(self._row_to_analysis_entity(row)) for row in rows]
        return {'analyses': analyses, 'total': total}

    def get_decision_analysis_stats(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregate statistics for decision analyses."""
        where_clause = "WHERE game_id = ?" if game_id else ""
        params = [game_id] if game_id else []

        # Count by quality
        rows = self._db.fetch_all(
            f"""
            SELECT decision_quality, COUNT(*) as count
            FROM player_decision_analysis {where_clause}
            GROUP BY decision_quality
            """,
            tuple(params),
        )
        by_quality = {row["decision_quality"]: row["count"] for row in rows}

        # Count by action
        rows = self._db.fetch_all(
            f"""
            SELECT action_taken, COUNT(*) as count
            FROM player_decision_analysis {where_clause}
            GROUP BY action_taken
            """,
            tuple(params),
        )
        by_action = {row["action_taken"]: row["count"] for row in rows}

        # Aggregate stats
        row = self._db.fetch_one(
            f"""
            SELECT
                COUNT(*) as total,
                COALESCE(SUM(ev_lost), 0) as total_ev_lost,
                COALESCE(AVG(equity), 0) as avg_equity,
                COALESCE(AVG(processing_ms), 0) as avg_processing_ms
            FROM player_decision_analysis {where_clause}
            """,
            tuple(params),
        )

        return {
            'total': row['total'] if row else 0,
            'by_quality': by_quality,
            'by_action': by_action,
            'total_ev_lost': row['total_ev_lost'] if row else 0,
            'avg_equity': row['avg_equity'] if row else 0,
            'avg_processing_ms': row['avg_processing_ms'] if row else 0,
        }

    def list_playground_captures(
        self,
        call_type: Optional[str] = None,
        provider: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        **kwargs,
    ) -> Dict[str, Any]:
        """List captures for the playground (filtered by call_type)."""
        conditions = []
        params = []

        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if provider:
            conditions.append("provider = ?")
            params.append(provider)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        # Get total count
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) as count FROM prompt_captures {where_clause}",
            tuple(params),
        )
        total = count_row["count"] if count_row else 0

        # Get paginated results
        rows = self._db.fetch_all(
            f"""
            SELECT * FROM prompt_captures
            {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        )

        captures = [self._entity_to_dict(self._row_to_capture_entity(row)) for row in rows]
        return {'captures': captures, 'total': total}

    def get_playground_capture_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics for all prompt captures."""
        # Count by call_type
        rows = self._db.fetch_all(
            """
            SELECT COALESCE(call_type, 'player_decision') as call_type, COUNT(*) as count
            FROM prompt_captures
            GROUP BY COALESCE(call_type, 'player_decision')
            ORDER BY count DESC
            """
        )
        by_call_type = {row["call_type"]: row["count"] for row in rows}

        # Count by provider
        rows = self._db.fetch_all(
            """
            SELECT COALESCE(provider, 'openai') as provider, COUNT(*) as count
            FROM prompt_captures
            GROUP BY COALESCE(provider, 'openai')
            ORDER BY count DESC
            """
        )
        by_provider = {row["provider"]: row["count"] for row in rows}

        # Total and date range
        row = self._db.fetch_one(
            """
            SELECT
                COUNT(*) as total,
                MIN(timestamp) as oldest,
                MAX(timestamp) as newest
            FROM prompt_captures
            """
        )

        return {
            'by_call_type': by_call_type,
            'by_provider': by_provider,
            'total': row['total'] if row else 0,
            'oldest': row['oldest'] if row else None,
            'newest': row['newest'] if row else None,
        }

    def cleanup_old_captures(self, retention_days: int) -> int:
        """Delete captures older than the retention period."""
        if retention_days <= 0:
            return 0

        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=retention_days)

        with self._db.transaction() as conn:
            # Delete related decision analyses first
            conn.execute(
                """
                DELETE FROM player_decision_analysis
                WHERE prompt_capture_id IN (
                    SELECT id FROM prompt_captures WHERE timestamp < ?
                )
                """,
                (cutoff_date.isoformat(),),
            )
            # Delete old captures
            cursor = conn.execute(
                "DELETE FROM prompt_captures WHERE timestamp < ?",
                (cutoff_date.isoformat(),),
            )
            return cursor.rowcount

    def _entity_to_dict(self, entity) -> dict:
        """Convert an entity to a dict for JSON serialization."""
        if entity is None:
            return None
        if isinstance(entity, PromptCaptureEntity):
            return {
                'id': entity.id,
                'game_id': entity.game_id,
                'hand_number': entity.hand_number,
                'player_name': entity.player_name,
                'action_taken': entity.action_taken,
                'system_prompt': entity.system_prompt,
                'user_prompt': entity.user_prompt,
                'user_message': entity.user_prompt,  # Alias for compatibility
                'raw_response': entity.raw_response,
                'ai_response': entity.raw_response,  # Alias for compatibility
                'parsed_response': entity.parsed_response,
                'model_used': entity.model_used,
                'model': entity.model_used,  # Alias
                'temperature': entity.temperature,
                'latency_ms': entity.latency_ms,
                'timestamp': entity.timestamp.isoformat() if entity.timestamp else None,
                'source': entity.source,
                'experiment_id': entity.experiment_id,
            }
        elif isinstance(entity, DecisionAnalysisEntity):
            return {
                'id': entity.id,
                'prompt_capture_id': entity.prompt_capture_id,
                'game_id': entity.game_id,
                'player_name': entity.player_name,
                'request_id': entity.request_id,
                'hand_number': entity.hand_number,
                'ev_analysis': entity.ev_analysis,
                'gto_deviation': entity.gto_deviation,
                'personality_alignment': entity.personality_alignment,
                'decision_quality_score': entity.decision_quality_score,
                'analysis_metadata': entity.analysis_metadata,
                'created_at': entity.created_at.isoformat() if entity.created_at else None,
            }
        return entity

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

    def get_game_snapshot(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Get a live game snapshot for monitoring.

        Returns:
            Dictionary with game state including players, cards, pot, psychology data,
            and LLM debug info, or None if game not found.
        """
        # Load game state
        game_row = self._db.fetch_one(
            "SELECT game_state_json, phase, updated_at FROM games WHERE game_id = ?",
            (game_id,),
        )
        if not game_row:
            return None

        try:
            state_dict = from_json(game_row["game_state_json"]) or {}
        except Exception:
            return None

        # Extract basic game info
        phase = game_row["phase"]
        pot = state_dict.get('pot', {})
        pot_total = pot.get('total', 0) if isinstance(pot, dict) else pot
        community_cards = state_dict.get('community_cards', [])
        current_player_idx = state_dict.get('current_player_idx', 0)

        # Load psychology data for all players
        psychology_data = self._load_all_controller_states(game_id)
        emotional_data = self._load_all_emotional_states(game_id)

        # Load LLM debug info from api_usage records per player
        llm_debug_rows = self._db.fetch_all(
            """
            SELECT player_name, provider, model, reasoning_effort,
                   COUNT(*) as total_calls,
                   AVG(latency_ms) as avg_latency_ms,
                   AVG(estimated_cost) as avg_cost
            FROM api_usage
            WHERE game_id = ?
            GROUP BY player_name
            """,
            (game_id,),
        )
        llm_debug_by_player = {}
        for row in llm_debug_rows:
            if row["player_name"]:
                llm_debug_by_player[row["player_name"]] = {
                    'provider': row["provider"],
                    'model': row["model"],
                    'reasoning_effort': row.get("reasoning_effort"),
                    'total_calls': row["total_calls"],
                    'avg_latency_ms': round(row["avg_latency_ms"] or 0, 2),
                    'avg_cost_per_call': round(row["avg_cost"] or 0, 6),
                }

        # Build player list
        players = []
        players_data = state_dict.get('players', [])
        for idx, p in enumerate(players_data):
            player_name = p.get('name', f'Player_{idx}')

            # Get psychology for this player
            ctrl_state = psychology_data.get(player_name, {})
            emo_state = emotional_data.get(player_name, {})

            # Merge tilt and emotional data into psychology
            tilt_state = ctrl_state.get('tilt_state', {}) if ctrl_state else {}
            psychology = {
                'narrative': emo_state.get('narrative', ''),
                'inner_voice': emo_state.get('inner_voice', ''),
                'tilt_level': round((tilt_state.get('tilt_level', 0) if tilt_state else 0) * 100),
                'tilt_category': tilt_state.get('category', 'none') if tilt_state else 'none',
                'tilt_source': tilt_state.get('source', '') if tilt_state else '',
            }

            players.append({
                'name': player_name,
                'stack': p.get('stack', 0),
                'bet': p.get('bet', 0),
                'hole_cards': p.get('hand', []),  # Always show cards in monitoring mode
                'is_folded': p.get('is_folded', False),
                'is_all_in': p.get('is_all_in', False),
                'is_current': idx == current_player_idx,
                'psychology': psychology,
                'llm_debug': llm_debug_by_player.get(player_name, {}),
            })

        # Get hand number from api_usage (most recent)
        hand_row = self._db.fetch_one(
            "SELECT MAX(hand_number) as hand_number FROM api_usage WHERE game_id = ?",
            (game_id,),
        )
        hand_number = hand_row["hand_number"] if hand_row and hand_row["hand_number"] else 1

        return {
            'game_id': game_id,
            'phase': phase,
            'hand_number': hand_number,
            'pot': pot_total,
            'community_cards': community_cards,
            'players': players,
        }

    def get_player_detail(
        self, game_id: str, player_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get detailed player info for drill-down panel.

        Returns:
            Dictionary with player info, psychology, LLM debug, play style,
            and recent decisions, or None if not found.
        """
        # Load game state for player info
        game_row = self._db.fetch_one(
            "SELECT game_state_json FROM games WHERE game_id = ?",
            (game_id,),
        )
        if not game_row:
            return None

        try:
            state_dict = from_json(game_row["game_state_json"]) or {}
        except Exception:
            return None

        # Find player in game state
        player_data = None
        for p in state_dict.get('players', []):
            if p.get('name') == player_name:
                player_data = p
                break

        if not player_data:
            return None

        # Get psychology data
        ctrl_state = self._load_controller_state(game_id, player_name)
        emo_state = self._load_emotional_state(game_id, player_name)

        tilt_state = ctrl_state.get('tilt_state', {}) if ctrl_state else {}
        psychology = {
            'narrative': emo_state.get('narrative', '') if emo_state else '',
            'inner_voice': emo_state.get('inner_voice', '') if emo_state else '',
            'tilt_level': round((tilt_state.get('tilt_level', 0) if tilt_state else 0) * 100),
            'tilt_category': tilt_state.get('category', 'none') if tilt_state else 'none',
            'tilt_source': tilt_state.get('source', '') if tilt_state else '',
        }

        # Get LLM debug info
        llm_row = self._db.fetch_one(
            """
            SELECT provider, model, reasoning_effort,
                   COUNT(*) as total_calls,
                   AVG(latency_ms) as avg_latency_ms,
                   AVG(estimated_cost) as avg_cost
            FROM api_usage
            WHERE game_id = ? AND player_name = ?
            GROUP BY provider, model
            """,
            (game_id, player_name),
        )

        llm_debug = {}
        if llm_row:
            # Also get percentile latencies
            latency_rows = self._db.fetch_all(
                """
                SELECT latency_ms FROM api_usage
                WHERE game_id = ? AND player_name = ? AND latency_ms IS NOT NULL
                ORDER BY latency_ms
                """,
                (game_id, player_name),
            )
            latencies = [r["latency_ms"] for r in latency_rows]

            p95 = 0
            p99 = 0
            if latencies:
                try:
                    import numpy as np
                    p95 = round(float(np.percentile(latencies, 95)), 2) if len(latencies) >= 5 else max(latencies)
                    p99 = round(float(np.percentile(latencies, 99)), 2) if len(latencies) >= 10 else max(latencies)
                except ImportError:
                    sorted_lat = sorted(latencies)
                    p95 = round(sorted_lat[int(len(sorted_lat) * 0.95)], 2) if len(sorted_lat) >= 5 else max(latencies)
                    p99 = round(sorted_lat[int(len(sorted_lat) * 0.99)], 2) if len(sorted_lat) >= 10 else max(latencies)

            llm_debug = {
                'provider': llm_row["provider"],
                'model': llm_row["model"],
                'reasoning_effort': llm_row.get("reasoning_effort"),
                'total_calls': llm_row["total_calls"],
                'avg_latency_ms': round(llm_row["avg_latency_ms"] or 0, 2),
                'p95_latency_ms': p95,
                'p99_latency_ms': p99,
                'avg_cost_per_call': round(llm_row["avg_cost"] or 0, 6),
            }

        # Get play style from opponent models
        opp_row = self._db.fetch_one(
            """
            SELECT hands_observed, vpip, pfr, aggression_factor
            FROM opponent_models
            WHERE game_id = ? AND opponent_name = ?
            ORDER BY hands_observed DESC
            LIMIT 1
            """,
            (game_id, player_name),
        )

        play_style = {}
        if opp_row:
            vpip = round((opp_row["vpip"] or 0) * 100, 1)
            pfr = round((opp_row["pfr"] or 0) * 100, 1)
            af = round(opp_row["aggression_factor"] or 0, 2)

            # Classify play style
            if vpip < 25:
                tightness = 'tight'
            elif vpip > 35:
                tightness = 'loose'
            else:
                tightness = 'balanced'

            if af > 2:
                aggression = 'aggressive'
            elif af < 1:
                aggression = 'passive'
            else:
                aggression = 'balanced'

            summary = f'{tightness}-{aggression}'

            play_style = {
                'vpip': vpip,
                'pfr': pfr,
                'aggression_factor': af,
                'hands_observed': opp_row["hands_observed"],
                'summary': summary,
            }

        # Get recent decisions
        decision_rows = self._db.fetch_all(
            """
            SELECT hand_number, phase, action_taken, decision_quality, ev_lost
            FROM player_decision_analysis
            WHERE game_id = ? AND player_name = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (game_id, player_name),
        )

        recent_decisions = [
            {
                'hand_number': r["hand_number"],
                'phase': r["phase"],
                'action': r["action_taken"],
                'decision_quality': r["decision_quality"],
                'ev_lost': round(r["ev_lost"] or 0, 2) if r["ev_lost"] else None,
            }
            for r in decision_rows
        ]

        return {
            'player': {
                'name': player_name,
                'stack': player_data.get('stack', 0),
                'cards': player_data.get('hand', []),
            },
            'psychology': psychology,
            'llm_debug': llm_debug,
            'play_style': play_style,
            'recent_decisions': recent_decisions,
        }

    def _load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game."""
        rows = self._db.fetch_all(
            "SELECT player_name, state_data FROM ai_controller_states WHERE game_id = ?",
            (game_id,),
        )
        return {
            row["player_name"]: from_json(row["state_data"]) or {}
            for row in rows
        }

    def _load_all_emotional_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game."""
        rows = self._db.fetch_all(
            "SELECT player_name, state_data FROM ai_emotional_states WHERE game_id = ?",
            (game_id,),
        )
        return {
            row["player_name"]: from_json(row["state_data"]) or {}
            for row in rows
        }

    def _load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a specific player."""
        row = self._db.fetch_one(
            "SELECT state_data FROM ai_controller_states WHERE game_id = ? AND player_name = ?",
            (game_id, player_name),
        )
        return from_json(row["state_data"]) if row else None

    def _load_emotional_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a specific player."""
        row = self._db.fetch_one(
            "SELECT state_data FROM ai_emotional_states WHERE game_id = ? AND player_name = ?",
            (game_id, player_name),
        )
        return from_json(row["state_data"]) if row else None
