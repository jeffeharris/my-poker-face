"""Repository for experiment lifecycle, chat sessions, live stats, and tournament analytics.

Prompt captures, decision analysis, prompt presets, capture labels, and replay
experiments have been extracted to their own focused repositories.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from poker.repositories.game_repository import GameRepository

import numpy as np

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class ExperimentRepository(BaseRepository):
    """Handles experiment lifecycle, chat sessions, live stats, and tournament analytics."""

    def __init__(self, db_path: str, game_repo: GameRepository):
        super().__init__(db_path)
        self._game_repo = game_repo

    # ==================== Experiment Lifecycle Methods ====================

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

    def get_experiment_game(self, game_id: str, experiment_id: int) -> Optional[Dict]:
        """Get a single experiment game record by game_id and experiment_id.

        Returns:
            Dict with id, variant, variant_config, tournament_number, or None.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, variant, variant_config_json, tournament_number
                FROM experiment_games WHERE game_id = ? AND experiment_id = ?
            """, (game_id, experiment_id))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[0],
                'variant': row[1],
                'variant_config': json.loads(row[2]) if row[2] else None,
                'tournament_number': row[3],
            }

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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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
        with self._get_connection() as conn:
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

    # ==================== Experiment Chat Session Methods ====================

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

    # ==================== Experiment Chat Storage Methods ====================

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

    # ==================== Live Stats & Analytics Methods ====================

    def _load_all_emotional_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all emotional states for a game. Delegates to GameRepository."""
        return self._game_repo.load_all_emotional_states(game_id)

    def _load_all_controller_states(self, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Load all controller states for a game. Delegates to GameRepository."""
        return self._game_repo.load_all_controller_states(game_id)

    def _load_emotional_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load emotional state for a player. Delegates to GameRepository."""
        return self._game_repo.load_emotional_state(game_id, player_name)

    def _load_controller_state(self, game_id: str, player_name: str) -> Optional[Dict[str, Any]]:
        """Load controller state for a player. Delegates to GameRepository."""
        return self._game_repo.load_controller_state(game_id, player_name)

    def _compute_latency_metrics(self, conn, experiment_id: int,
                                 variant_clause: str, variant_params: list) -> Optional[Dict]:
        """Compute latency percentile metrics for an experiment variant."""
        cursor = conn.execute(f"""
            SELECT au.latency_ms FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause} AND au.latency_ms IS NOT NULL
        """, [experiment_id] + variant_params)
        latencies = [row[0] for row in cursor.fetchall()]

        if not latencies:
            return None
        return {
            'avg_ms': round(float(np.mean(latencies)), 2),
            'p50_ms': round(float(np.percentile(latencies, 50)), 2),
            'p95_ms': round(float(np.percentile(latencies, 95)), 2),
            'p99_ms': round(float(np.percentile(latencies, 99)), 2),
            'count': len(latencies),
            '_raw_latencies': latencies,
        }

    def _compute_decision_quality(self, conn, experiment_id: int,
                                   variant_clause: str, variant_params: list) -> Optional[Dict]:
        """Compute decision quality metrics for an experiment variant."""
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

        if total == 0:
            return None
        return {
            'total': total,
            'correct': row[1] or 0,
            'correct_pct': round((row[1] or 0) * 100 / total, 1),
            'mistakes': row[2] or 0,
            'avg_ev_lost': round(row[3] or 0, 2),
        }

    def _compute_progress_metrics(self, conn, experiment_id: int,
                                   variant_clause: str, variant_params: list,
                                   max_hands: int, num_tournaments: int) -> Dict:
        """Compute progress metrics for an experiment variant."""
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

        return {
            'current_hands': current_hands,
            'max_hands': variant_max_hands,
            'games_count': games_count,
            'games_expected': num_tournaments,
            'progress_pct': round(current_hands * 100 / variant_max_hands, 1) if variant_max_hands else 0,
        }

    def _compute_cost_metrics(self, conn, experiment_id: int,
                               variant_clause: str, variant_params: list) -> Dict:
        """Compute cost metrics for an experiment variant."""
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

        cursor = conn.execute(f"""
            SELECT AVG(au.estimated_cost), COUNT(*)
            FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause} AND au.call_type = 'player_decision'
        """, [experiment_id] + variant_params)
        decision_cost_row = cursor.fetchone()

        cursor = conn.execute(f"""
            SELECT COUNT(DISTINCT au.game_id || '-' || au.hand_number) as total_hands
            FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause} AND au.hand_number IS NOT NULL
        """, [experiment_id] + variant_params)
        total_hands = cursor.fetchone()[0] or 1

        return {
            'total_cost': round(cost_row[0] or 0, 6),
            'total_calls': cost_row[1] or 0,
            'avg_cost_per_call': round(cost_row[2] or 0, 8),
            'by_model': by_model,
            'avg_cost_per_decision': round(decision_cost_row[0] or 0, 8) if decision_cost_row[0] else 0,
            'total_decisions': decision_cost_row[1] or 0,
            'cost_per_hand': round((cost_row[0] or 0) / total_hands, 6),
            'total_hands': total_hands,
        }

    def _compute_quality_indicators(self, conn, experiment_id: int,
                                     variant_clause: str, variant_params: list) -> Optional[Dict]:
        """Compute quality indicators for an experiment variant."""
        from poker.quality_metrics import compute_allin_categorizations

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

        if not qi_row or qi_row[3] == 0:
            return None

        cursor = conn.execute(f"""
            SELECT pc.stack_bb, pc.ai_response, pda.equity
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
        suspicious_allins, marginal_allins = compute_allin_categorizations(cursor.fetchall())

        cursor = conn.execute(f"""
            SELECT
                SUM(COALESCE(ts.times_eliminated, 0)) as total_eliminations,
                SUM(COALESCE(ts.all_in_wins, 0)) as total_all_in_wins,
                SUM(COALESCE(ts.all_in_losses, 0)) as total_all_in_losses
            FROM tournament_standings ts
            JOIN experiment_games eg ON ts.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause}
        """, [experiment_id] + variant_params)
        survival_row = cursor.fetchone()

        fold_mistakes = qi_row[0] or 0
        total_folds = qi_row[2] or 0
        total_all_in_wins = survival_row[1] or 0 if survival_row else 0
        total_all_in_losses = survival_row[2] or 0 if survival_row else 0
        total_all_in_showdowns = total_all_in_wins + total_all_in_losses

        result = {
            'suspicious_allins': suspicious_allins,
            'marginal_allins': marginal_allins,
            'fold_mistakes': fold_mistakes,
            'fold_mistake_rate': round(fold_mistakes * 100 / total_folds, 1) if total_folds > 0 else 0,
            'total_all_ins': qi_row[1] or 0,
            'total_folds': total_folds,
            'total_decisions': qi_row[3],
        }

        # Include survival metrics only for per-variant (has tournament_standings data)
        if survival_row:
            result.update({
                'total_eliminations': survival_row[0] or 0,
                'all_in_wins': total_all_in_wins,
                'all_in_losses': total_all_in_losses,
                'all_in_survival_rate': round(total_all_in_wins * 100 / total_all_in_showdowns, 1) if total_all_in_showdowns > 0 else None,
            })

        return result

    def get_experiment_live_stats(self, experiment_id: int) -> Dict:
        """Get real-time unified stats per variant for running/completed experiments.

        Returns all metrics per variant in one call: latency, decision quality,
        progress, cost, and quality indicators.
        """
        with self._get_connection() as conn:
            exp = self.get_experiment(experiment_id)
            if not exp:
                return {'by_variant': {}, 'overall': None}

            config = exp.get('config', {})
            max_hands = config.get('hands_per_tournament', 100)
            num_tournaments = config.get('num_tournaments', 1)
            control = config.get('control')
            variants = config.get('variants', [])

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
                    variant_labels = [None]

            all_latencies = []

            for variant in variant_labels:
                variant_key = variant or 'default'

                if variant is None:
                    variant_clause = "AND (eg.variant IS NULL OR eg.variant = '')"
                    variant_params = []
                else:
                    variant_clause = "AND eg.variant = ?"
                    variant_params = [variant]

                latency = self._compute_latency_metrics(conn, experiment_id, variant_clause, variant_params)
                if latency:
                    all_latencies.extend(latency.pop('_raw_latencies'))

                progress = self._compute_progress_metrics(
                    conn, experiment_id, variant_clause, variant_params, max_hands, num_tournaments)

                result['by_variant'][variant_key] = {
                    'latency_metrics': latency,
                    'decision_quality': self._compute_decision_quality(conn, experiment_id, variant_clause, variant_params),
                    'progress': progress,
                    'cost_metrics': self._compute_cost_metrics(conn, experiment_id, variant_clause, variant_params),
                    'quality_indicators': self._compute_quality_indicators(conn, experiment_id, variant_clause, variant_params),
                }

            # Compute overall stats
            no_filter = ""
            no_params = []

            overall_latency = self._compute_latency_metrics(conn, experiment_id, no_filter, no_params)
            if overall_latency:
                overall_latency.pop('_raw_latencies')

            overall_progress = self._compute_progress_metrics(
                conn, experiment_id, no_filter, no_params, max_hands, num_tournaments)
            # Overall progress aggregates all variants, so recalculate max_hands
            num_variant_configs = (1 + len(variants or [])) if control is not None else 1
            overall_max = num_variant_configs * num_tournaments * max_hands
            overall_current = overall_progress['current_hands']
            overall_progress_result = {
                'current_hands': overall_current,
                'max_hands': overall_max,
                'progress_pct': round(overall_current * 100 / overall_max, 1) if overall_max else 0,
            }

            result['overall'] = {
                'latency_metrics': overall_latency,
                'decision_quality': self._compute_decision_quality(conn, experiment_id, no_filter, no_params),
                'progress': overall_progress_result,
                'cost_metrics': self._compute_cost_metrics(conn, experiment_id, no_filter, no_params),
                'quality_indicators': self._compute_quality_indicators(conn, experiment_id, no_filter, no_params),
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
                psychology_data = self._load_all_controller_states(game_id)
                emotional_data = self._load_all_emotional_states(game_id)

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
            ctrl_state = self._load_controller_state(game_id, player_name)
            emo_state = self._load_emotional_state(game_id, player_name)

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

    # ========== Tournament / Experiment Analytics ==========

    def get_decision_stats(self, game_id: str) -> dict:
        """Get aggregated decision quality statistics for a game.

        Returns:
            Dict with total, correct, marginal, mistake counts and avg_ev_lost.
            Empty dict if no data found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) as total,
                    SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                    SUM(CASE WHEN decision_quality = 'marginal' THEN 1 ELSE 0 END) as marginal,
                    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                    AVG(COALESCE(ev_lost, 0)) as avg_ev_lost
                FROM player_decision_analysis WHERE game_id = ?
            """, (game_id,))

            row = cursor.fetchone()
            if not row or row['total'] == 0:
                return {}

            return {
                'total': row['total'],
                'correct': row['correct'],
                'marginal': row['marginal'],
                'mistake': row['mistake'],
                'avg_ev_lost': row['avg_ev_lost']
            }

    def get_player_outcomes(self, game_id: str) -> dict:
        """Aggregate per-player outcomes from hand history.

        Returns:
            Dict mapping player_name to {hands_played: N, hands_won: N}.
            Empty dict on error or if no data.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT players_json, winners_json FROM hand_history WHERE game_id = ?
                """, (game_id,))

                outcomes: Dict[str, Dict[str, int]] = {}
                for row in cursor.fetchall():
                    try:
                        players = json.loads(row['players_json']) if row['players_json'] else []
                        winners = json.loads(row['winners_json']) if row['winners_json'] else []
                    except (json.JSONDecodeError, TypeError):
                        continue

                    # Count hands played
                    for player in players:
                        name = player if isinstance(player, str) else player.get('name', str(player))
                        if name not in outcomes:
                            outcomes[name] = {'hands_played': 0, 'hands_won': 0}
                        outcomes[name]['hands_played'] += 1

                    # Count hands won
                    for winner in winners:
                        name = winner if isinstance(winner, str) else winner.get('name', str(winner))
                        if name not in outcomes:
                            outcomes[name] = {'hands_played': 0, 'hands_won': 0}
                        outcomes[name]['hands_won'] += 1

                return outcomes
        except Exception as e:
            logger.error(f"Error getting player outcomes for game {game_id}: {e}")
            return {}

    def get_latency_metrics(self, game_ids: list) -> Optional[dict]:
        """Calculate latency percentile metrics across multiple games.

        Args:
            game_ids: List of game IDs to analyze.

        Returns:
            Dict with avg_ms, p50_ms, p95_ms, p99_ms, count. None if no data.
        """
        if not game_ids:
            return None

        try:
            import numpy as np_local
        except ImportError:
            logger.warning("numpy not available for latency metrics calculation")
            return None

        placeholders = ','.join('?' for _ in game_ids)
        with self._get_connection() as conn:
            cursor = conn.execute(f"""
                SELECT latency_ms FROM api_usage
                WHERE game_id IN ({placeholders}) AND latency_ms IS NOT NULL
            """, game_ids)

            latencies = [row['latency_ms'] for row in cursor.fetchall()]
            if not latencies:
                return None

            arr = np_local.array(latencies)
            return {
                'avg_ms': float(np_local.mean(arr)),
                'p50_ms': float(np_local.percentile(arr, 50)),
                'p95_ms': float(np_local.percentile(arr, 95)),
                'p99_ms': float(np_local.percentile(arr, 99)),
                'count': len(latencies)
            }

    def get_error_stats(self, game_ids: list) -> Optional[dict]:
        """Get error statistics across multiple games.

        Args:
            game_ids: List of game IDs to analyze.

        Returns:
            Dict with total_calls, error_count, error_rate, error_types. None if empty.
        """
        if not game_ids:
            return None

        placeholders = ','.join('?' for _ in game_ids)
        with self._get_connection() as conn:
            cursor = conn.execute(f"""
                SELECT COUNT(*) as total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
                FROM api_usage WHERE game_id IN ({placeholders})
            """, game_ids)

            row = cursor.fetchone()
            if not row or row['total'] == 0:
                return None

            total_calls = row['total']
            error_count = row['errors'] or 0

            # Get error type breakdown
            cursor = conn.execute(f"""
                SELECT COALESCE(error_type, 'unknown') as error_type, COUNT(*) as cnt
                FROM api_usage WHERE game_id IN ({placeholders}) AND status = 'error'
                GROUP BY error_type ORDER BY cnt DESC
            """, game_ids)

            error_types = [
                {'error_type': r['error_type'], 'count': r['cnt']}
                for r in cursor.fetchall()
            ]

            return {
                'total_calls': total_calls,
                'error_count': error_count,
                'error_rate': error_count / total_calls if total_calls > 0 else 0.0,
                'error_types': error_types
            }

    def get_quality_metrics(self, experiment_id: str) -> Optional[dict]:
        """Get decision quality metrics for an experiment.

        Aggregates fold mistakes, all-in categorizations, and quality indicators.

        Returns:
            Dict with quality indicators, or None if no data.
        """
        with self._get_connection() as conn:
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

            row = cursor.fetchone()
            if not row or row['total_decisions'] == 0:
                return None

            # Get all-in rows for categorization
            cursor = conn.execute("""
                SELECT pda.action_taken, pda.hand_strength, pda.equity, pda.bluff_likelihood,
                       pda.player_name, pda.game_id
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND pda.action_taken = 'all_in'
            """, (experiment_id,))

            allin_rows = [dict(r) for r in cursor.fetchall()]

            from poker.quality_metrics import compute_allin_categorizations, build_quality_indicators
            categorizations = compute_allin_categorizations(allin_rows)
            return build_quality_indicators(row, categorizations)
