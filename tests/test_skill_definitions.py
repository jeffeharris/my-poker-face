"""Tests for skill definitions and core data structures."""

import unittest

from flask_app.services.skill_definitions import (
    ALL_GATES, ALL_SKILLS, CoachingDecision, CoachingMode,
    EvidenceRules, GateDefinition, GateProgress, PlayerSkillState,
    SkillDefinition, SkillState, get_skill_by_id, get_skills_for_gate,
)


class TestSkillState(unittest.TestCase):
    """Test SkillState enum."""

    def test_values(self):
        self.assertEqual(SkillState.INTRODUCED.value, 'introduced')
        self.assertEqual(SkillState.PRACTICING.value, 'practicing')
        self.assertEqual(SkillState.RELIABLE.value, 'reliable')
        self.assertEqual(SkillState.AUTOMATIC.value, 'automatic')

    def test_from_string(self):
        self.assertEqual(SkillState('introduced'), SkillState.INTRODUCED)
        self.assertEqual(SkillState('automatic'), SkillState.AUTOMATIC)


class TestCoachingMode(unittest.TestCase):
    """Test CoachingMode enum."""

    def test_values(self):
        self.assertEqual(CoachingMode.LEARN.value, 'learn')
        self.assertEqual(CoachingMode.COMPETE.value, 'compete')
        self.assertEqual(CoachingMode.REVIEW.value, 'review')
        self.assertEqual(CoachingMode.SILENT.value, 'silent')


class TestPlayerSkillState(unittest.TestCase):
    """Test PlayerSkillState dataclass."""

    def test_defaults(self):
        ss = PlayerSkillState(skill_id='test')
        self.assertEqual(ss.state, SkillState.INTRODUCED)
        self.assertEqual(ss.total_opportunities, 0)
        self.assertEqual(ss.window_accuracy, 0.0)

    def test_window_accuracy(self):
        ss = PlayerSkillState(skill_id='test', window_opportunities=10, window_correct=7)
        self.assertAlmostEqual(ss.window_accuracy, 0.7)

    def test_total_accuracy(self):
        ss = PlayerSkillState(skill_id='test', total_opportunities=20, total_correct=15)
        self.assertAlmostEqual(ss.total_accuracy, 0.75)

    def test_zero_division_safety(self):
        ss = PlayerSkillState(skill_id='test')
        self.assertEqual(ss.window_accuracy, 0.0)
        self.assertEqual(ss.total_accuracy, 0.0)


class TestEvidenceRules(unittest.TestCase):
    """Test EvidenceRules frozen dataclass."""

    def test_frozen(self):
        rules = EvidenceRules(min_opportunities=10)
        with self.assertRaises(AttributeError):
            rules.min_opportunities = 20

    def test_defaults(self):
        rules = EvidenceRules(min_opportunities=10)
        self.assertEqual(rules.window_size, 20)
        self.assertEqual(rules.advancement_threshold, 0.75)
        self.assertEqual(rules.regression_threshold, 0.60)


class TestGate1Skills(unittest.TestCase):
    """Test Gate 1 skill definitions."""

    def test_three_gate1_skills(self):
        skills = get_skills_for_gate(1)
        self.assertEqual(len(skills), 3)
        ids = {s.skill_id for s in skills}
        self.assertEqual(ids, {'fold_trash_hands', 'position_matters', 'raise_or_fold'})

    def test_all_skills_registry(self):
        self.assertEqual(len(ALL_SKILLS), 3)
        self.assertIn('fold_trash_hands', ALL_SKILLS)
        self.assertIn('position_matters', ALL_SKILLS)
        self.assertIn('raise_or_fold', ALL_SKILLS)

    def test_get_skill_by_id(self):
        skill = get_skill_by_id('fold_trash_hands')
        self.assertIsNotNone(skill)
        self.assertEqual(skill.gate, 1)
        self.assertIn('PRE_FLOP', skill.phases)

    def test_get_skill_by_id_not_found(self):
        self.assertIsNone(get_skill_by_id('nonexistent'))

    def test_fold_trash_evidence_rules(self):
        skill = ALL_SKILLS['fold_trash_hands']
        self.assertEqual(skill.evidence_rules.min_opportunities, 12)
        self.assertAlmostEqual(skill.evidence_rules.advancement_threshold, 0.75)

    def test_position_matters_evidence_rules(self):
        skill = ALL_SKILLS['position_matters']
        self.assertEqual(skill.evidence_rules.min_opportunities, 20)

    def test_raise_or_fold_evidence_rules(self):
        skill = ALL_SKILLS['raise_or_fold']
        self.assertEqual(skill.evidence_rules.min_opportunities, 10)
        self.assertAlmostEqual(skill.evidence_rules.advancement_threshold, 0.80)


class TestGateDefinition(unittest.TestCase):
    """Test gate definitions."""

    def test_gate1(self):
        gate = ALL_GATES[1]
        self.assertEqual(gate.gate_number, 1)
        self.assertEqual(gate.required_reliable, 2)
        self.assertEqual(len(gate.skill_ids), 3)

    def test_get_skills_for_nonexistent_gate(self):
        self.assertEqual(get_skills_for_gate(99), [])


class TestCoachingDecision(unittest.TestCase):
    """Test CoachingDecision dataclass."""

    def test_defaults(self):
        cd = CoachingDecision(mode=CoachingMode.LEARN)
        self.assertIsNone(cd.primary_skill_id)
        self.assertEqual(cd.relevant_skill_ids, ())
        self.assertEqual(cd.coaching_prompt, '')


if __name__ == '__main__':
    unittest.main()
