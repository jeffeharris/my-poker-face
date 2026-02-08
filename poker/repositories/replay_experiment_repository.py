"""Repository for replay experiment persistence.

Covers the experiments (type='replay'), replay_experiment_captures,
and replay_results tables.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class ReplayExperimentRepository(BaseRepository):
    """Handles replay experiment CRUD, capture linking, and result storage."""

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

    def update_experiment_status(
        self,
        experiment_id: int,
        status: str,
        error_message: Optional[str] = None
    ) -> None:
        """Update experiment status."""
        with self._get_connection() as conn:
            if error_message:
                conn.execute(
                    "UPDATE experiments SET status = ?, notes = ? WHERE id = ?",
                    (status, error_message, experiment_id)
                )
            else:
                conn.execute(
                    "UPDATE experiments SET status = ? WHERE id = ?",
                    (status, experiment_id)
                )

    def complete_experiment(self, experiment_id: int, summary: Dict[str, Any]) -> None:
        """Mark experiment as completed and store summary."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE experiments SET status = 'completed', summary_json = ? WHERE id = ?",
                (json.dumps(summary), experiment_id)
            )

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
