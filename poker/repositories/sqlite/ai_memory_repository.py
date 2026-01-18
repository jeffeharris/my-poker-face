"""
SQLite implementation of AI memory repository.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import (
    AIPlayerStateEntity,
    PersonalitySnapshotEntity,
    OpponentModelEntity,
    MemorableHandEntity,
    HandCommentaryEntity,
)
from ..serialization import to_json, from_json


class SQLiteAIMemoryRepository:
    """SQLite implementation of AIMemoryRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save_player_state(self, state: AIPlayerStateEntity) -> None:
        """Save or update AI player state."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ai_player_state (
                    game_id, player_name, conversation_history,
                    personality_state, last_updated
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(game_id, player_name) DO UPDATE SET
                    conversation_history = excluded.conversation_history,
                    personality_state = excluded.personality_state,
                    last_updated = excluded.last_updated
                """,
                (
                    state.game_id,
                    state.player_name,
                    to_json(state.conversation_history),
                    to_json(state.personality_state),
                    state.last_updated.isoformat(),
                ),
            )

    def load_player_states(self, game_id: str) -> Dict[str, AIPlayerStateEntity]:
        """Load all AI player states for a game."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM ai_player_state WHERE game_id = ?
            """,
            (game_id,),
        )

        result = {}
        for row in rows:
            entity = AIPlayerStateEntity(
                game_id=row["game_id"],
                player_name=row["player_name"],
                conversation_history=from_json(row["conversation_history"]) or [],
                personality_state=from_json(row["personality_state"]) or {},
                last_updated=datetime.fromisoformat(row["last_updated"]),
            )
            result[entity.player_name] = entity

        return result

    def save_personality_snapshot(self, snapshot: PersonalitySnapshotEntity) -> None:
        """Save a personality snapshot."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO personality_snapshots (
                    player_name, game_id, hand_number,
                    personality_traits, pressure_levels, snapshot_time
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.player_name,
                    snapshot.game_id,
                    snapshot.hand_number,
                    to_json(snapshot.personality_traits),
                    to_json(snapshot.pressure_levels),
                    snapshot.timestamp.isoformat(),
                ),
            )

    def save_opponent_model(self, model: OpponentModelEntity) -> None:
        """Save opponent model observations."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO opponent_models (
                    game_id, observer_name, opponent_name,
                    observations_json, last_updated
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(game_id, observer_name, opponent_name) DO UPDATE SET
                    observations_json = excluded.observations_json,
                    last_updated = excluded.last_updated
                """,
                (
                    model.game_id,
                    model.observer_name,
                    model.opponent_name,
                    to_json(model.observations),
                    model.last_updated.isoformat(),
                ),
            )

    def load_opponent_models(self, game_id: str) -> List[OpponentModelEntity]:
        """Load all opponent models for a game."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM opponent_models WHERE game_id = ?
            """,
            (game_id,),
        )

        return [
            OpponentModelEntity(
                game_id=row["game_id"],
                observer_name=row["observer_name"],
                opponent_name=row["opponent_name"],
                observations=from_json(row["observations_json"]) or {},
                last_updated=datetime.fromisoformat(row["last_updated"]),
            )
            for row in rows
        ]

    def save_memorable_hand(self, hand: MemorableHandEntity) -> None:
        """Save a memorable hand."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO memorable_hands (
                    game_id, hand_number, player_name,
                    memorability_score, reason, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hand.game_id,
                    hand.hand_number,
                    hand.player_name,
                    hand.memorability_score,
                    hand.reason,
                    to_json(hand.details),
                    hand.created_at.isoformat(),
                ),
            )

    def save_hand_commentary(self, commentary: HandCommentaryEntity) -> None:
        """Save hand commentary/reflection."""
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO hand_commentary (
                    game_id, hand_number, player_name,
                    commentary, reflection_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    commentary.game_id,
                    commentary.hand_number,
                    commentary.player_name,
                    commentary.commentary,
                    commentary.reflection_type,
                    commentary.created_at.isoformat(),
                ),
            )

    def get_recent_reflections(
        self, game_id: str, player_name: str, limit: int = 5
    ) -> List[HandCommentaryEntity]:
        """Get recent reflections for a player."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM hand_commentary
            WHERE game_id = ? AND player_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (game_id, player_name, limit),
        )

        return [
            HandCommentaryEntity(
                id=row["id"],
                game_id=row["game_id"],
                hand_number=row["hand_number"],
                player_name=row["player_name"],
                commentary=row["commentary"],
                reflection_type=row["reflection_type"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all AI memory data for a game."""
        with self._db.transaction() as conn:
            tables = [
                "ai_player_state",
                "personality_snapshots",
                "opponent_models",
                "memorable_hands",
                "hand_commentary",
            ]
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE game_id = ?", (game_id,))
