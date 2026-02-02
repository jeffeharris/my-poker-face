"""Tests for the coach progression service — state machine, gate unlocks, persistence."""

import os
import tempfile
import unittest

from poker.repositories import create_repos
from flask_app.services.coach_models import GateProgress, PlayerSkillState, SkillState
from flask_app.services.skill_definitions import ALL_SKILLS
from flask_app.services.coach_progression import CoachProgressionService, SessionMemory
from flask_app.services.situation_classifier import SituationClassification
from flask_app.services.skill_evaluator import SkillEvaluation


class TestCoachProgressionWithDB(unittest.TestCase):
    """Integration tests with a real SQLite database."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
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
        self.coach_repo.save_skill_state(self.user_id, ss)
        loaded = self.coach_repo.load_skill_state(self.user_id, 'fold_trash_hands')
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.state, SkillState.PRACTICING)
        self.assertEqual(loaded.total_opportunities, 10)
        self.assertEqual(loaded.window_correct, 8)

    def test_gate_progress_persistence(self):
        gp = GateProgress(gate_number=1, unlocked=True, unlocked_at='2024-01-01T00:00:00')
        self.coach_repo.save_gate_progress(self.user_id, gp)
        loaded = self.coach_repo.load_gate_progress(self.user_id)
        self.assertIn(1, loaded)
        self.assertTrue(loaded[1].unlocked)
        self.assertEqual(loaded[1].unlocked_at, '2024-01-01T00:00:00')

    def test_coach_profile_persistence(self):
        self.coach_repo.save_coach_profile(self.user_id, 'beginner', 'beginner')
        loaded = self.coach_repo.load_coach_profile(self.user_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['effective_level'], 'beginner')

    def test_load_all_skill_states(self):
        for skill_id in ['fold_trash_hands', 'position_matters', 'raise_or_fold']:
            ss = PlayerSkillState(skill_id=skill_id, total_opportunities=5)
            self.coach_repo.save_skill_state(self.user_id, ss)

        all_states = self.coach_repo.load_all_skill_states(self.user_id)
        self.assertEqual(len(all_states), 3)
        self.assertIn('fold_trash_hands', all_states)

    def test_load_nonexistent_skill_state(self):
        result = self.coach_repo.load_skill_state(self.user_id, 'nonexistent')
        self.assertIsNone(result)

    def test_load_nonexistent_profile(self):
        result = self.coach_repo.load_coach_profile('nonexistent_user')
        self.assertIsNone(result)


class TestCoachingDecision(unittest.TestCase):
    """Test coaching decision generation."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
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

    def test_silent_mode_for_postflop_gate1_only(self):
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
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)

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
        # 20/25 = 0.8, int(0.8 * 20) = 16
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

    def test_trim_uses_floor_not_round(self):
        """Verify int() floors — conservative rounding (M1 fix)."""
        # 15/20 = 0.75, 0.75 * 20 = 15.0 (exact)
        ss = PlayerSkillState(
            skill_id='test',
            window_opportunities=21,
            window_correct=16,
        )
        trimmed = self.service._trim_window(ss, 20)
        # 16/21 = 0.7619, int(0.7619 * 20) = int(15.238) = 15
        self.assertEqual(trimmed.window_correct, 15)


