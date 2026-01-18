"""
SQLite implementation of experiment repository.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import ExperimentEntity, ExperimentGameEntity
from ..serialization import to_json, from_json


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
                    name, description, config, status,
                    created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment.name,
                    experiment.description,
                    to_json(experiment.config),
                    experiment.status,
                    experiment.created_at.isoformat(),
                    experiment.started_at.isoformat() if experiment.started_at else None,
                    experiment.completed_at.isoformat() if experiment.completed_at else None,
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
                    config = ?,
                    status = ?,
                    started_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    experiment.name,
                    experiment.description,
                    to_json(experiment.config),
                    experiment.status,
                    experiment.started_at.isoformat() if experiment.started_at else None,
                    experiment.completed_at.isoformat() if experiment.completed_at else None,
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
        self, status: Optional[str] = None, limit: int = 50
    ) -> List[ExperimentEntity]:
        """List experiments with optional status filter."""
        if status:
            rows = self._db.fetch_all(
                """
                SELECT * FROM experiments
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = self._db.fetch_all(
                """
                SELECT * FROM experiments
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [self._row_to_experiment_entity(row) for row in rows]

    def add_game_to_experiment(self, game: ExperimentGameEntity) -> int:
        """Add a game to an experiment. Returns the link ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO experiment_games (
                    experiment_id, game_id, game_number, status,
                    started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    game.experiment_id,
                    game.game_id,
                    game.game_number,
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

    def _row_to_experiment_entity(self, row) -> ExperimentEntity:
        """Convert a database row to an ExperimentEntity."""
        return ExperimentEntity(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            config=from_json(row["config"]) or {},
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
        )

    def _row_to_game_entity(self, row) -> ExperimentGameEntity:
        """Convert a database row to an ExperimentGameEntity."""
        return ExperimentGameEntity(
            id=row["id"],
            experiment_id=row["experiment_id"],
            game_id=row["game_id"],
            game_number=row["game_number"],
            status=row["status"],
            started_at=datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
        )
