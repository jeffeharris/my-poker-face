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
                            position='Under The Gun', cost_to_call=0, pot_total=30):
        return {
            'phase': phase,
            'hand_strength': hand_strength,
            'position': position,
            'cost_to_call': cost_to_call,
            'pot_total': pot_total,
        }

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


if __name__ == '__main__':
    unittest.main()
