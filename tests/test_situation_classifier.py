"""Tests for the situation classifier."""

import unittest

from flask_app.services.situation_classifier import SituationClassifier, SituationClassification
from flask_app.services.skill_definitions import PlayerSkillState, SkillState


class TestSituationClassifier(unittest.TestCase):
    """Test rule-based situation classification."""

    def setUp(self):
        self.classifier = SituationClassifier()
        self.unlocked_gates = [1]
        self.skill_states = {}

    def _make_coaching_data(self, phase='PRE_FLOP', hand_strength='72o - Unconnected cards, Bottom 10%',
                            position='Under The Gun', cost_to_call=0, pot_total=30,
                            hand_rank=None, outs=None, big_blind=10):
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

    # ---- fold_trash_hands triggers ----

    def test_trash_hand_triggers_fold_trash(self):
        data = self._make_coaching_data(hand_strength='72o - Unconnected cards, Bottom 10%')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('fold_trash_hands', result.relevant_skills)

    def test_premium_hand_does_not_trigger_fold_trash(self):
        data = self._make_coaching_data(hand_strength='AA - High pocket pair, Top 3%')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('fold_trash_hands', result.relevant_skills)

    def test_top35_hand_does_not_trigger_fold_trash(self):
        data = self._make_coaching_data(hand_strength='A5s - Suited ace, Top 35%')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('fold_trash_hands', result.relevant_skills)

    # ---- position_matters triggers ----

    def test_preflop_triggers_position_matters(self):
        data = self._make_coaching_data(hand_strength='AKs - Suited broadway, Top 10%')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('position_matters', result.relevant_skills)

    def test_postflop_does_not_trigger_position_matters(self):
        data = self._make_coaching_data(phase='FLOP', hand_strength='AKs - Suited broadway')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('position_matters', result.relevant_skills)

    # ---- raise_or_fold triggers ----

    def test_unopened_pot_triggers_raise_or_fold(self):
        data = self._make_coaching_data(
            hand_strength='AKs - Suited broadway, Top 10%',
            cost_to_call=0,
        )
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('raise_or_fold', result.relevant_skills)

    def test_opened_pot_does_not_trigger_raise_or_fold(self):
        data = self._make_coaching_data(
            hand_strength='AKs - Suited broadway, Top 10%',
            cost_to_call=50,
        )
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('raise_or_fold', result.relevant_skills)

    # ---- primary skill selection ----

    def test_primary_skill_is_least_progressed(self):
        """When multiple skills trigger, the least-progressed is primary."""
        # Include all triggered skills so the one with lowest state wins
        self.skill_states = {
            'fold_trash_hands': PlayerSkillState(
                skill_id='fold_trash_hands',
                state=SkillState.PRACTICING,
                total_opportunities=15,
            ),
            'position_matters': PlayerSkillState(
                skill_id='position_matters',
                state=SkillState.PRACTICING,
                total_opportunities=10,
            ),
            'raise_or_fold': PlayerSkillState(
                skill_id='raise_or_fold',
                state=SkillState.RELIABLE,
                total_opportunities=20,
            ),
        }
        data = self._make_coaching_data(hand_strength='72o - Trash, Bottom 10%')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        # position_matters and fold_trash are both PRACTICING, but
        # position_matters has fewer opportunities so it wins
        self.assertEqual(result.primary_skill, 'position_matters')

    def test_no_skills_for_postflop(self):
        """No Gate 1 skills should trigger for post-flop phases."""
        data = self._make_coaching_data(phase='TURN')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertEqual(result.relevant_skills, ())
        self.assertIsNone(result.primary_skill)

    # ---- situation tags ----

    def test_trash_hand_tag(self):
        data = self._make_coaching_data(hand_strength='72o - Unconnected cards, Bottom 10%')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('trash_hand', result.situation_tags)

    def test_early_position_tag(self):
        data = self._make_coaching_data(
            hand_strength='AKs - Suited broadway, Top 10%',
            position='Under The Gun',
        )
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('early_position', result.situation_tags)

    def test_late_position_tag(self):
        data = self._make_coaching_data(
            hand_strength='AKs - Suited broadway, Top 10%',
            position='Button',
        )
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('late_position', result.situation_tags)

    # ---- edge cases ----

    def test_empty_coaching_data(self):
        result = self.classifier.classify({}, self.unlocked_gates, self.skill_states)
        self.assertEqual(result.relevant_skills, ())

    def test_no_hand_strength(self):
        data = self._make_coaching_data(hand_strength='')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        # No canonical hand means limited skill triggers
        self.assertNotIn('fold_trash_hands', result.relevant_skills)


