"""Repository for prompt capture persistence.

Covers the prompt_captures table including game captures and playground captures.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository
from poker.repositories.repository_utils import parse_json_fields, build_where_clause

logger = logging.getLogger(__name__)


class PromptCaptureRepository(BaseRepository):
    """Handles prompt capture storage, retrieval, and statistics."""

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
            parse_json_fields(
                capture,
                ['community_cards', 'player_hand', 'valid_actions', 'tags', 'conversation_history'],
                context=f"prompt capture {capture.get('id')}"
            )

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
        display_emotion: Optional[str] = None,
        min_tilt_level: Optional[float] = None,
        max_tilt_level: Optional[float] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Dict[str, Any]:
        """List prompt captures with optional filtering.

        Args:
            error_type: Filter by specific error type (e.g., 'malformed_json', 'missing_field')
            has_error: Filter to captures with errors (True) or without errors (False)
            is_correction: Filter to correction attempts only (True) or original only (False)
            display_emotion: Filter by display emotion (e.g., 'confident', 'tilted')
            min_tilt_level: Filter by minimum tilt level
            max_tilt_level: Filter by maximum tilt level

        Returns:
            Dict with 'captures' list and 'total' count.
        """
        conditions = []
        params = []

        # Determine if we need the psychology join
        needs_psychology_join = any([display_emotion, min_tilt_level is not None, max_tilt_level is not None])

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
        if tags:
            # Match any of the provided tags
            tag_conditions = []
            for tag in tags:
                tag_conditions.append("pc.tags LIKE ?")
                params.append(f'%"{tag}"%')
            conditions.append(f"({' OR '.join(tag_conditions)})")

        # Psychology filters (require join)
        if display_emotion:
            conditions.append("pda.display_emotion = ?")
            params.append(display_emotion)
        if min_tilt_level is not None:
            conditions.append("pda.tilt_level >= ?")
            params.append(min_tilt_level)
        if max_tilt_level is not None:
            conditions.append("pda.tilt_level <= ?")
            params.append(max_tilt_level)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        join_clause = "LEFT JOIN player_decision_analysis pda ON pda.capture_id = pc.id" if needs_psychology_join else ""

        with self._get_connection() as conn:

            # Get total count
            count_cursor = conn.execute(
                f"SELECT COUNT(DISTINCT pc.id) FROM prompt_captures pc {join_clause} {where_clause}",
                params
            )
            total = count_cursor.fetchone()[0]

            # Get captures with pagination
            query = f"""
                SELECT DISTINCT pc.id, pc.created_at, pc.game_id, pc.player_name, pc.hand_number, pc.phase,
                       pc.action_taken, pc.pot_total, pc.cost_to_call, pc.pot_odds, pc.player_stack,
                       pc.community_cards, pc.player_hand, pc.model, pc.provider, pc.latency_ms, pc.tags, pc.notes,
                       pc.error_type, pc.error_description, pc.parent_id, pc.correction_attempt
                FROM prompt_captures pc
                {join_clause}
                {where_clause}
                ORDER BY pc.created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            cursor = conn.execute(query, params)

            captures = []
            for row in cursor.fetchall():
                capture = dict(row)
                parse_json_fields(
                    capture,
                    ['community_cards', 'player_hand', 'tags'],
                    context=f"prompt capture {capture.get('id')}"
                )
                captures.append(capture)

            return {
                'captures': captures,
                'total': total
            }

    def get_distinct_emotions(self) -> List[str]:
        """Get distinct display_emotion values from player_decision_analysis."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT display_emotion FROM player_decision_analysis "
                "WHERE display_emotion IS NOT NULL ORDER BY display_emotion"
            )
            return [row[0] for row in cursor.fetchall()]

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

        where_clause = build_where_clause(conditions)

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

        where_clause = build_where_clause(conditions)

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

        where_clause = build_where_clause(conditions)

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
