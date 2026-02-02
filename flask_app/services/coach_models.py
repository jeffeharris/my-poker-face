"""Shared data structures for the coach progression system.

This module is deliberately dependency-free (no imports from other
flask_app.services modules) so that it can be imported by
poker/persistence.py without creating circular dependencies.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SkillState(str, Enum):
    """Progression state for an individual skill."""
    INTRODUCED = 'introduced'
    PRACTICING = 'practicing'
    RELIABLE = 'reliable'
    AUTOMATIC = 'automatic'


SKILL_STATE_ORDER: Dict['SkillState', int] = {
    SkillState.INTRODUCED: 0,
    SkillState.PRACTICING: 1,
    SkillState.RELIABLE: 2,
    SkillState.AUTOMATIC: 3,
}


class CoachingMode(str, Enum):
    """Coaching delivery mode based on skill state and context."""
    LEARN = 'learn'       # Teach concepts, explain reasoning
    COMPETE = 'compete'   # Brief reminders, focus on execution
    SILENT = 'silent'     # No coaching (skill is automatic)


# ---------------------------------------------------------------------------
# Evidence / thresholds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceRules:
    """Thresholds governing skill state transitions."""
    min_opportunities: int          # Min opps before practicing -> reliable
    window_size: int = 20           # Rolling window size
    advancement_threshold: float = 0.75   # Window accuracy to advance
    regression_threshold: float = 0.60    # Window accuracy to regress
    automatic_min_opps: int = 30          # Min opps for reliable -> automatic
    automatic_threshold: float = 0.85     # Window accuracy for automatic
    automatic_regression: float = 0.70    # Window accuracy to regress from automatic
    introduced_min_opps: int = 3          # Min opps before introduced -> practicing


# ---------------------------------------------------------------------------
# Player state dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlayerSkillState:
    """Immutable tracking of a player's progress on a single skill."""
    skill_id: str
    state: SkillState = SkillState.INTRODUCED
    total_opportunities: int = 0
    total_correct: int = 0
    window_opportunities: int = 0
    window_correct: int = 0
    streak_correct: int = 0
    streak_incorrect: int = 0
    last_evaluated_at: Optional[str] = None
    first_seen_at: Optional[str] = None

    @property
    def window_accuracy(self) -> float:
        if self.window_opportunities == 0:
            return 0.0
        return self.window_correct / self.window_opportunities

    @property
    def total_accuracy(self) -> float:
        if self.total_opportunities == 0:
            return 0.0
        return self.total_correct / self.total_opportunities


@dataclass(frozen=True)
class GateProgress:
    """Tracks whether a gate has been unlocked for a player."""
    gate_number: int
    unlocked: bool = False
    unlocked_at: Optional[str] = None


@dataclass(frozen=True)
class CoachingDecision:
    """Outcome of the coaching engine deciding what to coach on."""
    mode: CoachingMode
    primary_skill_id: Optional[str] = None
    relevant_skill_ids: tuple = ()
    coaching_prompt: str = ''
    situation_tags: tuple = ()
