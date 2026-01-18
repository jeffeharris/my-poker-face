"""
SQLite implementation of experiment repository.
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import ExperimentEntity, ExperimentGameEntity
from ..serialization import to_json, from_json

logger = logging.getLogger(__name__)


class SQLiteExperimentRepository:
    """SQLite implementation of ExperimentRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def create_experiment(self, experiment: ExperimentEntity) -> int:
        """Create a new experiment. Returns the experiment ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO experiments (
                    name, description, hypothesis, tags, notes, config, status,
                    created_at, started_at, completed_at, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment.name,
                    experiment.description,
                    experiment.hypothesis,
                    to_json(experiment.tags) if experiment.tags else None,
                    experiment.notes,
                    to_json(experiment.config),
                    experiment.status,
                    experiment.created_at.isoformat(),
                    experiment.started_at.isoformat() if experiment.started_at else None,
                    experiment.completed_at.isoformat() if experiment.completed_at else None,
                    to_json(experiment.summary) if experiment.summary else None,
                ),
            )
            return cursor.lastrowid

    def update_experiment(self, experiment: ExperimentEntity) -> None:
        """Update an existing experiment."""
        if experiment.id is None:
            raise ValueError("Cannot update experiment without ID")

        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE experiments SET
                    name = ?,
                    description = ?,
                    hypothesis = ?,
                    tags = ?,
                    notes = ?,
                    config = ?,
                    status = ?,
                    started_at = ?,
                    completed_at = ?,
                    summary_json = ?
                WHERE id = ?
                """,
                (
                    experiment.name,
                    experiment.description,
                    experiment.hypothesis,
                    to_json(experiment.tags) if experiment.tags else None,
                    experiment.notes,
                    to_json(experiment.config),
                    experiment.status,
                    experiment.started_at.isoformat() if experiment.started_at else None,
                    experiment.completed_at.isoformat() if experiment.completed_at else None,
                    to_json(experiment.summary) if experiment.summary else None,
                    experiment.id,
                ),
            )

    def get_experiment(self, experiment_id: int) -> Optional[ExperimentEntity]:
        """Get an experiment by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM experiments WHERE id = ?",
            (experiment_id,),
        )

        if not row:
            return None

        return self._row_to_experiment_entity(row)

    def get_experiment_by_name(self, name: str) -> Optional[ExperimentEntity]:
        """Get an experiment by name."""
        row = self._db.fetch_one(
            "SELECT * FROM experiments WHERE name = ?",
            (name,),
        )

        if not row:
            return None

        return self._row_to_experiment_entity(row)

    def list_experiments(
        self, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> List[ExperimentEntity]:
        """List experiments with optional status filter."""
        if status:
            rows = self._db.fetch_all(
                """
                SELECT * FROM experiments
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            )
        else:
            rows = self._db.fetch_all(
                """
                SELECT * FROM experiments
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )

        return [self._row_to_experiment_entity(row) for row in rows]

    def add_game_to_experiment(self, game: ExperimentGameEntity) -> int:
        """Add a game to an experiment. Returns the link ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO experiment_games (
                    experiment_id, game_id, game_number, variant, variant_config_json,
                    tournament_number, status, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game.experiment_id,
                    game.game_id,
                    game.game_number,
                    game.variant,
                    to_json(game.variant_config) if game.variant_config else None,
                    game.tournament_number,
                    game.status,
                    game.started_at.isoformat() if game.started_at else None,
                    game.completed_at.isoformat() if game.completed_at else None,
                ),
            )
            return cursor.lastrowid

    def get_experiment_games(self, experiment_id: int) -> List[ExperimentGameEntity]:
        """Get all games for an experiment."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM experiment_games
            WHERE experiment_id = ?
            ORDER BY game_number ASC
            """,
            (experiment_id,),
        )

        return [self._row_to_game_entity(row) for row in rows]

    def update_experiment_game(self, game: ExperimentGameEntity) -> None:
        """Update an experiment game record."""
        if game.id is None:
            raise ValueError("Cannot update experiment game without ID")

        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE experiment_games SET
                    status = ?,
                    started_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    game.status,
                    game.started_at.isoformat() if game.started_at else None,
                    game.completed_at.isoformat() if game.completed_at else None,
                    game.id,
                ),
            )

    def get_experiment_stats(self, experiment_id: int) -> Dict[str, Any]:
        """Get aggregated statistics for an experiment."""
        # Get game counts by status
        status_rows = self._db.fetch_all(
            """
            SELECT status, COUNT(*) as count
            FROM experiment_games
            WHERE experiment_id = ?
            GROUP BY status
            """,
            (experiment_id,),
        )

        status_counts = {row["status"]: row["count"] for row in status_rows}

        # Get total games
        total_row = self._db.fetch_one(
            """
            SELECT COUNT(*) as total
            FROM experiment_games
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        )

        # Get prompt capture stats for experiment
        capture_row = self._db.fetch_one(
            """
            SELECT COUNT(*) as total_captures
            FROM prompt_captures
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        )

        return {
            "total_games": total_row["total"] if total_row else 0,
            "games_by_status": status_counts,
            "completed_games": status_counts.get("completed", 0),
            "running_games": status_counts.get("running", 0),
            "pending_games": status_counts.get("pending", 0),
            "failed_games": status_counts.get("failed", 0),
            "total_prompt_captures": capture_row["total_captures"] if capture_row else 0,
        }

    def _safe_get(self, row, key, default=None):
        """Safely get a value from a row (handles both dict and sqlite3.Row)."""
        try:
            val = row[key]
            return val if val is not None else default
        except (KeyError, IndexError):
            return default

    def _row_to_experiment_entity(self, row) -> ExperimentEntity:
        """Convert a database row to an ExperimentEntity."""
        return ExperimentEntity(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            hypothesis=self._safe_get(row, "hypothesis"),
            tags=from_json(self._safe_get(row, "tags")) if self._safe_get(row, "tags") else None,
            notes=self._safe_get(row, "notes"),
            config=from_json(row["config"]) or {},
            status=row["status"],
            summary=from_json(self._safe_get(row, "summary_json")) if self._safe_get(row, "summary_json") else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"])
            if self._safe_get(row, "started_at")
            else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if self._safe_get(row, "completed_at")
            else None,
        )

    def _row_to_game_entity(self, row) -> ExperimentGameEntity:
        """Convert a database row to an ExperimentGameEntity."""
        return ExperimentGameEntity(
            id=row["id"],
            experiment_id=row["experiment_id"],
            game_id=row["game_id"],
            game_number=row["game_number"],
            variant=self._safe_get(row, "variant"),
            variant_config=from_json(self._safe_get(row, "variant_config_json")) if self._safe_get(row, "variant_config_json") else None,
            tournament_number=self._safe_get(row, "tournament_number"),
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"])
            if self._safe_get(row, "started_at")
            else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if self._safe_get(row, "completed_at")
            else None,
            created_at=datetime.fromisoformat(row["created_at"])
            if self._safe_get(row, "created_at")
            else None,
        )

    def update_experiment_status(
        self, experiment_id: int, status: str, error_message: Optional[str] = None
    ) -> None:
        """Update experiment status."""
        valid_statuses = {'pending', 'running', 'completed', 'failed', 'paused', 'interrupted'}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        with self._db.transaction() as conn:
            if status == 'completed':
                conn.execute(
                    """
                    UPDATE experiments
                    SET status = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, experiment_id),
                )
            elif status == 'failed' and error_message:
                conn.execute(
                    """
                    UPDATE experiments
                    SET status = ?, notes = COALESCE(notes || '\n', '') || ?
                    WHERE id = ?
                    """,
                    (status, f"Error: {error_message}", experiment_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE experiments
                    SET status = ?
                    WHERE id = ?
                    """,
                    (status, experiment_id),
                )
        logger.info(f"Updated experiment {experiment_id} status to {status}")

    def complete_experiment(
        self, experiment_id: int, summary: Optional[Dict[str, Any]] = None
    ) -> None:
        """Mark an experiment as completed and store summary."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                UPDATE experiments
                SET status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    summary_json = ?
                WHERE id = ?
                """,
                (to_json(summary) if summary else None, experiment_id),
            )
        logger.info(f"Completed experiment {experiment_id}")

    def get_incomplete_tournaments(self, experiment_id: int) -> List[ExperimentGameEntity]:
        """Get games for tournaments that haven't completed (no tournament_results entry)."""
        rows = self._db.fetch_all(
            """
            SELECT eg.* FROM experiment_games eg
            LEFT JOIN tournament_results tr ON eg.game_id = tr.game_id
            WHERE eg.experiment_id = ?
            AND tr.id IS NULL
            ORDER BY eg.tournament_number
            """,
            (experiment_id,),
        )
        return [self._row_to_game_entity(row) for row in rows]

    def get_experiment_decision_stats(
        self, experiment_id: int, variant: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get aggregated decision analysis stats for an experiment."""
        # Build query with optional variant filter
        variant_clause = "AND eg.variant = ?" if variant else ""
        params = [experiment_id]
        if variant:
            params.append(variant)

        # Aggregate stats
        row = self._db.fetch_one(
            f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                SUM(CASE WHEN pda.decision_quality = 'marginal' THEN 1 ELSE 0 END) as marginal,
                SUM(CASE WHEN pda.decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
            FROM player_decision_analysis pda
            JOIN experiment_games eg ON pda.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause}
            """,
            tuple(params),
        )

        total = row["total"] or 0 if row else 0
        result = {
            'total': total,
            'correct': row["correct"] or 0 if row else 0,
            'marginal': row["marginal"] or 0 if row else 0,
            'mistake': row["mistake"] or 0 if row else 0,
            'correct_pct': round((row["correct"] or 0) * 100 / total, 1) if row and total else 0,
            'avg_ev_lost': round(row["avg_ev_lost"] or 0, 2) if row else 0,
        }

        # Stats by player
        player_rows = self._db.fetch_all(
            f"""
            SELECT
                pda.player_name,
                COUNT(*) as total,
                SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
            FROM player_decision_analysis pda
            JOIN experiment_games eg ON pda.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause}
            GROUP BY pda.player_name
            """,
            tuple(params),
        )

        result['by_player'] = {
            row["player_name"]: {
                'total': row["total"],
                'correct': row["correct"] or 0,
                'correct_pct': round((row["correct"] or 0) * 100 / row["total"], 1) if row["total"] else 0,
                'avg_ev_lost': round(row["avg_ev_lost"] or 0, 2),
            }
            for row in player_rows
        }

        return result

    def mark_running_experiments_interrupted(self) -> int:
        """Mark all 'running' experiments as 'interrupted'.

        Called on startup to handle experiments that were running when the
        server was stopped. Users can manually resume these experiments.
        """
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE experiments
                SET status = 'interrupted',
                    notes = COALESCE(notes || '\n', '') || 'Server restarted while experiment was running.'
                WHERE status = 'running'
                """
            )
            count = cursor.rowcount
        if count > 0:
            logger.info(f"Marked {count} running experiment(s) as interrupted")
        return count

    # --- Helper methods for get_experiment_live_stats ---

    def _build_variant_filter(self, variant: Optional[str]) -> tuple:
        """Build SQL clause and params for variant filtering."""
        if variant is None:
            return "AND (eg.variant IS NULL OR eg.variant = '')", []
        return "AND eg.variant = ?", [variant]

    def _calculate_latency_metrics(self, latencies: List[float]) -> Optional[Dict[str, Any]]:
        """Calculate latency percentiles (avg, p50, p95, p99)."""
        if not latencies:
            return None
        try:
            import numpy as np
            return {
                'avg_ms': round(float(np.mean(latencies)), 2),
                'p50_ms': round(float(np.percentile(latencies, 50)), 2),
                'p95_ms': round(float(np.percentile(latencies, 95)), 2),
                'p99_ms': round(float(np.percentile(latencies, 99)), 2),
                'count': len(latencies),
            }
        except ImportError:
            # Fallback if numpy not available
            sorted_latencies = sorted(latencies)
            return {
                'avg_ms': round(sum(latencies) / len(latencies), 2),
                'p50_ms': round(sorted_latencies[len(sorted_latencies) // 2], 2),
                'p95_ms': round(sorted_latencies[int(len(sorted_latencies) * 0.95)], 2),
                'p99_ms': round(sorted_latencies[int(len(sorted_latencies) * 0.99)], 2),
                'count': len(latencies),
            }

    def _get_cost_metrics(
        self, experiment_id: int, variant_clause: str = "", variant_params: list = None
    ) -> Dict[str, Any]:
        """Get cost metrics with optional variant filter."""
        if variant_params is None:
            variant_params = []

        # Total cost metrics
        cost_row = self._db.fetch_one(
            f"""
            SELECT
                COALESCE(SUM(au.estimated_cost), 0) as total_cost,
                COUNT(*) as total_calls,
                COALESCE(AVG(au.estimated_cost), 0) as avg_cost_per_call
            FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause}
            """,
            tuple([experiment_id] + variant_params),
        )

        # Cost by model
        model_rows = self._db.fetch_all(
            f"""
            SELECT
                au.provider || '/' || au.model as model_key,
                SUM(au.estimated_cost) as cost,
                COUNT(*) as calls
            FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause} AND au.estimated_cost IS NOT NULL
            GROUP BY au.provider, au.model
            """,
            tuple([experiment_id] + variant_params),
        )
        by_model = {row["model_key"]: {'cost': row["cost"], 'calls': row["calls"]} for row in model_rows}

        # Cost per decision (player_decision call type)
        decision_cost_row = self._db.fetch_one(
            f"""
            SELECT AVG(au.estimated_cost) as avg_cost, COUNT(*) as count
            FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause} AND au.call_type = 'player_decision'
            """,
            tuple([experiment_id] + variant_params),
        )

        # Count hands for normalized cost
        hand_row = self._db.fetch_one(
            f"""
            SELECT COUNT(DISTINCT au.game_id || '-' || au.hand_number) as total_hands
            FROM api_usage au
            JOIN experiment_games eg ON au.game_id = eg.game_id
            WHERE eg.experiment_id = ? {variant_clause} AND au.hand_number IS NOT NULL
            """,
            tuple([experiment_id] + variant_params),
        )
        total_hands = hand_row["total_hands"] or 1 if hand_row else 1

        return {
            'total_cost': round(cost_row["total_cost"] or 0, 6) if cost_row else 0,
            'total_calls': cost_row["total_calls"] or 0 if cost_row else 0,
            'avg_cost_per_call': round(cost_row["avg_cost_per_call"] or 0, 8) if cost_row else 0,
            'by_model': by_model,
            'avg_cost_per_decision': round(decision_cost_row["avg_cost"] or 0, 8) if decision_cost_row and decision_cost_row["avg_cost"] else 0,
            'total_decisions': decision_cost_row["count"] or 0 if decision_cost_row else 0,
            'cost_per_hand': round((cost_row["total_cost"] or 0) / total_hands, 6) if cost_row else 0,
            'total_hands': total_hands,
        }

    def _calculate_progress(
        self, games_count: int, current_max_hand: int, max_hands: int, num_tournaments: int
    ) -> Dict[str, Any]:
        """Calculate progress for a variant."""
        if games_count == 0:
            current_hands = 0
        elif current_max_hand > 0 and current_max_hand < max_hands:
            # One game in progress
            current_hands = (games_count - 1) * max_hands + current_max_hand
        else:
            # All games complete
            current_hands = games_count * max_hands

        variant_max_hands = num_tournaments * max_hands
        current_hands = min(current_hands, variant_max_hands)

        return {
            'current_hands': current_hands,
            'max_hands': variant_max_hands,
            'games_count': games_count,
            'games_expected': num_tournaments,
            'progress_pct': round(current_hands * 100 / variant_max_hands, 1) if variant_max_hands else 0,
        }

    def get_experiment_live_stats(self, experiment_id: int) -> Dict[str, Any]:
        """Get real-time unified stats per variant for running/completed experiments."""
        # Get experiment config for max_hands calculation
        exp = self.get_experiment(experiment_id)
        if not exp:
            return {'by_variant': {}, 'overall': None}

        config = exp.config or {}
        max_hands = config.get('target_hands') or config.get('max_hands_per_tournament', 100)
        num_tournaments = config.get('num_tournaments', 1)

        # Determine number of variants from control/variants config
        control = config.get('control')
        variants = config.get('variants', [])

        result = {'by_variant': {}, 'overall': None}

        # Get all variants for this experiment from actual games
        variant_rows = self._db.fetch_all(
            "SELECT DISTINCT variant FROM experiment_games WHERE experiment_id = ?",
            (experiment_id,),
        )
        variant_labels = [row["variant"] for row in variant_rows]

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

            # Build variant clause using helper
            variant_clause, variant_params = self._build_variant_filter(variant)

            # 1. Latency metrics from api_usage
            latency_rows = self._db.fetch_all(
                f"""
                SELECT au.latency_ms FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause} AND au.latency_ms IS NOT NULL
                """,
                tuple([experiment_id] + variant_params),
            )
            latencies = [row["latency_ms"] for row in latency_rows]

            latency_metrics = self._calculate_latency_metrics(latencies)
            if latencies:
                all_latencies.extend(latencies)

            # 2. Decision quality from player_decision_analysis
            decision_row = self._db.fetch_one(
                f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
                    SUM(CASE WHEN pda.decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistake,
                    AVG(COALESCE(pda.ev_lost, 0)) as avg_ev_lost
                FROM player_decision_analysis pda
                JOIN experiment_games eg ON pda.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause}
                """,
                tuple([experiment_id] + variant_params),
            )
            total = decision_row["total"] or 0 if decision_row else 0

            if total > 0:
                decision_quality = {
                    'total': total,
                    'correct': decision_row["correct"] or 0,
                    'correct_pct': round((decision_row["correct"] or 0) * 100 / total, 1),
                    'mistakes': decision_row["mistake"] or 0,
                    'avg_ev_lost': round(decision_row["avg_ev_lost"] or 0, 2),
                }
                overall_decision['total'] += total
                overall_decision['correct'] += decision_row["correct"] or 0
                overall_decision['mistake'] += decision_row["mistake"] or 0
                overall_decision['ev_lost_sum'] += (decision_row["avg_ev_lost"] or 0) * total
            else:
                decision_quality = None

            # 3. Progress - count games and max hand number per variant
            progress_row = self._db.fetch_one(
                f"""
                SELECT
                    COUNT(DISTINCT eg.game_id) as games_count,
                    MAX(au.hand_number) as max_hand
                FROM experiment_games eg
                LEFT JOIN api_usage au ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? {variant_clause}
                """,
                tuple([experiment_id] + variant_params),
            )
            games_count = progress_row["games_count"] or 0 if progress_row else 0
            current_max_hand = progress_row["max_hand"] or 0 if progress_row else 0

            progress = self._calculate_progress(games_count, current_max_hand, max_hands, num_tournaments)
            overall_progress['current_hands'] += progress['current_hands']
            overall_progress['max_hands'] += progress['max_hands']

            # 4. Cost metrics using helper
            cost_metrics = self._get_cost_metrics(experiment_id, variant_clause, variant_params)

            result['by_variant'][variant_key] = {
                'latency_metrics': latency_metrics,
                'decision_quality': decision_quality,
                'progress': progress,
                'cost_metrics': cost_metrics,
            }

        # Compute overall stats using helpers
        overall_latency = self._calculate_latency_metrics(all_latencies)

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

        # Overall cost metrics using helper (no variant filter)
        overall_cost_metrics = self._get_cost_metrics(experiment_id)

        result['overall'] = {
            'latency_metrics': overall_latency,
            'decision_quality': overall_decision_quality,
            'progress': overall_progress_result,
            'cost_metrics': overall_cost_metrics,
        }

        return result

    def get_experiment_game_snapshots(
        self, experiment_id: int, debug_repo=None
    ) -> List[Dict[str, Any]]:
        """Get live game snapshots for monitoring running experiments.

        Args:
            experiment_id: The experiment ID
            debug_repo: Optional DebugRepository for getting game snapshots

        Returns:
            List of game snapshot dictionaries
        """
        # Get all games for this experiment
        games = self.get_experiment_games(experiment_id)

        if not debug_repo:
            # Return basic game info without snapshots
            return [
                {
                    'game_id': game.game_id,
                    'variant': game.variant,
                    'status': game.status,
                    'tournament_number': game.tournament_number,
                }
                for game in games
            ]

        # Get full snapshots using debug repository
        snapshots = []
        for game in games:
            snapshot = debug_repo.get_game_snapshot(game.game_id)
            if snapshot:
                snapshot['variant'] = game.variant
                snapshot['tournament_number'] = game.tournament_number
                snapshots.append(snapshot)

        return snapshots

    def get_experiment_player_detail(
        self, experiment_id: int, game_id: str, player_name: str, debug_repo=None
    ) -> Optional[Dict[str, Any]]:
        """Get detailed player info for the drill-down panel.

        Args:
            experiment_id: The experiment ID
            game_id: The game ID
            player_name: The player name
            debug_repo: Optional DebugRepository for getting player details

        Returns:
            Dictionary with detailed player info or None if not found
        """
        # Verify game belongs to experiment
        game_row = self._db.fetch_one(
            "SELECT id, variant_config_json FROM experiment_games WHERE experiment_id = ? AND game_id = ?",
            (experiment_id, game_id),
        )
        if not game_row:
            return None

        # Get variant config for psychology_enabled check
        variant_config = from_json(game_row["variant_config_json"]) if game_row.get("variant_config_json") else {}
        psychology_enabled = variant_config.get('enable_psychology', False)

        if not debug_repo:
            # Return minimal info
            return {
                'player': {'name': player_name},
                'psychology_enabled': psychology_enabled,
            }

        # Get full details using debug repository
        detail = debug_repo.get_player_detail(game_id, player_name)
        if detail:
            detail['psychology_enabled'] = psychology_enabled

        return detail
