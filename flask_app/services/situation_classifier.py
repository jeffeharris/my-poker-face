"""Rule-based situation classifier for coach progression.

Examines coaching data and determines which skills are relevant
to the current game situation, selecting a primary skill to coach on.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from poker.controllers import (
    PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS,
    _get_canonical_hand,
)

from .skill_definitions import (
    ALL_SKILLS, PlayerSkillState, SkillState, get_skills_for_gate,
)

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

        # Determine position category
        early_positions = {'under the gun', 'utg', 'utg+1', 'early position'}
        late_positions = {'button', 'cutoff', 'btn', 'co', 'dealer'}
        is_early = any(ep in position for ep in early_positions)
        is_late = any(lp in position for lp in late_positions)

        # Hand tier
        is_trash = canonical and canonical not in TOP_35_HANDS
        is_premium = canonical and canonical in PREMIUM_HANDS
        is_playable = canonical and canonical in TOP_35_HANDS

        # Build tags
        tags = []
        if is_trash:
            tags.append('trash_hand')
        if is_premium:
            tags.append('premium_hand')
        if is_early:
            tags.append('early_position')
        if is_late:
            tags.append('late_position')

        return {
            'phase': phase,
            'canonical': canonical,
            'position': position,
            'is_early': is_early,
            'is_late': is_late,
            'is_trash': is_trash,
            'is_premium': is_premium,
            'is_playable': is_playable,
            'cost_to_call': cost_to_call,
            'pot_total': pot_total,
            'tags': tags,
        }

    def _check_skill_trigger(self, skill_id: str, ctx: Dict) -> bool:
        """Check if a specific skill's trigger conditions are met."""
        checkers = {
            'fold_trash_hands': self._check_fold_trash_trigger,
            'position_matters': self._check_position_matters_trigger,
            'raise_or_fold': self._check_raise_or_fold_trigger,
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

        An 'unopened' pot means no raise yet — cost_to_call is 0 or
        just the big blind (the minimum forced bet).
        """
        if ctx['phase'] != 'PRE_FLOP':
            return False
        # Unopened pot: cost_to_call == 0 means no one has raised yet
        # (player is in the blinds or it limped around)
        # We trigger when cost_to_call <= big blind essentially
        # but we don't have BB here, so use cost_to_call == 0 as proxy
        return ctx['cost_to_call'] == 0

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