class TestGate2SituationClassifier(unittest.TestCase):
    """Test Gate 2 post-flop situation classification."""

    def setUp(self):
        self.classifier = SituationClassifier()
        self.unlocked_gates = [1, 2]
        self.skill_states = {}

    def _make_postflop_data(self, phase='FLOP', hand_rank=10, cost_to_call=0,
                             outs=0, hand_strength='High Card'):
        return {
            'phase': phase,
            'hand_strength': hand_strength,
            'hand_rank': hand_rank,
            'position': 'Button',
            'cost_to_call': cost_to_call,
            'pot_total': 100,
            'big_blind': 10,
            'outs': outs,
        }

    # ---- flop_connection triggers ----

    def test_air_on_flop_triggers_flop_connection(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=10, outs=2)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('flop_connection', result.relevant_skills)

    def test_pair_on_flop_does_not_trigger_flop_connection(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=9, hand_strength='One Pair')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('flop_connection', result.relevant_skills)

    def test_draw_on_flop_does_not_trigger_flop_connection(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=10, outs=8)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('flop_connection', result.relevant_skills)

    def test_flop_connection_only_on_flop(self):
        data = self._make_postflop_data(phase='TURN', hand_rank=10, outs=2)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('flop_connection', result.relevant_skills)

    # ---- bet_when_strong triggers ----

    def test_strong_hand_triggers_bet_when_strong(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=8, hand_strength='Two Pair')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('bet_when_strong', result.relevant_skills)

    def test_weak_hand_does_not_trigger_bet_when_strong(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=9, hand_strength='One Pair')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('bet_when_strong', result.relevant_skills)

    def test_strong_hand_on_river_triggers_bet_when_strong(self):
        data = self._make_postflop_data(phase='RIVER', hand_rank=5, hand_strength='Flush')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('bet_when_strong', result.relevant_skills)

    def test_strong_hand_preflop_does_not_trigger(self):
        data = self._make_postflop_data(phase='PRE_FLOP', hand_rank=8)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('bet_when_strong', result.relevant_skills)

    # ---- checking_is_allowed triggers ----

    def test_weak_hand_can_check_triggers(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=10, cost_to_call=0)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('checking_is_allowed', result.relevant_skills)

    def test_weak_hand_facing_bet_does_not_trigger(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=10, cost_to_call=20)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('checking_is_allowed', result.relevant_skills)

    def test_pair_does_not_trigger_checking_is_allowed(self):
        data = self._make_postflop_data(phase='FLOP', hand_rank=9, cost_to_call=0,
                                         hand_strength='One Pair')
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertNotIn('checking_is_allowed', result.relevant_skills)

    # ---- overlap ----

    def test_air_on_flop_can_check_triggers_both_skills(self):
        """Air on flop + can check should trigger both flop_connection and checking_is_allowed."""
        data = self._make_postflop_data(phase='FLOP', hand_rank=10, cost_to_call=0, outs=2)
        result = self.classifier.classify(data, self.unlocked_gates, self.skill_states)
        self.assertIn('flop_connection', result.relevant_skills)
        self.assertIn('checking_is_allowed', result.relevant_skills)

    # ---- gate unlock gating ----

    def test_gate2_skills_not_triggered_when_gate2_locked(self):
        """Gate 2 skills should not trigger when only Gate 1 is unlocked."""
        unlocked = [1]
        data = self._make_postflop_data(phase='FLOP', hand_rank=10, cost_to_call=0)
        result = self.classifier.classify(data, unlocked, self.skill_states)
        self.assertNotIn('flop_connection', result.relevant_skills)
        self.assertNotIn('checking_is_allowed', result.relevant_skills)


if __name__ == '__main__':
    unittest.main()
