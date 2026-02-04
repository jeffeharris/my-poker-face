"""Hand equity repository — equity snapshots for pressure detection and analytics."""
import json
import logging
from typing import Dict, List, Optional, Any

from .base_repository import BaseRepository
from ..equity_snapshot import EquitySnapshot, HandEquityHistory

logger = logging.getLogger(__name__)


class HandEquityRepository(BaseRepository):
    """Manages equity snapshots for hands."""

    def save_equity_history(self, equity_history: HandEquityHistory) -> None:
        """Save all equity snapshots for a hand.

        Args:
            equity_history: HandEquityHistory with all snapshots for the hand
        """
        if not equity_history.snapshots:
            return

        with self._get_connection() as conn:
            for snap in equity_history.snapshots:
                conn.execute("""
                    INSERT OR REPLACE INTO hand_equity
                    (hand_history_id, game_id, hand_number, street, player_name,
                     player_hole_cards, board_cards, equity, was_active, sample_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    equity_history.hand_history_id,
                    equity_history.game_id,
                    equity_history.hand_number,
                    snap.street,
                    snap.player_name,
                    json.dumps(list(snap.hole_cards)),
                    json.dumps(list(snap.board_cards)),
                    snap.equity,
                    snap.was_active,
                    snap.sample_count,
                ))

            logger.debug(
                f"Saved {len(equity_history.snapshots)} equity snapshots for "
                f"hand #{equity_history.hand_number} in game {equity_history.game_id}"
            )

    def get_equity_history(self, hand_history_id: int) -> Optional[HandEquityHistory]:
        """Retrieve equity history for a hand by hand_history_id.

        Args:
            hand_history_id: The hand_history table ID

        Returns:
            HandEquityHistory or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT game_id, hand_number, street, player_name,
                       player_hole_cards, board_cards, equity, was_active, sample_count
                FROM hand_equity
                WHERE hand_history_id = ?
                ORDER BY
                    CASE street
                        WHEN 'PRE_FLOP' THEN 1
                        WHEN 'FLOP' THEN 2
                        WHEN 'TURN' THEN 3
                        WHEN 'RIVER' THEN 4
                    END,
                    player_name
            """, (hand_history_id,))

            rows = cursor.fetchall()
            if not rows:
                return None

            snapshots = []
            game_id = None
            hand_number = None

            for row in rows:
                game_id = row['game_id']
                hand_number = row['hand_number']
                snapshots.append(EquitySnapshot(
                    player_name=row['player_name'],
                    street=row['street'],
                    equity=row['equity'],
                    hole_cards=tuple(json.loads(row['player_hole_cards'] or '[]')),
                    board_cards=tuple(json.loads(row['board_cards'] or '[]')),
                    was_active=bool(row['was_active']),
                    sample_count=row['sample_count'],
                ))

            return HandEquityHistory(
                hand_history_id=hand_history_id,
                game_id=game_id or '',
                hand_number=hand_number or 0,
                snapshots=tuple(snapshots),
            )

    def get_equity_history_by_game_hand(
        self, game_id: str, hand_number: int
    ) -> Optional[HandEquityHistory]:
        """Retrieve equity history for a hand by game_id and hand_number.

        Args:
            game_id: The game identifier
            hand_number: The hand number within the game

        Returns:
            HandEquityHistory or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT hand_history_id, street, player_name,
                       player_hole_cards, board_cards, equity, was_active, sample_count
                FROM hand_equity
                WHERE game_id = ? AND hand_number = ?
                ORDER BY
                    CASE street
                        WHEN 'PRE_FLOP' THEN 1
                        WHEN 'FLOP' THEN 2
                        WHEN 'TURN' THEN 3
                        WHEN 'RIVER' THEN 4
                    END,
                    player_name
            """, (game_id, hand_number))

            rows = cursor.fetchall()
            if not rows:
                return None

            hand_history_id = rows[0]['hand_history_id']
            snapshots = [
                EquitySnapshot(
                    player_name=row['player_name'],
                    street=row['street'],
                    equity=row['equity'],
                    hole_cards=tuple(json.loads(row['player_hole_cards'] or '[]')),
                    board_cards=tuple(json.loads(row['board_cards'] or '[]')),
                    was_active=bool(row['was_active']),
                    sample_count=row['sample_count'],
                )
                for row in rows
            ]

            return HandEquityHistory(
                hand_history_id=hand_history_id,
                game_id=game_id,
                hand_number=hand_number,
                snapshots=tuple(snapshots),
            )

    def get_player_equity_stats(
        self, player_name: str, game_id: Optional[str] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """Get aggregate equity statistics for a player.

        Args:
            player_name: The player's name
            game_id: Optional game ID to filter by
            limit: Maximum number of hands to analyze

        Returns:
            Dict with avg_equity_by_street, suckout_count, got_sucked_out_count, etc.
        """
        with self._get_connection() as conn:
            # Average equity by street
            if game_id:
                cursor = conn.execute("""
                    SELECT street, AVG(equity) as avg_equity, COUNT(*) as count
                    FROM hand_equity
                    WHERE player_name = ? AND game_id = ?
                    GROUP BY street
                    ORDER BY
                        CASE street
                            WHEN 'PRE_FLOP' THEN 1
                            WHEN 'FLOP' THEN 2
                            WHEN 'TURN' THEN 3
                            WHEN 'RIVER' THEN 4
                        END
                """, (player_name, game_id))
            else:
                cursor = conn.execute("""
                    SELECT street, AVG(equity) as avg_equity, COUNT(*) as count
                    FROM hand_equity
                    WHERE player_name = ?
                    GROUP BY street
                    ORDER BY
                        CASE street
                            WHEN 'PRE_FLOP' THEN 1
                            WHEN 'FLOP' THEN 2
                            WHEN 'TURN' THEN 3
                            WHEN 'RIVER' THEN 4
                        END
                    LIMIT ?
                """, (player_name, limit))

            equity_by_street = {
                row['street']: {'avg_equity': row['avg_equity'], 'count': row['count']}
                for row in cursor.fetchall()
            }

            return {
                'player_name': player_name,
                'game_id': game_id,
                'equity_by_street': equity_by_street,
            }

    def find_suckouts(
        self, game_id: str, threshold: float = 0.40
    ) -> List[Dict[str, Any]]:
        """Find hands where winner was behind on turn but won.

        Args:
            game_id: The game identifier
            threshold: Equity threshold below which player is considered "behind"

        Returns:
            List of dicts with hand_number, player_name, turn_equity, river_equity
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    turn.hand_number,
                    turn.player_name,
                    turn.equity as turn_equity,
                    river.equity as river_equity,
                    turn.player_hole_cards
                FROM hand_equity turn
                JOIN hand_equity river
                    ON turn.hand_history_id = river.hand_history_id
                    AND turn.player_name = river.player_name
                WHERE turn.game_id = ?
                    AND turn.street = 'TURN'
                    AND river.street = 'RIVER'
                    AND turn.equity < ?
                    AND river.equity > 0.99
                ORDER BY turn.hand_number
            """, (game_id, threshold))

            return [
                {
                    'hand_number': row['hand_number'],
                    'player_name': row['player_name'],
                    'turn_equity': row['turn_equity'],
                    'river_equity': row['river_equity'],
                    'hole_cards': json.loads(row['player_hole_cards'] or '[]'),
                }
                for row in cursor.fetchall()
            ]

    def find_coolers(
        self, game_id: str, min_equity: float = 0.30
    ) -> List[Dict[str, Any]]:
        """Find hands where both players had strong flop equity.

        Args:
            game_id: The game identifier
            min_equity: Minimum equity to be considered "strong"

        Returns:
            List of dicts with hand_number, players, and their flop equities
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    he1.hand_number,
                    he1.player_name as player1,
                    he1.equity as equity1,
                    he2.player_name as player2,
                    he2.equity as equity2
                FROM hand_equity he1
                JOIN hand_equity he2
                    ON he1.hand_history_id = he2.hand_history_id
                    AND he1.street = he2.street
                    AND he1.player_name < he2.player_name
                WHERE he1.game_id = ?
                    AND he1.street = 'FLOP'
                    AND he1.equity >= ?
                    AND he2.equity >= ?
                ORDER BY he1.hand_number
            """, (game_id, min_equity, min_equity))

            return [
                {
                    'hand_number': row['hand_number'],
                    'player1': row['player1'],
                    'equity1': row['equity1'],
                    'player2': row['player2'],
                    'equity2': row['equity2'],
                }
                for row in cursor.fetchall()
            ]
