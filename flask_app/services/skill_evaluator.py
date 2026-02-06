"""Skill evaluator for coach progression.

Evaluates whether a player's action demonstrates correct application
of a specific skill, returning a structured evaluation result.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from .context_builder import build_poker_context

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillEvaluation:
    """Result of evaluating one action against one skill."""
    skill_id: str
    action_taken: str
    evaluation: str       # 'correct', 'incorrect', 'marginal', 'not_applicable'
    confidence: float     # 0.0 to 1.0
    reasoning: str
    in_personal_range: bool = False  # True when hand is within player's personal range target


class SkillEvaluator:
    """Evaluate player actions against specific skill criteria."""

    def evaluate(
        self,
        skill_id: str,
        action_taken: str,
        coaching_data: Dict,
        decision_analysis: Optional[Dict] = None,
        range_targets: Optional[Dict[str, float]] = None,
    ) -> SkillEvaluation:
        """Evaluate an action against a skill.

        Args:
            skill_id: The skill being evaluated.
            action_taken: The action string (e.g. 'fold', 'call', 'raise', 'check').
            coaching_data: Dict from compute_coaching_data().
            decision_analysis: Optional analysis from analyze_player_decision().
            range_targets: Optional personal range targets for the player.

        Returns:
            SkillEvaluation with the result.
        """
        ctx = build_poker_context(coaching_data, range_targets=range_targets) or {}
        if not ctx:
            return SkillEvaluation(
                skill_id=skill_id,
                action_taken=action_taken,
                evaluation='not_applicable',
                confidence=0.0,
                reasoning='No game context available for evaluation',
            )
        action = action_taken.lower()

        # Forced all-in: stack <= cost_to_call means no meaningful decision
        if action == 'all_in':
            stack = coaching_data.get('stack', float('inf'))
            cost_to_call = coaching_data.get('cost_to_call', 0)
            if stack <= cost_to_call:
                return SkillEvaluation(
                    skill_id=skill_id,
                    action_taken=action,
                    evaluation='not_applicable',
                    confidence=1.0,
                    reasoning='Forced all-in — no meaningful decision',
                )

        evaluators = {
            'fold_trash_hands': self._eval_fold_trash,
            'position_matters': self._eval_position_matters,
            'raise_or_fold': self._eval_raise_or_fold,
            'flop_connection': self._eval_flop_connection,
            'bet_when_strong': self._eval_bet_when_strong,
            'checking_is_allowed': self._eval_checking_is_allowed,
            # Gate 3
            'draws_need_price': self._eval_draws_need_price,
            'respect_big_bets': self._eval_respect_big_bets,
            'have_a_plan': self._eval_have_a_plan,
            # Gate 4
            'dont_pay_double_barrels': self._eval_dont_pay_double_barrels,
            'size_bets_with_purpose': self._eval_size_bets_with_purpose,
        }

        evaluator = evaluators.get(skill_id)
        if not evaluator:
            logger.warning("No evaluator for skill %s", skill_id)
            return SkillEvaluation(
                skill_id=skill_id,
                action_taken=action,
                evaluation='not_applicable',
                confidence=0.0,
                reasoning=f'No evaluator for skill {skill_id}',
            )

        return evaluator(action, ctx)

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

    @staticmethod
    def _position_reasoning(
        has_personal_range: bool, range_str: str,
        personal_msg: str, fallback_msg: str,
    ) -> str:
        """Build a reasoning string that uses personal range when available."""
        if has_personal_range:
            return personal_msg.format(range_str=range_str)
        return fallback_msg

    def _eval_position_matters(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate position_matters: position-appropriate hand selection.

        Uses personal range targets when available, falling back to static
        TOP_10/TOP_35 tiers for players without range targets.
        """
        canonical = ctx['canonical']
        if not canonical:
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='not_applicable',
                confidence=0.0,
                reasoning='Could not determine hand.',
            )

        # Check if personal range targets are available
        has_personal_range = ctx.get('personal_range_target') is not None
        is_in_range = ctx.get('is_in_personal_range', False)
        range_pct = ctx.get('personal_range_target', 0)

        # Format range as percentage string for reasoning messages
        range_str = f"top {int(range_pct * 100)}%" if has_personal_range else ""
        _reason = lambda personal, fallback: self._position_reasoning(
            has_personal_range, range_str, personal, fallback,
        )

        # Helper: use personal range if available, else fall back to a static tier flag
        def in_range_for(fallback_key: str) -> bool:
            return is_in_range if has_personal_range else ctx[fallback_key]

        # Early position evaluation
        if ctx['is_early']:
            in_range = in_range_for('is_top10')
            if action == 'fold':
                if in_range:
                    return SkillEvaluation(
                        skill_id='position_matters',
                        action_taken=action,
                        evaluation='incorrect',
                        confidence=0.8,
                        reasoning=_reason(
                            'Folded a hand in your range ({range_str}) from early position.',
                            'Folded a strong hand in early position.',
                        ),
                        in_personal_range=has_personal_range and is_in_range,
                    )
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.8,
                    reasoning='Correctly tightened range in early position.',
                )

            # Playing (raise/call) in early position
            if in_range:
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.9,
                    reasoning=_reason(
                        'Played a hand in your range ({range_str}) from early position.',
                        'Played a strong hand from early position.',
                    ),
                )

            # Outside personal range (or TOP_20 marginal for fallback)
            if not has_personal_range and ctx['is_top20']:
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
                reasoning=_reason(
                    'Played a hand outside your range ({range_str}) from early position.',
                    'Played a weak hand from early position.',
                ),
            )

        # Late position evaluation
        if ctx['is_late']:
            in_range = in_range_for('is_playable')
            if action == 'fold':
                if in_range:
                    return SkillEvaluation(
                        skill_id='position_matters',
                        action_taken=action,
                        evaluation='incorrect',
                        confidence=0.7,
                        reasoning=_reason(
                            'Folded a hand in your range ({range_str}) from late position.',
                            'Folded a playable hand from late position.',
                        ),
                        in_personal_range=has_personal_range and is_in_range,
                    )
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.7,
                    reasoning='Folded trash from late position.',
                )

            # Playing from late position
            if in_range:
                return SkillEvaluation(
                    skill_id='position_matters',
                    action_taken=action,
                    evaluation='correct',
                    confidence=0.9,
                    reasoning=_reason(
                        'Played a hand in your range ({range_str}) from late position.',
                        'Played a reasonable hand from late position.',
                    ),
                )

            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='marginal',
                confidence=0.5,
                reasoning='Played a weak hand from late position; borderline.',
            )

        # Middle position or blinds — moderate evaluation
        in_range = in_range_for('is_playable')

        if action == 'fold' and not in_range:
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='correct',
                confidence=0.6,
                reasoning='Folded trash from middle/blind position.',
            )

        if action == 'fold' and in_range:
            return SkillEvaluation(
                skill_id='position_matters',
                action_taken=action,
                evaluation='marginal',
                confidence=0.5,
                reasoning=_reason(
                    'Folded a hand in your range ({range_str}) from middle/blind position.',
                    'Folded a playable hand from middle/blind position.',
                ),
                in_personal_range=has_personal_range and is_in_range,
            )

        if action != 'fold' and in_range:
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
        # This skill only applies when the pot is unopened (cost_to_call <= big blind)
        if ctx['cost_to_call'] > ctx.get('big_blind', 0):
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

        if action.startswith('raise'):
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

    # ---- Gate 2 evaluators (post-flop) ----

    def _eval_flop_connection(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate flop_connection: air on flop → fold is correct."""
        if not ctx.get('is_air', False):
            return SkillEvaluation(
                skill_id='flop_connection',
                action_taken=action,
                evaluation='not_applicable',
                confidence=1.0,
                reasoning='Player has a made hand or draw, not air.',
            )

        if action == 'fold':
            return SkillEvaluation(
                skill_id='flop_connection',
                action_taken=action,
                evaluation='correct',
                confidence=1.0,
                reasoning='Correctly folded with no flop connection.',
            )

        if action == 'check':
            return SkillEvaluation(
                skill_id='flop_connection',
                action_taken=action,
                evaluation='marginal',
                confidence=0.6,
                reasoning='Checking with air is acceptable but folding to any bet is preferred.',
            )

        return SkillEvaluation(
            skill_id='flop_connection',
            action_taken=action,
            evaluation='incorrect',
            confidence=0.9,
            reasoning=f'Put money in ({action}) with no flop connection.',
        )

    def _eval_bet_when_strong(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate bet_when_strong: two pair+ post-flop → bet/raise is correct."""
        if not ctx.get('is_strong_hand', False):
            return SkillEvaluation(
                skill_id='bet_when_strong',
                action_taken=action,
                evaluation='not_applicable',
                confidence=1.0,
                reasoning='Hand is not strong enough for this skill.',
            )

        if action in ('raise', 'bet', 'all_in') or action.startswith('raise'):
            return SkillEvaluation(
                skill_id='bet_when_strong',
                action_taken=action,
                evaluation='correct',
                confidence=1.0,
                reasoning='Bet/raised for value with a strong hand.',
            )

        if action == 'call':
            return SkillEvaluation(
                skill_id='bet_when_strong',
                action_taken=action,
                evaluation='marginal',
                confidence=0.6,
                reasoning='Called with a strong hand — a raise would extract more value.',
            )

        if action == 'check':
            return SkillEvaluation(
                skill_id='bet_when_strong',
                action_taken=action,
                evaluation='marginal',
                confidence=0.5,
                reasoning='Checked with a strong hand — betting for value is preferred.',
            )

        # fold with strong hand
        return SkillEvaluation(
            skill_id='bet_when_strong',
            action_taken=action,
            evaluation='incorrect',
            confidence=0.9,
            reasoning='Folded a strong hand instead of betting for value.',
        )

    def _eval_checking_is_allowed(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Evaluate checking_is_allowed: weak hand + can check → check/fold is correct."""
        if ctx.get('has_pair', False):
            return SkillEvaluation(
                skill_id='checking_is_allowed',
                action_taken=action,
                evaluation='not_applicable',
                confidence=1.0,
                reasoning='Player has a pair or better.',
            )

        if not ctx.get('can_check', False):
            return SkillEvaluation(
                skill_id='checking_is_allowed',
                action_taken=action,
                evaluation='not_applicable',
                confidence=1.0,
                reasoning='Player is facing a bet and cannot check.',
            )

        if action in ('check', 'fold'):
            return SkillEvaluation(
                skill_id='checking_is_allowed',
                action_taken=action,
                evaluation='correct',
                confidence=0.9,
                reasoning='Correctly checked or folded with a weak hand.',
            )

        # bet/raise with weak hand when could have checked
        return SkillEvaluation(
            skill_id='checking_is_allowed',
            action_taken=action,
            evaluation='incorrect',
            confidence=0.8,
            reasoning=f'Bluffed ({action}) with a weak hand when checking was available.',
        )

    # ---- Gate 3 evaluators (pressure recognition) ----

    def _eval_draws_need_price(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Draw + facing bet: call when pot odds are good, fold when bad."""
        if not ctx.get('has_draw', False) or ctx['cost_to_call'] <= 0:
            return SkillEvaluation(
                skill_id='draws_need_price', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='Not facing bet with draw',
            )

        required_equity = ctx.get('required_equity') or 0
        equity = ctx.get('equity') or 0

        if required_equity > 0 and equity > 0:
            if equity >= required_equity:
                if action == 'call' or action.startswith('raise'):
                    return SkillEvaluation(
                        skill_id='draws_need_price', action_taken=action,
                        evaluation='correct', confidence=0.9,
                        reasoning='Called/raised with good pot odds on draw',
                    )
                if action == 'fold':
                    return SkillEvaluation(
                        skill_id='draws_need_price', action_taken=action,
                        evaluation='incorrect', confidence=0.8,
                        reasoning='Folded a profitable draw',
                    )
            else:
                if action == 'fold':
                    return SkillEvaluation(
                        skill_id='draws_need_price', action_taken=action,
                        evaluation='correct', confidence=0.9,
                        reasoning='Folded draw without proper pot odds',
                    )
                if action == 'call':
                    return SkillEvaluation(
                        skill_id='draws_need_price', action_taken=action,
                        evaluation='incorrect', confidence=0.8,
                        reasoning='Called draw without pot odds to justify it',
                    )

        return SkillEvaluation(
            skill_id='draws_need_price', action_taken=action,
            evaluation='marginal', confidence=0.3,
            reasoning='Insufficient equity data to evaluate pot odds',
        )

    def _eval_respect_big_bets(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Medium hand + big bet on turn/river: fold is correct."""
        if not (ctx.get('is_big_bet') and ctx.get('is_marginal_hand')):
            return SkillEvaluation(
                skill_id='respect_big_bets', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='Not a big bet with medium hand',
            )

        if action == 'fold':
            return SkillEvaluation(
                skill_id='respect_big_bets', action_taken=action,
                evaluation='correct', confidence=0.9,
                reasoning='Folded medium hand facing big bet — good discipline',
            )
        if action == 'call':
            return SkillEvaluation(
                skill_id='respect_big_bets', action_taken=action,
                evaluation='incorrect', confidence=0.8,
                reasoning='Called big bet with medium hand — likely dominated',
            )
        if action.startswith('raise'):
            return SkillEvaluation(
                skill_id='respect_big_bets', action_taken=action,
                evaluation='incorrect', confidence=0.8,
                reasoning='Raised into big bet with medium hand',
            )
        return SkillEvaluation(
            skill_id='respect_big_bets', action_taken=action,
            evaluation='marginal', confidence=0.5,
            reasoning='Ambiguous action facing big bet',
        )

    def _eval_have_a_plan(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Turn after betting flop: check-fold = incorrect, follow through = correct."""
        if not ctx.get('player_bet_flop'):
            return SkillEvaluation(
                skill_id='have_a_plan', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='Player did not bet the flop',
            )

        if action in ('raise', 'bet') or action.startswith('raise'):
            return SkillEvaluation(
                skill_id='have_a_plan', action_taken=action,
                evaluation='correct', confidence=0.9,
                reasoning='Followed through on flop aggression',
            )
        if action == 'call':
            return SkillEvaluation(
                skill_id='have_a_plan', action_taken=action,
                evaluation='marginal', confidence=0.6,
                reasoning='Called on turn after flop bet — passive but not a collapse',
            )
        if action == 'check':
            return SkillEvaluation(
                skill_id='have_a_plan', action_taken=action,
                evaluation='marginal', confidence=0.5,
                reasoning='Checked turn after flop bet — lost initiative',
            )
        if action == 'fold':
            return SkillEvaluation(
                skill_id='have_a_plan', action_taken=action,
                evaluation='incorrect', confidence=0.8,
                reasoning='Bet flop then folded turn — no plan',
            )
        return SkillEvaluation(
            skill_id='have_a_plan', action_taken=action,
            evaluation='marginal', confidence=0.4,
            reasoning='Ambiguous action on turn after flop bet',
        )

    # ---- Gate 4 evaluators (multi-street thinking) ----

    def _eval_dont_pay_double_barrels(self, action: str, ctx: Dict) -> SkillEvaluation:
        """Facing double barrel with marginal hand: fold is correct."""
        if not ctx.get('opponent_double_barrel') or ctx['cost_to_call'] <= 0:
            return SkillEvaluation(
                skill_id='dont_pay_double_barrels', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='Not facing double barrel',
            )

        if not ctx.get('is_marginal_hand'):
            return SkillEvaluation(
                skill_id='dont_pay_double_barrels', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='Hand is not marginal',
            )

        if action == 'fold':
            return SkillEvaluation(
                skill_id='dont_pay_double_barrels', action_taken=action,
                evaluation='correct', confidence=0.9,
                reasoning='Folded marginal hand vs double barrel',
            )
        if action == 'call':
            return SkillEvaluation(
                skill_id='dont_pay_double_barrels', action_taken=action,
                evaluation='incorrect', confidence=0.8,
                reasoning='Called double barrel with marginal hand',
            )
        if action.startswith('raise'):
            return SkillEvaluation(
                skill_id='dont_pay_double_barrels', action_taken=action,
                evaluation='marginal', confidence=0.5,
                reasoning='Raised vs double barrel — could be a bluff raise',
            )
        return SkillEvaluation(
            skill_id='dont_pay_double_barrels', action_taken=action,
            evaluation='marginal', confidence=0.4,
            reasoning='Ambiguous action vs double barrel',
        )

    def _eval_size_bets_with_purpose(self, action: str, ctx: Dict) -> SkillEvaluation:
        """When player bets/raises: check bet-to-pot ratio is in 33%-100% range."""
        if action not in ('bet', 'all_in') and not action.startswith('raise'):
            return SkillEvaluation(
                skill_id='size_bets_with_purpose', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='Player did not bet or raise',
            )

        ratio = ctx.get('bet_to_pot_ratio', 0)
        if ratio <= 0:
            return SkillEvaluation(
                skill_id='size_bets_with_purpose', action_taken=action,
                evaluation='not_applicable', confidence=1.0,
                reasoning='No bet sizing data',
            )

        if 0.33 <= ratio <= 1.0:
            return SkillEvaluation(
                skill_id='size_bets_with_purpose', action_taken=action,
                evaluation='correct', confidence=0.9,
                reasoning=f'Good bet sizing ({ratio:.0%} of pot)',
            )
        if 0.25 <= ratio < 0.33 or 1.0 < ratio <= 1.5:
            return SkillEvaluation(
                skill_id='size_bets_with_purpose', action_taken=action,
                evaluation='marginal', confidence=0.6,
                reasoning=f'Borderline bet sizing ({ratio:.0%} of pot)',
            )
        if ratio < 0.25:
            return SkillEvaluation(
                skill_id='size_bets_with_purpose', action_taken=action,
                evaluation='incorrect', confidence=0.8,
                reasoning=f'Bet too small ({ratio:.0%} of pot) — gives cheap draws',
            )
        # ratio > 1.5
        return SkillEvaluation(
            skill_id='size_bets_with_purpose', action_taken=action,
            evaluation='incorrect', confidence=0.7,
            reasoning=f'Bet too large ({ratio:.0%} of pot) — overcommitting',
        )
