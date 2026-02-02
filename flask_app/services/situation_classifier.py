"""Rule-based situation classifier for coach progression.

Examines coaching data and determines which skills are relevant
to the current game situation, selecting a primary skill to coach on.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from .coach_models import PlayerSkillState, SkillState
from .skill_definitions import ALL_SKILLS, build_poker_context, get_skills_for_gate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SituationClassification:
    """Result of classifying a game situation against the skill tree."""
    relevant_skills: tuple          # Tuple of skill_id strings
    primary_skill: Optional[str]    # The skill to focus coaching on (or None)
    situation_tags: tuple           # Descriptive tags (e.g. 'trash_hand', 'early_position')
    confidence: float = 1.0         # How confident the classification is


class SituationClassifier:
    """Classify game situations to determine which skills are relevant."""

    def classify(
        self,
        coaching_data: Dict,
        unlocked_gates: List[int],
        skill_states: Dict[str, PlayerSkillState],
    ) -> SituationClassification:
        """Classify the current situation against unlocked skills.

        Args:
            coaching_data: Dict from compute_coaching_data() with phase, position,
                           hand_strength, pot_total, cost_to_call, etc.
            unlocked_gates: List of gate numbers the player has unlocked.
            skill_states: Dict of skill_id -> PlayerSkillState.

        Returns:
            SituationClassification with relevant skills and primary skill.
        """
        ctx = self._build_trigger_context(coaching_data)
        if not ctx:
            return SituationClassification(
                relevant_skills=(), primary_skill=None, situation_tags=()
            )

        # Collect relevant skills from unlocked gates
        relevant = []
        tags = list(ctx.get('tags', []))

        # Always check gate 1 (auto-unlocked for all players)
        gates_to_check = set(unlocked_gates) | {1}

        for gate_num in sorted(gates_to_check):
            for skill_def in get_skills_for_gate(gate_num):
                # Only consider skills matching the current phase
                if ctx['phase'] not in skill_def.phases:
                    continue

                # Check trigger for this specific skill
                triggered = self._check_skill_trigger(skill_def.skill_id, ctx)
                if triggered:
                    relevant.append(skill_def.skill_id)

        primary = self._select_primary(relevant, skill_states)

        return SituationClassification(
            relevant_skills=tuple(relevant),
            primary_skill=primary,
            situation_tags=tuple(tags),
        )

    def _build_trigger_context(self, coaching_data: Dict) -> Optional[Dict]:
        """Extract context needed for trigger evaluation."""
        return build_poker_context(coaching_data)

    def _check_skill_trigger(self, skill_id: str, ctx: Dict) -> bool:
        """Check if a specific skill's trigger conditions are met."""
        checkers = {
            'fold_trash_hands': self._check_fold_trash_trigger,
            'position_matters': self._check_position_matters_trigger,
            'raise_or_fold': self._check_raise_or_fold_trigger,
            'flop_connection': self._check_flop_connection_trigger,
            'bet_when_strong': self._check_bet_when_strong_trigger,
            'checking_is_allowed': self._check_checking_is_allowed_trigger,
        }
        checker = checkers.get(skill_id)
        if not checker:
            return False
        return checker(ctx)

    def _check_fold_trash_trigger(self, ctx: Dict) -> bool:
        """Trigger when player has a hand NOT in top 35% during preflop."""
        return ctx['phase'] == 'PRE_FLOP' and ctx['is_trash']

    def _check_position_matters_trigger(self, ctx: Dict) -> bool:
        """Trigger on all preflop situations (position always matters)."""
        return ctx['phase'] == 'PRE_FLOP' and ctx['canonical'] != ''

    def _check_raise_or_fold_trigger(self, ctx: Dict) -> bool:
        """Trigger when facing an unopened pot preflop.

        An 'unopened' pot means no raise yet — cost_to_call is at most
        the big blind (the minimum forced bet).
        """
        if ctx['phase'] != 'PRE_FLOP':
            return False
        return ctx['cost_to_call'] <= ctx.get('big_blind', 0)

    # ---- Gate 2 triggers (post-flop) ----

    def _check_flop_connection_trigger(self, ctx: Dict) -> bool:
        """Trigger on flop when player has air (no pair, no draw)."""
        return ctx['phase'] == 'FLOP' and ctx.get('is_air', False)

    def _check_bet_when_strong_trigger(self, ctx: Dict) -> bool:
        """Trigger on any post-flop street when player has a strong hand."""
        return (ctx['phase'] in ('FLOP', 'TURN', 'RIVER')
                and ctx.get('is_strong_hand', False))

    def _check_checking_is_allowed_trigger(self, ctx: Dict) -> bool:
        """Trigger when player has a weak hand and can check."""
        return (ctx['phase'] in ('FLOP', 'TURN', 'RIVER')
                and not ctx.get('has_pair', False)
                and ctx.get('can_check', False))

    def _select_primary(
        self,
        relevant: List[str],
        skill_states: Dict[str, PlayerSkillState],
    ) -> Optional[str]:
        """Select the primary skill to coach on (least-progressed wins)."""
        if not relevant:
            return None

        state_order = {
            SkillState.INTRODUCED: 0,
            SkillState.PRACTICING: 1,
            SkillState.RELIABLE: 2,
            SkillState.AUTOMATIC: 3,
        }

        def sort_key(skill_id: str):
            ss = skill_states.get(skill_id)
            if not ss:
                return (0, 0)  # Not yet seen = highest priority
            return (state_order.get(ss.state, 0), ss.total_opportunities)

        return min(relevant, key=sort_key)
