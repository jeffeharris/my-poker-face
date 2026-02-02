"""Skill definitions, gates, and core data structures for coach progression.

Defines the skill tree (Gate 1 preflop skills, Gate 2 post-flop skills),
and skill/gate definitions.

Shared data structures (enums, PlayerSkillState, etc.) live in
poker/coach_models.py to avoid circular imports with persistence.py.
The build_poker_context() helper lives in context_builder.py.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

from poker.coach_models import EvidenceRules


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
# Gate 2 skill definitions (post-flop basics)
# ---------------------------------------------------------------------------

SKILL_FLOP_CONNECTION = SkillDefinition(
    skill_id='flop_connection',
    name='Flop Connection',
    description='Fold when the flop misses your hand (no pair, no draw).',
    gate=2,
    evidence_rules=EvidenceRules(
        min_opportunities=8,
        window_size=30,
        advancement_threshold=0.70,
        regression_threshold=0.55,
    ),
    phases=frozenset({'FLOP'}),
    tags=frozenset({'hand_reading', 'postflop'}),
)

SKILL_BET_WHEN_STRONG = SkillDefinition(
    skill_id='bet_when_strong',
    name='Bet When Strong',
    description='Bet or raise for value when you have two pair or better.',
    gate=2,
    evidence_rules=EvidenceRules(
        min_opportunities=8,
        window_size=30,
        advancement_threshold=0.70,
        regression_threshold=0.55,
    ),
    phases=frozenset({'FLOP', 'TURN', 'RIVER'}),
    tags=frozenset({'value_betting', 'postflop'}),
)

SKILL_CHECKING_IS_ALLOWED = SkillDefinition(
    skill_id='checking_is_allowed',
    name='Checking Is Allowed',
    description='Check or fold with weak hands instead of bluffing into strength.',
    gate=2,
    evidence_rules=EvidenceRules(
        min_opportunities=8,
        window_size=30,
        advancement_threshold=0.65,
        regression_threshold=0.50,
    ),
    phases=frozenset({'FLOP', 'TURN', 'RIVER'}),
    tags=frozenset({'pot_control', 'postflop'}),
)

# ---------------------------------------------------------------------------
# Gate 3 skill definitions (pressure recognition)
# ---------------------------------------------------------------------------

SKILL_DRAWS_NEED_PRICE = SkillDefinition(
    skill_id='draws_need_price',
    name='Draws Need Price',
    description='Only call with a draw when pot odds justify it.',
    gate=3,
    evidence_rules=EvidenceRules(
        min_opportunities=6,
        window_size=30,
        advancement_threshold=0.70,
        regression_threshold=0.55,
    ),
    phases=frozenset({'FLOP', 'TURN'}),
    tags=frozenset({'pot_odds', 'draws', 'postflop'}),
)

SKILL_RESPECT_BIG_BETS = SkillDefinition(
    skill_id='respect_big_bets',
    name='Respect Big Bets',
    description='Fold medium hands facing large bets (>=50% pot) on turn or river.',
    gate=3,
    evidence_rules=EvidenceRules(
        min_opportunities=6,
        window_size=30,
        advancement_threshold=0.65,
        regression_threshold=0.50,
    ),
    phases=frozenset({'TURN', 'RIVER'}),
    tags=frozenset({'bet_reading', 'postflop'}),
)

SKILL_HAVE_A_PLAN = SkillDefinition(
    skill_id='have_a_plan',
    name='Have a Plan for the Hand',
    description="Don't bet the flop then check-fold the turn without reason.",
    gate=3,
    evidence_rules=EvidenceRules(
        min_opportunities=6,
        window_size=30,
        advancement_threshold=0.75,
        regression_threshold=0.60,
    ),
    phases=frozenset({'TURN'}),
    tags=frozenset({'multi_street', 'planning', 'postflop'}),
)

# ---------------------------------------------------------------------------
# Gate 4 skill definitions (multi-street thinking)
# ---------------------------------------------------------------------------

SKILL_DONT_PAY_DOUBLE_BARRELS = SkillDefinition(
    skill_id='dont_pay_double_barrels',
    name="Don't Pay Off Double Barrels",
    description='Fold marginal hands when opponents bet multiple streets.',
    gate=4,
    evidence_rules=EvidenceRules(
        min_opportunities=5,
        window_size=30,
        advancement_threshold=0.60,
        regression_threshold=0.45,
    ),
    phases=frozenset({'TURN', 'RIVER'}),
    tags=frozenset({'multi_street', 'bet_reading', 'postflop'}),
)

SKILL_SIZE_BETS_WITH_PURPOSE = SkillDefinition(
    skill_id='size_bets_with_purpose',
    name='Size Your Bets With Purpose',
    description='Size bets proportional to the pot — not too small, not too big.',
    gate=4,
    evidence_rules=EvidenceRules(
        min_opportunities=12,
        window_size=30,
        advancement_threshold=0.65,
        regression_threshold=0.50,
    ),
    phases=frozenset({'FLOP', 'TURN', 'RIVER'}),
    tags=frozenset({'bet_sizing', 'postflop'}),
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

GATE_2 = GateDefinition(
    gate_number=2,
    name='Post-Flop Basics',
    description='Fold when you miss, bet when you hit, check when uncertain.',
    skill_ids=('flop_connection', 'bet_when_strong', 'checking_is_allowed'),
    required_reliable=2,
)

GATE_3 = GateDefinition(
    gate_number=3,
    name='Pressure Recognition',
    description='Understand pot odds on draws, respect aggression, follow through on plans.',
    skill_ids=('draws_need_price', 'respect_big_bets', 'have_a_plan'),
    required_reliable=2,
)

GATE_4 = GateDefinition(
    gate_number=4,
    name='Multi-Street Thinking',
    description='Recognize multi-street aggression and size your bets with purpose.',
    skill_ids=('dont_pay_double_barrels', 'size_bets_with_purpose'),
    required_reliable=2,
)

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

ALL_SKILLS: Dict[str, SkillDefinition] = {
    s.skill_id: s for s in [
        SKILL_FOLD_TRASH, SKILL_POSITION_MATTERS, SKILL_RAISE_OR_FOLD,
        SKILL_FLOP_CONNECTION, SKILL_BET_WHEN_STRONG, SKILL_CHECKING_IS_ALLOWED,
        SKILL_DRAWS_NEED_PRICE, SKILL_RESPECT_BIG_BETS, SKILL_HAVE_A_PLAN,
        SKILL_DONT_PAY_DOUBLE_BARRELS, SKILL_SIZE_BETS_WITH_PURPOSE,
    ]
}

ALL_GATES: Dict[int, GateDefinition] = {
    GATE_1.gate_number: GATE_1,
    GATE_2.gate_number: GATE_2,
    GATE_3.gate_number: GATE_3,
    GATE_4.gate_number: GATE_4,
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


