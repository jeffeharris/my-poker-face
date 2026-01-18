"""
SQLite implementation of emotional state repository.
"""
from datetime import datetime
from typing import Optional, List, Dict

from ..database import DatabaseContext
from ..protocols import (
    EmotionalStateEntity,
    ControllerStateEntity,
    PressureEventEntity,
)
from ..serialization import to_json, from_json


class SQLiteEmotionalStateRepository:
    """SQLite implementation of EmotionalStateRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save_emotional_state(self, state: EmotionalStateEntity) -> None:
        """Save or update emotional state."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO emotional_state (
                    game_id, player_name, tilt_level, current_mood,
                    trigger_events, modifier_stack, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, player_name) DO UPDATE SET
                    tilt_level = excluded.tilt_level,
                    current_mood = excluded.current_mood,
                    trigger_events = excluded.trigger_events,
                    modifier_stack = excluded.modifier_stack,
                    last_updated = excluded.last_updated
                """,
                (
                    state.game_id,
                    state.player_name,
                    state.tilt_level,
                    state.current_mood,
                    to_json(state.trigger_events),
                    to_json(state.modifier_stack),
                    state.last_updated.isoformat(),
                ),
            )

    def load_emotional_state(
        self, game_id: str, player_name: str
    ) -> Optional[EmotionalStateEntity]:
        """Load emotional state for a player."""
        row = self._db.fetch_one(
            """
            SELECT * FROM emotional_state
            WHERE game_id = ? AND player_name = ?
            """,
            (game_id, player_name),
        )

        if not row:
            return None

        return EmotionalStateEntity(
            game_id=row["game_id"],
            player_name=row["player_name"],
            tilt_level=row["tilt_level"],
            current_mood=row["current_mood"],
            trigger_events=from_json(row["trigger_events"]) or [],
            modifier_stack=from_json(row["modifier_stack"]) or [],
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )

    def load_all_emotional_states(self, game_id: str) -> Dict[str, EmotionalStateEntity]:
        """Load all emotional states for a game."""
        rows = self._db.fetch_all(
            "SELECT * FROM emotional_state WHERE game_id = ?",
            (game_id,),
        )

        result = {}
        for row in rows:
            entity = EmotionalStateEntity(
                game_id=row["game_id"],
                player_name=row["player_name"],
                tilt_level=row["tilt_level"],
                current_mood=row["current_mood"],
                trigger_events=from_json(row["trigger_events"]) or [],
                modifier_stack=from_json(row["modifier_stack"]) or [],
                last_updated=datetime.fromisoformat(row["last_updated"]),
            )
            result[entity.player_name] = entity

        return result

    def save_controller_state(self, state: ControllerStateEntity) -> None:
        """Save or update controller state."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO controller_state (
                    game_id, player_name, state_type, state_data, last_updated
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(game_id, player_name, state_type) DO UPDATE SET
                    state_data = excluded.state_data,
                    last_updated = excluded.last_updated
                """,
                (
                    state.game_id,
                    state.player_name,
                    state.state_type,
                    to_json(state.state_data),
                    state.last_updated.isoformat(),
                ),
            )

    def load_controller_state(
        self, game_id: str, player_name: str
    ) -> Optional[ControllerStateEntity]:
        """Load controller state for a player."""
        row = self._db.fetch_one(
            """
            SELECT * FROM controller_state
            WHERE game_id = ? AND player_name = ?
            """,
            (game_id, player_name),
        )

        if not row:
            return None

        return ControllerStateEntity(
            game_id=row["game_id"],
            player_name=row["player_name"],
            state_type=row["state_type"],
            state_data=from_json(row["state_data"]) or {},
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )

    def load_all_controller_states(self, game_id: str) -> Dict[str, ControllerStateEntity]:
        """Load all controller states for a game."""
        rows = self._db.fetch_all(
            "SELECT * FROM controller_state WHERE game_id = ?",
            (game_id,),
        )

        result = {}
        for row in rows:
            entity = ControllerStateEntity(
                game_id=row["game_id"],
                player_name=row["player_name"],
                state_type=row["state_type"],
                state_data=from_json(row["state_data"]) or {},
                last_updated=datetime.fromisoformat(row["last_updated"]),
            )
            result[entity.player_name] = entity

        return result

    def save_pressure_event(self, event: PressureEventEntity) -> None:
        """Save a pressure event."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO pressure_events (
                    game_id, player_name, event_type, details_json, timestamp
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.game_id,
                    event.player_name,
                    event.event_type,
                    to_json(event.details) if event.details else None,
                    event.timestamp.isoformat(),
                ),
            )

    def get_pressure_events(self, game_id: str) -> List[PressureEventEntity]:
        """Get all pressure events for a game."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM pressure_events
            WHERE game_id = ?
            ORDER BY timestamp ASC
            """,
            (game_id,),
        )

        return [
            PressureEventEntity(
                id=row["id"],
                game_id=row["game_id"],
                player_name=row["player_name"],
                event_type=row["event_type"],
                details=from_json(row["details_json"]) if row["details_json"] else None,
                timestamp=datetime.fromisoformat(row["timestamp"]),
            )
            for row in rows
        ]

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all emotional state data for a game."""
        with self._db.transaction() as conn:
            tables = ["emotional_state", "controller_state", "pressure_events"]
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE game_id = ?", (game_id,))
