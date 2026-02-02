"""Rule-based situation classifier for coach progression.

Examines coaching data and determines which skills are relevant
to the current game situation, selecting a primary skill to coach on.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from .coach_models import PlayerSkillState, SkillState
from .coach_models import SKILL_STATE_ORDER
from .skill_definitions import build_poker_context, get_skills_for_gate

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
        ctx = build_poker_context(coaching_data)
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

    def _check_skill_trigger(self, skill_id: str, ctx: Dict) -> bool:
        """Check if a specific skill's trigger conditions are met."""
        checkers = {
            'fold_trash_hands': self._check_fold_trash_trigger,
            'position_matters': self._check_position_matters_trigger,
            'raise_or_fold': self._check_raise_or_fold_trigger,
            'flop_connection': self._check_flop_connection_trigger,
            'bet_when_strong': self._check_bet_when_strong_trigger,
            'checking_is_allowed': self._check_checking_is_allowed_trigger,
            # Gate 3
            'draws_need_price': self._check_draws_need_price_trigger,
            'respect_big_bets': self._check_respect_big_bets_trigger,
            'have_a_plan': self._check_have_a_plan_trigger,
            # Gate 4
            'dont_pay_double_barrels': self._check_dont_pay_double_barrels_trigger,
            'size_bets_with_purpose': self._check_size_bets_with_purpose_trigger,
        }
        checker = checkers.get(skill_id)
        if not checker:
            logger.warning("No trigger checker for skill %s", skill_id)
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

    # ---- Gate 3 triggers (pressure recognition) ----

    def _check_draws_need_price_trigger(self, ctx: Dict) -> bool:
        """Trigger when facing a bet with a draw."""
        return (ctx['phase'] in ('FLOP', 'TURN')
                and ctx.get('has_draw', False)
                and ctx['cost_to_call'] > 0)

    def _check_respect_big_bets_trigger(self, ctx: Dict) -> bool:
        """Trigger when facing a big bet (>=50% pot) on turn/river with medium hand."""
        if ctx['phase'] not in ('TURN', 'RIVER'):
            return False
        return ctx.get('is_big_bet', False) and ctx.get('is_marginal_hand', False)

    def _check_have_a_plan_trigger(self, ctx: Dict) -> bool:
        """Trigger on turn when player bet the flop."""
        return (ctx['phase'] == 'TURN'
                and ctx.get('player_bet_flop', False))

    # ---- Gate 4 triggers (multi-street thinking) ----

    def _check_dont_pay_double_barrels_trigger(self, ctx: Dict) -> bool:
        """Trigger when opponent has bet both flop and turn, player has marginal hand."""
        if ctx['phase'] not in ('TURN', 'RIVER'):
            return False
        is_double_barrel = ctx.get('opponent_double_barrel', False)
        is_marginal = ctx.get('has_pair', False) and not ctx.get('is_strong_hand', False)
        return is_double_barrel and is_marginal and ctx['cost_to_call'] > 0

    def _check_size_bets_with_purpose_trigger(self, ctx: Dict) -> bool:
        """Trigger on any post-flop street. The evaluator filters to actual bets/raises."""
        return ctx['phase'] in ('FLOP', 'TURN', 'RIVER')

    def _select_primary(
        self,
        relevant: List[str],
        skill_states: Dict[str, PlayerSkillState],
    ) -> Optional[str]:
        """Select the primary skill to coach on (least-progressed wins)."""
        if not relevant:
            return None

        def sort_key(skill_id: str):
            ss = skill_states.get(skill_id)
            if not ss:
                return (0, 0)  # Not yet seen = highest priority
            return (SKILL_STATE_ORDER.get(ss.state, 0), ss.total_opportunities)

        return min(relevant, key=sort_key)
