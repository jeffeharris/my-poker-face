"""Repository for experiment-related persistence (Part 1: captures, decisions, presets, labels).

Manages the prompt_captures, player_decision_analysis, prompt_presets,
and capture_labels tables.
Extracted from GamePersistence as part of T3-35-B4a.
"""
import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any

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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row

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
            conn.row_factory = sqlite3.Row

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
            conn.row_factory = sqlite3.Row

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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row

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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row
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
            conn.row_factory = sqlite3.Row

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
