"""Tests for the skill evaluator."""

import unittest

from flask_app.services.skill_evaluator import SkillEvaluator, SkillEvaluation


class TestSkillEvaluator(unittest.TestCase):
    """Test per-skill action evaluation."""

    def setUp(self):
        self.evaluator = SkillEvaluator()

    def _make_data(self, hand_strength='72o - Unconnected cards, Bottom 10%',
                   position='Under The Gun', cost_to_call=0, pot_total=30,
                   phase='PRE_FLOP', hand_rank=None, outs=None, big_blind=10):
        data = {
            'phase': phase,
            'hand_strength': hand_strength,
            'position': position,
            'cost_to_call': cost_to_call,
            'pot_total': pot_total,
            'big_blind': big_blind,
        }
        if hand_rank is not None:
            data['hand_rank'] = hand_rank
        if outs is not None:
            data['outs'] = outs
        return data


class TestFoldTrashEvaluation(TestSkillEvaluator):
    """Test fold_trash_hands evaluation."""

    def test_fold_trash_is_correct(self):
        data = self._make_data(hand_strength='72o - Unconnected cards, Bottom 10%')
        result = self.evaluator.evaluate('fold_trash_hands', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_trash_is_incorrect(self):
        data = self._make_data(hand_strength='72o - Unconnected cards, Bottom 10%')
        result = self.evaluator.evaluate('fold_trash_hands', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_raise_trash_is_incorrect(self):
        data = self._make_data(hand_strength='72o - Unconnected cards, Bottom 10%')
        result = self.evaluator.evaluate('fold_trash_hands', 'raise', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_check_trash_is_marginal(self):
        data = self._make_data(hand_strength='72o - Unconnected cards, Bottom 10%')
        result = self.evaluator.evaluate('fold_trash_hands', 'check', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_playable_hand_not_applicable(self):
        data = self._make_data(hand_strength='AKs - Suited broadway, Top 10%')
        result = self.evaluator.evaluate('fold_trash_hands', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestPositionMattersEvaluation(TestSkillEvaluator):
    """Test position_matters evaluation."""

    def test_early_position_fold_weak_is_correct(self):
        data = self._make_data(
            hand_strength='J8o - Offsuit connector, Below average',
            position='Under The Gun',
        )
        result = self.evaluator.evaluate('position_matters', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_early_position_play_premium_is_correct(self):
        data = self._make_data(
            hand_strength='AA - High pocket pair, Top 3%',
            position='Under The Gun',
        )
        result = self.evaluator.evaluate('position_matters', 'raise', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_early_position_play_weak_is_incorrect(self):
        data = self._make_data(
            hand_strength='J8o - Offsuit connector, Below average',
            position='Under The Gun',
        )
        result = self.evaluator.evaluate('position_matters', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_late_position_play_playable_is_correct(self):
        data = self._make_data(
            hand_strength='A5s - Suited ace, Top 35%',
            position='Button',
        )
        result = self.evaluator.evaluate('position_matters', 'raise', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_late_position_fold_playable_is_incorrect(self):
        data = self._make_data(
            hand_strength='A5s - Suited ace, Top 35%',
            position='Button',
        )
        result = self.evaluator.evaluate('position_matters', 'fold', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_early_position_fold_premium_is_incorrect(self):
        data = self._make_data(
            hand_strength='AA - High pocket pair, Top 3%',
            position='Under The Gun',
        )
        result = self.evaluator.evaluate('position_matters', 'fold', data)
        self.assertEqual(result.evaluation, 'incorrect')


class TestRaiseOrFoldEvaluation(TestSkillEvaluator):
    """Test raise_or_fold evaluation."""

    def test_raise_unopened_is_correct(self):
        data = self._make_data(cost_to_call=0)
        result = self.evaluator.evaluate('raise_or_fold', 'raise', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_fold_unopened_is_correct(self):
        data = self._make_data(cost_to_call=0)
        result = self.evaluator.evaluate('raise_or_fold', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_unopened_is_incorrect(self):
        data = self._make_data(cost_to_call=0)
        result = self.evaluator.evaluate('raise_or_fold', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_opened_pot_not_applicable(self):
        data = self._make_data(cost_to_call=50)
        result = self.evaluator.evaluate('raise_or_fold', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_check_from_blind_is_marginal(self):
        data = self._make_data(cost_to_call=0, position='Small Blind')
        result = self.evaluator.evaluate('raise_or_fold', 'check', data)
        self.assertEqual(result.evaluation, 'marginal')


class TestFlopConnectionEvaluation(TestSkillEvaluator):
    """Test flop_connection evaluation."""

    def _make_flop_air(self, **kwargs):
        defaults = dict(phase='FLOP', hand_rank=10, hand_strength='High Card',
                        position='Button', cost_to_call=0, outs=2)
        defaults.update(kwargs)
        return self._make_data(**defaults)

    def test_fold_air_is_correct(self):
        data = self._make_flop_air()
        result = self.evaluator.evaluate('flop_connection', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_check_air_is_marginal(self):
        data = self._make_flop_air()
        result = self.evaluator.evaluate('flop_connection', 'check', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_bet_air_is_incorrect(self):
        data = self._make_flop_air()
        result = self.evaluator.evaluate('flop_connection', 'raise', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_call_air_is_incorrect(self):
        data = self._make_flop_air()
        result = self.evaluator.evaluate('flop_connection', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_all_in_air_is_incorrect(self):
        data = self._make_flop_air()
        result = self.evaluator.evaluate('flop_connection', 'all_in', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_pair_is_not_applicable(self):
        data = self._make_data(phase='FLOP', hand_rank=9, hand_strength='One Pair',
                               position='Button')
        result = self.evaluator.evaluate('flop_connection', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestBetWhenStrongEvaluation(TestSkillEvaluator):
    """Test bet_when_strong evaluation."""

    def _make_strong(self, **kwargs):
        defaults = dict(phase='FLOP', hand_rank=8, hand_strength='Two Pair',
                        position='Button', cost_to_call=0)
        defaults.update(kwargs)
        return self._make_data(**defaults)

    def test_raise_strong_is_correct(self):
        data = self._make_strong()
        result = self.evaluator.evaluate('bet_when_strong', 'raise', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_all_in_strong_is_correct(self):
        data = self._make_strong()
        result = self.evaluator.evaluate('bet_when_strong', 'all_in', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_strong_is_marginal(self):
        data = self._make_strong()
        result = self.evaluator.evaluate('bet_when_strong', 'call', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_check_strong_is_marginal(self):
        data = self._make_strong()
        result = self.evaluator.evaluate('bet_when_strong', 'check', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_fold_strong_is_incorrect(self):
        data = self._make_strong()
        result = self.evaluator.evaluate('bet_when_strong', 'fold', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_weak_hand_not_applicable(self):
        data = self._make_data(phase='FLOP', hand_rank=9, hand_strength='One Pair',
                               position='Button')
        result = self.evaluator.evaluate('bet_when_strong', 'check', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestCheckingIsAllowedEvaluation(TestSkillEvaluator):
    """Test checking_is_allowed evaluation."""

    def _make_weak_can_check(self, **kwargs):
        defaults = dict(phase='FLOP', hand_rank=10, hand_strength='High Card',
                        position='Button', cost_to_call=0, outs=1)
        defaults.update(kwargs)
        return self._make_data(**defaults)

    def test_check_weak_is_correct(self):
        data = self._make_weak_can_check()
        result = self.evaluator.evaluate('checking_is_allowed', 'check', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_fold_weak_is_correct(self):
        data = self._make_weak_can_check()
        result = self.evaluator.evaluate('checking_is_allowed', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_raise_weak_is_incorrect(self):
        data = self._make_weak_can_check()
        result = self.evaluator.evaluate('checking_is_allowed', 'raise', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_bet_weak_is_incorrect(self):
        data = self._make_weak_can_check()
        result = self.evaluator.evaluate('checking_is_allowed', 'bet', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_all_in_weak_is_incorrect(self):
        data = self._make_weak_can_check()
        result = self.evaluator.evaluate('checking_is_allowed', 'all_in', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_pair_not_applicable(self):
        data = self._make_data(phase='FLOP', hand_rank=9, hand_strength='One Pair',
                               position='Button', cost_to_call=0)
        result = self.evaluator.evaluate('checking_is_allowed', 'raise', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_facing_bet_not_applicable(self):
        data = self._make_data(phase='FLOP', hand_rank=10, hand_strength='High Card',
                               position='Button', cost_to_call=20)
        result = self.evaluator.evaluate('checking_is_allowed', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestForcedAllIn(TestSkillEvaluator):
    """Test forced all-in exclusion."""

    def test_forced_all_in_returns_not_applicable(self):
        """When stack <= cost_to_call, all_in is forced — no meaningful decision."""
        data = self._make_data(cost_to_call=100)
        data['stack'] = 80  # Stack less than cost to call
        result = self.evaluator.evaluate('fold_trash_hands', 'all_in', data)
        self.assertEqual(result.evaluation, 'not_applicable')
        self.assertIn('Forced all-in', result.reasoning)

    def test_voluntary_all_in_is_evaluated(self):
        """When stack > cost_to_call, all_in is voluntary and should be evaluated."""
        data = self._make_data(
            phase='FLOP', hand_rank=8, hand_strength='Two Pair',
            position='Button', cost_to_call=20,
        )
        data['stack'] = 500  # Stack much larger than cost to call
        result = self.evaluator.evaluate('bet_when_strong', 'all_in', data)
        self.assertEqual(result.evaluation, 'correct')


class TestDrawsNeedPriceEvaluation(TestSkillEvaluator):
    """Test draws_need_price evaluation."""

    def _make_draw_data(self, equity=0.35, required_equity=0.22, **kwargs):
        defaults = dict(phase='FLOP', hand_rank=10, hand_strength='High Card',
                        position='Button', cost_to_call=20, pot_total=100,
                        outs=8, big_blind=10)
        defaults.update(kwargs)
        data = self._make_data(**defaults)
        data['equity'] = equity
        data['required_equity'] = required_equity
        data['hand_actions'] = []
        data['player_name'] = 'Hero'
        return data

    def test_call_good_odds_is_correct(self):
        data = self._make_draw_data(equity=0.35, required_equity=0.22)
        result = self.evaluator.evaluate('draws_need_price', 'call', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_fold_good_odds_is_incorrect(self):
        data = self._make_draw_data(equity=0.35, required_equity=0.22)
        result = self.evaluator.evaluate('draws_need_price', 'fold', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_fold_bad_odds_is_correct(self):
        data = self._make_draw_data(equity=0.15, required_equity=0.30)
        result = self.evaluator.evaluate('draws_need_price', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_bad_odds_is_incorrect(self):
        data = self._make_draw_data(equity=0.15, required_equity=0.30)
        result = self.evaluator.evaluate('draws_need_price', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_no_draw_is_not_applicable(self):
        data = self._make_draw_data(outs=2)  # Below 4 outs threshold
        result = self.evaluator.evaluate('draws_need_price', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_no_cost_to_call_is_not_applicable(self):
        data = self._make_draw_data(cost_to_call=0)
        result = self.evaluator.evaluate('draws_need_price', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_missing_equity_is_marginal(self):
        data = self._make_draw_data(equity=0, required_equity=0)
        result = self.evaluator.evaluate('draws_need_price', 'call', data)
        self.assertEqual(result.evaluation, 'marginal')


class TestRespectBigBetsEvaluation(TestSkillEvaluator):
    """Test respect_big_bets evaluation."""

    def _make_big_bet_data(self, **kwargs):
        # pot_total=150, cost_to_call=100 => pot_before_bet=50, bet >= 50*0.5
        defaults = dict(phase='TURN', hand_rank=9, hand_strength='One Pair',
                        position='Button', cost_to_call=100, pot_total=150,
                        big_blind=10)
        defaults.update(kwargs)
        data = self._make_data(**defaults)
        data['hand_actions'] = []
        data['player_name'] = 'Hero'
        return data

    def test_fold_medium_facing_big_bet_is_correct(self):
        data = self._make_big_bet_data()
        result = self.evaluator.evaluate('respect_big_bets', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_medium_facing_big_bet_is_incorrect(self):
        data = self._make_big_bet_data()
        result = self.evaluator.evaluate('respect_big_bets', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_raise_medium_facing_big_bet_is_incorrect(self):
        data = self._make_big_bet_data()
        result = self.evaluator.evaluate('respect_big_bets', 'raise', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_small_bet_not_applicable(self):
        data = self._make_big_bet_data(cost_to_call=10, pot_total=200)
        result = self.evaluator.evaluate('respect_big_bets', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_strong_hand_not_applicable(self):
        data = self._make_big_bet_data(hand_rank=8)  # Two pair = strong
        result = self.evaluator.evaluate('respect_big_bets', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestHaveAPlanEvaluation(TestSkillEvaluator):
    """Test have_a_plan evaluation."""

    def _make_turn_data(self, player_bet_flop=True, **kwargs):
        defaults = dict(phase='TURN', hand_rank=9, hand_strength='One Pair',
                        position='Button', cost_to_call=0, pot_total=100,
                        big_blind=10)
        defaults.update(kwargs)
        data = self._make_data(**defaults)
        actions = []
        if player_bet_flop:
            actions.append({'player_name': 'Hero', 'action': 'bet', 'phase': 'FLOP', 'amount': 20})
        data['hand_actions'] = actions
        data['player_name'] = 'Hero'
        return data

    def test_bet_turn_after_flop_bet_is_correct(self):
        data = self._make_turn_data()
        result = self.evaluator.evaluate('have_a_plan', 'bet', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_raise_turn_after_flop_bet_is_correct(self):
        data = self._make_turn_data()
        result = self.evaluator.evaluate('have_a_plan', 'raise', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_turn_after_flop_bet_is_marginal(self):
        data = self._make_turn_data()
        result = self.evaluator.evaluate('have_a_plan', 'call', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_check_turn_after_flop_bet_is_marginal(self):
        data = self._make_turn_data()
        result = self.evaluator.evaluate('have_a_plan', 'check', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_fold_turn_after_flop_bet_is_incorrect(self):
        data = self._make_turn_data()
        result = self.evaluator.evaluate('have_a_plan', 'fold', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_no_flop_bet_not_applicable(self):
        data = self._make_turn_data(player_bet_flop=False)
        result = self.evaluator.evaluate('have_a_plan', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestDontPayDoubleBarrelsEvaluation(TestSkillEvaluator):
    """Test dont_pay_double_barrels evaluation."""

    def _make_double_barrel_data(self, **kwargs):
        defaults = dict(phase='TURN', hand_rank=9, hand_strength='One Pair',
                        position='Button', cost_to_call=40, pot_total=120,
                        big_blind=10)
        defaults.update(kwargs)
        data = self._make_data(**defaults)
        data['hand_actions'] = [
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'FLOP', 'amount': 20},
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'TURN', 'amount': 40},
        ]
        data['player_name'] = 'Hero'
        return data

    def test_fold_marginal_vs_double_barrel_is_correct(self):
        data = self._make_double_barrel_data()
        result = self.evaluator.evaluate('dont_pay_double_barrels', 'fold', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_call_marginal_vs_double_barrel_is_incorrect(self):
        data = self._make_double_barrel_data()
        result = self.evaluator.evaluate('dont_pay_double_barrels', 'call', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_raise_vs_double_barrel_is_marginal(self):
        data = self._make_double_barrel_data()
        result = self.evaluator.evaluate('dont_pay_double_barrels', 'raise', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_not_double_barrel_is_not_applicable(self):
        data = self._make_double_barrel_data()
        data['hand_actions'] = [
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'FLOP', 'amount': 20},
        ]
        result = self.evaluator.evaluate('dont_pay_double_barrels', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_strong_hand_not_applicable(self):
        data = self._make_double_barrel_data(hand_rank=8)  # Two pair
        result = self.evaluator.evaluate('dont_pay_double_barrels', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestSizeBetsWithPurposeEvaluation(TestSkillEvaluator):
    """Test size_bets_with_purpose evaluation."""

    def _make_bet_data(self, ratio=0.5, **kwargs):
        defaults = dict(phase='FLOP', hand_rank=8, hand_strength='Two Pair',
                        position='Button', cost_to_call=0, pot_total=100,
                        big_blind=10)
        defaults.update(kwargs)
        data = self._make_data(**defaults)
        data['bet_to_pot_ratio'] = ratio
        data['hand_actions'] = []
        data['player_name'] = 'Hero'
        return data

    def test_good_sizing_is_correct(self):
        data = self._make_bet_data(ratio=0.5)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_33_percent_is_correct(self):
        data = self._make_bet_data(ratio=0.33)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_100_percent_is_correct(self):
        data = self._make_bet_data(ratio=1.0)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'raise', data)
        self.assertEqual(result.evaluation, 'correct')

    def test_borderline_small_is_marginal(self):
        data = self._make_bet_data(ratio=0.28)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_borderline_large_is_marginal(self):
        data = self._make_bet_data(ratio=1.3)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'marginal')

    def test_too_small_is_incorrect(self):
        data = self._make_bet_data(ratio=0.15)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_too_large_is_incorrect(self):
        data = self._make_bet_data(ratio=2.0)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'incorrect')

    def test_non_bet_action_not_applicable(self):
        data = self._make_bet_data(ratio=0.5)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'call', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_no_ratio_not_applicable(self):
        data = self._make_bet_data(ratio=0)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'not_applicable')

    def test_zero_pot_not_applicable(self):
        data = self._make_bet_data(ratio=0, pot_total=0)
        result = self.evaluator.evaluate('size_bets_with_purpose', 'bet', data)
        self.assertEqual(result.evaluation, 'not_applicable')


class TestUnknownSkill(TestSkillEvaluator):
    """Test handling of unknown skills."""

    def test_unknown_skill_not_applicable(self):
        data = self._make_data()
        result = self.evaluator.evaluate('nonexistent_skill', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')
        self.assertEqual(result.confidence, 0.0)


if __name__ == '__main__':
    unittest.main()
