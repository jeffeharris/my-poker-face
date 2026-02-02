"""Tests for the skill evaluator."""

import unittest

from flask_app.services.skill_evaluator import SkillEvaluator, SkillEvaluation


class TestSkillEvaluator(unittest.TestCase):
    """Test per-skill action evaluation."""

    def setUp(self):
        self.evaluator = SkillEvaluator()

    def _make_data(self, hand_strength='72o - Unconnected cards, Bottom 10%',
                   position='Under The Gun', cost_to_call=0, pot_total=30):
        return {
            'hand_strength': hand_strength,
            'position': position,
            'cost_to_call': cost_to_call,
            'pot_total': pot_total,
        }


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


class TestUnknownSkill(TestSkillEvaluator):
    """Test handling of unknown skills."""

    def test_unknown_skill_not_applicable(self):
        data = self._make_data()
        result = self.evaluator.evaluate('nonexistent_skill', 'fold', data)
        self.assertEqual(result.evaluation, 'not_applicable')
        self.assertEqual(result.confidence, 0.0)


if __name__ == '__main__':
    unittest.main()
