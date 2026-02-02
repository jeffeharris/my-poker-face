"""Hand history repository — hand records, commentary, and session stats."""
import json
import logging
from datetime import datetime
from typing import Dict, List, Any

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class HandHistoryRepository(BaseRepository):
    """Manages hand history, hand commentary, and session statistics."""

    def save_hand_history(self, recorded_hand) -> int:
        """Save a completed hand to the database.

        Args:
            recorded_hand: RecordedHand instance from hand_history.py

        Returns:
            The database ID of the saved hand
        """
        # Convert to dict if it's a RecordedHand object
        if hasattr(recorded_hand, 'to_dict'):
            hand_dict = recorded_hand.to_dict()
        else:
            hand_dict = recorded_hand

        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT OR REPLACE INTO hand_history
                (game_id, hand_number, timestamp, players_json, hole_cards_json,
                 community_cards_json, actions_json, winners_json, pot_size, showdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hand_dict['game_id'],
                hand_dict['hand_number'],
                hand_dict.get('timestamp', datetime.now().isoformat()),
                json.dumps(hand_dict.get('players', [])),
                json.dumps(hand_dict.get('hole_cards', {})),
                json.dumps(hand_dict.get('community_cards', [])),
                json.dumps(hand_dict.get('actions', [])),
                json.dumps(hand_dict.get('winners', [])),
                hand_dict.get('pot_size', 0),
                hand_dict.get('was_showdown', False)
            ))

            hand_id = cursor.lastrowid
            logger.debug(f"Saved hand #{hand_dict['hand_number']} for game {hand_dict['game_id']}")
            return hand_id

    # Hand Commentary Persistence Methods
    def save_hand_commentary(self, game_id: str, hand_number: int, player_name: str,
                             commentary) -> None:
        """Save AI commentary for a completed hand.

        Args:
            game_id: The game identifier
            hand_number: The hand number
            player_name: The AI player's name
            commentary: HandCommentary instance or dict
        """
        if hasattr(commentary, 'to_dict'):
            c = commentary.to_dict()
        else:
            c = commentary

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO hand_commentary
                (game_id, hand_number, player_name, emotional_reaction,
                 strategic_reflection, opponent_observations, key_insight, decision_plans)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                hand_number,
                player_name,
                c.get('emotional_reaction'),
                c.get('strategic_reflection'),
                json.dumps(c.get('opponent_observations', [])),
                c.get('key_insight'),
                json.dumps(c.get('decision_plans', []))
            ))
            logger.debug(f"Saved commentary for {player_name} hand #{hand_number}")

    def get_recent_reflections(self, game_id: str, player_name: str,
                               limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent strategic reflections for a player.

        Args:
            game_id: The game identifier
            player_name: The AI player's name
            limit: Maximum number of reflections to return

        Returns:
            List of dicts with hand_number, strategic_reflection, key_insight
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT hand_number, strategic_reflection, key_insight,
                       opponent_observations
                FROM hand_commentary
                WHERE game_id = ? AND player_name = ?
                ORDER BY hand_number DESC
                LIMIT ?
            """, (game_id, player_name, limit))

            return [dict(row) for row in cursor.fetchall()]

    def get_hand_count(self, game_id: str) -> int:
        """Get the current hand count for a game.

        Args:
            game_id: The game identifier

        Returns:
            The maximum hand_number for this game, or 0 if no hands recorded
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT MAX(hand_number) FROM hand_history WHERE game_id = ?",
                (game_id,)
            )
            result = cursor.fetchone()[0]
            return result or 0

    def load_hand_history(self, game_id: str, limit: int = None) -> List[Dict[str, Any]]:
        """Load hand history for a game.

        Args:
            game_id: The game identifier
            limit: Optional limit on number of hands to load (most recent first)

        Returns:
            List of hand dicts suitable for RecordedHand.from_dict()
        """
        hands = []

        with self._get_connection() as conn:
            query = """
                SELECT * FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number DESC
            """
            params = [game_id]
            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor = conn.execute(query, params)

            for row in cursor.fetchall():
                hand = {
                    'id': row['id'],
                    'game_id': row['game_id'],
                    'hand_number': row['hand_number'],
                    'timestamp': row['timestamp'],
                    'players': json.loads(row['players_json'] or '[]'),
                    'hole_cards': json.loads(row['hole_cards_json'] or '{}'),
                    'community_cards': json.loads(row['community_cards_json'] or '[]'),
                    'actions': json.loads(row['actions_json'] or '[]'),
                    'winners': json.loads(row['winners_json'] or '[]'),
                    'pot_size': row['pot_size'] or 0,
                    'was_showdown': bool(row['showdown'])
                }
                hands.append(hand)

        # Return in chronological order (oldest first)
        hands.reverse()

        if hands:
            logger.debug(f"Loaded {len(hands)} hands for game {game_id}")

        return hands

    def delete_hand_history_for_game(self, game_id: str) -> None:
        """Delete all hand history for a game."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM hand_history WHERE game_id = ?", (game_id,))

    def get_session_stats(self, game_id: str, player_name: str) -> Dict[str, Any]:
        """Compute session statistics for a player from hand history.

        Args:
            game_id: The game identifier
            player_name: The player to get stats for

        Returns:
            Dict with session statistics:
                - hands_played: Total hands where player participated
                - hands_won: Hands where player won
                - total_winnings: Net chip change (positive = up, negative = down)
                - biggest_pot_won: Largest pot won
                - biggest_pot_lost: Largest pot lost at showdown
                - current_streak: 'winning', 'losing', or 'neutral'
                - streak_count: Length of current streak
                - recent_hands: List of last N hand summaries
        """
        stats = {
            'hands_played': 0,
            'hands_won': 0,
            'total_winnings': 0,
            'biggest_pot_won': 0,
            'biggest_pot_lost': 0,
            'current_streak': 'neutral',
            'streak_count': 0,
            'recent_hands': []
        }

        with self._get_connection() as conn:
            # Get all hands for this game, ordered by hand_number
            cursor = conn.execute("""
                SELECT hand_number, players_json, winners_json, actions_json, pot_size, showdown
                FROM hand_history
                WHERE game_id = ?
                ORDER BY hand_number ASC
            """, (game_id,))

            outcomes = []  # Track outcomes for streak calculation

            for row in cursor.fetchall():
                players = json.loads(row['players_json'] or '[]')
                winners = json.loads(row['winners_json'] or '[]')
                actions = json.loads(row['actions_json'] or '[]')
                pot_size = row['pot_size'] or 0

                # Check if player participated in this hand
                player_in_hand = any(p.get('name') == player_name for p in players)
                if not player_in_hand:
                    continue

                stats['hands_played'] += 1

                # Check if player won
                player_won = False
                amount_won = 0
                for winner in winners:
                    if winner.get('name') == player_name:
                        player_won = True
                        amount_won = winner.get('amount_won', 0)
                        break

                # Calculate amount lost (sum of player's bets)
                amount_bet = sum(
                    a.get('amount', 0)
                    for a in actions
                    if a.get('player_name') == player_name
                )

                # Determine outcome
                if player_won:
                    stats['hands_won'] += 1
                    stats['total_winnings'] += (amount_won - amount_bet)
                    outcomes.append('won')
                    if pot_size > stats['biggest_pot_won']:
                        stats['biggest_pot_won'] = pot_size
                else:
                    # Check if folded or lost at showdown
                    folded = any(
                        a.get('player_name') == player_name and a.get('action') == 'fold'
                        for a in actions
                    )
                    if folded:
                        outcomes.append('folded')
                        stats['total_winnings'] -= amount_bet
                    else:
                        # Lost at showdown
                        outcomes.append('lost')
                        stats['total_winnings'] -= amount_bet
                        if pot_size > stats['biggest_pot_lost']:
                            stats['biggest_pot_lost'] = pot_size

                # Build recent hand summary (keep last 5)
                if len(stats['recent_hands']) >= 5:
                    stats['recent_hands'].pop(0)

                outcome_str = outcomes[-1]
                if outcome_str == 'won':
                    summary = f"Hand {row['hand_number']}: Won ${amount_won}"
                elif outcome_str == 'folded':
                    summary = f"Hand {row['hand_number']}: Folded"
                else:
                    summary = f"Hand {row['hand_number']}: Lost ${amount_bet}"
                stats['recent_hands'].append(summary)

            # Calculate current streak from outcomes
            if outcomes:
                current = outcomes[-1]
                if current in ('won', 'lost'):
                    streak_type = 'winning' if current == 'won' else 'losing'
                    streak_count = 1
                    for outcome in reversed(outcomes[:-1]):
                        if (streak_type == 'winning' and outcome == 'won') or \
                           (streak_type == 'losing' and outcome == 'lost'):
                            streak_count += 1
                        else:
                            break
                    stats['current_streak'] = streak_type
                    stats['streak_count'] = streak_count

        return stats

    def get_session_context_for_prompt(self, game_id: str, player_name: str,
                                        max_recent: int = 3) -> str:
        """Get formatted session context string for AI prompts.

        Args:
            game_id: The game identifier
            player_name: The player to get context for
            max_recent: Maximum number of recent hands to include

        Returns:
            Formatted string suitable for injection into AI prompts
        """
        stats = self.get_session_stats(game_id, player_name)

        parts = []

        # Session overview
        if stats['hands_played'] > 0:
            win_rate = (stats['hands_won'] / stats['hands_played']) * 100
            parts.append(f"Session: {stats['hands_won']}/{stats['hands_played']} hands won ({win_rate:.0f}%)")

            # Net result
            if stats['total_winnings'] > 0:
                parts.append(f"Up ${stats['total_winnings']}")
            elif stats['total_winnings'] < 0:
                parts.append(f"Down ${abs(stats['total_winnings'])}")

            # Current streak (only show if 2+)
            if stats['streak_count'] >= 2:
                parts.append(f"On a {stats['streak_count']}-hand {stats['current_streak']} streak")

        # Recent hands
        recent = stats['recent_hands'][-max_recent:]
        if recent:
            parts.append("Recent: " + " | ".join(recent))

        return ". ".join(parts) if parts else ""
