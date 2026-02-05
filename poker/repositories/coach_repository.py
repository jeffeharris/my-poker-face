"""Coach progression repository — skill states, gate progress, and coach profiles."""
import json
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
        window_decisions_json = json.dumps(list(skill_state.window_decisions))
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO player_skill_progress
                    (user_id, skill_id, state, total_opportunities, total_correct,
                     window_opportunities, window_correct, window_decisions,
                     streak_correct, streak_incorrect,
                     last_evaluated_at, first_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, skill_id) DO UPDATE SET
                    state = excluded.state,
                    total_opportunities = excluded.total_opportunities,
                    total_correct = excluded.total_correct,
                    window_opportunities = excluded.window_opportunities,
                    window_correct = excluded.window_correct,
                    window_decisions = excluded.window_decisions,
                    streak_correct = excluded.streak_correct,
                    streak_incorrect = excluded.streak_incorrect,
                    last_evaluated_at = excluded.last_evaluated_at,
                    first_seen_at = excluded.first_seen_at
            """, (
                user_id, skill_state.skill_id, skill_state.state.value
                if hasattr(skill_state.state, 'value') else skill_state.state,
                skill_state.total_opportunities, skill_state.total_correct,
                skill_state.window_opportunities, skill_state.window_correct,
                window_decisions_json,
                skill_state.streak_correct, skill_state.streak_incorrect,
                skill_state.last_evaluated_at, skill_state.first_seen_at,
            ))

    def load_skill_state(self, user_id: str, skill_id: str):
        """Load a single PlayerSkillState. Returns None if not found."""
        from poker.coach_models import PlayerSkillState, SkillState
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT skill_id, state, total_opportunities, total_correct, "
                "window_opportunities, window_correct, window_decisions, "
                "streak_correct, streak_incorrect, "
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
                window_decisions=tuple(json.loads(row[6] or '[]')),
                streak_correct=row[7],
                streak_incorrect=row[8],
                last_evaluated_at=row[9],
                first_seen_at=row[10],
            )

    def load_all_skill_states(self, user_id: str):
        """Load all PlayerSkillState records for a user. Returns dict keyed by skill_id."""
        from poker.coach_models import PlayerSkillState, SkillState
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT skill_id, state, total_opportunities, total_correct, "
                "window_opportunities, window_correct, window_decisions, "
                "streak_correct, streak_incorrect, "
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
                    window_decisions=tuple(json.loads(row[6] or '[]')),
                    streak_correct=row[7],
                    streak_incorrect=row[8],
                    last_evaluated_at=row[9],
                    first_seen_at=row[10],
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

    def load_gate_progress(self, user_id: str):
        """Load all gate progress for a user. Returns dict keyed by gate_number."""
        from poker.coach_models import GateProgress
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
                           effective_level: str = 'beginner',
                           onboarding_completed_at: str = None,
                           range_targets: Dict = None) -> None:
        """Persist the player's coaching profile.

        Args:
            user_id: Player's user ID
            self_reported_level: Player's self-assessment (beginner/intermediate/experienced)
            effective_level: System-adjusted level based on observed play
            onboarding_completed_at: ISO timestamp when onboarding finished
            range_targets: Dict of position -> percentage for personal range targets
        """
        now = datetime.now().isoformat()
        range_targets_json = json.dumps(range_targets) if range_targets else None
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO player_coach_profile
                    (user_id, self_reported_level, effective_level, created_at, updated_at,
                     onboarding_completed_at, range_targets)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    self_reported_level = excluded.self_reported_level,
                    effective_level = excluded.effective_level,
                    updated_at = excluded.updated_at,
                    onboarding_completed_at = COALESCE(excluded.onboarding_completed_at,
                                                       player_coach_profile.onboarding_completed_at),
                    range_targets = COALESCE(excluded.range_targets,
                                             player_coach_profile.range_targets)
            """, (user_id, self_reported_level, effective_level, now, now,
                  onboarding_completed_at, range_targets_json))

    def load_coach_profile(self, user_id: str) -> Optional[Dict]:
        """Load coaching profile. Returns dict or None."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id, self_reported_level, effective_level, created_at, updated_at, "
                "onboarding_completed_at, range_targets "
                "FROM player_coach_profile WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            # Parse range_targets JSON if present
            range_targets = None
            if row[6]:
                try:
                    range_targets = json.loads(row[6])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Invalid range_targets JSON for user {user_id}")

            return {
                'user_id': row[0],
                'self_reported_level': row[1],
                'effective_level': row[2],
                'created_at': row[3],
                'updated_at': row[4],
                'onboarding_completed_at': row[5],
                'range_targets': range_targets,
            }

    def save_range_targets(self, user_id: str, range_targets: Dict[str, float]) -> None:
        """Update only the range_targets for a user.

        Useful when expanding ranges on gate unlock without modifying other fields.
        """
        now = datetime.now().isoformat()
        range_targets_json = json.dumps(range_targets)
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE player_coach_profile
                SET range_targets = ?, updated_at = ?
                WHERE user_id = ?
            """, (range_targets_json, now, user_id))

    def load_range_targets(self, user_id: str) -> Optional[Dict[str, float]]:
        """Load just the range_targets for a user. Returns None if not set."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT range_targets FROM player_coach_profile WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return None
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Invalid range_targets JSON for user {user_id}")
                return None

    # --- Metrics queries (admin) ---

    def get_profile_stats(self) -> Dict:
        """Aggregate overview of coaching profiles and gate progress."""
        with self._get_connection() as conn:
            # Total and by-level counts
            total = conn.execute(
                "SELECT COUNT(*) FROM player_coach_profile"
            ).fetchone()[0]

            level_rows = conn.execute(
                "SELECT self_reported_level, COUNT(*) as cnt "
                "FROM player_coach_profile GROUP BY self_reported_level"
            ).fetchall()
            by_level = {row[0]: row[1] for row in level_rows}

            # Active players (skill progress updated in last 7 days)
            active = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM player_skill_progress "
                "WHERE last_evaluated_at >= datetime('now', '-7 days')"
            ).fetchone()[0]

            # Gate funnel
            gate_rows = conn.execute(
                "SELECT gate, COUNT(*) as cnt "
                "FROM player_gate_progress WHERE unlocked = 1 "
                "GROUP BY gate ORDER BY gate"
            ).fetchall()
            gates_unlocked = {str(row[0]): row[1] for row in gate_rows}

            return {
                'total_players': total,
                'active_last_7d': active,
                'by_level': by_level,
                'gates_unlocked': gates_unlocked,
            }

    def get_skill_distribution(self) -> Dict:
        """Per-skill player counts by state and accuracy stats."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT skill_id, state, "
                "COUNT(*) as player_count, "
                "ROUND(AVG(CASE WHEN total_opportunities > 0 "
                "  THEN CAST(total_correct AS REAL) / total_opportunities ELSE 0 END), 3) as avg_accuracy, "
                "ROUND(AVG(total_opportunities), 1) as avg_opportunities "
                "FROM player_skill_progress "
                "GROUP BY skill_id, state "
                "ORDER BY skill_id, state"
            ).fetchall()

            skills = {}
            for row in rows:
                sid = row[0]
                if sid not in skills:
                    skills[sid] = {'states': {}, 'total_players': 0}
                skills[sid]['states'][row[1]] = {
                    'count': row[2],
                    'avg_accuracy': row[3],
                    'avg_opportunities': row[4],
                }
                skills[sid]['total_players'] += row[2]

            return {'skills': skills}

    def get_skill_advancement_stats(self) -> Dict:
        """Advancement timing: avg opportunities to reach each state."""
        with self._get_connection() as conn:
            # For each skill+state, what's the average total_opportunities?
            # This shows how many opportunities it typically takes to reach each state.
            rows = conn.execute(
                "SELECT skill_id, state, "
                "COUNT(*) as player_count, "
                "ROUND(AVG(total_opportunities), 1) as avg_total_opps, "
                "MIN(total_opportunities) as min_opps, "
                "MAX(total_opportunities) as max_opps "
                "FROM player_skill_progress "
                "WHERE state IN ('reliable', 'automatic') "
                "GROUP BY skill_id, state "
                "ORDER BY skill_id, state"
            ).fetchall()

            advancement = []
            for row in rows:
                advancement.append({
                    'skill_id': row[0],
                    'state': row[1],
                    'player_count': row[2],
                    'avg_total_opportunities': row[3],
                    'min_opportunities': row[4],
                    'max_opportunities': row[5],
                })

            # Regression indicator: skills where players are in a lower state
            # despite having many opportunities (potential threshold issue)
            regression_rows = conn.execute(
                "SELECT skill_id, COUNT(*) as player_count, "
                "ROUND(AVG(total_opportunities), 1) as avg_opps "
                "FROM player_skill_progress "
                "WHERE state IN ('introduced', 'practicing') "
                "AND total_opportunities > 20 "
                "GROUP BY skill_id "
                "ORDER BY avg_opps DESC"
            ).fetchall()

            stuck_players = [
                {
                    'skill_id': row[0],
                    'player_count': row[1],
                    'avg_opportunities': row[2],
                }
                for row in regression_rows
            ]

            return {
                'advancement': advancement,
                'stuck_players': stuck_players,
            }
