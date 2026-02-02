"""Tests for the coach progression service — state machine, gate unlocks, persistence."""

import os
import tempfile
import unittest

from poker.persistence import GamePersistence
from flask_app.services.skill_definitions import (
    ALL_SKILLS, GateProgress, PlayerSkillState, SkillState,
)
from flask_app.services.coach_progression import CoachProgressionService
from flask_app.services.situation_classifier import SituationClassification
from flask_app.services.skill_evaluator import SkillEvaluation


class TestCoachProgressionWithDB(unittest.TestCase):
    """Integration tests with a real SQLite database."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        self.persistence = GamePersistence(self.db_path)
        self.service = CoachProgressionService(self.persistence)
        self.user_id = 'test_user_123'

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    # ---- initialization ----

    def test_initialize_player(self):
        state = self.service.initialize_player(self.user_id)
        self.assertIsNotNone(state['profile'])
        self.assertEqual(state['profile']['effective_level'], 'beginner')
        self.assertIn(1, state['gate_progress'])
        self.assertTrue(state['gate_progress'][1].unlocked)
        self.assertEqual(len(state['skill_states']), 3)
        for ss in state['skill_states'].values():
            self.assertEqual(ss.state, SkillState.INTRODUCED)

    def test_get_player_state_empty(self):
        state = self.service.get_player_state(self.user_id)
        self.assertIsNone(state['profile'])
        self.assertEqual(state['skill_states'], {})
        self.assertEqual(state['gate_progress'], {})

    # ---- state transitions ----

    def test_introduced_to_practicing(self):
        self.service.initialize_player(self.user_id)

        # Simulate 3 correct evaluations to move from introduced -> practicing
        classification = SituationClassification(
            relevant_skills=('fold_trash_hands',),
            primary_skill='fold_trash_hands',
            situation_tags=('trash_hand',),
        )
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
        }

        for _ in range(3):
            self.service.evaluate_and_update(
                self.user_id, 'fold', coaching_data, classification
            )

        state = self.service.get_player_state(self.user_id)
        ss = state['skill_states']['fold_trash_hands']
        self.assertEqual(ss.state, SkillState.PRACTICING)
        self.assertEqual(ss.total_opportunities, 3)
        self.assertEqual(ss.total_correct, 3)

    def test_practicing_to_reliable(self):
        self.service.initialize_player(self.user_id)

        classification = SituationClassification(
            relevant_skills=('fold_trash_hands',),
            primary_skill='fold_trash_hands',
            situation_tags=('trash_hand',),
        )
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
        }

        # Need 12+ opportunities with >= 75% accuracy
        # Do 12 correct actions (100% accuracy)
        for _ in range(12):
            self.service.evaluate_and_update(
                self.user_id, 'fold', coaching_data, classification
            )

        state = self.service.get_player_state(self.user_id)
        ss = state['skill_states']['fold_trash_hands']
        self.assertEqual(ss.state, SkillState.RELIABLE)

    def test_regression_reliable_to_practicing(self):
        """Test that consistent incorrect actions cause regression."""
        self.service.initialize_player(self.user_id)

        classification = SituationClassification(
            relevant_skills=('fold_trash_hands',),
            primary_skill='fold_trash_hands',
            situation_tags=('trash_hand',),
        )
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
        }

        # First advance to reliable (12 correct)
        for _ in range(12):
            self.service.evaluate_and_update(
                self.user_id, 'fold', coaching_data, classification
            )

        state = self.service.get_player_state(self.user_id)
        self.assertEqual(state['skill_states']['fold_trash_hands'].state, SkillState.RELIABLE)

        # Now do many incorrect actions to trigger regression
        # Need window_accuracy < 0.60 with enough opportunities
        # Window size is 20, currently 12 correct out of 12
        # Need to add enough incorrect to push below 60%
        # After 12 more incorrect: window is ~20, with ~12 of 24 total but window trimmed
        for _ in range(15):
            self.service.evaluate_and_update(
                self.user_id, 'call', coaching_data, classification
            )

        state = self.service.get_player_state(self.user_id)
        ss = state['skill_states']['fold_trash_hands']
        # Should have regressed back to practicing
        self.assertEqual(ss.state, SkillState.PRACTICING)

    # ---- persistence round-trip ----

    def test_skill_state_persistence(self):
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.PRACTICING,
            total_opportunities=10,
            total_correct=8,
            window_opportunities=10,
            window_correct=8,
            streak_correct=3,
        )
        self.persistence.save_skill_state(self.user_id, ss)
        loaded = self.persistence.load_skill_state(self.user_id, 'fold_trash_hands')
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.state, SkillState.PRACTICING)
        self.assertEqual(loaded.total_opportunities, 10)
        self.assertEqual(loaded.window_correct, 8)

    def test_gate_progress_persistence(self):
        gp = GateProgress(gate_number=1, unlocked=True, unlocked_at='2024-01-01T00:00:00')
        self.persistence.save_gate_progress(self.user_id, gp)
        loaded = self.persistence.load_gate_progress(self.user_id)
        self.assertIn(1, loaded)
        self.assertTrue(loaded[1].unlocked)
        self.assertEqual(loaded[1].unlocked_at, '2024-01-01T00:00:00')

    def test_coach_profile_persistence(self):
        self.persistence.save_coach_profile(self.user_id, 'beginner', 'beginner')
        loaded = self.persistence.load_coach_profile(self.user_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['effective_level'], 'beginner')

    def test_load_all_skill_states(self):
        for skill_id in ['fold_trash_hands', 'position_matters', 'raise_or_fold']:
            ss = PlayerSkillState(skill_id=skill_id, total_opportunities=5)
            self.persistence.save_skill_state(self.user_id, ss)

        all_states = self.persistence.load_all_skill_states(self.user_id)
        self.assertEqual(len(all_states), 3)
        self.assertIn('fold_trash_hands', all_states)

    def test_load_nonexistent_skill_state(self):
        result = self.persistence.load_skill_state(self.user_id, 'nonexistent')
        self.assertIsNone(result)

    def test_load_nonexistent_profile(self):
        result = self.persistence.load_coach_profile('nonexistent_user')
        self.assertIsNone(result)


class TestCoachingDecision(unittest.TestCase):
    """Test coaching decision generation."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        self.persistence = GamePersistence(self.db_path)
        self.service = CoachProgressionService(self.persistence)
        self.user_id = 'test_user_456'
        self.service.initialize_player(self.user_id)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_learn_mode_for_introduced_skill(self):
        state = self.service.get_player_state(self.user_id)
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Under The Gun',
            'cost_to_call': 0,
            'pot_total': 30,
        }
        decision = self.service.get_coaching_decision(
            self.user_id, coaching_data,
            state['skill_states'], state['gate_progress'],
        )
        self.assertEqual(decision.mode.value, 'learn')
        self.assertIsNotNone(decision.primary_skill_id)
        self.assertTrue(len(decision.relevant_skill_ids) > 0)

    def test_silent_mode_for_postflop(self):
        state = self.service.get_player_state(self.user_id)
        coaching_data = {
            'phase': 'FLOP',
            'hand_strength': 'Two Pair',
            'position': 'Button',
            'cost_to_call': 20,
            'pot_total': 100,
        }
        decision = self.service.get_coaching_decision(
            self.user_id, coaching_data,
            state['skill_states'], state['gate_progress'],
        )
        self.assertEqual(decision.mode.value, 'silent')


class TestWindowTrimming(unittest.TestCase):
    """Test rolling window trimming behavior."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        self.persistence = GamePersistence(self.db_path)
        self.service = CoachProgressionService(self.persistence)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_window_does_not_exceed_size(self):
        """Window should be trimmed to window_size."""
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            window_opportunities=25,
            window_correct=20,
        )
        trimmed = self.service._trim_window(ss, 20)
        self.assertEqual(trimmed.window_opportunities, 20)
        # 20/25 = 0.8, so window_correct = round(0.8 * 20) = 16
        self.assertEqual(trimmed.window_correct, 16)

    def test_window_no_trim_when_under_size(self):
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            window_opportunities=10,
            window_correct=8,
        )
        result = self.service._trim_window(ss, 20)
        self.assertEqual(result.window_opportunities, 10)
        self.assertEqual(result.window_correct, 8)


if __name__ == '__main__':
    unittest.main()
