"""Repository for player decision analysis persistence.

Covers the player_decision_analysis table.
"""
from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List

from poker.repositories.base_repository import BaseRepository
from poker.repositories.repository_utils import build_where_clause

logger = logging.getLogger(__name__)


class DecisionAnalysisRepository(BaseRepository):
    """Handles player decision analysis storage and retrieval."""

    def save_decision_analysis(self, analysis) -> int:
        """Save a decision analysis to the database.

        Args:
            analysis: DecisionAnalysis dataclass or dict with analysis data

        Returns:
            The ID of the inserted row.
        """
        # Convert dataclass to dict if needed
        if hasattr(analysis, 'to_dict'):
            data = analysis.to_dict()
        else:
            data = analysis

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO player_decision_analysis (
                    request_id, capture_id,
                    game_id, player_name, hand_number, phase, player_position,
                    pot_total, cost_to_call, player_stack, num_opponents,
                    player_hand, community_cards,
                    action_taken, raise_amount, raise_amount_bb,
                    equity, required_equity, ev_call,
                    optimal_action, decision_quality, ev_lost,
                    hand_rank, relative_strength,
                    equity_vs_ranges, opponent_positions,
                    tilt_level, tilt_source,
                    valence, arousal, control, focus,
                    display_emotion, elastic_aggression, elastic_bluff_tendency,
                    elastic_tightness, elastic_confidence, elastic_composure, elastic_table_talk,
                    opponent_ranges_json, board_texture_json,
                    player_hand_canonical, player_hand_in_range, player_hand_tier, standard_range_pct,
                    zone_confidence, zone_composure, zone_energy, zone_manifestation,
                    zone_sweet_spots_json, zone_penalties_json,
                    zone_primary_sweet_spot, zone_primary_penalty,
                    zone_total_penalty_strength, zone_in_neutral_territory,
                    zone_intrusive_thoughts_injected, zone_intrusive_thoughts_json,
                    zone_penalty_strategy_applied, zone_info_degraded, zone_strategy_selected,
                    analyzer_version, processing_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('request_id'),
                data.get('capture_id'),
                data.get('game_id'),
                data.get('player_name'),
                data.get('hand_number'),
                data.get('phase'),
                data.get('player_position'),
                data.get('pot_total'),
                data.get('cost_to_call'),
                data.get('player_stack'),
                data.get('num_opponents'),
                data.get('player_hand'),
                data.get('community_cards'),
                data.get('action_taken'),
                data.get('raise_amount'),
                data.get('raise_amount_bb'),
                data.get('equity'),
                data.get('required_equity'),
                data.get('ev_call'),
                data.get('optimal_action'),
                data.get('decision_quality'),
                data.get('ev_lost'),
                data.get('hand_rank'),
                data.get('relative_strength'),
                data.get('equity_vs_ranges'),
                data.get('opponent_positions'),
                data.get('tilt_level'),
                data.get('tilt_source'),
                data.get('valence'),
                data.get('arousal'),
                data.get('control'),
                data.get('focus'),
                data.get('display_emotion'),
                data.get('elastic_aggression'),
                data.get('elastic_bluff_tendency'),
                data.get('elastic_tightness'),
                data.get('elastic_confidence'),
                data.get('elastic_composure'),
                data.get('elastic_table_talk'),
                data.get('opponent_ranges_json'),
                data.get('board_texture_json'),
                data.get('player_hand_canonical'),
                data.get('player_hand_in_range'),
                data.get('player_hand_tier'),
                data.get('standard_range_pct'),
                data.get('zone_confidence'),
                data.get('zone_composure'),
                data.get('zone_energy'),
                data.get('zone_manifestation'),
                data.get('zone_sweet_spots_json'),
                data.get('zone_penalties_json'),
                data.get('zone_primary_sweet_spot'),
                data.get('zone_primary_penalty'),
                data.get('zone_total_penalty_strength'),
                data.get('zone_in_neutral_territory'),
                data.get('zone_intrusive_thoughts_injected'),
                data.get('zone_intrusive_thoughts_json'),
                data.get('zone_penalty_strategy_applied'),
                data.get('zone_info_degraded'),
                data.get('zone_strategy_selected'),
                data.get('analyzer_version'),
                data.get('processing_time_ms'),
            ))
            return cursor.lastrowid

    def get_decision_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        """Get a single decision analysis by ID."""
        with self._get_connection() as conn:

            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE id = ?",
                (analysis_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_decision_analysis_by_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get decision analysis by api_usage request_id."""
        with self._get_connection() as conn:

            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE request_id = ?",
                (request_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_decision_analysis_by_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get decision analysis linked to a prompt capture.

        Links via capture_id (preferred) or request_id (fallback).
        Note: request_id fallback only works when request_id is non-empty,
        as some providers (Google/Gemini) don't return request IDs.
        """
        with self._get_connection() as conn:

            # First try direct capture_id link (preferred, always reliable)
            cursor = conn.execute(
                "SELECT * FROM player_decision_analysis WHERE capture_id = ?",
                (capture_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # Fall back to request_id link, but ONLY if request_id is non-empty
            # Empty string matches would cause incorrect results
            cursor = conn.execute("""
                SELECT pda.*
                FROM player_decision_analysis pda
                JOIN prompt_captures pc ON pc.original_request_id = pda.request_id
                WHERE pc.id = ?
                  AND pc.original_request_id IS NOT NULL
                  AND pc.original_request_id != ''
                  AND pda.request_id IS NOT NULL
                  AND pda.request_id != ''
            """, (capture_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def list_decision_analyses(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        decision_quality: Optional[str] = None,
        min_ev_lost: Optional[float] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List decision analyses with optional filtering.

        Returns:
            Dict with 'analyses' list and 'total' count.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)
        if decision_quality:
            conditions.append("decision_quality = ?")
            params.append(decision_quality)
        if min_ev_lost is not None:
            conditions.append("ev_lost >= ?")
            params.append(min_ev_lost)

        where_clause = build_where_clause(conditions)

        with self._get_connection() as conn:

            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM player_decision_analysis {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get analyses with pagination
            query = f"""
                SELECT *
                FROM player_decision_analysis
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            analyses = [dict(row) for row in cursor.fetchall()]

            return {
                'analyses': analyses,
                'total': total
            }

    def get_decision_analysis_stats(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Get aggregate statistics for decision analyses.

        Args:
            game_id: Optional filter by game

        Returns:
            Dict with aggregate stats including:
            - total: Total number of analyses
            - by_quality: Count by decision quality
            - by_action: Count by action taken
            - total_ev_lost: Sum of EV lost
            - avg_equity: Average equity across decisions
            - avg_processing_ms: Average processing time
        """
        where_clause = "WHERE game_id = ?" if game_id else ""
        params = [game_id] if game_id else []

        with self._get_connection() as conn:
            # Count by quality
            cursor = conn.execute(f"""
                SELECT decision_quality, COUNT(*) as count
                FROM player_decision_analysis {where_clause}
                GROUP BY decision_quality
            """, params)
            by_quality = {row[0]: row[1] for row in cursor.fetchall()}

            # Count by action
            cursor = conn.execute(f"""
                SELECT action_taken, COUNT(*) as count
                FROM player_decision_analysis {where_clause}
                GROUP BY action_taken
            """, params)
            by_action = {row[0]: row[1] for row in cursor.fetchall()}

            # Aggregate stats
            cursor = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(ev_lost) as total_ev_lost,
                    AVG(equity) as avg_equity,
                    AVG(equity_vs_ranges) as avg_equity_vs_ranges,
                    AVG(processing_time_ms) as avg_processing_ms,
                    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistakes,
                    SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) as correct
                FROM player_decision_analysis {where_clause}
            """, params)
            row = cursor.fetchone()

            return {
                'total': row[0] or 0,
                'total_ev_lost': row[1] or 0,
                'avg_equity': row[2],
                'avg_equity_vs_ranges': row[3],
                'avg_processing_ms': row[4],
                'mistakes': row[5] or 0,
                'correct': row[6] or 0,
                'by_quality': by_quality,
                'by_action': by_action,
            }

    def get_range_timeline(self, game_id: str, hand_number: int) -> List[Dict[str, Any]]:
        """Get range evolution for a hand across all streets.

        Returns list of decisions ordered by time with range and texture data.
        Useful for analyzing how opponent ranges narrowed through the hand.

        Args:
            game_id: Game identifier
            hand_number: Hand number within the game

        Returns:
            List of dicts with:
            - phase, player_name, opponent_ranges_json, board_texture_json,
              equity_vs_ranges, community_cards, action_taken,
              player_hand_canonical, player_hand_in_range, player_hand_tier
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    phase,
                    player_name,
                    opponent_ranges_json,
                    board_texture_json,
                    equity_vs_ranges,
                    community_cards,
                    action_taken,
                    player_hand_canonical,
                    player_hand_in_range,
                    player_hand_tier,
                    created_at
                FROM player_decision_analysis
                WHERE game_id = ? AND hand_number = ?
                ORDER BY created_at ASC
            """, (game_id, hand_number))

            return [dict(row) for row in cursor.fetchall()]
