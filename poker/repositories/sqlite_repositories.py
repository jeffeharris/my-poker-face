"""
SQLite implementation of pressure event repository.
"""
import sqlite3
import json
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository


class PressureEventRepository(BaseRepository):
    """Repository for managing pressure event persistence."""

    def save_event(self, game_id: str, player_name: str, event_type: str,
                   details: Optional[Dict[str, Any]] = None) -> None:
        """Save a pressure event to the database."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pressure_events
                (game_id, player_name, event_type, details_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    game_id,
                    player_name,
                    event_type,
                    json.dumps(details) if details else None
                )
            )

    def get_events_for_game(self, game_id: str) -> List[Dict[str, Any]]:
        """Get all pressure events for a specific game."""
        events = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM pressure_events
                WHERE game_id = ?
                ORDER BY timestamp ASC
                """,
                (game_id,)
            )

            for row in cursor:
                events.append({
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'player_name': row['player_name'],
                    'event_type': row['event_type'],
                    'timestamp': row['timestamp'],
                    'details': json.loads(row['details_json']) if row['details_json'] else {}
                })

        return events

    def get_events_for_player(self, player_name: str) -> List[Dict[str, Any]]:
        """Get all pressure events for a specific player across all games."""
        events = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM pressure_events
                WHERE player_name = ?
                ORDER BY timestamp DESC
                """,
                (player_name,)
            )

            for row in cursor:
                events.append({
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'player_name': row['player_name'],
                    'event_type': row['event_type'],
                    'timestamp': row['timestamp'],
                    'details': json.loads(row['details_json']) if row['details_json'] else {}
                })

        return events

    def get_aggregated_stats_for_game(self, game_id: str) -> Dict[str, Dict[str, int]]:
        """Get aggregated stats for all players in a game."""
        stats = {}

        with self._get_connection() as conn:
            # Get count of each event type per player
            cursor = conn.execute(
                """
                SELECT player_name, event_type, COUNT(*) as count
                FROM pressure_events
                WHERE game_id = ?
                GROUP BY player_name, event_type
                """,
                (game_id,)
            )

            for row in cursor:
                player_name = row['player_name']
                if player_name not in stats:
                    stats[player_name] = {}
                stats[player_name][row['event_type']] = row['count']

            # Get biggest pots from details
            cursor = conn.execute(
                """
                SELECT player_name, event_type, details_json
                FROM pressure_events
                WHERE game_id = ? AND event_type IN ('win', 'big_win', 'big_loss')
                """,
                (game_id,)
            )

            for row in cursor:
                player_name = row['player_name']
                if player_name not in stats:
                    stats[player_name] = {}

                details = json.loads(row['details_json']) if row['details_json'] else {}
                pot_size = details.get('pot_size', 0)

                if row['event_type'] in ('win', 'big_win'):
                    current_max = stats[player_name].get('biggest_pot_won', 0)
                    stats[player_name]['biggest_pot_won'] = max(current_max, pot_size)
                elif row['event_type'] == 'big_loss':
                    current_max = stats[player_name].get('biggest_pot_lost', 0)
                    stats[player_name]['biggest_pot_lost'] = max(current_max, pot_size)

        return stats

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all pressure events for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM pressure_events WHERE game_id = ?",
                (game_id,)
            )
