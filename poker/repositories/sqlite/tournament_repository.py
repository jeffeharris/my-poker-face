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

    def get_eliminated_personalities(self, player_name: str) -> List[dict]:
        """Get all unique personalities eliminated by this player across all games."""
        rows = self._db.fetch_all(
            """
            SELECT
                ts.player_name as personality_name,
                MIN(tr.ended_at) as first_eliminated_at,
                COUNT(*) as times_eliminated
            FROM tournament_standings ts
            JOIN tournament_results tr ON ts.game_id = tr.game_id
            WHERE ts.eliminated_by = ? AND ts.is_human = 0
            GROUP BY ts.player_name
            ORDER BY MIN(tr.ended_at) ASC
            """,
            (player_name,),
        )

        return [
            {
                'name': row['personality_name'],
                'first_eliminated_at': row['first_eliminated_at'],
                'times_eliminated': row['times_eliminated']
            }
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

    def save_tracker_object(self, game_id: str, tracker) -> None:
        """Save a TournamentTracker object (convenience method).

        Args:
            game_id: The game identifier
            tracker: A TournamentTracker object with to_dict() method
        """
        entity = TournamentTrackerEntity(
            game_id=game_id,
            tracker_data=tracker.to_dict() if hasattr(tracker, 'to_dict') else {},
            last_updated=datetime.now(),
        )
        self.save_tracker(entity)

    def save_result_from_dict(self, game_id: str, result: Dict[str, Any]) -> None:
        """Save tournament result from a dict (convenience method).

        Args:
            game_id: The game identifier
            result: Dict with keys: winner_name, total_hands, biggest_pot,
                   starting_player_count, started_at, standings
        """
        # Parse started_at
        started_at = result.get('started_at')
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)
        elif started_at is None:
            started_at = datetime.now()

        entity = TournamentResultEntity(
            game_id=game_id,
            tournament_type='elimination',
            starting_players=result.get('starting_player_count', 0),
            final_standings=result.get('standings', []),
            total_hands=result.get('total_hands', 0),
            started_at=started_at,
            ended_at=datetime.now(),
        )
        self.save_result(entity)

        # Also save individual standings
        for standing_dict in result.get('standings', []):
            standing = TournamentStandingEntity(
                game_id=game_id,
                player_name=standing_dict.get('player_name', ''),
                final_position=standing_dict.get('finishing_position', 0),
                final_chips=standing_dict.get('final_chips', 0),
                hands_played=standing_dict.get('hands_played', 0),
                eliminations=standing_dict.get('eliminations', 0),
            )
            self.save_standing(standing)

    def update_career_stats_from_result(self, player_name: str, result: Dict[str, Any]) -> None:
        """Update career stats for a player after a tournament.

        Args:
            player_name: The human player's name
            result: Dict with tournament result data including standings
        """
        # Find the player's standing in this tournament
        standings = result.get('standings', [])
        player_standing = next(
            (s for s in standings if s.get('player_name') == player_name),
            None
        )

        if not player_standing:
            return

        finishing_position = player_standing.get('finishing_position', 0)
        is_winner = finishing_position == 1
        is_final_table = finishing_position <= 3

        # Count eliminations by this player
        eliminations_this_game = sum(
            1 for s in standings
            if s.get('eliminated_by') == player_name
        )

        # Get existing stats
        existing = self.get_career_stats(player_name)

        if existing:
            # Update existing stats
            new_stats = CareerStatsEntity(
                player_name=player_name,
                tournaments_played=existing.tournaments_played + 1,
                total_wins=existing.total_wins + (1 if is_winner else 0),
                total_final_tables=existing.total_final_tables + (1 if is_final_table else 0),
                best_finish=min(existing.best_finish, finishing_position) if existing.best_finish > 0 else finishing_position,
                avg_finish=((existing.avg_finish * existing.tournaments_played) + finishing_position) / (existing.tournaments_played + 1),
                total_eliminations=existing.total_eliminations + eliminations_this_game,
                total_hands_played=existing.total_hands_played + result.get('total_hands', 0),
                last_updated=datetime.now(),
            )
        else:
            # Create new stats
            new_stats = CareerStatsEntity(
                player_name=player_name,
                tournaments_played=1,
                total_wins=1 if is_winner else 0,
                total_final_tables=1 if is_final_table else 0,
                best_finish=finishing_position,
                avg_finish=float(finishing_position),
                total_eliminations=eliminations_this_game,
                total_hands_played=result.get('total_hands', 0),
                last_updated=datetime.now(),
            )

        self.save_career_stats(new_stats)
