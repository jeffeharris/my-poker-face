"""Tournament repository — tournament results, career stats, and history."""
import logging
from typing import Dict, List, Any, Optional

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class TournamentRepository(BaseRepository):
    """Manages tournament results, standings, career stats, and history."""

    def save_tournament_result(self, game_id: str, result: Dict[str, Any]) -> None:
        """Save tournament result when game completes.

        Args:
            game_id: The game identifier
            result: Dict with keys: winner_name, total_hands, biggest_pot,
                   starting_player_count, human_player_name, human_finishing_position,
                   started_at, standings (list of player standings),
                   owner_id (optional, human player's auth identity)
        """
        owner_id = result.get('owner_id')

        with self._get_connection() as conn:
            # Save main tournament result
            conn.execute("""
                INSERT OR REPLACE INTO tournament_results
                (game_id, winner_name, total_hands, biggest_pot, starting_player_count,
                 human_player_name, human_finishing_position, started_at, ended_at, human_owner_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """, (
                game_id,
                result.get('winner_name'),
                result.get('total_hands', 0),
                result.get('biggest_pot', 0),
                result.get('starting_player_count'),
                result.get('human_player_name'),
                result.get('human_finishing_position'),
                result.get('started_at'),
                owner_id
            ))

            # Save individual standings
            standings = result.get('standings', [])
            for standing in standings:
                # Set owner_id on the human player's standing row
                standing_owner_id = owner_id if standing.get('is_human') else None
                conn.execute("""
                    INSERT OR REPLACE INTO tournament_standings
                    (game_id, player_name, is_human, finishing_position,
                     eliminated_by, eliminated_at_hand, final_stack, hands_won, hands_played,
                     times_eliminated, all_in_wins, all_in_losses, owner_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    game_id,
                    standing.get('player_name'),
                    standing.get('is_human', False),
                    standing.get('finishing_position'),
                    standing.get('eliminated_by'),
                    standing.get('eliminated_at_hand'),
                    standing.get('final_stack'),
                    standing.get('hands_won'),
                    standing.get('hands_played'),
                    standing.get('times_eliminated', 0),
                    standing.get('all_in_wins', 0),
                    standing.get('all_in_losses', 0),
                    standing_owner_id,
                ))

    def get_tournament_result(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Load tournament result for a completed game."""
        with self._get_connection() as conn:
            # Get main result
            cursor = conn.execute("""
                SELECT * FROM tournament_results WHERE game_id = ?
            """, (game_id,))
            row = cursor.fetchone()

            if not row:
                return None

            # Get standings
            standings_cursor = conn.execute("""
                SELECT * FROM tournament_standings
                WHERE game_id = ?
                ORDER BY finishing_position ASC
            """, (game_id,))

            standings = []
            for s_row in standings_cursor.fetchall():
                standings.append({
                    'player_name': s_row['player_name'],
                    'is_human': bool(s_row['is_human']),
                    'finishing_position': s_row['finishing_position'],
                    'eliminated_by': s_row['eliminated_by'],
                    'eliminated_at_hand': s_row['eliminated_at_hand']
                })

            return {
                'game_id': row['game_id'],
                'winner_name': row['winner_name'],
                'total_hands': row['total_hands'],
                'biggest_pot': row['biggest_pot'],
                'starting_player_count': row['starting_player_count'],
                'human_player_name': row['human_player_name'],
                'human_finishing_position': row['human_finishing_position'],
                'started_at': row['started_at'],
                'ended_at': row['ended_at'],
                'standings': standings
            }

    def update_career_stats(self, owner_id: str, player_name: str, tournament_result: Dict[str, Any]) -> None:
        """Update career stats for a player after a tournament.

        Args:
            owner_id: The user's auth identity (e.g., 'guest_jeff' or Google ID)
            player_name: The human player's display name
            tournament_result: Dict with tournament result data
        """
        # Find the player's standing in this tournament
        standings = tournament_result.get('standings', [])
        player_standing = next(
            (s for s in standings if s.get('player_name') == player_name),
            None
        )

        if not player_standing:
            logger.warning(f"Player {player_name} not found in tournament standings")
            return

        finishing_position = player_standing.get('finishing_position', 0)
        is_winner = finishing_position == 1

        # Count eliminations by this player
        eliminations_this_game = sum(
            1 for s in standings
            if s.get('eliminated_by') == player_name
        )

        biggest_pot = tournament_result.get('biggest_pot', 0)

        with self._get_connection() as conn:
            # Look up by owner_id first, fall back to player_name for legacy data
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE owner_id = ?
            """, (owner_id,))
            row = cursor.fetchone()

            if not row:
                # Try legacy lookup by player_name (for pre-migration data)
                cursor = conn.execute("""
                    SELECT * FROM player_career_stats WHERE player_name = ? AND owner_id IS NULL
                """, (player_name,))
                row = cursor.fetchone()

            if row:
                # Update existing stats
                games_played = row['games_played'] + 1
                games_won = row['games_won'] + (1 if is_winner else 0)
                total_eliminations = row['total_eliminations'] + eliminations_this_game

                # Update best/worst finish
                best_finish = row['best_finish']
                if best_finish is None or finishing_position < best_finish:
                    best_finish = finishing_position

                worst_finish = row['worst_finish']
                if worst_finish is None or finishing_position > worst_finish:
                    worst_finish = finishing_position

                # Calculate new average
                old_avg = row['avg_finish'] or finishing_position
                avg_finish = ((old_avg * (games_played - 1)) + finishing_position) / games_played

                # Update biggest pot
                biggest_pot_ever = max(row['biggest_pot_ever'] or 0, biggest_pot)

                conn.execute("""
                    UPDATE player_career_stats
                    SET games_played = ?,
                        games_won = ?,
                        total_eliminations = ?,
                        best_finish = ?,
                        worst_finish = ?,
                        avg_finish = ?,
                        biggest_pot_ever = ?,
                        owner_id = ?,
                        player_name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (
                    games_played, games_won, total_eliminations,
                    best_finish, worst_finish, avg_finish, biggest_pot_ever,
                    owner_id, player_name,
                    row['id']
                ))
            else:
                # Insert new player
                conn.execute("""
                    INSERT INTO player_career_stats
                    (player_name, owner_id, games_played, games_won, total_eliminations,
                     best_finish, worst_finish, avg_finish, biggest_pot_ever)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                """, (
                    player_name,
                    owner_id,
                    1 if is_winner else 0,
                    eliminations_this_game,
                    finishing_position,
                    finishing_position,
                    float(finishing_position),
                    biggest_pot
                ))

    def get_career_stats(self, owner_id: str) -> Optional[Dict[str, Any]]:
        """Get career stats for a player by owner_id."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM player_career_stats WHERE owner_id = ?
            """, (owner_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'player_name': row['player_name'],
                'games_played': row['games_played'],
                'games_won': row['games_won'],
                'total_eliminations': row['total_eliminations'],
                'best_finish': row['best_finish'],
                'worst_finish': row['worst_finish'],
                'avg_finish': row['avg_finish'],
                'biggest_pot_ever': row['biggest_pot_ever'],
                'win_rate': row['games_won'] / row['games_played'] if row['games_played'] > 0 else 0
            }

    def get_tournament_history(self, owner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get tournament history for a player by owner_id."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT tr.*, ts.finishing_position, ts.eliminated_by
                FROM tournament_results tr
                JOIN tournament_standings ts ON tr.game_id = ts.game_id
                WHERE ts.owner_id = ?
                ORDER BY tr.ended_at DESC
                LIMIT ?
            """, (owner_id, limit))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'game_id': row['game_id'],
                    'winner_name': row['winner_name'],
                    'total_hands': row['total_hands'],
                    'biggest_pot': row['biggest_pot'],
                    'player_count': row['starting_player_count'],
                    'your_position': row['finishing_position'],
                    'eliminated_by': row['eliminated_by'],
                    'ended_at': row['ended_at']
                })

            return history

    def get_eliminated_personalities(self, owner_id: str) -> List[Dict[str, Any]]:
        """Get all unique personalities eliminated by this player across all games.

        Uses owner_id to find the human player's names, then looks for AI players
        eliminated by any of those names.

        Returns a list of personalities with the first time they were eliminated.
        """
        with self._get_connection() as conn:
            # Get unique personalities eliminated by this player, with first elimination date.
            # The eliminated_by column stores player_name, so we find all names associated
            # with this owner_id via tournament_standings, then match.
            cursor = conn.execute("""
                SELECT
                    ts.player_name as personality_name,
                    MIN(tr.ended_at) as first_eliminated_at,
                    COUNT(*) as times_eliminated
                FROM tournament_standings ts
                JOIN tournament_results tr ON ts.game_id = tr.game_id
                WHERE ts.eliminated_by IN (
                    SELECT DISTINCT player_name FROM tournament_standings WHERE owner_id = ?
                ) AND ts.is_human = 0
                GROUP BY ts.player_name
                ORDER BY MIN(tr.ended_at) ASC
            """, (owner_id,))

            personalities = []
            for row in cursor.fetchall():
                personalities.append({
                    'name': row['personality_name'],
                    'first_eliminated_at': row['first_eliminated_at'],
                    'times_eliminated': row['times_eliminated']
                })

            return personalities
