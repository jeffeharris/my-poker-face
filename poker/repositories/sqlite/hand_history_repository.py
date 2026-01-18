"""
SQLite implementation of hand history repository.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..database import DatabaseContext
from ..protocols import HandHistoryEntity
from ..serialization import to_json, from_json


class SQLiteHandHistoryRepository:
    """SQLite implementation of HandHistoryRepositoryProtocol."""

    def __init__(self, db: DatabaseContext):
        self._db = db

    def save(self, hand: HandHistoryEntity) -> int:
        """Save a hand record. Returns the hand ID."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO hand_history (
                    game_id, hand_number, phase, community_cards,
                    pot_size, player_hands, actions, winners, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hand.game_id,
                    hand.hand_number,
                    hand.phase,
                    to_json(hand.community_cards),
                    hand.pot_size,
                    to_json(hand.player_hands),
                    to_json(hand.actions),
                    to_json(hand.winners),
                    hand.timestamp.isoformat(),
                ),
            )
            return cursor.lastrowid

    def find_by_game_id(
        self, game_id: str, limit: Optional[int] = None
    ) -> List[HandHistoryEntity]:
        """Find hand history for a game."""
        if limit:
            rows = self._db.fetch_all(
                """
                SELECT * FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number DESC
                LIMIT ?
                """,
                (game_id, limit),
            )
        else:
            rows = self._db.fetch_all(
                """
                SELECT * FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number DESC
                """,
                (game_id,),
            )

        return [self._row_to_entity(row) for row in rows]

    def find_by_hand_number(self, game_id: str, hand_number: int) -> Optional[HandHistoryEntity]:
        """Find a specific hand by game_id and hand_number."""
        row = self._db.fetch_one(
            """
            SELECT * FROM hand_history
            WHERE game_id = ? AND hand_number = ?
            """,
            (game_id, hand_number),
        )
        if not row:
            return None
        return self._row_to_entity(row)

    def get_hand_count(self, game_id: str) -> int:
        """Get the number of hands played in a game."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM hand_history WHERE game_id = ?",
            (game_id,),
        )
        return row["count"] if row else 0

    def get_session_stats(self, game_id: str, player_name: str) -> Dict[str, Any]:
        """Get session statistics for a player."""
        # Get all hands for the game
        rows = self._db.fetch_all(
            """
            SELECT * FROM hand_history
            WHERE game_id = ?
            ORDER BY hand_number ASC
            """,
            (game_id,),
        )

        stats = {
            "hands_played": 0,
            "hands_won": 0,
            "biggest_pot_won": 0,
            "biggest_pot_lost": 0,
            "total_won": 0,
            "total_lost": 0,
            "showdowns": 0,
            "showdowns_won": 0,
        }

        for row in rows:
            stats["hands_played"] += 1

            winners = from_json(row["winners"]) or []
            pot_size = row["pot_size"]

            if player_name in winners:
                stats["hands_won"] += 1
                stats["biggest_pot_won"] = max(stats["biggest_pot_won"], pot_size)
                stats["total_won"] += pot_size / len(winners)  # Split pot
            else:
                # Check if player was in the hand
                player_hands = from_json(row["player_hands"]) or {}
                if player_name in player_hands:
                    # Player was in hand but didn't win
                    actions = from_json(row["actions"]) or []
                    player_contributions = sum(
                        a.get("amount", 0)
                        for a in actions
                        if a.get("player") == player_name
                        and a.get("action") in ("bet", "raise", "call", "blind")
                    )
                    stats["total_lost"] += player_contributions
                    stats["biggest_pot_lost"] = max(
                        stats["biggest_pot_lost"], player_contributions
                    )

            # Check for showdowns
            if row["phase"] == "SHOWDOWN":
                player_hands = from_json(row["player_hands"]) or {}
                if player_name in player_hands:
                    stats["showdowns"] += 1
                    if player_name in winners:
                        stats["showdowns_won"] += 1

        return stats

    def delete_by_game_id(self, game_id: str) -> None:
        """Delete all hand history for a game."""
        self._db.execute(
            "DELETE FROM hand_history WHERE game_id = ?",
            (game_id,),
        )

    def _row_to_entity(self, row) -> HandHistoryEntity:
        """Convert a database row to a HandHistoryEntity."""
        return HandHistoryEntity(
            id=row["id"],
            game_id=row["game_id"],
            hand_number=row["hand_number"],
            phase=row["phase"],
            community_cards=from_json(row["community_cards"]) or [],
            pot_size=row["pot_size"],
            player_hands=from_json(row["player_hands"]) or {},
            actions=from_json(row["actions"]) or [],
            winners=from_json(row["winners"]) or [],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )
