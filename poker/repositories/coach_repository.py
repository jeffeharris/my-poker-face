"""Coach progression repository — skill states, gate progress, and coach profiles."""
import logging
from datetime import datetime
from typing import Optional, Dict

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


class CoachRepository(BaseRepository):
    """Repository for coach progression persistence.

    Handles skill state tracking, gate progress, and player coaching profiles.
    """

    # --- Skill States ---

    def save_skill_state(self, user_id: str, skill_state) -> None:
        """Persist a PlayerSkillState to the database."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO player_skill_progress
                    (user_id, skill_id, state, total_opportunities, total_correct,
                     window_opportunities, window_correct, streak_correct, streak_incorrect,
                     last_evaluated_at, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, skill_id) DO UPDATE SET
                    state = excluded.state,
                    total_opportunities = excluded.total_opportunities,
                    total_correct = excluded.total_correct,
                    window_opportunities = excluded.window_opportunities,
                    window_correct = excluded.window_correct,
                    streak_correct = excluded.streak_correct,
                    streak_incorrect = excluded.streak_incorrect,
                    last_evaluated_at = excluded.last_evaluated_at,
                    first_seen_at = excluded.first_seen_at
            """, (
                user_id, skill_state.skill_id, skill_state.state.value
                if hasattr(skill_state.state, 'value') else skill_state.state,
                skill_state.total_opportunities, skill_state.total_correct,
                skill_state.window_opportunities, skill_state.window_correct,
                skill_state.streak_correct, skill_state.streak_incorrect,
                skill_state.last_evaluated_at, skill_state.first_seen_at,
            ))
            conn.commit()

    def load_skill_state(self, user_id: str, skill_id: str):
        """Load a single PlayerSkillState. Returns None if not found."""
        from flask_app.services.coach_models import PlayerSkillState, SkillState
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT skill_id, state, total_opportunities, total_correct, "
                "window_opportunities, window_correct, streak_correct, streak_incorrect, "
                "last_evaluated_at, first_seen_at "
                "FROM player_skill_progress WHERE user_id = ? AND skill_id = ?",
                (user_id, skill_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return PlayerSkillState(
                skill_id=row[0],
                state=SkillState(row[1]),
                total_opportunities=row[2],
                total_correct=row[3],
                window_opportunities=row[4],
                window_correct=row[5],
                streak_correct=row[6],
                streak_incorrect=row[7],
                last_evaluated_at=row[8],
                first_seen_at=row[9],
            )

    def load_all_skill_states(self, user_id: str):
        """Load all PlayerSkillState records for a user. Returns dict keyed by skill_id."""
        from flask_app.services.coach_models import PlayerSkillState, SkillState
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT skill_id, state, total_opportunities, total_correct, "
                "window_opportunities, window_correct, streak_correct, streak_incorrect, "
                "last_evaluated_at, first_seen_at "
                "FROM player_skill_progress WHERE user_id = ?",
                (user_id,),
            )
            result = {}
            for row in cursor.fetchall():
                result[row[0]] = PlayerSkillState(
                    skill_id=row[0],
                    state=SkillState(row[1]),
                    total_opportunities=row[2],
                    total_correct=row[3],
                    window_opportunities=row[4],
                    window_correct=row[5],
                    streak_correct=row[6],
                    streak_incorrect=row[7],
                    last_evaluated_at=row[8],
                    first_seen_at=row[9],
                )
            return result

    # --- Gate Progress ---

    def save_gate_progress(self, user_id: str, gate_progress) -> None:
        """Persist a GateProgress record."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO player_gate_progress (user_id, gate, unlocked, unlocked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, gate) DO UPDATE SET
                    unlocked = excluded.unlocked,
                    unlocked_at = excluded.unlocked_at
            """, (
                user_id, gate_progress.gate_number,
                gate_progress.unlocked, gate_progress.unlocked_at,
            ))
            conn.commit()

    def load_gate_progress(self, user_id: str):
        """Load all gate progress for a user. Returns dict keyed by gate_number."""
        from flask_app.services.coach_models import GateProgress
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT gate, unlocked, unlocked_at FROM player_gate_progress WHERE user_id = ?",
                (user_id,),
            )
            result = {}
            for row in cursor.fetchall():
                result[row[0]] = GateProgress(
                    gate_number=row[0],
                    unlocked=bool(row[1]),
                    unlocked_at=row[2],
                )
            return result

    # --- Coach Profile ---

    def save_coach_profile(self, user_id: str, self_reported_level: str = None,
                           effective_level: str = 'beginner') -> None:
        """Persist the player's coaching profile."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO player_coach_profile
                    (user_id, self_reported_level, effective_level, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    self_reported_level = excluded.self_reported_level,
                    effective_level = excluded.effective_level,
                    updated_at = excluded.updated_at
            """, (user_id, self_reported_level, effective_level, now, now))
            conn.commit()

    def load_coach_profile(self, user_id: str) -> Optional[Dict]:
        """Load coaching profile. Returns dict or None."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id, self_reported_level, effective_level, created_at, updated_at "
                "FROM player_coach_profile WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'user_id': row[0],
                'self_reported_level': row[1],
                'effective_level': row[2],
                'created_at': row[3],
                'updated_at': row[4],
            }
