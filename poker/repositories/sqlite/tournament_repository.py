"""
SQLite implementation of tournament repository.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import (
    TournamentResultEntity,
    TournamentStandingEntity,
    CareerStatsEntity,
    TournamentTrackerEntity,
)
from ..serialization import to_json, from_json


class SQLiteTournamentRepository:
    """SQLite implementation of TournamentRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save_result(self, result: TournamentResultEntity) -> int:
        """Save tournament result. Returns the result ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tournament_results (
                    game_id, tournament_type, starting_players,
                    final_standings, total_hands, started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    tournament_type = excluded.tournament_type,
                    starting_players = excluded.starting_players,
                    final_standings = excluded.final_standings,
                    total_hands = excluded.total_hands,
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at
                """,
                (
                    result.game_id,
                    result.tournament_type,
                    result.starting_players,
                    to_json(result.final_standings),
                    result.total_hands,
                    result.started_at.isoformat(),
                    result.ended_at.isoformat(),
                ),
            )
            return cursor.lastrowid

    def get_result(self, game_id: str) -> Optional[TournamentResultEntity]:
        """Get tournament result for a game."""
        row = self._db.fetch_one(
            "SELECT * FROM tournament_results WHERE game_id = ?",
            (game_id,),
        )

        if not row:
            return None

        return TournamentResultEntity(
            id=row["id"],
            game_id=row["game_id"],
            tournament_type=row["tournament_type"],
            starting_players=row["starting_players"],
            final_standings=from_json(row["final_standings"]) or [],
            total_hands=row["total_hands"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]),
        )

    def save_standing(self, standing: TournamentStandingEntity) -> None:
        """Save a player's tournament standing."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tournament_standings (
                    game_id, player_name, final_position,
                    final_chips, hands_played, eliminations
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, player_name) DO UPDATE SET
                    final_position = excluded.final_position,
                    final_chips = excluded.final_chips,
                    hands_played = excluded.hands_played,
                    eliminations = excluded.eliminations
                """,
                (
                    standing.game_id,
                    standing.player_name,
                    standing.final_position,
                    standing.final_chips,
                    standing.hands_played,
                    standing.eliminations,
                ),
            )

    def get_standings(self, game_id: str) -> List[TournamentStandingEntity]:
        """Get all standings for a tournament."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM tournament_standings
            WHERE game_id = ?
            ORDER BY final_position ASC
            """,
            (game_id,),
        )

        return [
            TournamentStandingEntity(
                id=row["id"],
                game_id=row["game_id"],
                player_name=row["player_name"],
                final_position=row["final_position"],
                final_chips=row["final_chips"],
                hands_played=row["hands_played"],
                eliminations=row["eliminations"],
            )
            for row in rows
        ]

    def save_career_stats(self, stats: CareerStatsEntity) -> None:
        """Save or update career stats."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO player_career_stats (
                    player_name, tournaments_played, total_wins,
                    total_final_tables, best_finish, avg_finish,
                    total_eliminations, total_hands_played, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_name) DO UPDATE SET
                    tournaments_played = excluded.tournaments_played,
                    total_wins = excluded.total_wins,
                    total_final_tables = excluded.total_final_tables,
                    best_finish = excluded.best_finish,
                    avg_finish = excluded.avg_finish,
                    total_eliminations = excluded.total_eliminations,
                    total_hands_played = excluded.total_hands_played,
                    last_updated = excluded.last_updated
                """,
                (
                    stats.player_name,
                    stats.tournaments_played,
                    stats.total_wins,
                    stats.total_final_tables,
                    stats.best_finish,
                    stats.avg_finish,
                    stats.total_eliminations,
                    stats.total_hands_played,
                    stats.last_updated.isoformat(),
                ),
            )

    def get_career_stats(self, player_name: str) -> Optional[CareerStatsEntity]:
        """Get career stats for a player."""
        row = self._db.fetch_one(
            "SELECT * FROM player_career_stats WHERE player_name = ?",
            (player_name,),
        )

        if not row:
            return None

        return CareerStatsEntity(
            player_name=row["player_name"],
            tournaments_played=row["tournaments_played"],
            total_wins=row["total_wins"],
            total_final_tables=row["total_final_tables"],
            best_finish=row["best_finish"],
            avg_finish=row["avg_finish"],
            total_eliminations=row["total_eliminations"],
            total_hands_played=row["total_hands_played"],
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )

    def get_tournament_history(
        self, player_name: str, limit: int = 20
    ) -> List[TournamentResultEntity]:
        """Get tournament history for a player."""
        rows = self._db.fetch_all(
            """
            SELECT tr.* FROM tournament_results tr
            JOIN tournament_standings ts ON tr.game_id = ts.game_id
            WHERE ts.player_name = ?
            ORDER BY tr.ended_at DESC
            LIMIT ?
            """,
            (player_name, limit),
        )

        return [
            TournamentResultEntity(
                id=row["id"],
                game_id=row["game_id"],
                tournament_type=row["tournament_type"],
                starting_players=row["starting_players"],
                final_standings=from_json(row["final_standings"]) or [],
                total_hands=row["total_hands"],
                started_at=datetime.fromisoformat(row["started_at"]),
                ended_at=datetime.fromisoformat(row["ended_at"]),
            )
            for row in rows
        ]

    def save_tracker(self, tracker: TournamentTrackerEntity) -> None:
        """Save tournament tracker state."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO tournament_tracker (game_id, tracker_data, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    tracker_data = excluded.tracker_data,
                    last_updated = excluded.last_updated
                """,
                (
                    tracker.game_id,
                    to_json(tracker.tracker_data),
                    tracker.last_updated.isoformat(),
                ),
            )

    def load_tracker(self, game_id: str) -> Optional[TournamentTrackerEntity]:
        """Load tournament tracker state."""
        row = self._db.fetch_one(
            "SELECT * FROM tournament_tracker WHERE game_id = ?",
            (game_id,),
        )

        if not row:
            return None

        return TournamentTrackerEntity(
            game_id=row["game_id"],
            tracker_data=from_json(row["tracker_data"]) or {},
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )
