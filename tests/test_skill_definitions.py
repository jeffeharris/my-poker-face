"""Tests for skill definitions and core data structures."""

import unittest

from poker.coach_models import (
    CoachingDecision, CoachingMode, EvidenceRules,
    GateProgress, PlayerSkillState, SkillState,
)
from flask_app.services.context_builder import build_poker_context
from flask_app.services.skill_definitions import (
    ALL_GATES, ALL_SKILLS, GateDefinition, SkillDefinition,
    get_skill_by_id, get_skills_for_gate,
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
        self.assertEqual(len(ALL_SKILLS), 11)
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


class TestGate2Skills(unittest.TestCase):
    """Test Gate 2 skill definitions."""

    def test_three_gate2_skills(self):
        skills = get_skills_for_gate(2)
        self.assertEqual(len(skills), 3)
        ids = {s.skill_id for s in skills}
        self.assertEqual(ids, {'flop_connection', 'bet_when_strong', 'checking_is_allowed'})

    def test_gate2_skills_in_registry(self):
        self.assertIn('flop_connection', ALL_SKILLS)
        self.assertIn('bet_when_strong', ALL_SKILLS)
        self.assertIn('checking_is_allowed', ALL_SKILLS)

    def test_flop_connection_phases(self):
        skill = ALL_SKILLS['flop_connection']
        self.assertEqual(skill.phases, frozenset({'FLOP'}))
        self.assertEqual(skill.gate, 2)

    def test_bet_when_strong_phases(self):
        skill = ALL_SKILLS['bet_when_strong']
        self.assertEqual(skill.phases, frozenset({'FLOP', 'TURN', 'RIVER'}))

    def test_checking_is_allowed_phases(self):
        skill = ALL_SKILLS['checking_is_allowed']
        self.assertEqual(skill.phases, frozenset({'FLOP', 'TURN', 'RIVER'}))

    def test_gate2_window_size(self):
        for sid in ('flop_connection', 'bet_when_strong', 'checking_is_allowed'):
            skill = ALL_SKILLS[sid]
            self.assertEqual(skill.evidence_rules.window_size, 30)

    def test_gate2_evidence_rules(self):
        skill = ALL_SKILLS['flop_connection']
        self.assertEqual(skill.evidence_rules.min_opportunities, 8)
        self.assertAlmostEqual(skill.evidence_rules.advancement_threshold, 0.70)

    def test_checking_is_allowed_lower_thresholds(self):
        skill = ALL_SKILLS['checking_is_allowed']
        self.assertAlmostEqual(skill.evidence_rules.advancement_threshold, 0.65)
        self.assertAlmostEqual(skill.evidence_rules.regression_threshold, 0.50)


class TestGateDefinition(unittest.TestCase):
    """Test gate definitions."""

    def test_gate1(self):
        gate = ALL_GATES[1]
        self.assertEqual(gate.gate_number, 1)
        self.assertEqual(gate.required_reliable, 2)
        self.assertEqual(len(gate.skill_ids), 3)

    def test_gate2(self):
        gate = ALL_GATES[2]
        self.assertEqual(gate.gate_number, 2)
        self.assertEqual(gate.name, 'Post-Flop Basics')
        self.assertEqual(gate.required_reliable, 2)
        self.assertEqual(len(gate.skill_ids), 3)

    def test_all_gates_registry(self):
        self.assertEqual(len(ALL_GATES), 4)
        self.assertIn(1, ALL_GATES)
        self.assertIn(2, ALL_GATES)
        self.assertIn(3, ALL_GATES)
        self.assertIn(4, ALL_GATES)

    def test_get_skills_for_nonexistent_gate(self):
        self.assertEqual(get_skills_for_gate(99), [])


class TestBuildPokerContextPostFlop(unittest.TestCase):
    """Test build_poker_context() with post-flop data."""

    def test_strong_hand(self):
        data = {
            'phase': 'FLOP',
            'hand_strength': 'Two Pair',
            'hand_rank': 8,
            'position': 'Button',
            'cost_to_call': 20,
            'pot_total': 100,
            'big_blind': 10,
        }
        ctx = build_poker_context(data)
        self.assertTrue(ctx['is_strong_hand'])
        self.assertTrue(ctx['has_pair'])
        self.assertFalse(ctx['is_air'])

    def test_one_pair_is_not_strong(self):
        data = {
            'phase': 'FLOP',
            'hand_strength': 'One Pair',
            'hand_rank': 9,
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 100,
            'big_blind': 10,
        }
        ctx = build_poker_context(data)
        self.assertFalse(ctx['is_strong_hand'])
        self.assertTrue(ctx['has_pair'])
        self.assertFalse(ctx['is_air'])

    def test_air_hand(self):
        data = {
            'phase': 'FLOP',
            'hand_strength': 'High Card',
            'hand_rank': 10,
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 50,
            'big_blind': 10,
            'outs': 2,
        }
        ctx = build_poker_context(data)
        self.assertFalse(ctx['is_strong_hand'])
        self.assertFalse(ctx['has_pair'])
        self.assertTrue(ctx['is_air'])
        self.assertTrue(ctx['can_check'])

    def test_draw_hand_not_air(self):
        data = {
            'phase': 'FLOP',
            'hand_strength': 'High Card',
            'hand_rank': 10,
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 50,
            'big_blind': 10,
            'outs': 8,
        }
        ctx = build_poker_context(data)
        self.assertTrue(ctx['has_draw'])
        self.assertFalse(ctx['is_air'])

    def test_can_check_when_no_cost(self):
        data = {
            'phase': 'TURN',
            'hand_strength': 'One Pair',
            'hand_rank': 9,
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 50,
            'big_blind': 10,
        }
        ctx = build_poker_context(data)
        self.assertTrue(ctx['can_check'])

    def test_cannot_check_when_facing_bet(self):
        data = {
            'phase': 'TURN',
            'hand_strength': 'One Pair',
            'hand_rank': 9,
            'position': 'Button',
            'cost_to_call': 30,
            'pot_total': 50,
            'big_blind': 10,
        }
        ctx = build_poker_context(data)
        self.assertFalse(ctx['can_check'])

    def test_preflop_no_hand_rank(self):
        data = {
            'phase': 'PRE_FLOP',
            'hand_strength': 'AA - High pocket pair, Top 3%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
            'big_blind': 10,
        }
        ctx = build_poker_context(data)
        self.assertIsNone(ctx['hand_rank'])
        self.assertFalse(ctx['is_strong_hand'])


class TestGate3Skills(unittest.TestCase):
    """Test Gate 3 skill definitions."""

    def test_three_gate3_skills(self):
        skills = get_skills_for_gate(3)
        self.assertEqual(len(skills), 3)
        ids = {s.skill_id for s in skills}
        self.assertEqual(ids, {'draws_need_price', 'respect_big_bets', 'have_a_plan'})

    def test_gate3_skills_in_registry(self):
        self.assertIn('draws_need_price', ALL_SKILLS)
        self.assertIn('respect_big_bets', ALL_SKILLS)
        self.assertIn('have_a_plan', ALL_SKILLS)

    def test_draws_need_price_phases(self):
        skill = ALL_SKILLS['draws_need_price']
        self.assertEqual(skill.phases, frozenset({'FLOP', 'TURN'}))
        self.assertEqual(skill.gate, 3)

    def test_respect_big_bets_phases(self):
        skill = ALL_SKILLS['respect_big_bets']
        self.assertEqual(skill.phases, frozenset({'TURN', 'RIVER'}))

    def test_have_a_plan_phases(self):
        skill = ALL_SKILLS['have_a_plan']
        self.assertEqual(skill.phases, frozenset({'TURN'}))

    def test_gate3_window_size(self):
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            skill = ALL_SKILLS[sid]
            self.assertEqual(skill.evidence_rules.window_size, 30)

    def test_gate3_definition(self):
        gate = ALL_GATES[3]
        self.assertEqual(gate.name, 'Pressure Recognition')
        self.assertEqual(gate.required_reliable, 2)
        self.assertEqual(len(gate.skill_ids), 3)


class TestGate4Skills(unittest.TestCase):
    """Test Gate 4 skill definitions."""

    def test_two_gate4_skills(self):
        skills = get_skills_for_gate(4)
        self.assertEqual(len(skills), 2)
        ids = {s.skill_id for s in skills}
        self.assertEqual(ids, {'dont_pay_double_barrels', 'size_bets_with_purpose'})

    def test_gate4_skills_in_registry(self):
        self.assertIn('dont_pay_double_barrels', ALL_SKILLS)
        self.assertIn('size_bets_with_purpose', ALL_SKILLS)

    def test_dont_pay_double_barrels_phases(self):
        skill = ALL_SKILLS['dont_pay_double_barrels']
        self.assertEqual(skill.phases, frozenset({'TURN', 'RIVER'}))
        self.assertEqual(skill.gate, 4)

    def test_size_bets_with_purpose_phases(self):
        skill = ALL_SKILLS['size_bets_with_purpose']
        self.assertEqual(skill.phases, frozenset({'FLOP', 'TURN', 'RIVER'}))

    def test_gate4_definition(self):
        gate = ALL_GATES[4]
        self.assertEqual(gate.name, 'Multi-Street Thinking')
        self.assertEqual(gate.required_reliable, 2)
        self.assertEqual(len(gate.skill_ids), 2)


class TestBuildPokerContextMultiStreet(unittest.TestCase):
    """Test multi-street context fields in build_poker_context()."""

    def _make_data(self, **kwargs):
        defaults = {
            'phase': 'TURN',
            'hand_strength': 'One Pair',
            'hand_rank': 9,
            'position': 'Button',
            'cost_to_call': 20,
            'pot_total': 100,
            'big_blind': 10,
            'hand_actions': [],
            'player_name': 'Hero',
        }
        defaults.update(kwargs)
        return defaults

    def test_player_bet_flop_detected(self):
        data = self._make_data(hand_actions=[
            {'player_name': 'Hero', 'action': 'bet', 'phase': 'FLOP', 'amount': 20},
        ])
        ctx = build_poker_context(data)
        self.assertTrue(ctx['player_bet_flop'])

    def test_opponent_double_barrel_detected(self):
        data = self._make_data(hand_actions=[
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'FLOP', 'amount': 20},
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'TURN', 'amount': 40},
        ])
        ctx = build_poker_context(data)
        self.assertTrue(ctx['opponent_double_barrel'])
        self.assertTrue(ctx['opponent_bet_turn'])

    def test_no_double_barrel_when_only_flop(self):
        data = self._make_data(hand_actions=[
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'FLOP', 'amount': 20},
        ])
        ctx = build_poker_context(data)
        self.assertFalse(ctx['opponent_double_barrel'])

    def test_empty_hand_actions(self):
        data = self._make_data(hand_actions=[])
        ctx = build_poker_context(data)
        self.assertFalse(ctx['player_bet_flop'])
        self.assertFalse(ctx['opponent_double_barrel'])

    def test_player_name_filtering(self):
        data = self._make_data(hand_actions=[
            {'player_name': 'Villain', 'action': 'bet', 'phase': 'FLOP', 'amount': 20},
        ])
        ctx = build_poker_context(data)
        self.assertFalse(ctx['player_bet_flop'])  # Villain's bet, not Hero's

    def test_equity_passthrough(self):
        data = self._make_data(equity=0.45, required_equity=0.22)
        ctx = build_poker_context(data)
        self.assertEqual(ctx['equity'], 0.45)
        self.assertEqual(ctx['required_equity'], 0.22)

    def test_bet_to_pot_ratio_passthrough(self):
        data = self._make_data(bet_to_pot_ratio=0.5)
        ctx = build_poker_context(data)
        self.assertEqual(ctx['bet_to_pot_ratio'], 0.5)

    def test_missing_player_name(self):
        data = self._make_data(player_name='')
        ctx = build_poker_context(data)
        self.assertFalse(ctx['player_bet_flop'])


class TestCoachingDecision(unittest.TestCase):
    """Test CoachingDecision dataclass."""

    def test_defaults(self):
        cd = CoachingDecision(mode=CoachingMode.LEARN)
        self.assertIsNone(cd.primary_skill_id)
        self.assertEqual(cd.relevant_skill_ids, ())
        self.assertEqual(cd.coaching_prompt, '')


if __name__ == '__main__':
    unittest.main()