class TestGateUnlock(unittest.TestCase):
    """Test gate unlocking and skill initialization."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_gate_user'
        self.service.initialize_player(self.user_id)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_gate2_unlocks_when_gate1_skills_reliable(self):
        """Gate 2 should unlock when 2+ Gate 1 skills reach Reliable."""
        # Manually set 2 Gate 1 skills to reliable
        for skill_id in ('fold_trash_hands', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=skill_id,
                state=SkillState.RELIABLE,
                total_opportunities=20,
                total_correct=18,
                window_opportunities=20,
                window_correct=18,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        self.service.check_hand_end(self.user_id)

        gate_progress = self.coach_repo.load_gate_progress(self.user_id)
        self.assertIn(2, gate_progress)
        self.assertTrue(gate_progress[2].unlocked)

        # Gate 2 skills should be initialized as INTRODUCED
        skill_states = self.coach_repo.load_all_skill_states(self.user_id)
        for sid in ('flop_connection', 'bet_when_strong', 'checking_is_allowed'):
            self.assertIn(sid, skill_states)
            self.assertEqual(skill_states[sid].state, SkillState.INTRODUCED)

    def test_gate2_does_not_unlock_with_one_reliable(self):
        """Gate 2 needs 2 reliable skills, not just 1."""
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.RELIABLE,
            total_opportunities=20,
            total_correct=18,
            window_opportunities=20,
            window_correct=18,
        )
        self.coach_repo.save_skill_state(self.user_id, ss)

        self.service.check_hand_end(self.user_id)

        gate_progress = self.coach_repo.load_gate_progress(self.user_id)
        self.assertNotIn(2, gate_progress)

    def test_gate_does_not_unlock_mid_hand(self):
        """evaluate_and_update alone should NOT trigger gate unlocks."""
        # Set 2 Gate 1 skills to just below reliable threshold
        for skill_id in ('fold_trash_hands', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=skill_id,
                state=SkillState.PRACTICING,
                total_opportunities=11,
                total_correct=11,
                window_opportunities=11,
                window_correct=11,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

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

        # This 12th correct should advance fold_trash to reliable,
        # but gate unlock should NOT happen yet
        self.service.evaluate_and_update(
            self.user_id, 'fold', coaching_data, classification
        )

        gate_progress = self.coach_repo.load_gate_progress(self.user_id)
        self.assertNotIn(2, gate_progress)

    def test_gate_unlocks_on_check_hand_end(self):
        """Gate unlocks when check_hand_end() is called after sufficient evaluations."""
        for skill_id in ('fold_trash_hands', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=skill_id,
                state=SkillState.RELIABLE,
                total_opportunities=20,
                total_correct=18,
                window_opportunities=20,
                window_correct=18,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        # evaluate_and_update alone — no gate unlock
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
        self.service.evaluate_and_update(
            self.user_id, 'fold', coaching_data, classification
        )
        gate_progress = self.coach_repo.load_gate_progress(self.user_id)
        self.assertNotIn(2, gate_progress)

        # Now call check_hand_end — gate should unlock
        self.service.check_hand_end(self.user_id)
        gate_progress = self.coach_repo.load_gate_progress(self.user_id)
        self.assertIn(2, gate_progress)
        self.assertTrue(gate_progress[2].unlocked)


class TestSessionMemory(unittest.TestCase):
    """Test SessionMemory for coaching cadence."""

    def test_new_hand_clears_coached_skills(self):
        mem = SessionMemory()
        mem.record_coaching('fold_trash_hands')
        self.assertTrue(mem.was_coached_this_hand('fold_trash_hands'))

        mem.new_hand(2)
        self.assertFalse(mem.was_coached_this_hand('fold_trash_hands'))

    def test_same_hand_does_not_clear(self):
        mem = SessionMemory()
        mem.current_hand_number = 1
        mem.record_coaching('fold_trash_hands')
        mem.new_hand(1)  # same hand
        self.assertTrue(mem.was_coached_this_hand('fold_trash_hands'))

    def test_concept_count_persists_across_hands(self):
        mem = SessionMemory()
        for i in range(4):
            mem.new_hand(i + 1)
            mem.record_coaching('fold_trash_hands')

        self.assertEqual(mem.concept_count['fold_trash_hands'], 4)
        self.assertTrue(mem.should_shorten('fold_trash_hands'))

    def test_should_shorten_false_under_threshold(self):
        mem = SessionMemory()
        mem.record_coaching('fold_trash_hands')
        mem.record_coaching('fold_trash_hands')
        self.assertFalse(mem.should_shorten('fold_trash_hands'))

    def test_should_shorten_true_at_threshold(self):
        mem = SessionMemory()
        for _ in range(3):
            mem.record_coaching('fold_trash_hands')
        self.assertTrue(mem.should_shorten('fold_trash_hands'))


class TestSessionMemoryCadence(unittest.TestCase):
    """Test coaching cadence integration with session memory."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_cadence_user'
        self.service.initialize_player(self.user_id)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_practicing_skill_suppressed_second_time_same_hand(self):
        """Practicing skills should only be coached once per hand."""
        # Advance fold_trash to practicing
        for skill_id in ('fold_trash_hands', 'position_matters', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=skill_id,
                state=SkillState.PRACTICING,
                total_opportunities=5,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        state = self.service.get_player_state(self.user_id)
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
        }

        mem = SessionMemory()
        # First call should coach
        d1 = self.service.get_coaching_decision(
            self.user_id, coaching_data,
            state['skill_states'], state['gate_progress'],
            session_memory=mem, hand_number=1,
        )
        self.assertNotEqual(d1.mode.value, 'silent')

        # Second call same hand — primary skill was already coached
        d2 = self.service.get_coaching_decision(
            self.user_id, coaching_data,
            state['skill_states'], state['gate_progress'],
            session_memory=mem, hand_number=1,
        )
        # Should be silent since the primary was already coached this hand
        self.assertEqual(d2.mode.value, 'silent')

    def test_reliable_skill_suppressed_pre_action(self):
        """Reliable skills should be silent pre-action (only coach on deviation post-action)."""
        for skill_id in ('fold_trash_hands', 'position_matters', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=skill_id,
                state=SkillState.RELIABLE,
                total_opportunities=20,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        state = self.service.get_player_state(self.user_id)
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
        }

        mem = SessionMemory()
        decision = self.service.get_coaching_decision(
            self.user_id, coaching_data,
            state['skill_states'], state['gate_progress'],
            session_memory=mem, hand_number=1,
        )
        self.assertEqual(decision.mode.value, 'silent')

    def test_shorten_prompt_after_repeated_coaching(self):
        """After 3+ explanations of same concept, prompt should include BREVITY."""
        state = self.service.get_player_state(self.user_id)
        coaching_data = {
            'phase': 'PRE_FLOP',
            'hand_strength': '72o - Unconnected cards, Bottom 10%',
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 30,
        }

        mem = SessionMemory()
        # Simulate 3 prior explanations
        for _ in range(3):
            mem.record_coaching('fold_trash_hands')

        mem.new_hand(4)
        decision = self.service.get_coaching_decision(
            self.user_id, coaching_data,
            state['skill_states'], state['gate_progress'],
            session_memory=mem, hand_number=4,
        )
        if decision.mode.value != 'silent':
            self.assertIn('BREVITY', decision.coaching_prompt)


