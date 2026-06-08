"""Repository for decision label persistence.

Covers the `decision_labels` table — tags/labels keyed on the decision spine
(`player_decision_analysis.id`) so EVERY decision (human, tiered/sharp, rule,
or LLM) is taggable, not just LLM prompt captures.

Two id-spaces meet here:

* The Decision Analyzer works natively in decision-id space — use
  `add_labels` / `remove_labels` / `get_labels` / `get_labels_for_decisions`.
* The Prompt Playground (capture selector + replay experiments) works in
  capture-id space. The `*_by_capture` bridges and `search_captures_with_labels`
  translate through `player_decision_analysis.capture_id`; only LLM decisions
  (the ones with a capture) are reachable that way, which is correct — replay
  can only re-run a captured prompt.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from poker.repositories.prompt_capture_repository import PromptCaptureRepository

from poker.repositories.base_repository import BaseRepository
from poker.repositories.repository_utils import build_where_clause

logger = logging.getLogger(__name__)


class CaptureLabelRepository(BaseRepository):
    """Handles decision label operations and label-based capture searches."""

    def __init__(self, db_path: str, prompt_capture_repo: PromptCaptureRepository):
        super().__init__(db_path)
        self._prompt_capture_repo = prompt_capture_repo

    # ------------------------------------------------------------------
    # Decision-id space (the canonical surface)
    # ------------------------------------------------------------------

    def add_labels(
        self, decision_id: int, labels: List[str], label_type: str = 'user'
    ) -> List[str]:
        """Add labels to a decision.

        Args:
            decision_id: The player_decision_analysis ID
            labels: List of label strings to add
            label_type: 'user' for manual, 'auto' for auto-generated

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
                    conn.execute(
                        """
                        INSERT INTO decision_labels (decision_id, label, label_type)
                        VALUES (?, ?, ?)
                    """,
                        (decision_id, label, label_type),
                    )
                    added.append(label)
                except sqlite3.IntegrityError:
                    # Label already exists for this decision, skip
                    pass
        if added:
            logger.debug(f"Added labels {added} to decision {decision_id}")
        return added

    def remove_labels(self, decision_id: int, labels: List[str]) -> int:
        """Remove labels from a decision.

        Returns:
            Number of labels that were removed
        """
        with self._get_connection() as conn:
            total_removed = 0
            for label in labels:
                label = label.strip().lower()
                if not label:
                    continue
                cursor = conn.execute(
                    """
                    DELETE FROM decision_labels
                    WHERE decision_id = ? AND label = ?
                """,
                    (decision_id, label),
                )
                total_removed += cursor.rowcount
        if total_removed:
            logger.debug(f"Removed {total_removed} label(s) from decision {decision_id}")
        return total_removed

    def get_labels(self, decision_id: int) -> List[Dict[str, Any]]:
        """Get all labels for a decision.

        Returns:
            List of label dicts with 'label', 'label_type', 'created_at'
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT label, label_type, created_at
                FROM decision_labels
                WHERE decision_id = ?
                ORDER BY label
            """,
                (decision_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_labels_for_decisions(self, decision_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Batch-fetch labels for many decisions (avoids N+1 in list views).

        Returns:
            Dict mapping decision_id -> list of label dicts. Decisions with no
            labels are absent from the dict.
        """
        if not decision_ids:
            return {}
        placeholders = ','.join(['?'] * len(decision_ids))
        result: Dict[int, List[Dict[str, Any]]] = {}
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT decision_id, label, label_type, created_at
                FROM decision_labels
                WHERE decision_id IN ({placeholders})
                ORDER BY decision_id, label
            """,
                decision_ids,
            )
            for row in cursor.fetchall():
                d = dict(row)
                result.setdefault(d.pop('decision_id'), []).append(d)
        return result

    def compute_and_store_auto_labels(
        self, decision_id: int, decision_data: Dict[str, Any]
    ) -> List[str]:
        """Compute auto-labels for a decision and store them (label_type='auto').

        Labels are computed from the decision state at decision time. Fields not
        present in ``decision_data`` simply skip their label, so this works for
        every player type — humans/rule bots supply the fold/pot fields and no
        drama context; LLM bots supply both.

        Args:
            decision_id: The player_decision_analysis ID
            decision_data: Dict with action_taken, pot_odds, stack_bb,
                already_bet_bb, and optional drama_context.

        Returns:
            List of auto-labels that were added
        """
        labels = []
        action = decision_data.get('action_taken')
        pot_odds = decision_data.get('pot_odds')
        stack_bb = decision_data.get('stack_bb')
        already_bet_bb = decision_data.get('already_bet_bb')

        # SHORT_STACK: Folding with < 3 BB is almost always wrong
        if action == 'fold' and stack_bb is not None and stack_bb < 3:
            labels.append('short_stack_fold')

        # POT_COMMITTED: Folding after investing more than remaining stack
        if (
            action == 'fold'
            and already_bet_bb is not None
            and stack_bb is not None
            and already_bet_bb > stack_bb
        ):
            labels.append('pot_committed_fold')

        # SUS_FOLD: Suspicious fold - high pot odds (getting good price)
        if action == 'fold' and pot_odds is not None and pot_odds >= 5:
            # Only add if not already flagged with more specific labels
            if 'short_stack_fold' not in labels and 'pot_committed_fold' not in labels:
                labels.append('suspicious_fold')

        # DRAMA: Add labels for notable drama situations
        drama = decision_data.get('drama_context')
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
            self.add_labels(decision_id, labels, label_type='auto')
            logger.debug(f"Auto-labeled decision {decision_id}: {labels}")

        return labels

    def list_all_labels(self, label_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all unique labels with counts.

        Args:
            label_type: Optional filter by label type ('user' or 'auto')

        Returns:
            List of dicts with 'name', 'count', 'label_type'
        """
        with self._get_connection() as conn:
            if label_type:
                cursor = conn.execute(
                    """
                    SELECT label as name, label_type, COUNT(*) as count
                    FROM decision_labels
                    WHERE label_type = ?
                    GROUP BY label, label_type
                    ORDER BY count DESC, label
                """,
                    (label_type,),
                )
            else:
                cursor = conn.execute("""
                    SELECT label as name, label_type, COUNT(*) as count
                    FROM decision_labels
                    GROUP BY label, label_type
                    ORDER BY count DESC, label
                """)
            return [dict(row) for row in cursor.fetchall()]

    def get_label_stats(
        self,
        game_id: Optional[str] = None,
        player_name: Optional[str] = None,
        call_type: Optional[str] = None,
    ) -> Dict[str, int]:
        """Get label counts filtered by game_id, player_name, and/or call_type.

        Spines on the decision table. ``call_type`` is a capture-only field, so
        when it is supplied (and is not the catch-all 'player_decision') we
        LEFT JOIN the linked capture to honor it; otherwise every decision row
        qualifies.

        Returns:
            Dict mapping label name to count
        """
        conditions = []
        params: List[Any] = []
        join = ""

        if game_id:
            conditions.append("pda.game_id = ?")
            params.append(game_id)
        if player_name:
            conditions.append("pda.player_name = ?")
            params.append(player_name)
        if call_type and call_type != 'player_decision':
            join = "LEFT JOIN prompt_captures pc ON pc.id = pda.capture_id"
            conditions.append("pc.call_type = ?")
            params.append(call_type)

        where_clause = build_where_clause(conditions)

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                SELECT dl.label, COUNT(*) as count
                FROM decision_labels dl
                JOIN player_decision_analysis pda ON pda.id = dl.decision_id
                {join}
                {where_clause}
                GROUP BY dl.label
                ORDER BY count DESC, dl.label
            """,
                params,
            )
            return {row['label']: row['count'] for row in cursor.fetchall()}

    def bulk_add_labels(
        self, decision_ids: List[int], labels: List[str], label_type: str = 'user'
    ) -> Dict[str, int]:
        """Add labels to multiple decisions at once.

        Returns:
            Dict with 'captures_affected' and 'labels_added' counts
            (key name kept for client compatibility — counts decisions).
        """
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels or not decision_ids:
            return {'captures_affected': 0, 'labels_added': 0}

        total_added = 0
        touched = set()

        with self._get_connection() as conn:
            for decision_id in decision_ids:
                for label in labels:
                    try:
                        conn.execute(
                            """
                            INSERT INTO decision_labels (decision_id, label, label_type)
                            VALUES (?, ?, ?)
                        """,
                            (decision_id, label, label_type),
                        )
                        total_added += 1
                        touched.add(decision_id)
                    except sqlite3.IntegrityError:
                        # Label already exists for this decision
                        pass

        logger.info(f"Bulk added {total_added} label(s) to {len(touched)} decision(s)")
        return {'captures_affected': len(touched), 'labels_added': total_added}

    def bulk_remove_labels(self, decision_ids: List[int], labels: List[str]) -> Dict[str, int]:
        """Remove labels from multiple decisions at once.

        Returns:
            Dict with 'captures_affected' and 'labels_removed' counts.
        """
        labels = [l.strip().lower() for l in labels if l.strip()]
        if not labels or not decision_ids:
            return {'captures_affected': 0, 'labels_removed': 0}

        with self._get_connection() as conn:
            id_placeholders = ','.join(['?' for _ in decision_ids])
            label_placeholders = ','.join(['?' for _ in labels])

            cursor = conn.execute(
                f"""
                DELETE FROM decision_labels
                WHERE decision_id IN ({id_placeholders})
                AND label IN ({label_placeholders})
            """,
                decision_ids + labels,
            )
            removed = cursor.rowcount

        logger.info(f"Bulk removed {removed} label(s) from decisions")
        return {'captures_affected': len(decision_ids), 'labels_removed': removed}

    # ------------------------------------------------------------------
    # Capture-id bridge (Prompt Playground / replay experiments)
    # ------------------------------------------------------------------

    def decision_id_for_capture(self, capture_id: int) -> Optional[int]:
        """Resolve the decision row id for a prompt capture, if one exists."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM player_decision_analysis WHERE capture_id = ? LIMIT 1",
                (capture_id,),
            ).fetchone()
        return row[0] if row else None

    def get_labels_by_capture(self, capture_id: int) -> List[Dict[str, Any]]:
        """Get labels for the decision linked to a capture (capture-id space)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT dl.label, dl.label_type, dl.created_at
                FROM decision_labels dl
                JOIN player_decision_analysis pda ON pda.id = dl.decision_id
                WHERE pda.capture_id = ?
                ORDER BY dl.label
            """,
                (capture_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

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
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search prompt captures by decision labels and optional filters.

        Returns capture rows (capture-id space) so the Prompt Playground and
        replay experiments keep working. The label filter resolves through the
        decision spine: a capture matches when its decision carries the label.
        Captures without a decision row are unreachable here (and unreplayable),
        which is correct.

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
                offset=offset,
            )

        # Build base conditions (on the capture row)
        conditions = []
        params: List[Any] = []

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
            # Label matching resolves capture -> decision -> decision_labels.
            label_placeholders = ','.join(['?' for _ in labels])
            params_for_labels: List[Any] = [l for l in labels]

            if match_all:
                # Capture's decision must carry ALL specified labels
                label_subquery = f"""
                    pc.id IN (
                        SELECT pda.capture_id
                        FROM player_decision_analysis pda
                        JOIN decision_labels dl ON dl.decision_id = pda.id
                        WHERE pda.capture_id IS NOT NULL
                          AND dl.label IN ({label_placeholders})
                        GROUP BY pda.capture_id
                        HAVING COUNT(DISTINCT dl.label) = ?
                    )
                """
                params_for_labels.append(len(labels))
            else:
                # Capture's decision must carry ANY of the specified labels
                label_subquery = f"""
                    pc.id IN (
                        SELECT pda.capture_id
                        FROM player_decision_analysis pda
                        JOIN decision_labels dl ON dl.decision_id = pda.id
                        WHERE pda.capture_id IS NOT NULL
                          AND dl.label IN ({label_placeholders})
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
                            logger.debug(
                                "Failed to parse JSON for field '%s' in capture id=%s",
                                field,
                                capture.get('id'),
                            )
                # Get labels for this capture's decision
                capture['labels'] = self.get_labels_by_capture(capture['id'])
                captures.append(capture)

            return {'captures': captures, 'total': total}
