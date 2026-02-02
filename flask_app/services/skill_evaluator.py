"""Skill evaluator for coach progression.

Evaluates whether a player's action demonstrates correct application
of a specific skill, returning a structured evaluation result.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from poker.controllers import (
    PREMIUM_HANDS, TOP_10_HANDS, TOP_20_HANDS, TOP_35_HANDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillEvaluation:
    """Result of evaluating one action against one skill."""
    skill_id: str
    action_taken: str
    evaluation: str       # 'correct', 'incorrect', 'marginal', 'not_applicable'
    confidence: float     # 0.0 to 1.0
    reasoning: str


class SkillEvaluator:
    """Evaluate player actions against specific skill criteria."""

    def evaluate(
        self,
        skill_id: str,
        action_taken: str,
        coaching_data: Dict,
        decision_analysis: Optional[Dict] = None,
    ) -> SkillEvaluation:
        """Evaluate an action against a skill.

        Args:
            skill_id: The skill being evaluated.
            action_taken: The action string (e.g. 'fold', 'call', 'raise', 'check').
            coaching_data: Dict from compute_coaching_data().
            decision_analysis: Optional analysis from analyze_player_decision().

        Returns:
            SkillEvaluation with the result.
        """
        ctx = self._build_eval_context(coaching_data)
        action = action_taken.lower()

        evaluators = {
            'fold_trash_hands': self._eval_fold_trash,
            'position_matters': self._eval_position_matters,
            'raise_or_fold': self._eval_raise_or_fold,
        }

        evaluator = evaluators.get(skill_id)
        if not evaluator:
            return SkillEvaluation(
                skill_id=skill_id,
                action_taken=action,
                evaluation='not_applicable',
                confidence=0.0,
                reasoning=f'No evaluator for skill {skill_id}',
            )

        return evaluator(action, ctx)

    def _build_eval_context(self, coaching_data: Dict) -> Dict:
        """Extract evaluation context from coaching data."""
        # Parse canonical hand from hand_strength
        canonical = ''
        hand_strength = coaching_data.get('hand_strength', '')
        if hand_strength and ' - ' in hand_strength:
            canonical = hand_strength.split(' - ')[0].strip()

        position = coaching_data.get('position', '').lower()
        early_positions = {'under the gun', 'utg', 'utg+1', 'early position'}
        late_positions = {'button', 'cutoff', 'btn', 'co', 'dealer'}
        is_early = any(ep in position for ep in early_positions)
        is_late = any(lp in position for lp in late_positions)
        is_blind = 'blind' in position

        return {
            'canonical': canonical,
            'position': position,
            'is_early': is_early,
            'is_late': is_late,
            'is_blind': is_blind,
            'is_trash': canonical and canonical not in TOP_35_HANDS,
            'is_premium': canonical and canonical in PREMIUM_HANDS,
            'is_top10': canonical and canonical in TOP_10_HANDS,
            'is_top20': canonical and canonical in TOP_20_HANDS,
            'is_playable': canonical and canonical in TOP_35_HANDS,
            'cost_to_call': coaching_data.get('cost_to_call', 0),
            'pot_total': coaching_data.get('pot_total', 0),
        }

    def _eval_fold_trash(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate fold_trash_hands: trash hand + fold = correct."""
        if not ctx['is_trash']:
            return SkillEvaluation(
                skill_id='fold_trash_hands',
                action_taken=action,
                evaluation='not_applicable',
                confidence=1.0,
                reasoning='Hand is in top 35%, not a trash hand.',
            )

        if action == 'fold':
            return SkillEvaluation(
                skill_id='fold_trash_hands',
                action_taken=action,
                evaluation='correct',
                confidence=1.0,
                reasoning='Correctly folded a trash hand.',
            )

        if action == 'check':
            return SkillEvaluation(
                skill_id='fold_trash_hands',
                action_taken=action,
                evaluation='marginal',
                confidence=0.7,
                reasoning='Checking with a trash hand is acceptable when free, but folding is preferred.',
            )

        return SkillEvaluation(
            skill_id='fold_trash_hands',
            action_taken=action,
            evaluation='incorrect',
            confidence=0.9,
            reasoning=f'Played a trash hand ({action}) instead of folding.',
        )

    def _eval_position_matters(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate position_matters: position-appropriate hand selection."""
        canonical = ctx['canonical']
        if not canonical:
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='not_applicable',
                confidence=0.0,
                reasoning='Could not determine hand.',
            )

        # Early position: should only play premium/top-10 hands
        if ctx['is_early']:
            if action == 'fold':
                if ctx['is_top10']:
                    return SkillEvaluation(
                        skill_id='position_matters',
                        action_taken=action,
                        evaluation='incorrect',
                        confidence=0.8,
                        reasoning='Folded a strong hand in early position.',
                    )
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.8,
                    reasoning='Correctly tightened range in early position.',
                )
            # Playing (raise/call) in early position
            if ctx['is_top10']:
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.9,
                    reasoning='Played a strong hand from early position.',
                )
            if ctx['is_top20']:
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='marginal',
                    confidence=0.6,
                    reasoning='Borderline hand for early position; tighter is better.',
                )
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='incorrect',
                confidence=0.8,
                reasoning='Played a weak hand from early position.',
            )

        # Late position: can play wider range
        if ctx['is_late']:
            if action == 'fold':
                if ctx['is_playable']:
                    return SkillEvaluation(
                        skill_id='position_matters',
                        action_taken=action,
                        evaluation='incorrect',
                        confidence=0.7,
                        reasoning='Folded a playable hand from late position.',
                    )
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.7,
                    reasoning='Folded trash from late position.',
                )
            # Playing from late position
            if ctx['is_playable']:
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.9,
                    reasoning='Played a reasonable hand from late position.',
                )
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='marginal',
                confidence=0.5,
                reasoning='Played a weak hand from late position; borderline.',
            )

        # Middle position or blinds — moderate evaluation
        if action == 'fold' and not ctx['is_playable']:
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='correct',
                confidence=0.6,
                reasoning='Folded trash from middle/blind position.',
            )

        if action != 'fold' and ctx['is_playable']:
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='correct',
                confidence=0.6,
                reasoning='Played a reasonable hand from middle/blind position.',
            )

        return SkillEvaluation(
            skill_id='position_matters',
            action_taken=action,
            evaluation='marginal',
            confidence=0.5,
            reasoning='Ambiguous position play.',
        )

    def _eval_raise_or_fold(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate raise_or_fold: when entering a pot, raise don't limp."""
        # This skill only applies when the pot is unopened (cost_to_call == 0)
        if ctx['cost_to_call'] > 0:
            return SkillEvaluation(
                skill_id='raise_or_fold',
                action_taken=action,
                evaluation='not_applicable',
                confidence=1.0,
                reasoning='Pot was already opened (facing a raise).',
            )

        if action == 'fold':
            # Folding is always fine for this skill (choosing not to enter)
            return SkillEvaluation(
                skill_id='raise_or_fold',
                action_taken=action,
                evaluation='correct',
                confidence=0.9,
                reasoning='Chose not to enter the pot (fold is acceptable).',
            )

        if action == 'raise' or action.startswith('raise'):
            return SkillEvaluation(
                skill_id='raise_or_fold',
                action_taken=action,
                evaluation='correct',
                confidence=1.0,
                reasoning='Entered the pot with a raise — correct aggression.',
            )

        if action == 'check':
            # Checking from the big blind in an unopened pot is fine
            if ctx['is_blind']:
                return SkillEvaluation(
                    skill_id='raise_or_fold',
                    action_taken=action,
                    evaluation='marginal',
                    confidence=0.5,
                    reasoning='Checked from the blind; a raise would show more initiative.',
                )
            return SkillEvaluation(
                skill_id='raise_or_fold',
                action_taken=action,
                evaluation='marginal',
                confidence=0.6,
                reasoning='Checked instead of raising.',
            )

        # call / limp = incorrect
        return SkillEvaluation(
            skill_id='raise_or_fold',
            action_taken=action,
            evaluation='incorrect',
            confidence=0.9,
            reasoning=f'Limped/called ({action}) instead of raising into an unopened pot.',
        )
