"""Skill definitions, gates, and core data structures for coach progression.

Defines the skill tree (Gate 1 preflop skills, Gate 2 post-flop skills),
skill/gate definitions, and the build_poker_context() helper.

Shared data structures (enums, PlayerSkillState, etc.) live in
coach_models.py to avoid circular imports with persistence.py.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

# Re-export shared data structures for backward compatibility
from .coach_models import (  # noqa: F401
    CoachingDecision, CoachingMode, EvidenceRules,
    GateProgress, PlayerSkillState, SkillState,
)


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
    description='Bet or raise for value when you have top pair or better.',
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

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

ALL_SKILLS: Dict[str, SkillDefinition] = {
    s.skill_id: s for s in [
        SKILL_FOLD_TRASH, SKILL_POSITION_MATTERS, SKILL_RAISE_OR_FOLD,
        SKILL_FLOP_CONNECTION, SKILL_BET_WHEN_STRONG, SKILL_CHECKING_IS_ALLOWED,
    ]
}

ALL_GATES: Dict[int, GateDefinition] = {
    GATE_1.gate_number: GATE_1,
    GATE_2.gate_number: GATE_2,
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

    # Post-flop hand strength (from HandEvaluator via coaching_data)
    # hand_rank: 1=Royal Flush, 2=Straight Flush, ... 8=One Pair, 9=High Card
    hand_rank = coaching_data.get('hand_rank')
    hand_name = coaching_data.get('hand_strength', '')

    # Derived booleans for Gate 2 evaluators
    is_strong_hand = hand_rank is not None and hand_rank <= 4  # Two pair or better
    has_pair = hand_rank is not None and hand_rank <= 8        # Any pair or better
    has_draw = (coaching_data.get('outs') or 0) >= 4           # 4+ outs = meaningful draw
    is_air = hand_rank is not None and hand_rank >= 9 and not has_draw  # High card, no draw
    can_check = cost_to_call == 0

    # Situation tags
    tag_conditions = [
        ('trash_hand', is_trash),
        ('premium_hand', is_premium),
        ('early_position', is_early),
        ('late_position', is_late),
        ('strong_hand', is_strong_hand),
        ('air', is_air),
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
        'hand_rank': hand_rank,
        'is_strong_hand': is_strong_hand,
        'has_pair': has_pair,
        'has_draw': has_draw,
        'is_air': is_air,
        'can_check': can_check,
        'tags': tags,
    }
