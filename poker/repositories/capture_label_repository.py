"""Repository for capture label persistence.

Covers the capture_labels table and label-based searching of prompt_captures.
"""
from __future__ import annotations

import sqlite3
import json
import logging
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from poker.repositories.prompt_capture_repository import PromptCaptureRepository

from poker.repositories.base_repository import BaseRepository
from poker.repositories.repository_utils import build_where_clause

logger = logging.getLogger(__name__)


class CaptureLabelRepository(BaseRepository):
    """Handles capture label operations and label-based capture searches."""

    def __init__(self, db_path: str, prompt_capture_repo: PromptCaptureRepository):
        super().__init__(db_path)
        self._prompt_capture_repo = prompt_capture_repo

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

        where_clause = build_where_clause(conditions)

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
            return self._prompt_capture_repo.list_prompt_captures(
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

        where_clause = build_where_clause(conditions)

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
