"""Repository for user management and RBAC persistence.

Manages the users, groups, user_groups, permissions, and group_permissions tables.
"""
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class UserRepository(BaseRepository):
    """Handles user management and RBAC operations."""

    def count_user_games(self, owner_id: str) -> int:
        """Count how many games a user owns."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM games WHERE owner_id = ?
            """, (owner_id,))
            return cursor.fetchone()[0]

    def get_last_game_creation_time(self, owner_id: str) -> Optional[float]:
        """Get the timestamp of the user's last game creation."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT last_game_created_at FROM users WHERE id = ?",
                (owner_id,)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else None

    def update_last_game_creation_time(self, owner_id: str, timestamp: float) -> None:
        """Update the user's last game creation timestamp."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE users SET last_game_created_at = ? WHERE id = ?",
                (timestamp, owner_id)
            )

    def create_google_user(
        self,
        google_sub: str,
        email: str,
        name: str,
        picture: Optional[str] = None,
        linked_guest_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new user from Google OAuth.

        Args:
            google_sub: Google's unique subject identifier
            email: User's email address
            name: User's display name
            picture: URL to user's profile picture
            linked_guest_id: Optional guest ID this account was linked from

        Returns:
            Dict containing user data

        Raises:
            sqlite3.IntegrityError: If email already exists
        """
        user_id = f"google_{google_sub}"
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO users (id, email, name, picture, created_at, last_login, linked_guest_id, is_guest)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (user_id, email, name, picture, now, now, linked_guest_id))

            # Auto-assign to 'user' group for full game access
            conn.execute("""
                INSERT OR IGNORE INTO user_groups (user_id, group_id, assigned_by)
                SELECT ?, id, 'system' FROM groups WHERE name = 'user'
            """, (user_id,))

        return {
            'id': user_id,
            'email': email,
            'name': name,
            'picture': picture,
            'is_guest': False,
            'created_at': now,
            'linked_guest_id': linked_guest_id
        }

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by their ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by their email address."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_user_by_linked_guest(self, guest_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by the guest ID they were linked from."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE linked_guest_id = ?",
                (guest_id,)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def update_user_last_login(self, user_id: str) -> None:
        """Update the last login timestamp for a user."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), user_id)
            )

    def transfer_game_ownership(self, from_owner_id: str, to_owner_id: str, to_owner_name: str) -> int:
        """Transfer all games from one owner to another."""
        return self.transfer_guest_to_user(from_owner_id, to_owner_id, to_owner_name)

    def transfer_guest_to_user(self, from_id: str, to_id: str, to_name: str) -> int:
        """Transfer all owner_id references from guest to authenticated user.

        Updates owner_id across all relevant tables in a single transaction.
        If the target user already has career stats, the guest stats are merged.

        Returns:
            Number of games transferred
        """
        with self._get_connection() as conn:
            # Transfer games
            cursor = conn.execute("""
                UPDATE games
                SET owner_id = ?, owner_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE owner_id = ?
            """, (to_id, to_name, from_id))
            games_transferred = cursor.rowcount

            # Transfer API usage records
            conn.execute("""
                UPDATE api_usage SET owner_id = ? WHERE owner_id = ?
            """, (to_id, from_id))

            # Transfer prompt captures
            conn.execute("""
                UPDATE prompt_captures SET owner_id = ? WHERE owner_id = ?
            """, (to_id, from_id))

            # Transfer career stats — merge if target already has stats
            existing_target = conn.execute(
                "SELECT id FROM player_career_stats WHERE owner_id = ?", (to_id,)
            ).fetchone()
            existing_guest = conn.execute(
                "SELECT id FROM player_career_stats WHERE owner_id = ?", (from_id,)
            ).fetchone()

            if existing_guest and existing_target:
                conn.execute("""
                    UPDATE player_career_stats
                    SET games_played = player_career_stats.games_played + g.games_played,
                        games_won = player_career_stats.games_won + g.games_won,
                        total_eliminations = player_career_stats.total_eliminations + g.total_eliminations,
                        best_finish = MIN(player_career_stats.best_finish, g.best_finish),
                        worst_finish = MAX(player_career_stats.worst_finish, g.worst_finish),
                        biggest_pot_ever = MAX(player_career_stats.biggest_pot_ever, g.biggest_pot_ever),
                        updated_at = CURRENT_TIMESTAMP
                    FROM player_career_stats g
                    WHERE player_career_stats.owner_id = ?
                      AND g.owner_id = ?
                """, (to_id, from_id))
                conn.execute(
                    "DELETE FROM player_career_stats WHERE owner_id = ?", (from_id,)
                )
            elif existing_guest:
                conn.execute("""
                    UPDATE player_career_stats SET owner_id = ? WHERE owner_id = ?
                """, (to_id, from_id))

            # Transfer tournament standings
            conn.execute("""
                UPDATE tournament_standings SET owner_id = ? WHERE owner_id = ?
            """, (to_id, from_id))

            # Transfer tournament results
            conn.execute("""
                UPDATE tournament_results SET human_owner_id = ? WHERE human_owner_id = ?
            """, (to_id, from_id))

            return games_transferred

    # --- RBAC / Group Management ---

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users with their groups."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, email, name, picture, created_at, last_login, linked_guest_id, is_guest
                FROM users
                ORDER BY last_login DESC NULLS LAST, created_at DESC
            """)
            rows = cursor.fetchall()

            return [
                {**dict(row), 'groups': self.get_user_groups(row['id'])}
                for row in rows
            ]

    def get_user_groups(self, user_id: str) -> List[str]:
        """Get all group names for a user."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT g.name
                FROM groups g
                JOIN user_groups ug ON g.id = ug.group_id
                WHERE ug.user_id = ?
                ORDER BY g.name
            """, (user_id,))
            return [row[0] for row in cursor.fetchall()]

    def get_user_permissions(self, user_id: str) -> List[str]:
        """Get all permissions for a user via their groups."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT DISTINCT p.name
                FROM permissions p
                JOIN group_permissions gp ON p.id = gp.permission_id
                JOIN user_groups ug ON gp.group_id = ug.group_id
                WHERE ug.user_id = ?
                ORDER BY p.name
            """, (user_id,))
            return [row[0] for row in cursor.fetchall()]

    def assign_user_to_group(self, user_id: str, group_name: str, assigned_by: Optional[str] = None) -> bool:
        """Assign a user to a group.

        Returns:
            True if successful, False if group doesn't exist

        Raises:
            ValueError: If trying to assign a guest user to admin group
            ValueError: If user_id doesn't exist in database (for non-guest users)
        """
        if group_name == 'admin' and user_id.startswith('guest_'):
            initial_admin = os.environ.get('INITIAL_ADMIN_EMAIL', '')
            if user_id != initial_admin:
                raise ValueError("Guest users cannot be assigned to the admin group")

        with self._get_connection() as conn:
            if not user_id.startswith('guest_'):
                cursor = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,))
                if not cursor.fetchone():
                    raise ValueError(f"User {user_id} does not exist")

            cursor = conn.execute("SELECT id FROM groups WHERE name = ?", (group_name,))
            row = cursor.fetchone()
            if not row:
                return False

            group_id = row[0]

            conn.execute("""
                INSERT OR IGNORE INTO user_groups (user_id, group_id, assigned_by)
                VALUES (?, ?, ?)
            """, (user_id, group_id, assigned_by))

            return True

    def remove_user_from_group(self, user_id: str, group_name: str) -> bool:
        """Remove a user from a group."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                DELETE FROM user_groups
                WHERE user_id = ? AND group_id = (SELECT id FROM groups WHERE name = ?)
            """, (user_id, group_name))
            return cursor.rowcount > 0

    def count_users_in_group(self, group_name: str) -> int:
        """Count the number of users in a group."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM user_groups ug
                JOIN groups g ON ug.group_id = g.id
                WHERE g.name = ?
            """, (group_name,))
            return cursor.fetchone()[0]

    def get_all_groups(self) -> List[Dict[str, Any]]:
        """Get all available groups."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, name, description, is_system, created_at
                FROM groups
                ORDER BY is_system DESC, name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """Get statistics for a user from api_usage and games tables."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT COALESCE(SUM(estimated_cost), 0) as total_cost
                FROM api_usage
                WHERE owner_id = ?
            """, (user_id,))
            total_cost = cursor.fetchone()[0] or 0

            cursor = conn.execute("""
                SELECT COUNT(*) as hands_played
                FROM api_usage
                WHERE owner_id = ? AND call_type = 'player_decision'
            """, (user_id,))
            hands_played = cursor.fetchone()[0] or 0

            cursor = conn.execute("""
                SELECT COUNT(DISTINCT game_id) as games_completed
                FROM games
                WHERE owner_id = ?
            """, (user_id,))
            games_completed = cursor.fetchone()[0] or 0

            cursor = conn.execute("""
                SELECT MAX(created_at) as last_active
                FROM api_usage
                WHERE owner_id = ?
            """, (user_id,))
            last_active_row = cursor.fetchone()
            last_active = last_active_row[0] if last_active_row else None

            return {
                'total_cost': round(total_cost, 4),
                'hands_played': hands_played,
                'games_completed': games_completed,
                'last_active': last_active
            }

    def initialize_admin_from_env(self) -> Optional[str]:
        """Assign admin group to user with INITIAL_ADMIN_EMAIL.

        Called on startup to ensure the initial admin is configured.
        Supports both email addresses (for Google users) and guest IDs.

        Returns:
            User ID of the admin if found and assigned, None otherwise
        """
        admin_id = os.environ.get('INITIAL_ADMIN_EMAIL')
        if not admin_id:
            return None

        if admin_id.startswith('guest_'):
            user_id = admin_id
            logger.info(f"INITIAL_ADMIN_EMAIL configured for guest user: {user_id}")
        else:
            user = self.get_user_by_email(admin_id)
            if not user:
                logger.info(f"Initial admin email {admin_id} not found in users table yet")
                return None
            user_id = user['id']

        groups = self.get_user_groups(user_id)
        if 'admin' in groups:
            logger.debug(f"User {user_id} already has admin group")
            return user_id

        if self.assign_user_to_group(user_id, 'admin', assigned_by='system'):
            logger.info(f"Assigned admin group to {user_id}")
            return user_id

        return None
