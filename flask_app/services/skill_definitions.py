"""Skill definitions, gates, and core data structures for coach progression.

Defines the skill tree (Gate 1 preflop skills), state machine enums,
and frozen dataclasses used throughout the progression system.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SkillState(str, Enum):
    """Progression state for an individual skill."""
    INTRODUCED = 'introduced'
    PRACTICING = 'practicing'
    RELIABLE = 'reliable'
    AUTOMATIC = 'automatic'


class CoachingMode(str, Enum):
    """Coaching delivery mode based on skill state and context."""
    LEARN = 'learn'       # Teach concepts, explain reasoning
    COMPETE = 'compete'   # Brief reminders, focus on execution
    REVIEW = 'review'     # Post-hand analysis
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
# Skill definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillDefinition:
    """A skill in the progression tree."""
    skill_id: str
    name: str
    description: str
    gate: int
    evidence_rules: EvidenceRules
    phases: FrozenSet[str]          # Game phases where this skill applies
    tags: FrozenSet[str] = frozenset()


# ---------------------------------------------------------------------------
# Gate definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GateDefinition:
    """A progression gate containing a set of skills."""
    gate_number: int
    name: str
    description: str
    skill_ids: Tuple[str, ...]
    required_reliable: int   # How many skills must be 'reliable' to unlock next gate


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


# ---------------------------------------------------------------------------
# Gate 1 skill definitions (preflop fundamentals)
# ---------------------------------------------------------------------------

SKILL_FOLD_TRASH = SkillDefinition(
    skill_id='fold_trash_hands',
    name='Fold Trash Hands',
    description='Fold hands outside the top 35% preflop instead of playing them.',
    gate=1,
    evidence_rules=EvidenceRules(
        min_opportunities=12,
        advancement_threshold=0.75,
        regression_threshold=0.60,
    ),
    phases=frozenset({'PRE_FLOP'}),
    tags=frozenset({'hand_selection', 'preflop'}),
)

SKILL_POSITION_MATTERS = SkillDefinition(
    skill_id='position_matters',
    name='Position Awareness',
    description='Adjust hand selection and aggression based on table position.',
    gate=1,
    evidence_rules=EvidenceRules(
        min_opportunities=20,
        advancement_threshold=0.70,
        regression_threshold=0.55,
    ),
    phases=frozenset({'PRE_FLOP'}),
    tags=frozenset({'position', 'preflop'}),
)

SKILL_RAISE_OR_FOLD = SkillDefinition(
    skill_id='raise_or_fold',
    name='Raise or Fold',
    description='When entering an unopened pot, raise rather than limp/call.',
    gate=1,
    evidence_rules=EvidenceRules(
        min_opportunities=10,
        advancement_threshold=0.80,
        regression_threshold=0.65,
    ),
    phases=frozenset({'PRE_FLOP'}),
    tags=frozenset({'aggression', 'preflop'}),
)

# ---------------------------------------------------------------------------
# Gate definitions
# ---------------------------------------------------------------------------

GATE_1 = GateDefinition(
    gate_number=1,
    name='Preflop Fundamentals',
    description='Core preflop decision-making: hand selection, position, and aggression.',
    skill_ids=('fold_trash_hands', 'position_matters', 'raise_or_fold'),
    required_reliable=2,
)

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

ALL_SKILLS: Dict[str, SkillDefinition] = {
    s.skill_id: s for s in [SKILL_FOLD_TRASH, SKILL_POSITION_MATTERS, SKILL_RAISE_OR_FOLD]
}

ALL_GATES: Dict[int, GateDefinition] = {
    GATE_1.gate_number: GATE_1,
}


def get_skills_for_gate(gate_number: int) -> List[SkillDefinition]:
    """Return all skill definitions belonging to a gate."""
    gate = ALL_GATES.get(gate_number)
    if not gate:
        return []
    return [ALL_SKILLS[sid] for sid in gate.skill_ids if sid in ALL_SKILLS]


def get_skill_by_id(skill_id: str) -> Optional[SkillDefinition]:
    """Look up a skill definition by its ID."""
    return ALL_SKILLS.get(skill_id)


def build_poker_context(coaching_data: Dict) -> Optional[Dict]:
    """Build a standardised context dict from coaching_data.

    Used by both SituationClassifier and SkillEvaluator so the
    hand-parsing / position / tier logic lives in one place.

    Returns None when there is no phase (nothing to evaluate).
    """
    from poker.controllers import (
        PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS,
    )

    phase = coaching_data.get('phase', '')
    if not phase:
        return None

    # Parse canonical hand from hand_strength string
    # Format: "AKs - Suited broadway, Top 10% of starting hands"
    canonical = ''
    hand_strength = coaching_data.get('hand_strength', '')
    if hand_strength and ' - ' in hand_strength:
        canonical = hand_strength.split(' - ')[0].strip()

    position = coaching_data.get('position', '').lower()
    cost_to_call = coaching_data.get('cost_to_call', 0)
    pot_total = coaching_data.get('pot_total', 0)
    big_blind = coaching_data.get('big_blind', 0)

    # Position categories
    early_positions = {'under the gun', 'utg', 'utg+1', 'early position'}
    late_positions = {'button', 'cutoff', 'btn', 'co', 'dealer'}
    is_early = any(ep in position for ep in early_positions)
    is_late = any(lp in position for lp in late_positions)
    is_blind = 'blind' in position

    # Hand tiers
    is_trash = canonical and canonical not in TOP_35_HANDS
    is_premium = canonical and canonical in PREMIUM_HANDS
    is_top10 = canonical and canonical in TOP_10_HANDS
    is_top20 = canonical and canonical in TOP_20_HANDS
    is_playable = canonical and canonical in TOP_35_HANDS

    # Situation tags
    tag_conditions = [
        ('trash_hand', is_trash),
        ('premium_hand', is_premium),
        ('early_position', is_early),
        ('late_position', is_late),
    ]
    tags = tuple(tag for tag, cond in tag_conditions if cond)

    return {
        'phase': phase,
        'canonical': canonical,
        'position': position,
        'is_early': is_early,
        'is_late': is_late,
        'is_blind': is_blind,
        'is_trash': is_trash,
        'is_premium': is_premium,
        'is_top10': is_top10,
        'is_top20': is_top20,
        'is_playable': is_playable,
        'cost_to_call': cost_to_call,
        'pot_total': pot_total,
        'big_blind': big_blind,
        'tags': tags,
    }