class TestOnboarding(unittest.TestCase):
    """Test self-reported starting level initialization."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_onboarding_user'

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_beginner_initialization(self):
        state = self.service.initialize_player(self.user_id, level='beginner')
        self.assertEqual(state['profile']['effective_level'], 'beginner')
        self.assertEqual(len(state['skill_states']), 3)
        for ss in state['skill_states'].values():
            self.assertEqual(ss.state, SkillState.INTRODUCED)
        self.assertNotIn(2, state['gate_progress'])

    def test_intermediate_initialization(self):
        state = self.service.initialize_player(self.user_id, level='intermediate')
        self.assertEqual(state['profile']['effective_level'], 'intermediate')

        # Gate 1 skills at Practicing
        gate1_skills = ('fold_trash_hands', 'position_matters', 'raise_or_fold')
        for sid in gate1_skills:
            self.assertEqual(state['skill_states'][sid].state, SkillState.PRACTICING)

        # Gate 2 unlocked with skills at Introduced
        self.assertIn(2, state['gate_progress'])
        self.assertTrue(state['gate_progress'][2].unlocked)
        gate2_skills = ('flop_connection', 'bet_when_strong', 'checking_is_allowed')
        for sid in gate2_skills:
            self.assertIn(sid, state['skill_states'])
            self.assertEqual(state['skill_states'][sid].state, SkillState.INTRODUCED)

    def test_experienced_initialization(self):
        state = self.service.initialize_player(self.user_id, level='experienced')
        self.assertEqual(state['profile']['effective_level'], 'experienced')

        # Gate 1 skills at Reliable
        for sid in ('fold_trash_hands', 'position_matters', 'raise_or_fold'):
            self.assertEqual(state['skill_states'][sid].state, SkillState.RELIABLE)

        # Gate 2 skills at Practicing
        for sid in ('flop_connection', 'bet_when_strong', 'checking_is_allowed'):
            self.assertEqual(state['skill_states'][sid].state, SkillState.PRACTICING)

        # Gate 3 unlocked with skills at Introduced
        self.assertIn(3, state['gate_progress'])
        self.assertTrue(state['gate_progress'][3].unlocked)
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            self.assertIn(sid, state['skill_states'])
            self.assertEqual(state['skill_states'][sid].state, SkillState.INTRODUCED)


class TestSilentDowngrade(unittest.TestCase):
    """Test silent downgrade when observed play contradicts self-reported level."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_downgrade_user'

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_experienced_downgrades_to_beginner(self):
        """Experienced player with all Gate 1 at Practicing → beginner."""
        self.service.initialize_player(self.user_id, level='experienced')

        # Set all gate 1 skills to practicing with enough observations
        for sid in ('fold_trash_hands', 'position_matters', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=sid,
                state=SkillState.PRACTICING,
                total_opportunities=10,
                total_correct=5,
                window_opportunities=10,
                window_correct=5,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        self.service.check_hand_end(self.user_id)

        profile = self.coach_repo.load_coach_profile(self.user_id)
        self.assertEqual(profile['effective_level'], 'beginner')
        # Self-reported level should be preserved
        self.assertEqual(profile['self_reported_level'], 'experienced')

    def test_experienced_downgrades_to_intermediate(self):
        """Experienced player with Gate 2 at Practicing → intermediate."""
        self.service.initialize_player(self.user_id, level='experienced')

        # Keep gate 1 skills at reliable (from experienced init)
        # Set gate 2 skills to practicing with enough observations
        for sid in ('flop_connection', 'bet_when_strong', 'checking_is_allowed'):
            ss = PlayerSkillState(
                skill_id=sid,
                state=SkillState.PRACTICING,
                total_opportunities=10,
                total_correct=5,
                window_opportunities=10,
                window_correct=5,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        self.service.check_hand_end(self.user_id)

        profile = self.coach_repo.load_coach_profile(self.user_id)
        self.assertEqual(profile['effective_level'], 'intermediate')

    def test_intermediate_downgrades_to_beginner(self):
        """Intermediate player with Gate 1 at Practicing → beginner."""
        self.service.initialize_player(self.user_id, level='intermediate')

        for sid in ('fold_trash_hands', 'position_matters', 'raise_or_fold'):
            ss = PlayerSkillState(
                skill_id=sid,
                state=SkillState.PRACTICING,
                total_opportunities=10,
                total_correct=5,
                window_opportunities=10,
                window_correct=5,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

        self.service.check_hand_end(self.user_id)

        profile = self.coach_repo.load_coach_profile(self.user_id)
        self.assertEqual(profile['effective_level'], 'beginner')

    def test_beginner_never_downgrades(self):
        """Beginner level should never trigger downgrade."""
        self.service.initialize_player(self.user_id, level='beginner')
        self.service.check_hand_end(self.user_id)
        profile = self.coach_repo.load_coach_profile(self.user_id)
        self.assertEqual(profile['effective_level'], 'beginner')

    def test_no_downgrade_without_enough_data(self):
        """Downgrade should not trigger with insufficient observations."""
        self.service.initialize_player(self.user_id, level='experienced')

        # Only set 1 skill with enough data (needs 2)
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.PRACTICING,
            total_opportunities=10,
        )
        self.coach_repo.save_skill_state(self.user_id, ss)

        self.service.check_hand_end(self.user_id)

        profile = self.coach_repo.load_coach_profile(self.user_id)
        self.assertEqual(profile['effective_level'], 'experienced')


class TestGateUnlockChain(unittest.TestCase):
    """Test gate unlock chain 1→2→3→4."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_chain_user'
        self.service.initialize_player(self.user_id)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _set_skills_reliable(self, skill_ids):
        for sid in skill_ids:
            ss = PlayerSkillState(
                skill_id=sid,
                state=SkillState.RELIABLE,
                total_opportunities=20,
                total_correct=18,
                window_opportunities=20,
                window_correct=18,
            )
            self.coach_repo.save_skill_state(self.user_id, ss)

    def test_gate_chain_1_to_4(self):
        """Full chain: Gate 1 reliable → Gate 2 unlocks → ... → Gate 4 unlocks."""
        # Gate 1 → 2
        self._set_skills_reliable(['fold_trash_hands', 'raise_or_fold'])
        self.service.check_hand_end(self.user_id)
        gp = self.coach_repo.load_gate_progress(self.user_id)
        self.assertTrue(gp[2].unlocked)

        # Gate 2 → 3
        self._set_skills_reliable(['flop_connection', 'bet_when_strong'])
        self.service.check_hand_end(self.user_id)
        gp = self.coach_repo.load_gate_progress(self.user_id)
        self.assertTrue(gp[3].unlocked)

        # Gate 3 → 4
        self._set_skills_reliable(['draws_need_price', 'respect_big_bets'])
        self.service.check_hand_end(self.user_id)
        gp = self.coach_repo.load_gate_progress(self.user_id)
        self.assertTrue(gp[4].unlocked)

    def test_gate3_skills_initialized_on_unlock(self):
        """Gate 3 skills should be initialized as INTRODUCED when Gate 3 unlocks."""
        self._set_skills_reliable(['flop_connection', 'bet_when_strong'])
        # Must also unlock gate 2 first
        from flask_app.services.coach_models import GateProgress
        self.coach_repo.save_gate_progress(
            self.user_id, GateProgress(gate_number=2, unlocked=True, unlocked_at='now')
        )
        self.service.check_hand_end(self.user_id)

        skill_states = self.coach_repo.load_all_skill_states(self.user_id)
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            self.assertIn(sid, skill_states)
            self.assertEqual(skill_states[sid].state, SkillState.INTRODUCED)


class TestSkillVersioning(unittest.TestCase):
    """Test skill definition versioning for existing players."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_versioning_user'

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_missing_skills_auto_initialized(self):
        """Skills added to unlocked gate should be auto-initialized."""
        # Simulate player with Gate 3 unlocked but no Gate 3 skill rows
        self.service.initialize_player(self.user_id, level='experienced')

        # Remove Gate 3 skill rows to simulate pre-M3 state
        # (experienced init now creates them, so we remove them to test versioning)
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            # Delete from DB by saving with state INTRODUCED then removing
            pass

        # Actually, test the versioning by adding a gate 3 unlock without skills
        user2 = 'test_versioning_user2'
        self.coach_repo.save_coach_profile(user2, 'experienced', 'experienced')
        from flask_app.services.coach_models import GateProgress
        self.coach_repo.save_gate_progress(
            user2, GateProgress(gate_number=1, unlocked=True, unlocked_at='now')
        )
        self.coach_repo.save_gate_progress(
            user2, GateProgress(gate_number=2, unlocked=True, unlocked_at='now')
        )
        self.coach_repo.save_gate_progress(
            user2, GateProgress(gate_number=3, unlocked=True, unlocked_at='now')
        )

        # Load state — should auto-initialize missing skills
        state = self.service.get_player_state(user2)
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            self.assertIn(sid, state['skill_states'])
            self.assertEqual(state['skill_states'][sid].state, SkillState.INTRODUCED)

    def test_passed_gate_skills_start_at_practicing(self):
        """Skills in passed gates should start at Practicing."""
        user = 'test_passed_gate_user'
        self.coach_repo.save_coach_profile(user, 'experienced', 'experienced')
        from flask_app.services.coach_models import GateProgress
        # Gates 1, 2, 3 unlocked; gate 3 is "passed" because gate 4 is also unlocked
        for g in (1, 2, 3, 4):
            self.coach_repo.save_gate_progress(
                user, GateProgress(gate_number=g, unlocked=True, unlocked_at='now')
            )

        state = self.service.get_player_state(user)
        # Gate 1, 2, 3 skills should be at Practicing (passed gates)
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            self.assertIn(sid, state['skill_states'])
            self.assertEqual(state['skill_states'][sid].state, SkillState.PRACTICING)
        # Gate 4 skills should be at Introduced (current gate)
        for sid in ('dont_pay_double_barrels', 'size_bets_with_purpose'):
            self.assertIn(sid, state['skill_states'])
            self.assertEqual(state['skill_states'][sid].state, SkillState.INTRODUCED)


class TestPracticingModeSplit(unittest.TestCase):
    """Test practicing mode split at 60% accuracy."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_mode_split_user'
        self.service.initialize_player(self.user_id)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_practicing_below_60_returns_learn(self):
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.PRACTICING,
            window_opportunities=10,
            window_correct=5,  # 50% accuracy
        )
        self.coach_repo.save_skill_state(self.user_id, ss)

        from flask_app.services.coach_models import CoachingMode
        mode = self.service._determine_mode(ss)
        self.assertEqual(mode, CoachingMode.LEARN)

    def test_practicing_at_60_returns_compete(self):
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.PRACTICING,
            window_opportunities=10,
            window_correct=6,  # 60% accuracy
        )
        from flask_app.services.coach_models import CoachingMode
        mode = self.service._determine_mode(ss)
        self.assertEqual(mode, CoachingMode.COMPETE)

    def test_practicing_above_60_returns_compete(self):
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.PRACTICING,
            window_opportunities=10,
            window_correct=8,  # 80% accuracy
        )
        from flask_app.services.coach_models import CoachingMode
        mode = self.service._determine_mode(ss)
        self.assertEqual(mode, CoachingMode.COMPETE)

    def test_introduced_always_learn(self):
        ss = PlayerSkillState(
            skill_id='fold_trash_hands',
            state=SkillState.INTRODUCED,
        )
        from flask_app.services.coach_models import CoachingMode
        mode = self.service._determine_mode(ss)
        self.assertEqual(mode, CoachingMode.LEARN)


class TestSessionMemoryHandEvaluations(unittest.TestCase):
    """Test SessionMemory hand evaluation tracking."""

    def test_record_and_retrieve(self):
        mem = SessionMemory()
        ev1 = SkillEvaluation(
            skill_id='fold_trash_hands', action_taken='fold',
            evaluation='correct', confidence=1.0, reasoning='Good fold',
        )
        ev2 = SkillEvaluation(
            skill_id='position_matters', action_taken='call',
            evaluation='incorrect', confidence=0.8, reasoning='Bad call',
        )
        mem.record_hand_evaluation(1, ev1)
        mem.record_hand_evaluation(1, ev2)

        evals = mem.get_hand_evaluations(1)
        self.assertEqual(len(evals), 2)
        # incorrect should sort first
        self.assertEqual(evals[0].evaluation, 'incorrect')
        self.assertEqual(evals[1].evaluation, 'correct')

    def test_not_applicable_filtered(self):
        mem = SessionMemory()
        ev = SkillEvaluation(
            skill_id='fold_trash_hands', action_taken='fold',
            evaluation='not_applicable', confidence=1.0, reasoning='N/A',
        )
        mem.record_hand_evaluation(1, ev)
        evals = mem.get_hand_evaluations(1)
        self.assertEqual(len(evals), 0)

    def test_empty_hand(self):
        mem = SessionMemory()
        evals = mem.get_hand_evaluations(99)
        self.assertEqual(evals, [])

    def test_multiple_hands_independent(self):
        mem = SessionMemory()
        ev1 = SkillEvaluation(
            skill_id='fold_trash_hands', action_taken='fold',
            evaluation='correct', confidence=1.0, reasoning='Good',
        )
        ev2 = SkillEvaluation(
            skill_id='position_matters', action_taken='call',
            evaluation='incorrect', confidence=0.8, reasoning='Bad',
        )
        mem.record_hand_evaluation(1, ev1)
        mem.record_hand_evaluation(2, ev2)

        self.assertEqual(len(mem.get_hand_evaluations(1)), 1)
        self.assertEqual(len(mem.get_hand_evaluations(2)), 1)


class TestExperiencedOnboardingGate3(unittest.TestCase):
    """Test experienced onboarding includes Gate 3."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_exp_g3_user'

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_experienced_has_gate3_unlocked(self):
        state = self.service.initialize_player(self.user_id, level='experienced')
        self.assertIn(3, state['gate_progress'])
        self.assertTrue(state['gate_progress'][3].unlocked)

    def test_experienced_has_gate3_skills_introduced(self):
        state = self.service.initialize_player(self.user_id, level='experienced')
        for sid in ('draws_need_price', 'respect_big_bets', 'have_a_plan'):
            self.assertIn(sid, state['skill_states'])
            self.assertEqual(state['skill_states'][sid].state, SkillState.INTRODUCED)

    def test_experienced_gate4_not_unlocked(self):
        state = self.service.initialize_player(self.user_id, level='experienced')
        self.assertNotIn(4, state['gate_progress'])

    def test_beginner_no_gate3(self):
        state = self.service.initialize_player(self.user_id, level='beginner')
        self.assertNotIn(3, state['gate_progress'])

    def test_intermediate_no_gate3(self):
        state = self.service.initialize_player(self.user_id, level='intermediate')
        self.assertNotIn(3, state['gate_progress'])


class TestMarginalNeutrality(unittest.TestCase):
    """Test that marginal evaluations have no progression effect."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_marginal_user'
        self.service.initialize_player(self.user_id)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_marginal_does_not_increment_opportunities(self):
        """Marginal evaluations should not change opportunity or correct counts."""
        # Advance to practicing first (need 3 correct)
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

        # Do 3 correct to get to practicing
        for _ in range(3):
            self.service.evaluate_and_update(
                self.user_id, 'fold', coaching_data, classification
            )

        state_before = self.service.get_player_state(self.user_id)
        ss_before = state_before['skill_states']['fold_trash_hands']
        self.assertEqual(ss_before.total_opportunities, 3)

        # Now do 5 marginal (check) evaluations
        for _ in range(5):
            self.service.evaluate_and_update(
                self.user_id, 'check', coaching_data, classification
            )

        state_after = self.service.get_player_state(self.user_id)
        ss_after = state_after['skill_states']['fold_trash_hands']

        # Opportunities and correct counts should be unchanged
        self.assertEqual(ss_after.total_opportunities, ss_before.total_opportunities)
        self.assertEqual(ss_after.total_correct, ss_before.total_correct)
        self.assertEqual(ss_after.window_opportunities, ss_before.window_opportunities)
        self.assertEqual(ss_after.window_correct, ss_before.window_correct)
        # State should remain practicing (not regressed or advanced)
        self.assertEqual(ss_after.state, SkillState.PRACTICING)

    def test_marginal_does_not_prevent_advancement(self):
        """Marginal evals between correct evals shouldn't dilute accuracy."""
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

        # Alternate: correct, marginal, correct, marginal, ...
        for i in range(24):
            action = 'fold' if i % 2 == 0 else 'check'
            self.service.evaluate_and_update(
                self.user_id, action, coaching_data, classification
            )

        state = self.service.get_player_state(self.user_id)
        ss = state['skill_states']['fold_trash_hands']
        # 12 correct, 0 incorrect (marginals don't count)
        self.assertEqual(ss.total_opportunities, 12)
        self.assertEqual(ss.total_correct, 12)
        # Should have advanced to reliable (12 opps, 100% accuracy)
        self.assertEqual(ss.state, SkillState.RELIABLE)


class TestOverlapEvaluation(unittest.TestCase):
    """Test that overlapping skills both get evaluated."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        repos = create_repos(self.db_path)
        self.coach_repo = repos['coach_repo']
        self.service = CoachProgressionService(self.coach_repo)
        self.user_id = 'test_overlap_user'
        self.service.initialize_player(self.user_id, level='intermediate')

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_both_skills_evaluated_on_overlap(self):
        """When flop_connection and checking_is_allowed both trigger, both are evaluated."""
        classification = SituationClassification(
            relevant_skills=('flop_connection', 'checking_is_allowed'),
            primary_skill='flop_connection',
            situation_tags=('air',),
        )
        coaching_data = {
            'phase': 'FLOP',
            'hand_strength': 'High Card',
            'hand_rank': 10,
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 100,
            'big_blind': 10,
            'outs': 2,
        }

        evaluations = self.service.evaluate_and_update(
            self.user_id, 'check', coaching_data, classification
        )

        eval_skills = {e.skill_id for e in evaluations}
        # Both skills should have been evaluated (not filtered out)
        # flop_connection: check with air → marginal (skipped from progression)
        # checking_is_allowed: check with weak hand → correct
        self.assertIn('checking_is_allowed', eval_skills)

        # checking_is_allowed should have recorded an opportunity
        state = self.service.get_player_state(self.user_id)
        ss_check = state['skill_states']['checking_is_allowed']
        self.assertEqual(ss_check.total_opportunities, 1)
        self.assertEqual(ss_check.total_correct, 1)

    def test_fold_is_correct_for_both_overlapping_skills(self):
        """Folding air when can check is correct for both skills."""
        classification = SituationClassification(
            relevant_skills=('flop_connection', 'checking_is_allowed'),
            primary_skill='flop_connection',
            situation_tags=('air',),
        )
        coaching_data = {
            'phase': 'FLOP',
            'hand_strength': 'High Card',
            'hand_rank': 10,
            'position': 'Button',
            'cost_to_call': 0,
            'pot_total': 100,
            'big_blind': 10,
            'outs': 2,
        }

        evaluations = self.service.evaluate_and_update(
            self.user_id, 'fold', coaching_data, classification
        )

        # Both should be correct
        for ev in evaluations:
            self.assertEqual(ev.evaluation, 'correct',
                             f'{ev.skill_id} expected correct, got {ev.evaluation}')

        # Both should have recorded opportunities
        state = self.service.get_player_state(self.user_id)
        for sid in ('flop_connection', 'checking_is_allowed'):
            ss = state['skill_states'][sid]
            self.assertEqual(ss.total_opportunities, 1)
            self.assertEqual(ss.total_correct, 1)


if __name__ == '__main__':
    unittest.main()
