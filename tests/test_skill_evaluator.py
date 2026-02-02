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


class TestUnknownSkill(TestSkillEvaluator):
    """Test handling of unknown skills."""

    def test_unknown_skill_not_applicable(self):
        data = self._make_data()
        result = self.evaluator.evaluate('nonexistent_skill', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')
        self.assertEqual(result.confidence, 0.0)


if __name__ == '__main__':
    unittest.main()
