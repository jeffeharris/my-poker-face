"""Repository for experiment-related persistence.

Part 1 (B4a): prompt_captures, player_decision_analysis, prompt_presets, capture_labels.
Part 2 (B4b): experiment lifecycle, chat sessions, live stats/analytics, replay experiments.
Extracted from GamePersistence as part of T3-35.
"""
import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any

import numpy as np

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class ExperimentRepository(BaseRepository):
    """Handles prompt captures, decision analysis, prompt presets, and capture labels."""

    # ========== Prompt Capture Methods ==========

    def save_prompt_capture(self, capture: Dict[str, Any]) -> int:
        """Save a prompt capture for debugging AI decisions.

        Args:
            capture: Dict containing capture data with keys:
                - game_id, player_name, hand_number, phase
                - system_prompt, user_message, ai_response
                - pot_total, cost_to_call, pot_odds, player_stack
                - community_cards, player_hand, valid_actions
                - action_taken, raise_amount
                - model, latency_ms, input_tokens, output_tokens

        Returns:
            The ID of the inserted capture.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO prompt_captures (
                    -- Identity
                    game_id, player_name, hand_number,
                    -- Game State
                    phase, pot_total, cost_to_call, pot_odds, player_stack,
                    stack_bb, already_bet_bb,
                    community_cards, player_hand, valid_actions,
                    -- Decision
                    action_taken, raise_amount,
                    -- Prompts (INPUT)
                    system_prompt, conversation_history, user_message, raw_request,
                    -- Response (OUTPUT)
                    ai_response, raw_api_response,
                    -- LLM Config
                    provider, model, reasoning_effort,
                    -- Metrics
                    latency_ms, input_tokens, output_tokens,
                    -- Tracking
                    original_request_id,
                    -- Prompt Versioning
                    prompt_template, prompt_version, prompt_hash,
                    -- User Annotations
                    tags, notes,
                    -- Prompt Config (for analysis)
                    prompt_config_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                # Identity
                capture.get('game_id'),
                capture.get('player_name'),
                capture.get('hand_number'),
                # Game State
                capture.get('phase'),
                capture.get('pot_total'),
                capture.get('cost_to_call'),
                capture.get('pot_odds'),
                capture.get('player_stack'),
                capture.get('stack_bb'),
                capture.get('already_bet_bb'),
                json.dumps(capture.get('community_cards')) if capture.get('community_cards') else None,
                json.dumps(capture.get('player_hand')) if capture.get('player_hand') else None,
                json.dumps(capture.get('valid_actions')) if capture.get('valid_actions') else None,
                # Decision
                capture.get('action_taken'),
                capture.get('raise_amount'),
                # Prompts (INPUT)
                capture.get('system_prompt'),
                json.dumps(capture.get('conversation_history')) if capture.get('conversation_history') else None,
                capture.get('user_message'),
                capture.get('raw_request'),
                # Response (OUTPUT)
                capture.get('ai_response'),
                capture.get('raw_api_response'),
                # LLM Config
                capture.get('provider', 'openai'),
                capture.get('model'),
                capture.get('reasoning_effort'),
                # Metrics
                capture.get('latency_ms'),
                capture.get('input_tokens'),
                capture.get('output_tokens'),
                # Tracking
                capture.get('original_request_id'),
                # Prompt Versioning
                capture.get('prompt_template'),
                capture.get('prompt_version'),
                capture.get('prompt_hash'),
                # User Annotations
                json.dumps(capture.get('tags', [])),
                capture.get('notes'),
                # Prompt Config (for analysis)
                json.dumps(capture.get('prompt_config')) if capture.get('prompt_config') else None,
            ))
            return cursor.lastrowid

    def get_prompt_capture(self, capture_id: int) -> Optional[Dict[str, Any]]:
        """Get a single prompt capture by ID.

        Joins with api_usage to get cached_tokens, reasoning_tokens, and estimated_cost.
        """
        with self._get_connection() as conn:

            # Join with api_usage to get usage metrics (cached tokens, reasoning tokens, cost)
            cursor = conn.execute("""
                SELECT pc.*,
                       au.cached_tokens,
                       au.reasoning_tokens,
                       au.estimated_cost
                FROM prompt_captures pc
                LEFT JOIN api_usage au ON pc.original_request_id = au.request_id
                WHERE pc.id = ?
            """, (capture_id,))
            row = cursor.fetchone()
            if not row:
                return None

            capture = dict(row)
            # Parse JSON fields
            capture_id_for_log = capture.get('id')
            for field in ['community_cards', 'player_hand', 'valid_actions', 'tags', 'conversation_history']:
                if capture.get(field):
                    try:
                        capture[field] = json.loads(capture[field])
                    except json.JSONDecodeError:
                        logger.debug(f"Failed to parse JSON for field '{field}' in prompt capture {capture_id_for_log}")

            # Handle image_data BLOB - convert to base64 data URL for JSON serialization
            if capture.get('is_image_capture') and capture.get('image_data'):
                import base64
                img_bytes = capture['image_data']
                if isinstance(img_bytes, bytes):
                    b64_data = base64.b64encode(img_bytes).decode('utf-8')
                    capture['image_url'] = f"data:image/png;base64,{b64_data}"
                # Remove raw bytes from response (not JSON serializable)
                del capture['image_data']
            elif 'image_data' in capture:
                # Remove even if None/empty to avoid serialization issues
                del capture['image_data']
            return capture

    def list_prompt_captures(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        tags: Optional[List[str]] = None,
        call_type: Optional[str] = None,
        error_type: Optional[str] = None,
        has_error: Optional[bool] = None,
        is_correction: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List prompt captures with optional filtering.

        Args:
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)

        Returns:
            Dict with 'captures' list and 'total' count.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("player_name = ?")
            params.append(player_name)
        if action:
            conditions.append("action_taken = ?")
            params.append(action)
        if phase:
            conditions.append("phase = ?")
            params.append(phase)
        if min_pot_odds is not None:
            conditions.append("pot_odds >= ?")
            params.append(min_pot_odds)
        if max_pot_odds is not None:
            conditions.append("pot_odds <= ?")
            params.append(max_pot_odds)
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if error_type:
            conditions.append("error_type = ?")
            params.append(error_type)
        if has_error is True:
            conditions.append("error_type IS NOT NULL")
        elif has_error is False:
            conditions.append("error_type IS NULL")
        if is_correction is True:
            conditions.append("parent_id IS NOT NULL")
        elif is_correction is False:
            conditions.append("parent_id IS NULL")
        if tags:
            # Match any of the provided tags
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:


            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM prompt_captures {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get captures with pagination
            query = f"""
                SELECT id, created_at, game_id, player_name, hand_number, phase,
                       action_taken, pot_total, cost_to_call, pot_odds, player_stack,
                       community_cards, player_hand, model, provider, latency_ms, tags, notes,
                       error_type, error_description, parent_id, correction_attempt
                FROM prompt_captures
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                capture_id_for_log = capture.get('id')
                for field in ['community_cards', 'player_hand', 'tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            logger.debug(f"Failed to parse JSON for field '{field}' in prompt capture {capture_id_for_log}")
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def get_prompt_capture_stats(
        self,
        game_id: Optional[str] = None,
        call_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get aggregate statistics for prompt captures."""
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            # Count by action (use 'unknown' for NULL to avoid JSON serialization issues)
            cursor = conn.execute(f"""
                SELECT action_taken, COUNT(*) as count
                FROM prompt_captures {where_clause}
                GROUP BY action_taken
            """, params)
            by_action = {(row[0] or 'unknown'): row[1] for row in cursor.fetchall()}

            # Count by phase (use 'unknown' for NULL)
            cursor = conn.execute(f"""
                SELECT phase, COUNT(*) as count
                FROM prompt_captures {where_clause}
                GROUP BY phase
            """, params)
            by_phase = {(row[0] or 'unknown'): row[1] for row in cursor.fetchall()}

            # Suspicious folds (high pot odds)
            suspicious_params = params + [5.0]  # pot odds > 5:1
            suspicious_where = f"{where_clause} {'AND' if where_clause else 'WHERE'} action_taken = 'fold' AND pot_odds > ?"
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM prompt_captures
                {suspicious_where}
            """, suspicious_params)
            suspicious_folds = cursor.fetchone()[0]

            # Total captures
            cursor = conn.execute(f"SELECT COUNT(*) FROM prompt_captures {where_clause}", params)
            total = cursor.fetchone()[0]

            return {
                'total': total,
                'by_action': by_action,
                'by_phase': by_phase,
                'suspicious_folds': suspicious_folds
            }

    def update_prompt_capture_tags(
        self,
        capture_id: int,
        tags: List[str],
        notes: Optional[str] = None
    ) -> bool:
        """Update tags and notes for a prompt capture."""
        with self._get_connection() as conn:
            if notes is not None:
                conn.execute(
                    "UPDATE prompt_captures SET tags = ?, notes = ? WHERE id = ?",
                    (json.dumps(tags), notes, capture_id)
                )
            else:
                conn.execute(
                    "UPDATE prompt_captures SET tags = ? WHERE id = ?",
                    (json.dumps(tags), capture_id)
                )
            return conn.total_changes > 0

    def delete_prompt_captures(self, game_id: Optional[str] = None, before_date: Optional[str] = None) -> int:
        """Delete prompt captures, optionally filtered by game or date.

        Args:
            game_id: Delete captures for a specific game
            before_date: Delete captures before this date (ISO format)

        Returns:
            Number of captures deleted.
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("game_id = ?")
            params.append(game_id)
        if before_date:
            conditions.append("created_at < ?")
            params.append(before_date)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:
            cursor = conn.execute(f"DELETE FROM prompt_captures {where_clause}", params)
            return cursor.rowcount

    # ========== Playground Capture Methods ==========

    def list_playground_captures(
        self,
        call_type: Optional[str] = None,
        provider: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List captures for the playground (filtered by call_type).

        This method is similar to list_prompt_captures but focuses on
        non-game captures identified by call_type.

        Args:
            call_type: Filter by call type (e.g., 'commentary', 'personality_generation')
            provider: Filter by LLM provider
            limit: Max results to return
            offset: Pagination offset
            date_from: Filter by start date (ISO format)
            date_to: Filter by end date (ISO format)

        Returns:
            Dict with 'captures' list and 'total' count
        """
        conditions = []  # Show all captures (including legacy ones without call_type)
        params = []

        if call_type:
            conditions.append("call_type = ?")
            params.append(call_type)
        if provider:
            conditions.append("provider = ?")
            params.append(provider)
        if date_from:
            conditions.append("created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("created_at <= ?")
            params.append(date_to)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:


            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(*) FROM prompt_captures {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get captures with pagination
            query = f"""
                SELECT id, created_at, game_id, player_name, hand_number,
                       phase, call_type, action_taken,
                       model, provider, reasoning_effort,
                       latency_ms, input_tokens, output_tokens,
                       tags, notes,
                       is_image_capture, image_size, image_width, image_height,
                       target_personality, target_emotion
                FROM prompt_captures
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                for field in ['tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            logger.warning(
                                "Failed to decode JSON for field '%s' on capture id=%s; keeping raw value",
                                field,
                                capture.get("id"),
                            )
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def get_playground_capture_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics for all prompt captures."""
        with self._get_connection() as conn:


            # Count by call_type (legacy captures without call_type shown as 'player_decision')
            cursor = conn.execute("""
                SELECT COALESCE(call_type, 'player_decision') as call_type, COUNT(*) as count
                FROM prompt_captures
                GROUP BY COALESCE(call_type, 'player_decision')
                ORDER BY count DESC
            """)
            by_call_type = {row['call_type']: row['count'] for row in cursor.fetchall()}

            # Count by provider
            cursor = conn.execute("""
                SELECT COALESCE(provider, 'openai') as provider, COUNT(*) as count
                FROM prompt_captures
                GROUP BY COALESCE(provider, 'openai')
                ORDER BY count DESC
            """)
            by_provider = {row['provider']: row['count'] for row in cursor.fetchall()}

            # Total count
            cursor = conn.execute("""
                SELECT COUNT(*) FROM prompt_captures
            """)
            total = cursor.fetchone()[0]

            return {
                'total': total,
                'by_call_type': by_call_type,
                'by_provider': by_provider,
            }

    def cleanup_old_captures(self, retention_days: int) -> int:
        """Delete captures older than the retention period.

        Args:
            retention_days: Delete captures older than this many days.
                           If 0, no deletion occurs (unlimited retention).

        Returns:
            Number of captures deleted.
        """
        if retention_days <= 0:
            return 0  # Unlimited retention

        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM prompt_captures
                WHERE call_type IS NOT NULL
                  AND created_at < datetime('now', '-' || ? || ' days')
            """, (retention_days,))

            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} playground captures older than {retention_days} days")
            return deleted

    # ========== Decision Analysis Methods ==========

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
                    analyzer_version, processing_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

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

    # ========== Prompt Preset Methods ==========

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
            if row:
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
            return None

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
            if row:
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
            return None

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

            return [
                {
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
                for row in cursor.fetchall()
            ]

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

    # ==================== Capture Labels Methods ====================

    def add_capture_labels(
        self,
        capture_id: int,
        labels: List[str],
        label_type: str = 'user'
    ) -> List[str]:
        """Add labels to a captured AI decision.

        Args:
            capture_id: The prompt_captures ID
            labels: List of label strings to add
            label_type: Type of label ('user' for manual, 'smart' for auto-generated)

        Returns:
            List of labels that were actually added (excludes duplicates)
        """
        added = []
        with self._get_connection() as conn:
            for label in labels:
                label = label.strip().lower()
                if not label:
                    continue
                try:
                    conn.execute("""
                        INSERT INTO capture_labels (capture_id, label, label_type)
                        VALUES (?, ?, ?)
                    """, (capture_id, label, label_type))
                    added.append(label)
                except sqlite3.IntegrityError:
                    # Label already exists for this capture, skip
                    pass
        if added:
            logger.debug(f"Added labels {added} to capture {capture_id}")
        return added

    def compute_and_store_auto_labels(self, capture_id: int, capture_data: Dict[str, Any]) -> List[str]:
        """Compute auto-labels for a capture based on rules and store them.

        Labels are computed based on the capture data at capture time.
        Stored with label_type='auto' to distinguish from user-added labels.

        Args:
            capture_id: The prompt_captures ID
            capture_data: Dict containing capture fields (action_taken, pot_odds, stack_bb, already_bet_bb, etc.)

        Returns:
            List of auto-labels that were added
        """
        labels = []
        action = capture_data.get('action_taken')
        pot_odds = capture_data.get('pot_odds')
        stack_bb = capture_data.get('stack_bb')
        already_bet_bb = capture_data.get('already_bet_bb')

        # SHORT_STACK: Folding with < 3 BB is almost always wrong
        if action == 'fold' and stack_bb is not None and stack_bb < 3:
            labels.append('short_stack_fold')

        # POT_COMMITTED: Folding after investing more than remaining stack
        if (action == 'fold' and
                already_bet_bb is not None and
                stack_bb is not None and
                already_bet_bb > stack_bb):
            labels.append('pot_committed_fold')

        # SUS_FOLD: Suspicious fold - high pot odds (getting good price)
        if action == 'fold' and pot_odds is not None and pot_odds >= 5:
            # Only add if not already flagged with more specific labels
            if 'short_stack_fold' not in labels and 'pot_committed_fold' not in labels:
                labels.append('suspicious_fold')

        # DRAMA: Add labels for notable drama situations
        drama = capture_data.get('drama_context')
        if drama:
            level = drama.get('level')
            tone = drama.get('tone')
            factors = drama.get('factors', [])

            # Label high-drama levels
            if level in ('climactic', 'high_stakes'):
                labels.append(f'drama:{level}')

            # Label non-neutral tones
            if tone and tone != 'neutral':
                labels.append(f'tone:{tone}')

            # Label specific dramatic factors
            for factor in factors:
                if factor in ('huge_raise', 'late_stage', 'all_in'):
                    labels.append(f'factor:{factor}')

        # Store labels if any were computed
        if labels:
            self.add_capture_labels(capture_id, labels, label_type='auto')
            logger.debug(f"Auto-labeled capture {capture_id}: {labels}")

        return labels

    def remove_capture_labels(
        self,
        capture_id: int,
        labels: List[str]
    ) -> int:
        """Remove labels from a captured AI decision.

        Args:
            capture_id: The prompt_captures ID
            labels: List of label strings to remove

        Returns:
            Number of labels that were removed
        """
        with self._get_connection() as conn:
            total_removed = 0
            for label in labels:
                label = label.strip().lower()
                if not label:
                    continue
                cursor = conn.execute("""
                    DELETE FROM capture_labels
                    WHERE capture_id = ? AND label = ?
                """, (capture_id, label))
                total_removed += cursor.rowcount
        if total_removed:
            logger.debug(f"Removed {total_removed} label(s) from capture {capture_id}")
        return total_removed

    def get_capture_labels(self, capture_id: int) -> List[Dict[str, Any]]:
        """Get all labels for a captured AI decision.

        Args:
            capture_id: The prompt_captures ID

        Returns:
            List of label dicts with 'label', 'label_type', 'created_at'
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT label, label_type, created_at
                FROM capture_labels
                WHERE capture_id = ?
                ORDER BY label
            """, (capture_id,))
            return [dict(row) for row in cursor.fetchall()]

    def list_all_labels(self, label_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all unique labels with counts.

        Args:
            label_type: Optional filter by label type ('user' or 'smart')

        Returns:
            List of dicts with 'name', 'count', 'label_type'
        """
        with self._get_connection() as conn:

            if label_type:
                cursor = conn.execute("""
                    SELECT label as name, label_type, COUNT(*) as count
                    FROM capture_labels
                    WHERE label_type = ?
                    GROUP BY label, label_type
                    ORDER BY count DESC, label
                """, (label_type,))
            else:
                cursor = conn.execute("""
                    SELECT label as name, label_type, COUNT(*) as count
                    FROM capture_labels
                    GROUP BY label, label_type
                    ORDER BY count DESC, label
                """)
            return [dict(row) for row in cursor.fetchall()]

    def get_label_stats(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        call_type: Optional[str] = None
    ) -> Dict[str, int]:
        """Get label counts filtered by game_id, player_name, and/or call_type.

        Args:
            game_id: Optional filter by game
            player_name: Optional filter by player
            call_type: Optional filter by call type

        Returns:
            Dict mapping label name to count
        """
        conditions = []
        params = []

        if game_id:
            conditions.append("pc.game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("pc.player_name = ?")
            params.append(player_name)
        if call_type:
            conditions.append("pc.call_type = ?")
            params.append(call_type)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:

            cursor = conn.execute(f"""
                SELECT cl.label, COUNT(*) as count
                FROM capture_labels cl
                JOIN prompt_captures pc ON cl.capture_id = pc.id
                {where_clause}
                GROUP BY cl.label
                ORDER BY count DESC, cl.label
            """, params)
            return {row['label']: row['count'] for row in cursor.fetchall()}

    def search_captures_with_labels(
        self,
        labels: List[str],
        match_all: bool = False,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        action: Optional[str] = None,
        phase: Optional[str] = None,
        min_pot_odds: Optional[float] = None,
        max_pot_odds: Optional[float] = None,
        call_type: Optional[str] = None,
        min_pot_size: Optional[float] = None,
        max_pot_size: Optional[float] = None,
        min_big_blind: Optional[float] = None,
        max_big_blind: Optional[float] = None,
        error_type: Optional[str] = None,
        has_error: Optional[bool] = None,
        is_correction: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Search captures by labels and optional filters.

        Args:
            labels: List of labels to search for
            match_all: If True, captures must have ALL labels; if False, ANY label
            game_id: Optional filter by game
            player_name: Optional filter by player
            action: Optional filter by action taken
            phase: Optional filter by game phase
            min_pot_odds: Optional minimum pot odds filter
            max_pot_odds: Optional maximum pot odds filter
            call_type: Optional filter by call type (e.g., 'player_decision')
            min_pot_size: Optional minimum pot total filter
            max_pot_size: Optional maximum pot total filter
            min_big_blind: Optional minimum big blind filter (computed from stack_bb)
            max_big_blind: Optional maximum big blind filter (computed from stack_bb)
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'captures' list and 'total' count
        """
        # Normalize labels
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels:
            # No labels specified, fallback to regular listing
            return self.list_prompt_captures(
                game_id=game_id,
                player_name=player_name,
                action=action,
                phase=phase,
                min_pot_odds=min_pot_odds,
                max_pot_odds=max_pot_odds,
                call_type=call_type,
                error_type=error_type,
                has_error=has_error,
                is_correction=is_correction,
                limit=limit,
                offset=offset
            )

        # Build base conditions
        conditions = []
        params = []

        if game_id:
            conditions.append("pc.game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("pc.player_name = ?")
            params.append(player_name)
        if action:
            conditions.append("pc.action_taken = ?")
            params.append(action)
        if phase:
            conditions.append("pc.phase = ?")
            params.append(phase)
        if min_pot_odds is not None:
            conditions.append("pc.pot_odds >= ?")
            params.append(min_pot_odds)
        if max_pot_odds is not None:
            conditions.append("pc.pot_odds <= ?")
            params.append(max_pot_odds)
        if call_type:
            conditions.append("pc.call_type = ?")
            params.append(call_type)
        if min_pot_size is not None:
            conditions.append("pc.pot_total >= ?")
            params.append(min_pot_size)
        if max_pot_size is not None:
            conditions.append("pc.pot_total <= ?")
            params.append(max_pot_size)
        # Big blind filtering: compute BB from player_stack / stack_bb
        if min_big_blind is not None:
            conditions.append("pc.stack_bb > 0 AND (pc.player_stack / pc.stack_bb) >= ?")
            params.append(min_big_blind)
        if max_big_blind is not None:
            conditions.append("pc.stack_bb > 0 AND (pc.player_stack / pc.stack_bb) <= ?")
            params.append(max_big_blind)
        # Error/correction resilience filters
        if error_type:
            conditions.append("pc.error_type = ?")
            params.append(error_type)
        if has_error is True:
            conditions.append("pc.error_type IS NOT NULL")
        elif has_error is False:
            conditions.append("pc.error_type IS NULL")
        if is_correction is True:
            conditions.append("pc.parent_id IS NOT NULL")
        elif is_correction is False:
            conditions.append("pc.parent_id IS NULL")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_connection() as conn:


            # Build label matching subquery
            label_placeholders = ','.join(['?' for _ in labels])
            params_for_labels = [l for l in labels]

            if match_all:
                # Must have ALL specified labels
                label_subquery = f"""
                    pc.id IN (
                        SELECT capture_id
                        FROM capture_labels
                        WHERE label IN ({label_placeholders})
                        GROUP BY capture_id
                        HAVING COUNT(DISTINCT label) = ?
                    )
                """
                params_for_labels.append(len(labels))
            else:
                # Must have ANY of the specified labels
                label_subquery = f"""
                    pc.id IN (
                        SELECT capture_id
                        FROM capture_labels
                        WHERE label IN ({label_placeholders})
                    )
                """

            # Combine label filter with other conditions
            if where_clause:
                full_where = f"{where_clause} AND {label_subquery}"
            else:
                full_where = f"WHERE {label_subquery}"

            # Count query
            count_query = f"""
                SELECT COUNT(DISTINCT pc.id)
                FROM prompt_captures pc
                {full_where}
            """
            count_params = params + params_for_labels
            cursor = conn.execute(count_query, count_params)
            total = cursor.fetchone()[0]

            # Data query
            data_query = f"""
                SELECT DISTINCT pc.id, pc.created_at, pc.game_id, pc.player_name,
                       pc.hand_number, pc.phase, pc.action_taken, pc.pot_total,
                       pc.cost_to_call, pc.pot_odds, pc.player_stack,
                       pc.community_cards, pc.player_hand, pc.model, pc.provider,
                       pc.latency_ms, pc.tags, pc.notes
                FROM prompt_captures pc
                {full_where}
                ORDER BY pc.created_at DESC
                LIMIT ? OFFSET ?
            """
            data_params = params + params_for_labels + [limit, offset]
            cursor = conn.execute(data_query, data_params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                # Parse JSON fields
                for field in ['community_cards', 'player_hand', 'tags']:
                    if capture.get(field):
                        try:
                            capture[field] = json.loads(capture[field])
                        except json.JSONDecodeError:
                            logger.debug("Failed to parse JSON for field '%s' in capture id=%s", field, capture.get('id'))
                # Get labels for this capture
                capture['labels'] = self.get_capture_labels(capture['id'])
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def bulk_add_capture_labels(
        self,
        capture_ids: List[int],
        labels: List[str],
        label_type: str = 'user'
    ) -> Dict[str, int]:
        """Add labels to multiple captures at once.

        Args:
            capture_ids: List of prompt_captures IDs
            labels: Labels to add to all captures
            label_type: Type of label

        Returns:
            Dict with 'captures_affected' and 'labels_added' counts
        """
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels or not capture_ids:
            return {'captures_affected': 0, 'labels_added': 0}

        total_added = 0
        captures_touched = set()

        with self._get_connection() as conn:
            for capture_id in capture_ids:
                for label in labels:
                    try:
                        conn.execute("""
                            INSERT INTO capture_labels (capture_id, label, label_type)
                            VALUES (?, ?, ?)
                        """, (capture_id, label, label_type))
                        total_added += 1
                        captures_touched.add(capture_id)
                    except sqlite3.IntegrityError:
                        # Label already exists for this capture
                        pass

        logger.info(f"Bulk added {total_added} label(s) to {len(captures_touched)} capture(s)")
        return {
            'captures_affected': len(captures_touched),
            'labels_added': total_added
        }

    def bulk_remove_capture_labels(
        self,
        capture_ids: List[int],
        labels: List[str]
    ) -> Dict[str, int]:
        """Remove labels from multiple captures at once.

        Args:
            capture_ids: List of prompt_captures IDs
            labels: Labels to remove from all captures

        Returns:
            Dict with 'captures_affected' and 'labels_removed' counts
        """
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels or not capture_ids:
            return {'captures_affected': 0, 'labels_removed': 0}

        with self._get_connection() as conn:
            # Build query with multiple capture_ids
            id_placeholders = ','.join(['?' for _ in capture_ids])
            label_placeholders = ','.join(['?' for _ in labels])

            cursor = conn.execute(f"""
                DELETE FROM capture_labels
                WHERE capture_id IN ({id_placeholders})
                AND label IN ({label_placeholders})
            """, capture_ids + labels)
            removed = cursor.rowcount

        logger.info(f"Bulk removed {removed} label(s) from captures")
        return {
            'captures_affected': len(capture_ids),
            'labels_removed': removed
        }

    # ==================== Experiment Lifecycle Methods (B4b) ====================

    def create_experiment(self, config: Dict, parent_experiment_id: Optional[int] = None) -> int:
        """Create a new experiment record.

        Args:
            config: Dictionary containing experiment configuration with keys:
                - name: Unique experiment name (required)
                - description: Experiment description (optional)
                - hypothesis: What we're testing (optional)
                - tags: List of tags (optional)
                - notes: Additional notes (optional)
                - Additional config fields stored as config_json
            parent_experiment_id: Optional ID of the parent experiment for lineage tracking

        Returns:
            The experiment_id of the created record

        Raises:
            sqlite3.IntegrityError: If experiment name already exists
        """
        name = config.get('name')
        if not name:
            raise ValueError("Experiment name is required")

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO experiments (name, description, hypothesis, tags, notes, config_json, parent_experiment_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                name,
                config.get('description'),
                config.get('hypothesis'),
                json.dumps(config.get('tags', [])),
                config.get('notes'),
                json.dumps(config),
                parent_experiment_id,
            ))
            experiment_id = cursor.lastrowid
            logger.info(f"Created experiment '{name}' with id {experiment_id}" +
                       (f" (parent: {parent_experiment_id})" if parent_experiment_id else ""))
            return experiment_id

    def link_game_to_experiment(
        self,
        experiment_id: int,
        game_id: str,
        variant: Optional[str] = None,
        variant_config: Optional[Dict] = None,
        tournament_number: Optional[int] = None
    ) -> int:
        """Link a game to an experiment.

        Args:
            experiment_id: The experiment ID
            game_id: The game ID to link
            variant: Optional variant label (e.g., 'baseline', 'treatment')
            variant_config: Optional variant-specific configuration
            tournament_number: Optional tournament sequence number

        Returns:
            The experiment_games record ID
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO experiment_games (experiment_id, game_id, variant, variant_config_json, tournament_number)
                VALUES (?, ?, ?, ?, ?)
            """, (
                experiment_id,
                game_id,
                variant,
                json.dumps(variant_config) if variant_config else None,
                tournament_number,
            ))
            return cursor.lastrowid

    def complete_experiment(self, experiment_id: int, summary: Optional[Dict] = None) -> None:
        """Mark an experiment as completed and store summary.

        Args:
            experiment_id: The experiment ID
            summary: Optional summary dictionary with aggregated results
        """
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE experiments
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    summary_json = ?
                WHERE id = ?
            """, (json.dumps(summary) if summary else None, experiment_id))
            logger.info(f"Completed experiment {experiment_id}")

    def get_experiment(self, experiment_id: int) -> Optional[Dict]:
        """Get experiment details by ID.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dictionary with experiment details or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, name, description, hypothesis, tags, notes, config_json,
                       status, created_at, completed_at, summary_json, parent_experiment_id
                FROM experiments WHERE id = ?
            """, (experiment_id,))
            row = cursor.fetchone()
            if not row:
                return None

            return {
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'hypothesis': row[3],
                'tags': json.loads(row[4]) if row[4] else [],
                'notes': row[5],
                'config': json.loads(row[6]) if row[6] else {},
                'status': row[7],
                'created_at': row[8],
                'completed_at': row[9],
                'summary': json.loads(row[10]) if row[10] else None,
                'parent_experiment_id': row[11],
            }

    def get_experiment_by_name(self, name: str) -> Optional[Dict]:
        """Get experiment details by name.

        Args:
            name: The experiment name

        Returns:
            Dictionary with experiment details or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT id FROM experiments WHERE name = ?", (name,))
            row = cursor.fetchone()
            if not row:
                return None
            return self.get_experiment(row[0])

    def get_experiment_games(self, experiment_id: int) -> List[Dict]:
        """Get all games linked to an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of dictionaries with game link details
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT eg.id, eg.game_id, eg.variant, eg.variant_config_json,
                       eg.tournament_number, eg.created_at
                FROM experiment_games eg
                WHERE eg.experiment_id = ?
                ORDER BY eg.tournament_number, eg.created_at
            """, (experiment_id,))

            return [
                {
                    'id': row[0],
                    'game_id': row[1],
                    'variant': row[2],
                    'variant_config': json.loads(row[3]) if row[3] else None,
                    'tournament_number': row[4],
                    'created_at': row[5],
                }
                for row in cursor.fetchall()
            ]

    def update_experiment_game_heartbeat(
        self,
        game_id: str,
        state: str,
        api_call_started: bool = False,
        process_id: Optional[int] = None
    ) -> None:
        """Update heartbeat for an experiment game.

        Args:
            game_id: The game ID (tournament_id)
            state: Current state ('idle', 'calling_api', 'processing')
            api_call_started: If True, also update last_api_call_started_at
            process_id: Optional process ID to record
        """
        with sqlite3.connect(self.db_path) as conn:
            if api_call_started:
                conn.execute("""
                    UPDATE experiment_games
                    SET state = ?,
                        last_heartbeat_at = CURRENT_TIMESTAMP,
                        last_api_call_started_at = CURRENT_TIMESTAMP,
                        process_id = COALESCE(?, process_id)
                    WHERE game_id = ?
                """, (state, process_id, game_id))
            else:
                conn.execute("""
                    UPDATE experiment_games
                    SET state = ?,
                        last_heartbeat_at = CURRENT_TIMESTAMP,
                        process_id = COALESCE(?, process_id)
                    WHERE game_id = ?
                """, (state, process_id, game_id))

    def get_stalled_variants(
        self,
        experiment_id: int,
        threshold_minutes: int = 5
    ) -> List[Dict]:
        """Get variants that appear to be stalled.

        A variant is considered stalled if:
        - state='calling_api' AND last_api_call_started_at < (NOW - threshold)
        - state='processing' AND last_heartbeat_at < (NOW - threshold)
        - NOT in tournament_results (not completed)

        Args:
            experiment_id: The experiment ID
            threshold_minutes: Minutes of inactivity before considered stalled

        Returns:
            List of stalled variant records
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT eg.id, eg.game_id, eg.variant, eg.variant_config_json,
                       eg.tournament_number, eg.state, eg.last_heartbeat_at,
                       eg.last_api_call_started_at, eg.process_id, eg.resume_lock_acquired_at
                FROM experiment_games eg
                WHERE eg.experiment_id = ?
                  AND eg.state IN ('calling_api', 'processing')
                  AND (
                      (eg.state = 'calling_api'
                       AND eg.last_api_call_started_at < datetime('now', ? || ' minutes'))
                      OR
                      (eg.state = 'processing'
                       AND eg.last_heartbeat_at < datetime('now', ? || ' minutes'))
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM tournament_results tr
                      WHERE tr.game_id = eg.game_id
                  )
                ORDER BY eg.last_heartbeat_at
            """, (experiment_id, -threshold_minutes, -threshold_minutes))

            return [
                {
                    'id': row[0],
                    'game_id': row[1],
                    'variant': row[2],
                    'variant_config': json.loads(row[3]) if row[3] else None,
                    'tournament_number': row[4],
                    'state': row[5],
                    'last_heartbeat_at': row[6],
                    'last_api_call_started_at': row[7],
                    'process_id': row[8],
                    'resume_lock_acquired_at': row[9],
                }
                for row in cursor.fetchall()
            ]

    # Resume lock timeout in minutes - lock expires after this period
    RESUME_LOCK_TIMEOUT_MINUTES = 5

    def acquire_resume_lock(self, experiment_game_id: int) -> bool:
        """Attempt to acquire a resume lock on an experiment game.

        Uses pessimistic locking to prevent race conditions when resuming.
        Lock expires after RESUME_LOCK_TIMEOUT_MINUTES.

        Args:
            experiment_game_id: The experiment_games.id

        Returns:
            True if lock was acquired, False if already locked
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"""
                UPDATE experiment_games
                SET resume_lock_acquired_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND (resume_lock_acquired_at IS NULL
                       OR resume_lock_acquired_at < datetime('now', '-{self.RESUME_LOCK_TIMEOUT_MINUTES} minutes'))
            """, (experiment_game_id,))
            return cursor.rowcount == 1

    def release_resume_lock(self, game_id: str) -> None:
        """Release the resume lock for a game.

        Args:
            game_id: The game_id to release lock for
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE experiment_games
                SET resume_lock_acquired_at = NULL
                WHERE game_id = ?
            """, (game_id,))

    def release_resume_lock_by_id(self, experiment_game_id: int) -> None:
        """Release the resume lock by experiment_games.id.

        Args:
            experiment_game_id: The experiment_games.id to release lock for
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE experiment_games
                SET resume_lock_acquired_at = NULL
                WHERE id = ?
            """, (experiment_game_id,))

    def check_resume_lock_superseded(self, game_id: str) -> bool:
        """Check if this process has been superseded by a resume.

        A process is superseded if resume_lock_acquired_at > last_heartbeat_at,
        meaning another process has claimed the resume lock after our last heartbeat.

        Args:
            game_id: The game_id to check

        Returns:
            True if superseded (should exit), False otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT resume_lock_acquired_at, last_heartbeat_at
                FROM experiment_games
                WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()
            if not row:
                return False

            resume_lock, last_heartbeat = row
            if not resume_lock:
                return False
            if not last_heartbeat:
                return True  # No heartbeat but lock exists = superseded

            # Compare timestamps
            return resume_lock > last_heartbeat

    def get_experiment_decision_stats(
        self,
        experiment_id: int,
        variant: Optional[str] = None
    ) -> Dict:
        """Get aggregated decision analysis stats for an experiment.

        Args:
            experiment_id: The experiment ID
            variant: Optional variant filter

        Returns:
            Dictionary with aggregated decision statistics
        """
        with self._get_connection() as conn:
            # Build query with optional variant filter
            variant_clause = "AND eg.variant = ?" if variant else ""
            params = [experiment_id]
            if variant:
                params.append(variant)

            # Aggregate stats
            cursor = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                    SUM(CASE WHEN pda.decision_quality = 'marginal' THEN 1 ELSE 0 END) as marginal,
                    SUM(CASE WHEN pda.decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                    AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause}
            """, params)

            row = cursor.fetchone()
            total = row[0] or 0

            result = {
                'total': total,
                'correct': row[1] or 0,
                'marginal': row[2] or 0,
                'mistake': row[3] or 0,
                'correct_pct': round((row[1] or 0) * 100 / total, 1) if total else 0,
                'avg_ev_lost': round(row[4] or 0, 2),
            }

            # Stats by player
            cursor = conn.execute(f"""
                SELECT
                    pda.player_name,
                    COUNT(*) as total,
                    SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                    AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause}
                GROUP BY pda.player_name
            """, params)

            result['by_player'] = {
                row[0]: {
                    'total': row[1],
                    'correct': row[2] or 0,
                    'correct_pct': round((row[2] or 0) * 100 / row[1], 1) if row[1] else 0,
                    'avg_ev_lost': round(row[3] or 0, 2),
                }
                for row in cursor.fetchall()
            }

            return result

    def list_experiments(
        self,
        status: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """List experiments with optional status filter.

        Args:
            status: Optional status filter ('pending', 'running', 'completed', 'failed')
            include_archived: If False (default), filter out experiments with _archived tag
            limit: Maximum number of experiments to return
            offset: Number of experiments to skip for pagination

        Returns:
            List of experiment dictionaries with basic info and progress
        """
        with self._get_connection() as conn:
            # Build query with optional filters
            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status)

            if not include_archived:
                # Filter out experiments with _archived tag
                conditions.append("(tags IS NULL OR tags NOT LIKE '%\"_archived\"%')")

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            cursor = conn.execute(f"""
                SELECT
                    e.id, e.name, e.description, e.hypothesis,
                    e.tags, e.status, e.created_at, e.completed_at,
                    e.config_json, e.summary_json,
                    (SELECT COUNT(*) FROM experiment_games WHERE experiment_id = e.id) as games_count
                FROM experiments e
                {where_clause}
                ORDER BY e.created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            experiments = []
            for row in cursor.fetchall():
                config = json.loads(row[8]) if row[8] else {}
                summary = json.loads(row[9]) if row[9] else None

                # Calculate total expected games accounting for A/B variants
                num_tournaments = config.get('num_tournaments', 1)
                variants = config.get('variants', [])
                control = config.get('control')

                # For A/B experiments, total games = num_tournaments * num_variants
                if control and variants:
                    num_variants = len(variants) + 1  # +1 for control
                    total_expected = num_tournaments * num_variants
                else:
                    total_expected = num_tournaments

                experiments.append({
                    'id': row[0],
                    'name': row[1],
                    'description': row[2],
                    'hypothesis': row[3],
                    'tags': json.loads(row[4]) if row[4] else [],
                    'status': row[5],
                    'created_at': row[6],
                    'completed_at': row[7],
                    'games_count': row[10],
                    'num_tournaments': total_expected,
                    'model': config.get('model'),
                    'provider': config.get('provider'),
                    'summary': summary,
                })

            return experiments

    def update_experiment_status(
        self,
        experiment_id: int,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """Update experiment status.

        Args:
            experiment_id: The experiment ID
            status: New status ('pending', 'running', 'completed', 'failed')
            error_message: Optional error message if status is 'failed'
        """
        valid_statuses = {'pending', 'running', 'completed', 'failed', 'paused', 'interrupted'}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        with self._get_connection() as conn:
            if status == 'completed':
                conn.execute("""
                    UPDATE experiments
                    SET status = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (status, experiment_id))
            elif status == 'failed' and error_message:
                # Store error in notes field
                conn.execute("""
                    UPDATE experiments
                    SET status = ?, notes = COALESCE(notes || '\n', '') || ?
                    WHERE id = ?
                """, (status, f"Error: {error_message}", experiment_id))
            else:
                conn.execute("""
                    UPDATE experiments
                    SET status = ?
                    WHERE id = ?
                """, (status, experiment_id))
            logger.info(f"Updated experiment {experiment_id} status to {status}")

    def update_experiment_tags(self, experiment_id: int, tags: List[str]) -> None:
        """Update experiment tags.

        Args:
            experiment_id: The experiment ID
            tags: List of tags to set (replaces existing tags)
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET tags = ? WHERE id = ?",
                (json.dumps(tags), experiment_id)
            )
            logger.info(f"Updated experiment {experiment_id} tags to {tags}")

    def mark_running_experiments_interrupted(self) -> int:
        """Mark all 'running' experiments as 'interrupted'.

        Called on startup to handle experiments that were running when the
        server was stopped. Users can manually resume these experiments.

        Returns:
            Number of experiments marked as interrupted.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE experiments
                SET status = 'interrupted',
                    notes = 'Server restarted while experiment was running. Click Resume to continue.'
                WHERE status = 'running'
            """)
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Marked {count} running experiment(s) as interrupted")
            return count

    def get_incomplete_tournaments(self, experiment_id: int) -> List[Dict]:
        """Get game_ids for tournaments that haven't completed (no tournament_results entry).

        Used when resuming a paused experiment to identify which tournaments need to continue.

        Args:
            experiment_id: The experiment ID to check

        Returns:
            List of dicts with game info for incomplete tournaments
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT eg.game_id, eg.variant, eg.variant_config_json, eg.tournament_number
                FROM experiment_games eg
                LEFT JOIN tournament_results tr ON eg.game_id = tr.game_id
                WHERE eg.experiment_id = ?
                AND tr.id IS NULL
                ORDER BY eg.tournament_number
            """, (experiment_id,))

            incomplete = []
            for row in cursor.fetchall():
                variant_config = None
                if row['variant_config_json']:
                    try:
                        variant_config = json.loads(row['variant_config_json'])
                    except (json.JSONDecodeError, TypeError):
                        pass

                incomplete.append({
                    'game_id': row['game_id'],
                    'variant': row['variant'],
                    'variant_config': variant_config,
                    'tournament_number': row['tournament_number'],
                })

            return incomplete

    # ==================== Experiment Chat Session Methods (B4b) ====================

    def save_chat_session(
        self,
        session_id: str,
        owner_id: str,
        messages: List[Dict],
        config_snapshot: Dict,
        config_versions: Optional[List[Dict]] = None
    ) -> None:
        """Save or update a chat session.

        Args:
            session_id: Unique session identifier
            owner_id: User/owner identifier
            messages: List of chat messages [{role, content, configDiff?}]
            config_snapshot: Current config state
            config_versions: List of config version snapshots
        """
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO experiment_chat_sessions (id, owner_id, messages_json, config_snapshot_json, config_versions_json, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    messages_json = excluded.messages_json,
                    config_snapshot_json = excluded.config_snapshot_json,
                    config_versions_json = excluded.config_versions_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                session_id,
                owner_id,
                json.dumps(messages),
                json.dumps(config_snapshot),
                json.dumps(config_versions) if config_versions else None,
            ))
            logger.debug(f"Saved chat session {session_id} for owner {owner_id}")

    def get_chat_session(self, session_id: str) -> Optional[Dict]:
        """Get a chat session by its ID.

        Args:
            session_id: The session ID to retrieve

        Returns:
            Dict with session data or None if not found
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT id, messages_json, config_snapshot_json, config_versions_json, updated_at
                FROM experiment_chat_sessions
                WHERE id = ?
            """, (session_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'session_id': row['id'],
                'messages': json.loads(row['messages_json']) if row['messages_json'] else [],
                'config': json.loads(row['config_snapshot_json']) if row['config_snapshot_json'] else {},
                'config_versions': json.loads(row['config_versions_json']) if row['config_versions_json'] else None,
                'updated_at': row['updated_at'],
            }

    def get_latest_chat_session(self, owner_id: str) -> Optional[Dict]:
        """Get the most recent non-archived chat session for an owner.

        Args:
            owner_id: User/owner identifier

        Returns:
            Dict with session data or None if no session exists
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT id, messages_json, config_snapshot_json, config_versions_json, updated_at
                FROM experiment_chat_sessions
                WHERE owner_id = ? AND is_archived = 0
                ORDER BY updated_at DESC
                LIMIT 1
            """, (owner_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'session_id': row['id'],
                'messages': json.loads(row['messages_json']) if row['messages_json'] else [],
                'config': json.loads(row['config_snapshot_json']) if row['config_snapshot_json'] else {},
                'config_versions': json.loads(row['config_versions_json']) if row['config_versions_json'] else None,
                'updated_at': row['updated_at'],
            }

    def archive_chat_session(self, session_id: str) -> None:
        """Archive a chat session so it won't be returned by get_latest_chat_session.

        Args:
            session_id: The session ID to archive
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiment_chat_sessions SET is_archived = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,)
            )
            logger.debug(f"Archived chat session {session_id}")

    def delete_chat_session(self, session_id: str) -> None:
        """Delete a chat session entirely.

        Args:
            session_id: The session ID to delete
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM experiment_chat_sessions WHERE id = ?", (session_id,))
            logger.debug(f"Deleted chat session {session_id}")

    # ==================== Experiment Chat Storage Methods (B4b) ====================

    def save_experiment_design_chat(self, experiment_id: int, chat_history: List[Dict]) -> None:
        """Store the design chat history with an experiment.

        Called when an experiment is created to preserve the conversation that led to its design.

        Args:
            experiment_id: The experiment ID
            chat_history: List of chat messages from the design session
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET design_chat_json = ? WHERE id = ?",
                (json.dumps(chat_history), experiment_id)
            )
            logger.info(f"Saved design chat ({len(chat_history)} messages) to experiment {experiment_id}")

    def get_experiment_design_chat(self, experiment_id: int) -> Optional[List[Dict]]:
        """Get the design chat history for an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of chat messages or None if no design chat stored
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT design_chat_json FROM experiments WHERE id = ?",
                (experiment_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None

    def save_experiment_assistant_chat(self, experiment_id: int, chat_history: List[Dict]) -> None:
        """Store the ongoing assistant chat history for an experiment.

        Used for the experiment-scoped assistant that can query results and answer questions.

        Args:
            experiment_id: The experiment ID
            chat_history: List of chat messages from the assistant session
        """
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET assistant_chat_json = ? WHERE id = ?",
                (json.dumps(chat_history), experiment_id)
            )
            logger.debug(f"Saved assistant chat ({len(chat_history)} messages) to experiment {experiment_id}")

    def get_experiment_assistant_chat(self, experiment_id: int) -> Optional[List[Dict]]:
        """Get the assistant chat history for an experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of chat messages or None if no assistant chat stored
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT assistant_chat_json FROM experiments WHERE id = ?",
                (experiment_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
            return None

    # ==================== Live Stats & Analytics Methods (B4b) ====================

    def _load_all_emotional_states(self, conn, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game (internal helper).

        This inlines the query from GamePersistence to avoid cross-repository dependencies.
        """

        cursor = conn.execute("""
            SELECT * FROM emotional_state
            WHERE game_id = ?
        """, (game_id,))

        states = {}
        for row in cursor.fetchall():
            metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}
            states[row['player_name']] = {
                'valence': row['valence'],
                'arousal': row['arousal'],
                'control': row['control'],
                'focus': row['focus'],
                'narrative': row['narrative'] or '',
                'inner_voice': row['inner_voice'] or '',
                'generated_at_hand': row['generated_at_hand'],
                'source_events': json.loads(row['source_events']) if row['source_events'] else [],
                'created_at': metadata.get('created_at'),
                'used_fallback': metadata.get('used_fallback', False)
            }

        return states

    def _load_all_controller_states(self, conn, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game (internal helper).

        This inlines the query from GamePersistence to avoid cross-repository dependencies.
        """

        cursor = conn.execute("""
            SELECT player_name, tilt_state_json, elastic_personality_json, prompt_config_json
            FROM controller_state
            WHERE game_id = ?
        """, (game_id,))

        states = {}
        for row in cursor.fetchall():
            prompt_config = None
            try:
                if row['prompt_config_json']:
                    prompt_config = json.loads(row['prompt_config_json'])
            except (KeyError, IndexError):
                pass

            states[row['player_name']] = {
                'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
                'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None,
                'prompt_config': prompt_config
            }

        return states

    def _load_emotional_state(self, conn, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a player (internal helper)."""

        cursor = conn.execute("""
            SELECT * FROM emotional_state
            WHERE game_id = ? AND player_name = ?
        """, (game_id, player_name))

        row = cursor.fetchone()
        if not row:
            return None

        metadata = json.loads(row['metadata_json']) if row['metadata_json'] else {}

        return {
            'valence': row['valence'],
            'arousal': row['arousal'],
            'control': row['control'],
            'focus': row['focus'],
            'narrative': row['narrative'] or '',
            'inner_voice': row['inner_voice'] or '',
            'generated_at_hand': row['generated_at_hand'],
            'source_events': json.loads(row['source_events']) if row['source_events'] else [],
            'created_at': metadata.get('created_at'),
            'used_fallback': metadata.get('used_fallback', False)
        }

    def _load_controller_state(self, conn, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player (internal helper)."""

        cursor = conn.execute("""
            SELECT tilt_state_json, elastic_personality_json, prompt_config_json
            FROM controller_state
            WHERE game_id = ? AND player_name = ?
        """, (game_id, player_name))

        row = cursor.fetchone()
        if not row:
            return None

        prompt_config = None
        try:
            if row['prompt_config_json']:
                prompt_config = json.loads(row['prompt_config_json'])
        except (KeyError, IndexError):
            logger.warning(f"prompt_config_json column not found for {player_name}, using defaults")

        return {
            'tilt_state': json.loads(row['tilt_state_json']) if row['tilt_state_json'] else None,
            'elastic_personality': json.loads(row['elastic_personality_json']) if row['elastic_personality_json'] else None,
            'prompt_config': prompt_config
        }

    def get_experiment_live_stats(self, experiment_id: int) -> Dict:
        """Get real-time unified stats per variant for running/completed experiments.

        Returns all metrics per variant in one call: latency, decision quality, and progress.
        This is designed to be called on every 5s refresh for running experiments.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dictionary with structure:
            {
                'by_variant': {
                    'Variant Label': {
                        'latency_metrics': { avg_ms, p50_ms, p95_ms, p99_ms, count },
                        'decision_quality': { total, correct, correct_pct, mistakes, avg_ev_lost },
                        'progress': { current_hands, max_hands, progress_pct }
                    },
                    ...
                },
                'overall': { ... same structure ... }
            }
        """
        with self._get_connection() as conn:
            # Get experiment config for max_hands calculation
            exp = self.get_experiment(experiment_id)
            if not exp:
                return {'by_variant': {}, 'overall': None}

            config = exp.get('config', {})
            max_hands = config.get('hands_per_tournament', 100)
            num_tournaments = config.get('num_tournaments', 1)

            # Determine number of variants from control/variants config
            control = config.get('control')
            variants = config.get('variants', [])
            if control is not None:
                # A/B testing mode: control + variants
                num_variant_configs = 1 + len(variants or [])
            else:
                # Legacy mode: single variant
                num_variant_configs = 1

            result = {'by_variant': {}, 'overall': None}

            # Get all variants for this experiment from actual games
            cursor = conn.execute("""
                SELECT DISTINCT variant FROM experiment_games
                WHERE experiment_id = ?
            """, (experiment_id,))
            variant_labels = [row[0] for row in cursor.fetchall()]

            # If no games yet, create placeholder entries from config
            if not variant_labels:
                if control is not None:
                    variant_labels = [control.get('label', 'Control')]
                    for v in (variants or []):
                        variant_labels.append(v.get('label', 'Variant'))
                else:
                    variant_labels = [None]  # Legacy single variant

            # Aggregate stats for overall calculation
            all_latencies = []
            overall_decision = {'total': 0, 'correct': 0, 'mistake': 0, 'ev_lost_sum': 0}
            overall_progress = {'current_hands': 0, 'max_hands': 0}

            for variant in variant_labels:
                variant_key = variant or 'default'

                # Build variant clause
                if variant is None:
                    variant_clause = "AND (eg.variant IS NULL OR eg.variant = '')"
                    variant_params = []
                else:
                    variant_clause = "AND eg.variant = ?"
                    variant_params = [variant]

                # 1. Latency metrics from api_usage
                cursor = conn.execute(f"""
                    SELECT au.latency_ms FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.latency_ms IS NOT NULL
                """, [experiment_id] + variant_params)
                latencies = [row[0] for row in cursor.fetchall()]

                if latencies:
                    latency_metrics = {
                        'avg_ms': round(float(np.mean(latencies)), 2),
                        'p50_ms': round(float(np.percentile(latencies, 50)), 2),
                        'p95_ms': round(float(np.percentile(latencies, 95)), 2),
                        'p99_ms': round(float(np.percentile(latencies, 99)), 2),
                        'count': len(latencies),
                    }
                    all_latencies.extend(latencies)
                else:
                    latency_metrics = None

                # 2. Decision quality from player_decision_analysis
                cursor = conn.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                        SUM(CASE WHEN pda.decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                        AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                    FROM player_decision_analysis pda
                    JOIN experiment_games eg ON pda.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                row = cursor.fetchone()
                total = row[0] or 0

                if total > 0:
                    decision_quality = {
                        'total': total,
                        'correct': row[1] or 0,
                        'correct_pct': round((row[1] or 0) * 100 / total, 1),
                        'mistakes': row[2] or 0,
                        'avg_ev_lost': round(row[3] or 0, 2),
                    }
                    overall_decision['total'] += total
                    overall_decision['correct'] += row[1] or 0
                    overall_decision['mistake'] += row[2] or 0
                    overall_decision['ev_lost_sum'] += (row[3] or 0) * total
                else:
                    decision_quality = None

                # 3. Progress - sum hands across all games for this variant
                cursor = conn.execute(f"""
                    SELECT
                        eg.game_id,
                        COALESCE(MAX(au.hand_number), 0) as max_hand
                    FROM experiment_games eg
                    LEFT JOIN api_usage au ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                    GROUP BY eg.game_id
                """, [experiment_id] + variant_params)
                games_data = cursor.fetchall()
                games_count = len(games_data)
                current_hands = sum(min(row[1], max_hands) for row in games_data)

                variant_max_hands = num_tournaments * max_hands

                progress = {
                    'current_hands': current_hands,
                    'max_hands': variant_max_hands,
                    'games_count': games_count,
                    'games_expected': num_tournaments,
                    'progress_pct': round(current_hands * 100 / variant_max_hands, 1) if variant_max_hands else 0,
                }

                overall_progress['current_hands'] += current_hands
                overall_progress['max_hands'] += variant_max_hands

                # 4. Cost metrics from api_usage
                cursor = conn.execute(f"""
                    SELECT
                        COALESCE(SUM(au.estimated_cost), 0) as total_cost,
                        COUNT(*) as total_calls,
                        COALESCE(AVG(au.estimated_cost), 0) as avg_cost_per_call
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                cost_row = cursor.fetchone()

                # Cost by model
                cursor = conn.execute(f"""
                    SELECT
                        au.provider || '/' || au.model as model_key,
                        SUM(au.estimated_cost) as cost,
                        COUNT(*) as calls
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.estimated_cost IS NOT NULL
                    GROUP BY au.provider, au.model
                """, [experiment_id] + variant_params)
                by_model = {row[0]: {'cost': row[1], 'calls': row[2]} for row in cursor.fetchall()}

                # Cost per decision (player_decision call type)
                cursor = conn.execute(f"""
                    SELECT AVG(au.estimated_cost), COUNT(*)
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.call_type = 'player_decision'
                """, [experiment_id] + variant_params)
                decision_cost_row = cursor.fetchone()

                # Count hands for normalized cost
                cursor = conn.execute(f"""
                    SELECT COUNT(DISTINCT au.game_id || '-' || au.hand_number) as total_hands
                    FROM api_usage au
                    JOIN experiment_games eg ON au.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause} AND au.hand_number IS NOT NULL
                """, [experiment_id] + variant_params)
                hand_row = cursor.fetchone()
                total_hands_for_cost = hand_row[0] or 1

                cost_metrics = {
                    'total_cost': round(cost_row[0] or 0, 6),
                    'total_calls': cost_row[1] or 0,
                    'avg_cost_per_call': round(cost_row[2] or 0, 8),
                    'by_model': by_model,
                    'avg_cost_per_decision': round(decision_cost_row[0] or 0, 8) if decision_cost_row[0] else 0,
                    'total_decisions': decision_cost_row[1] or 0,
                    'cost_per_hand': round((cost_row[0] or 0) / total_hands_for_cost, 6),
                    'total_hands': total_hands_for_cost,
                }

                # 5. Quality indicators from player_decision_analysis + prompt_captures
                cursor = conn.execute(f"""
                    SELECT
                        SUM(CASE WHEN action_taken = 'fold' AND decision_quality = 'mistake' THEN 1 ELSE 0 END) as fold_mistakes,
                        SUM(CASE WHEN action_taken = 'all_in' THEN 1 ELSE 0 END) as total_all_ins,
                        SUM(CASE WHEN action_taken = 'fold' THEN 1 ELSE 0 END) as total_folds,
                        COUNT(*) as total_decisions
                    FROM player_decision_analysis pda
                    JOIN experiment_games eg ON pda.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                qi_row = cursor.fetchone()

                # Query all-ins with AI response data for smarter categorization
                cursor = conn.execute(f"""
                    SELECT
                        pc.stack_bb,
                        pc.ai_response,
                        pda.equity
                    FROM prompt_captures pc
                    JOIN experiment_games eg ON pc.game_id = eg.game_id
                    LEFT JOIN player_decision_analysis pda
                        ON pc.game_id = pda.game_id
                        AND pc.hand_number = pda.hand_number
                        AND pc.player_name = pda.player_name
                        AND pc.phase = pda.phase
                    WHERE eg.experiment_id = ? {variant_clause}
                      AND pc.action_taken = 'all_in'
                """, [experiment_id] + variant_params)

                # Use shared categorization logic
                from poker.quality_metrics import compute_allin_categorizations
                suspicious_allins, marginal_allins = compute_allin_categorizations(cursor.fetchall())

                # 6. Survival metrics from tournament_standings
                cursor = conn.execute(f"""
                    SELECT
                        SUM(COALESCE(ts.times_eliminated, 0)) as total_eliminations,
                        SUM(COALESCE(ts.all_in_wins, 0)) as total_all_in_wins,
                        SUM(COALESCE(ts.all_in_losses, 0)) as total_all_in_losses,
                        COUNT(*) as total_standings
                    FROM tournament_standings ts
                    JOIN experiment_games eg ON ts.game_id = eg.game_id
                    WHERE eg.experiment_id = ? {variant_clause}
                """, [experiment_id] + variant_params)
                survival_row = cursor.fetchone()

                quality_indicators = None
                if qi_row and qi_row[3] > 0:
                    fold_mistakes = qi_row[0] or 0
                    total_all_ins = qi_row[1] or 0
                    total_folds = qi_row[2] or 0
                    total_decisions = qi_row[3]

                    # Survival metrics
                    total_eliminations = survival_row[0] or 0 if survival_row else 0
                    total_all_in_wins = survival_row[1] or 0 if survival_row else 0
                    total_all_in_losses = survival_row[2] or 0 if survival_row else 0
                    total_all_in_showdowns = total_all_in_wins + total_all_in_losses

                    quality_indicators = {
                        'suspicious_allins': suspicious_allins,
                        'marginal_allins': marginal_allins,
                        'fold_mistakes': fold_mistakes,
                        'fold_mistake_rate': round(fold_mistakes * 100 / total_folds, 1) if total_folds > 0 else 0,
                        'total_all_ins': total_all_ins,
                        'total_folds': total_folds,
                        'total_decisions': total_decisions,
                        # Survival metrics
                        'total_eliminations': total_eliminations,
                        'all_in_wins': total_all_in_wins,
                        'all_in_losses': total_all_in_losses,
                        'all_in_survival_rate': round(total_all_in_wins * 100 / total_all_in_showdowns, 1) if total_all_in_showdowns > 0 else None,
                    }

                result['by_variant'][variant_key] = {
                    'latency_metrics': latency_metrics,
                    'decision_quality': decision_quality,
                    'progress': progress,
                    'cost_metrics': cost_metrics,
                    'quality_indicators': quality_indicators,
                }

            # Compute overall stats
            if all_latencies:
                overall_latency = {
                    'avg_ms': round(float(np.mean(all_latencies)), 2),
                    'p50_ms': round(float(np.percentile(all_latencies, 50)), 2),
                    'p95_ms': round(float(np.percentile(all_latencies, 95)), 2),
                    'p99_ms': round(float(np.percentile(all_latencies, 99)), 2),
                    'count': len(all_latencies),
                }
            else:
                overall_latency = None

            if overall_decision['total'] > 0:
                overall_decision_quality = {
                    'total': overall_decision['total'],
                    'correct': overall_decision['correct'],
                    'correct_pct': round(overall_decision['correct'] * 100 / overall_decision['total'], 1),
                    'mistakes': overall_decision['mistake'],
                    'avg_ev_lost': round(overall_decision['ev_lost_sum'] / overall_decision['total'], 2),
                }
            else:
                overall_decision_quality = None

            overall_progress_result = {
                'current_hands': overall_progress['current_hands'],
                'max_hands': overall_progress['max_hands'],
                'progress_pct': round(overall_progress['current_hands'] * 100 / overall_progress['max_hands'], 1) if overall_progress['max_hands'] else 0,
            }

            # Overall cost metrics
            cursor = conn.execute("""
                SELECT
                    COALESCE(SUM(au.estimated_cost), 0) as total_cost,
                    COUNT(*) as total_calls,
                    COALESCE(AVG(au.estimated_cost), 0) as avg_cost_per_call
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ?
            """, (experiment_id,))
            overall_cost_row = cursor.fetchone()

            cursor = conn.execute("""
                SELECT
                    au.provider || '/' || au.model as model_key,
                    SUM(au.estimated_cost) as cost,
                    COUNT(*) as calls
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.estimated_cost IS NOT NULL
                GROUP BY au.provider, au.model
            """, (experiment_id,))
            overall_by_model = {row[0]: {'cost': row[1], 'calls': row[2]} for row in cursor.fetchall()}

            cursor = conn.execute("""
                SELECT AVG(au.estimated_cost), COUNT(*)
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.call_type = 'player_decision'
            """, (experiment_id,))
            overall_decision_cost_row = cursor.fetchone()

            cursor = conn.execute("""
                SELECT COUNT(DISTINCT au.game_id || '-' || au.hand_number) as total_hands
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.hand_number IS NOT NULL
            """, (experiment_id,))
            overall_hand_row = cursor.fetchone()
            overall_total_hands = overall_hand_row[0] or 1

            overall_cost_metrics = {
                'total_cost': round(overall_cost_row[0] or 0, 6),
                'total_calls': overall_cost_row[1] or 0,
                'avg_cost_per_call': round(overall_cost_row[2] or 0, 8),
                'by_model': overall_by_model,
                'avg_cost_per_decision': round(overall_decision_cost_row[0] or 0, 8) if overall_decision_cost_row[0] else 0,
                'total_decisions': overall_decision_cost_row[1] or 0,
                'cost_per_hand': round((overall_cost_row[0] or 0) / overall_total_hands, 6),
                'total_hands': overall_total_hands,
            }

            # Overall quality indicators
            cursor = conn.execute("""
                SELECT
                    SUM(CASE WHEN action_taken = 'fold' AND decision_quality = 'mistake' THEN 1 ELSE 0 END) as fold_mistakes,
                    SUM(CASE WHEN action_taken = 'all_in' THEN 1 ELSE 0 END) as total_all_ins,
                    SUM(CASE WHEN action_taken = 'fold' THEN 1 ELSE 0 END) as total_folds,
                    COUNT(*) as total_decisions
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ?
            """, (experiment_id,))
            overall_qi_row = cursor.fetchone()

            # Query all-ins for smarter categorization (overall)
            cursor = conn.execute("""
                SELECT
                    pc.stack_bb,
                    pc.ai_response,
                    pda.equity
                FROM prompt_captures pc
                JOIN experiment_games eg ON pc.game_id = eg.game_id
                LEFT JOIN player_decision_analysis pda
                    ON pc.game_id = pda.game_id
                    AND pc.hand_number = pda.hand_number
                    AND pc.player_name = pda.player_name
                    AND pc.phase = pda.phase
                WHERE eg.experiment_id = ?
                  AND pc.action_taken = 'all_in'
            """, (experiment_id,))

            from poker.quality_metrics import compute_allin_categorizations
            overall_suspicious_allins, overall_marginal_allins = compute_allin_categorizations(cursor.fetchall())

            overall_quality_indicators = None
            if overall_qi_row and overall_qi_row[3] > 0:
                fold_mistakes = overall_qi_row[0] or 0
                total_all_ins = overall_qi_row[1] or 0
                total_folds = overall_qi_row[2] or 0
                total_decisions = overall_qi_row[3]

                overall_quality_indicators = {
                    'suspicious_allins': overall_suspicious_allins,
                    'marginal_allins': overall_marginal_allins,
                    'fold_mistakes': fold_mistakes,
                    'fold_mistake_rate': round(fold_mistakes * 100 / total_folds, 1) if total_folds > 0 else 0,
                    'total_all_ins': total_all_ins,
                    'total_folds': total_folds,
                    'total_decisions': total_decisions,
                }

            result['overall'] = {
                'latency_metrics': overall_latency,
                'decision_quality': overall_decision_quality,
                'progress': overall_progress_result,
                'cost_metrics': overall_cost_metrics,
                'quality_indicators': overall_quality_indicators,
            }

            return result

    def get_experiment_game_snapshots(self, experiment_id: int) -> List[Dict]:
        """Load current game states for all running games in an experiment.

        This method provides live game snapshots for the monitoring view,
        including player states, community cards, pot, and psychology data.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of dictionaries with game snapshots
        """
        with self._get_connection() as conn:


            # Get all games for this experiment (stable order by game_id)
            cursor = conn.execute("""
                SELECT eg.game_id, eg.variant, g.game_state_json, g.phase, g.updated_at
                FROM experiment_games eg
                JOIN games g ON eg.game_id = g.game_id
                WHERE eg.experiment_id = ?
                ORDER BY eg.game_id
            """, (experiment_id,))

            games = []
            for row in cursor.fetchall():
                game_id = row['game_id']
                variant = row['variant']

                try:
                    state_dict = json.loads(row['game_state_json'])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse game state for {game_id}")
                    continue

                # Extract basic game info
                phase = row['phase']
                pot = state_dict.get('pot', {})
                pot_total = pot.get('total', 0) if isinstance(pot, dict) else pot

                # Get community cards
                community_cards = state_dict.get('community_cards', [])

                # Get current player index
                current_player_idx = state_dict.get('current_player_idx', 0)

                # Load psychology data for all players in this game
                psychology_data = self._load_all_controller_states(conn, game_id)
                emotional_data = self._load_all_emotional_states(conn, game_id)

                # Load LLM debug info from most recent api_usage records per player
                llm_debug_cursor = conn.execute("""
                    SELECT player_name, provider, model, reasoning_effort,
                           COUNT(*) as total_calls,
                           AVG(latency_ms) as avg_latency_ms,
                           AVG(estimated_cost) as avg_cost
                    FROM api_usage
                    WHERE game_id = ?
                    GROUP BY player_name
                """, (game_id,))
                llm_debug_by_player = {}
                for llm_row in llm_debug_cursor.fetchall():
                    if llm_row['player_name']:
                        llm_debug_by_player[llm_row['player_name']] = {
                            'provider': llm_row['provider'],
                            'model': llm_row['model'],
                            'reasoning_effort': llm_row['reasoning_effort'],
                            'total_calls': llm_row['total_calls'],
                            'avg_latency_ms': round(llm_row['avg_latency_ms'] or 0, 2),
                            'avg_cost_per_call': round(llm_row['avg_cost'] or 0, 6),
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
                        # Convert tilt_level from 0.0-1.0 to 0-100 percentage
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
                        'is_eliminated': p.get('stack', 0) == 0,
                        'seat_index': idx,  # Fixed seat position for monitoring
                        'psychology': psychology,
                        'llm_debug': llm_debug_by_player.get(player_name, {}),
                    })

                # Get hand number from api_usage (most recent)
                hand_cursor = conn.execute("""
                    SELECT MAX(hand_number) as hand_number
                    FROM api_usage WHERE game_id = ?
                """, (game_id,))
                hand_row = hand_cursor.fetchone()
                hand_number = hand_row['hand_number'] if hand_row and hand_row['hand_number'] else 1

                games.append({
                    'game_id': game_id,
                    'variant': variant,
                    'phase': phase,
                    'hand_number': hand_number,
                    'pot': pot_total,
                    'community_cards': community_cards,
                    'players': players,
                    'total_seats': len(players),  # Fixed seat count for positioning
                })

            return games

    def get_experiment_player_detail(
        self,
        experiment_id: int,
        game_id: str,
        player_name: str
    ) -> Optional[Dict]:
        """Get detailed player info for the drill-down panel.

        Args:
            experiment_id: The experiment ID
            game_id: The game ID
            player_name: The player name

        Returns:
            Dictionary with detailed player info or None if not found
        """
        with self._get_connection() as conn:


            # Verify game belongs to experiment and get variant config
            cursor = conn.execute("""
                SELECT eg.id, eg.variant_config_json FROM experiment_games eg
                WHERE eg.experiment_id = ? AND eg.game_id = ?
            """, (experiment_id, game_id))
            eg_row = cursor.fetchone()
            if not eg_row:
                return None

            # Check if psychology is enabled for this variant
            variant_config = {}
            if eg_row['variant_config_json']:
                try:
                    variant_config = json.loads(eg_row['variant_config_json'])
                except (json.JSONDecodeError, TypeError):
                    pass
            psychology_enabled = variant_config.get('enable_psychology', False)

            # Load game state for player info
            cursor = conn.execute(
                "SELECT game_state_json FROM games WHERE game_id = ?",
                (game_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            try:
                state_dict = json.loads(row['game_state_json'])
            except json.JSONDecodeError:
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
            ctrl_state = self._load_controller_state(conn, game_id, player_name)
            emo_state = self._load_emotional_state(conn, game_id, player_name)

            tilt_state = ctrl_state.get('tilt_state', {}) if ctrl_state else {}
            psychology = {
                'narrative': emo_state.get('narrative', '') if emo_state else '',
                'inner_voice': emo_state.get('inner_voice', '') if emo_state else '',
                # Convert tilt_level from 0.0-1.0 to 0-100 percentage
                'tilt_level': round((tilt_state.get('tilt_level', 0) if tilt_state else 0) * 100),
                'tilt_category': tilt_state.get('category', 'none') if tilt_state else 'none',
                'tilt_source': tilt_state.get('source', '') if tilt_state else '',
            }

            # Get LLM debug info
            cursor = conn.execute("""
                SELECT provider, model, reasoning_effort,
                       COUNT(*) as total_calls,
                       AVG(latency_ms) as avg_latency_ms,
                       AVG(estimated_cost) as avg_cost
                FROM api_usage
                WHERE game_id = ? AND player_name = ?
                GROUP BY provider, model
            """, (game_id, player_name))
            llm_row = cursor.fetchone()

            llm_debug = {}
            if llm_row:
                # Also get percentile latencies
                cursor = conn.execute("""
                    SELECT latency_ms FROM api_usage
                    WHERE game_id = ? AND player_name = ? AND latency_ms IS NOT NULL
                    ORDER BY latency_ms
                """, (game_id, player_name))
                latencies = [r['latency_ms'] for r in cursor.fetchall()]

                p95 = 0
                p99 = 0
                if latencies:
                    p95 = round(float(np.percentile(latencies, 95)), 2) if len(latencies) >= 5 else max(latencies)
                    p99 = round(float(np.percentile(latencies, 99)), 2) if len(latencies) >= 10 else max(latencies)

                llm_debug = {
                    'provider': llm_row['provider'],
                    'model': llm_row['model'],
                    'reasoning_effort': llm_row['reasoning_effort'],
                    'total_calls': llm_row['total_calls'],
                    'avg_latency_ms': round(llm_row['avg_latency_ms'] or 0, 2),
                    'p95_latency_ms': p95,
                    'p99_latency_ms': p99,
                    'avg_cost_per_call': round(llm_row['avg_cost'] or 0, 6),
                }

            # Get play style from opponent models (observed by any player)
            cursor = conn.execute("""
                SELECT hands_observed, vpip, pfr, aggression_factor
                FROM opponent_models
                WHERE game_id = ? AND opponent_name = ?
                ORDER BY hands_observed DESC
                LIMIT 1
            """, (game_id, player_name))
            opp_row = cursor.fetchone()

            play_style = {}
            if opp_row:
                vpip = round(opp_row['vpip'] * 100, 1)
                pfr = round(opp_row['pfr'] * 100, 1)
                af = round(opp_row['aggression_factor'], 2)

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
                    'hands_observed': opp_row['hands_observed'],
                    'summary': summary,
                }

            # Get recent decisions
            cursor = conn.execute("""
                SELECT hand_number, phase, action_taken, decision_quality, ev_lost
                FROM player_decision_analysis
                WHERE game_id = ? AND player_name = ?
                ORDER BY created_at DESC
                LIMIT 5
            """, (game_id, player_name))

            recent_decisions = [
                {
                    'hand_number': r['hand_number'],
                    'phase': r['phase'],
                    'action': r['action_taken'],
                    'decision_quality': r['decision_quality'],
                    'ev_lost': round(r['ev_lost'] or 0, 2) if r['ev_lost'] else None,
                }
                for r in cursor.fetchall()
            ]

            return {
                'player': {
                    'name': player_name,
                    'stack': player_data.get('stack', 0),
                    'cards': player_data.get('hand', []),
                },
                'psychology': psychology,
                'psychology_enabled': psychology_enabled,
                'llm_debug': llm_debug,
                'play_style': play_style,
                'recent_decisions': recent_decisions,
            }

    # ==================== Replay Experiment Methods (B4b) ====================

    def create_replay_experiment(
        self,
        name: str,
        capture_ids: List[int],
        variants: List[Dict[str, Any]],
        description: Optional[str] = None,
        hypothesis: Optional[str] = None,
        tags: Optional[List[str]] = None,
        parent_experiment_id: Optional[int] = None
    ) -> int:
        """Create a new replay experiment.

        Args:
            name: Unique experiment name
            capture_ids: List of prompt_captures IDs to replay
            variants: List of variant configurations
            description: Optional experiment description
            hypothesis: Optional hypothesis being tested
            tags: Optional list of tags
            parent_experiment_id: Optional parent for lineage tracking

        Returns:
            The experiment_id of the created record
        """
        config = {
            'name': name,
            'description': description,
            'hypothesis': hypothesis,
            'tags': tags or [],
            'experiment_type': 'replay',
            'capture_selection': {
                'mode': 'ids',
                'ids': capture_ids
            },
            'variants': variants
        }

        with self._get_connection() as conn:
            # Create the experiment record
            cursor = conn.execute("""
                INSERT INTO experiments (
                    name, description, hypothesis, tags, notes,
                    config_json, experiment_type, parent_experiment_id
                )
                VALUES (?, ?, ?, ?, ?, ?, 'replay', ?)
            """, (
                name,
                description,
                hypothesis,
                json.dumps(tags or []),
                None,  # notes
                json.dumps(config),
                parent_experiment_id,
            ))
            experiment_id = cursor.lastrowid

            # Link captures to the experiment
            for capture_id in capture_ids:
                # Get original capture info for reference
                capture_cursor = conn.execute("""
                    SELECT action_taken FROM prompt_captures WHERE id = ?
                """, (capture_id,))
                capture_row = capture_cursor.fetchone()
                original_action = capture_row[0] if capture_row else None

                # Get decision analysis if available
                analysis_cursor = conn.execute("""
                    SELECT decision_quality, ev_lost FROM player_decision_analysis
                    WHERE capture_id = ?
                """, (capture_id,))
                analysis_row = analysis_cursor.fetchone()
                original_quality = analysis_row[0] if analysis_row else None
                original_ev_lost = analysis_row[1] if analysis_row else None

                conn.execute("""
                    INSERT INTO replay_experiment_captures (
                        experiment_id, capture_id, original_action,
                        original_quality, original_ev_lost
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (experiment_id, capture_id, original_action, original_quality, original_ev_lost))

            logger.info(f"Created replay experiment '{name}' with id {experiment_id}, {len(capture_ids)} captures")
            return experiment_id

    def add_replay_result(
        self,
        experiment_id: int,
        capture_id: int,
        variant: str,
        new_response: str,
        new_action: str,
        new_raise_amount: Optional[int] = None,
        new_quality: Optional[str] = None,
        new_ev_lost: Optional[float] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> int:
        """Add a result from replaying a capture with a variant.

        Args:
            experiment_id: The experiment ID
            capture_id: The prompt_captures ID
            variant: The variant label
            new_response: The new AI response
            new_action: The new action taken
            new_raise_amount: Optional new raise amount
            new_quality: Optional quality assessment
            new_ev_lost: Optional EV lost calculation
            provider: LLM provider used
            model: Model used
            reasoning_effort: Reasoning effort setting
            input_tokens: Input token count
            output_tokens: Output token count
            latency_ms: Response latency
            error_message: Error if the replay failed

        Returns:
            The replay_results record ID
        """
        with self._get_connection() as conn:
            # Get original action and quality for comparison
            cursor = conn.execute("""
                SELECT original_action, original_quality, original_ev_lost
                FROM replay_experiment_captures
                WHERE experiment_id = ? AND capture_id = ?
            """, (experiment_id, capture_id))
            row = cursor.fetchone()
            original_action = row[0] if row else None
            original_quality = row[1] if row else None
            original_ev_lost = row[2] if row else None

            # Determine if action changed
            action_changed = new_action != original_action if original_action else None

            # Determine quality change
            quality_change = None
            if original_quality and new_quality:
                if original_quality == 'mistake' and new_quality != 'mistake':
                    quality_change = 'improved'
                elif original_quality != 'mistake' and new_quality == 'mistake':
                    quality_change = 'degraded'
                else:
                    quality_change = 'unchanged'

            # Calculate EV delta
            ev_delta = None
            if original_ev_lost is not None and new_ev_lost is not None:
                ev_delta = original_ev_lost - new_ev_lost  # Positive = improvement

            cursor = conn.execute("""
                INSERT INTO replay_results (
                    experiment_id, capture_id, variant, new_response, new_action,
                    new_raise_amount, new_quality, new_ev_lost, action_changed,
                    quality_change, ev_delta, provider, model, reasoning_effort,
                    input_tokens, output_tokens, latency_ms, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                experiment_id, capture_id, variant, new_response, new_action,
                new_raise_amount, new_quality, new_ev_lost, action_changed,
                quality_change, ev_delta, provider, model, reasoning_effort,
                input_tokens, output_tokens, latency_ms, error_message
            ))
            return cursor.lastrowid

    def get_replay_experiment(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """Get a replay experiment with its captures and progress.

        Args:
            experiment_id: The experiment ID

        Returns:
            Experiment data with capture count and result progress, or None
        """
        with self._get_connection() as conn:


            # Get experiment
            cursor = conn.execute("""
                SELECT * FROM experiments WHERE id = ? AND experiment_type = 'replay'
            """, (experiment_id,))
            row = cursor.fetchone()
            if not row:
                return None

            experiment = dict(row)

            # Parse JSON fields
            for field in ['config_json', 'summary_json', 'tags']:
                if experiment.get(field):
                    try:
                        experiment[field] = json.loads(experiment[field])
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse JSON for field '%s' in experiment id=%s", field, experiment_id)

            # Get capture count
            cursor = conn.execute("""
                SELECT COUNT(*) FROM replay_experiment_captures
                WHERE experiment_id = ?
            """, (experiment_id,))
            experiment['capture_count'] = cursor.fetchone()[0]

            # Get variants from config
            config = experiment.get('config_json', {})
            variants = config.get('variants', []) if isinstance(config, dict) else []
            experiment['variant_count'] = len(variants)

            # Get result progress
            cursor = conn.execute("""
                SELECT COUNT(*) FROM replay_results WHERE experiment_id = ?
            """, (experiment_id,))
            experiment['results_completed'] = cursor.fetchone()[0]
            experiment['results_total'] = experiment['capture_count'] * experiment['variant_count']

            return experiment

    def get_replay_results(
        self,
        experiment_id: int,
        variant: Optional[str] = None,
        quality_change: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Get replay results for an experiment.

        Args:
            experiment_id: The experiment ID
            variant: Optional filter by variant
            quality_change: Optional filter by quality change ('improved', 'degraded', 'unchanged')
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'results' list and 'total' count
        """
        conditions = ["replay_results.experiment_id = ?"]
        params = [experiment_id]

        if variant:
            conditions.append("replay_results.variant = ?")
            params.append(variant)
        if quality_change:
            conditions.append("replay_results.quality_change = ?")
            params.append(quality_change)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self._get_connection() as conn:


            # Get total count
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM replay_results {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # Get results with pagination
            cursor = conn.execute(f"""
                SELECT replay_results.*, pc.player_name, pc.phase, pc.pot_odds,
                       rec.original_action, rec.original_quality, rec.original_ev_lost
                FROM replay_results
                JOIN replay_experiment_captures rec
                    ON rec.experiment_id = replay_results.experiment_id
                    AND rec.capture_id = replay_results.capture_id
                JOIN prompt_captures pc ON pc.id = replay_results.capture_id
                {where_clause}
                ORDER BY replay_results.created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            results = [dict(row) for row in cursor.fetchall()]

            return {
                'results': results,
                'total': total
            }

    def get_replay_results_summary(self, experiment_id: int) -> Dict[str, Any]:
        """Get summary statistics for replay experiment results.

        Args:
            experiment_id: The experiment ID

        Returns:
            Dict with summary statistics by variant
        """
        with self._get_connection() as conn:
            # Overall stats
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_results,
                    SUM(CASE WHEN action_changed = 1 THEN 1 ELSE 0 END) as actions_changed,
                    SUM(CASE WHEN quality_change = 'improved' THEN 1 ELSE 0 END) as improved,
                    SUM(CASE WHEN quality_change = 'degraded' THEN 1 ELSE 0 END) as degraded,
                    SUM(CASE WHEN quality_change = 'unchanged' THEN 1 ELSE 0 END) as unchanged,
                    AVG(ev_delta) as avg_ev_delta,
                    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as errors
                FROM replay_results
                WHERE experiment_id = ?
            """, (experiment_id,))
            row = cursor.fetchone()

            overall = {
                'total_results': row[0] or 0,
                'actions_changed': row[1] or 0,
                'improved': row[2] or 0,
                'degraded': row[3] or 0,
                'unchanged': row[4] or 0,
                'avg_ev_delta': row[5],
                'errors': row[6] or 0,
            }

            # Stats by variant
            cursor = conn.execute("""
                SELECT
                    variant,
                    COUNT(*) as total,
                    SUM(CASE WHEN action_changed = 1 THEN 1 ELSE 0 END) as actions_changed,
                    SUM(CASE WHEN quality_change = 'improved' THEN 1 ELSE 0 END) as improved,
                    SUM(CASE WHEN quality_change = 'degraded' THEN 1 ELSE 0 END) as degraded,
                    AVG(ev_delta) as avg_ev_delta,
                    AVG(latency_ms) as avg_latency,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as errors
                FROM replay_results
                WHERE experiment_id = ?
                GROUP BY variant
            """, (experiment_id,))

            by_variant = {}
            for row in cursor.fetchall():
                by_variant[row[0]] = {
                    'total': row[1],
                    'actions_changed': row[2] or 0,
                    'improved': row[3] or 0,
                    'degraded': row[4] or 0,
                    'avg_ev_delta': row[5],
                    'avg_latency': row[6],
                    'total_input_tokens': row[7] or 0,
                    'total_output_tokens': row[8] or 0,
                    'errors': row[9] or 0,
                }

            return {
                'overall': overall,
                'by_variant': by_variant
            }

    def get_replay_experiment_captures(self, experiment_id: int) -> List[Dict[str, Any]]:
        """Get the captures linked to a replay experiment.

        Args:
            experiment_id: The experiment ID

        Returns:
            List of capture details with original info
        """
        with self._get_connection() as conn:

            cursor = conn.execute("""
                SELECT rec.*, pc.player_name, pc.phase, pc.pot_odds,
                       pc.pot_total, pc.cost_to_call, pc.player_stack,
                       pc.model as original_model, pc.provider as original_provider
                FROM replay_experiment_captures rec
                JOIN prompt_captures pc ON pc.id = rec.capture_id
                WHERE rec.experiment_id = ?
                ORDER BY pc.created_at DESC
            """, (experiment_id,))

            return [dict(row) for row in cursor.fetchall()]

    def list_replay_experiments(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List replay experiments.

        Args:
            status: Optional filter by status
            limit: Maximum results to return
            offset: Pagination offset

        Returns:
            Dict with 'experiments' list and 'total' count
        """
        conditions = ["experiment_type = 'replay'"]
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self._get_connection() as conn:


            # Get total count
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM experiments {where_clause}
            """, params)
            total = cursor.fetchone()[0]

            # Get experiments with pagination
            cursor = conn.execute(f"""
                SELECT e.*,
                    (SELECT COUNT(*) FROM replay_experiment_captures rec WHERE rec.experiment_id = e.id) as capture_count,
                    (SELECT COUNT(*) FROM replay_results rr WHERE rr.experiment_id = e.id) as results_completed
                FROM experiments e
                {where_clause}
                ORDER BY e.created_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            experiments = []
            for row in cursor.fetchall():
                exp = dict(row)
                # Parse JSON fields
                for field in ['config_json', 'summary_json', 'tags']:
                    if exp.get(field):
                        try:
                            exp[field] = json.loads(exp[field])
                        except json.JSONDecodeError:
                            logger.debug("Failed to parse JSON for field '%s' in experiment id=%s", field, exp.get('id'))

                # Calculate variant count from config
                config = exp.get('config_json', {})
                variants = config.get('variants', []) if isinstance(config, dict) else []
                exp['variant_count'] = len(variants)
                exp['results_total'] = exp['capture_count'] * exp['variant_count']

                experiments.append(exp)

            return {
                'experiments': experiments,
                'total': total
            }
