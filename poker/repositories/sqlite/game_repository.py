"""
SQLite implementation of game and message repositories.
"""
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import GameEntity, MessageEntity
from ..serialization import (
    serialize_state_machine,
    deserialize_state_machine,
    to_json,
    from_json,
)


class SQLiteGameRepository:
    """SQLite implementation of GameRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save(self, game: GameEntity) -> None:
        """Save or update a game."""
        state_json = to_json(serialize_state_machine(game.state_machine))

        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO games (
                    game_id, created_at, updated_at, phase, num_players,
                    pot_size, game_state_json, owner_id, owner_name,
                    debug_capture_enabled, llm_configs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    phase = excluded.phase,
                    num_players = excluded.num_players,
                    pot_size = excluded.pot_size,
                    game_state_json = excluded.game_state_json,
                    owner_id = excluded.owner_id,
                    owner_name = excluded.owner_name,
                    debug_capture_enabled = excluded.debug_capture_enabled,
                    llm_configs_json = excluded.llm_configs_json
                """,
                (
                    game.id,
                    game.created_at.isoformat(),
                    game.updated_at.isoformat(),
                    game.phase,
                    game.num_players,
                    game.pot_size,
                    state_json,
                    game.owner_id,
                    game.owner_name,
                    1 if game.debug_capture_enabled else 0,
                    to_json(game.llm_configs) if game.llm_configs else None,
                ),
            )

    def find_by_id(self, game_id: str) -> Optional[GameEntity]:
        """Find a game by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM games WHERE game_id = ?",
            (game_id,),
        )

        if not row:
            return None

        return self._row_to_entity(row)

    def find_recent(
        self, owner_id: Optional[str] = None, limit: int = 20
    ) -> List[GameEntity]:
        """Find recent games, optionally filtered by owner."""
        if owner_id:
            rows = self._db.fetch_all(
                """
                SELECT * FROM games
                WHERE owner_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (owner_id, limit),
            )
        else:
            rows = self._db.fetch_all(
                """
                SELECT * FROM games
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [self._row_to_entity(row) for row in rows]

    def delete(self, game_id: str) -> None:
        """Delete a game and all related data."""
        with self._db.transaction() as conn:
            # Delete in reverse dependency order
            related_tables = [
                "game_messages",
                "ai_player_state",
                "personality_snapshots",
                "pressure_events",
                "emotional_state",
                "controller_state",
                "hand_history",
                "opponent_models",
                "memorable_hands",
                "hand_commentary",
                "tournament_results",
                "tournament_standings",
                "tournament_tracker",
                "prompt_captures",
                "experiment_games",
            ]

            for table in related_tables:
                conn.execute(f"DELETE FROM {table} WHERE game_id = ?", (game_id,))

            conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))

    def exists(self, game_id: str) -> bool:
        """Check if a game exists."""
        row = self._db.fetch_one(
            "SELECT 1 FROM games WHERE game_id = ? LIMIT 1",
            (game_id,),
        )
        return row is not None

    def count_by_owner(self, owner_id: str) -> int:
        """Count games owned by a specific user."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM games WHERE owner_id = ?",
            (owner_id,),
        )
        return row["count"] if row else 0

    def save_llm_configs(self, game_id: str, configs: Dict[str, Any]) -> None:
        """Save LLM configurations for a game."""
        self._db.execute(
            "UPDATE games SET llm_configs_json = ? WHERE game_id = ?",
            (to_json(configs), game_id),
        )

    def load_llm_configs(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load LLM configurations for a game."""
        row = self._db.fetch_one(
            "SELECT llm_configs_json FROM games WHERE game_id = ?",
            (game_id,),
        )
        if not row or not row["llm_configs_json"]:
            return None
        return from_json(row["llm_configs_json"])

    def _row_to_entity(self, row) -> GameEntity:
        """Convert a database row to a GameEntity."""
        state_dict = from_json(row["game_state_json"])
        state_machine = deserialize_state_machine(state_dict)

        return GameEntity(
            id=row["game_id"],
            state_machine=state_machine,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            owner_id=row["owner_id"],
            owner_name=row["owner_name"],
            debug_capture_enabled=bool(row["debug_capture_enabled"]),
            llm_configs=from_json(row["llm_configs_json"])
            if row["llm_configs_json"]
            else None,
        )

    def save_from_state_machine(
        self,
        game_id: str,
        state_machine,
        owner_id: Optional[str] = None,
        owner_name: Optional[str] = None,
        llm_configs: Optional[Dict[str, Any]] = None,
        debug_capture_enabled: bool = False,
    ) -> None:
        """Save a game from a state machine (convenience method).

        Args:
            game_id: The game identifier
            state_machine: The game's state machine
            owner_id: The owner/user ID
            owner_name: The owner's display name
            llm_configs: Dict with LLM configs
            debug_capture_enabled: Whether debug capture is enabled
        """
        # Check if game exists to preserve created_at
        existing = self.find_by_id(game_id)
        created_at = existing.created_at if existing else datetime.now()

        entity = GameEntity(
            id=game_id,
            state_machine=state_machine,
            created_at=created_at,
            updated_at=datetime.now(),
            owner_id=owner_id,
            owner_name=owner_name,
            debug_capture_enabled=debug_capture_enabled,
            llm_configs=llm_configs,
        )
        self.save(entity)


class SQLiteMessageRepository:
    """SQLite implementation of MessageRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save(self, message: MessageEntity) -> MessageEntity:
        """Save a message and return it with ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO game_messages (game_id, message_type, message_text, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    message.game_id,
                    message.message_type,
                    message.message_text,
                    message.timestamp.isoformat(),
                ),
            )
            message.id = cursor.lastrowid

        return message

    def find_by_game_id(self, game_id: str, limit: int = 100) -> List[MessageEntity]:
        """Find messages for a game."""
        rows = self._db.fetch_all(
            """
            SELECT * FROM game_messages
            WHERE game_id = ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (game_id, limit),
        )

        return [self._row_to_entity(row) for row in rows]

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all messages for a game."""
        self._db.execute(
            "DELETE FROM game_messages WHERE game_id = ?",
            (game_id,),
        )

    def _row_to_entity(self, row) -> MessageEntity:
        """Convert a database row to a MessageEntity."""
        return MessageEntity(
            id=row["id"],
            game_id=row["game_id"],
            message_type=row["message_type"],
            message_text=row["message_text"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )
