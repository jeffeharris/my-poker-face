"""
SQLite implementation of pressure event repository.
"""
import json
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository


class PressureEventRepository(BaseRepository):
    """Repository for managing pressure event persistence."""

    def save_event(self, game_id: str, player_name: str, event_type: str,
                   details: Optional[Dict[str, Any]] = None,
                   hand_number: Optional[int] = None) -> None:
        """Save a pressure event to the database."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pressure_events
                (game_id, player_name, event_type, details_json, hand_number)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    player_name,
                    event_type,
                    json.dumps(details) if details else None,
                    hand_number,
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

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all pressure events for a game."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM pressure_events WHERE game_id = ?",
                (game_id,)
            )
