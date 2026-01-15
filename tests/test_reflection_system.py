"""
Tests for the Player Memory & Reflection System.

Tests cover:
- DecisionPlan capture and serialization
- HandCommentary extended fields
- OpponentModel narrative observations
- SessionMemory reflections
- Commentary persistence
"""

import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from poker.memory.commentary_generator import DecisionPlan, HandCommentary
from poker.memory.session_memory import SessionMemory
from poker.memory.opponent_model import OpponentModel, OpponentModelManager
from poker.persistence import GamePersistence
from poker.prompt_config import PromptConfig


class TestDecisionPlan(unittest.TestCase):
    """Test the DecisionPlan dataclass."""

    def test_creation(self):
        """Test creating a DecisionPlan."""
        plan = DecisionPlan(
            hand_number=1,
            phase='FLOP',
            player_name='TestPlayer',
            hand_strategy='Check-raise for value',
            inner_monologue='I think opponent is weak',
            action='raise',
            amount=100,
            pot_size=200
        )

        self.assertEqual(plan.hand_number, 1)
        self.assertEqual(plan.phase, 'FLOP')
        self.assertEqual(plan.player_name, 'TestPlayer')
        self.assertEqual(plan.hand_strategy, 'Check-raise for value')
        self.assertEqual(plan.action, 'raise')
        self.assertEqual(plan.amount, 100)
        self.assertEqual(plan.pot_size, 200)

    def test_serialization_roundtrip(self):
        """Test to_dict and from_dict round-trip."""
        original = DecisionPlan(
            hand_number=5,
            phase='TURN',
            player_name='Trump',
            hand_strategy='Bluff the river',
            inner_monologue='He looks scared',
            action='bet',
            amount=500,
            pot_size=1000
        )

        data = original.to_dict()
        restored = DecisionPlan.from_dict(data)

        self.assertEqual(restored.hand_number, original.hand_number)
        self.assertEqual(restored.phase, original.phase)
        self.assertEqual(restored.player_name, original.player_name)
        self.assertEqual(restored.hand_strategy, original.hand_strategy)
        self.assertEqual(restored.inner_monologue, original.inner_monologue)
        self.assertEqual(restored.action, original.action)
        self.assertEqual(restored.amount, original.amount)
        self.assertEqual(restored.pot_size, original.pot_size)

    def test_timestamp_default(self):
        """Test that timestamp defaults to now."""
        plan = DecisionPlan(
            hand_number=1,
            phase='PRE_FLOP',
            player_name='Test',
            hand_strategy=None,
            inner_monologue='',
            action='fold',
            amount=0,
            pot_size=100
        )

        self.assertIsInstance(plan.timestamp, datetime)


class TestHandCommentaryExtended(unittest.TestCase):
    """Test the extended HandCommentary dataclass."""

    def test_new_fields(self):
        """Test new fields are properly initialized."""
        commentary = HandCommentary(
            player_name='TestPlayer',
            emotional_reaction='Feeling good!',
            strategic_reflection='Played it well.',
            opponent_observations=['Trump: folds to pressure'],
            table_comment='Nice hand!',
            decision_plans=[],
            key_insight='Check-raise works.',
            hand_number=1
        )

        self.assertEqual(commentary.key_insight, 'Check-raise works.')
        self.assertEqual(commentary.hand_number, 1)
        self.assertEqual(commentary.decision_plans, [])

    def test_serialization_with_decision_plans(self):
        """Test serialization includes decision plans."""
        plan = DecisionPlan(
            hand_number=1,
            phase='FLOP',
            player_name='Test',
            hand_strategy='Value bet',
            inner_monologue='Strong hand',
            action='bet',
            amount=50,
            pot_size=100
        )

        commentary = HandCommentary(
            player_name='Test',
            emotional_reaction='Happy',
            strategic_reflection='Good play',
            opponent_observations=[],
            table_comment=None,
            decision_plans=[plan],
            key_insight='Betting pays off',
            hand_number=1
        )

        data = commentary.to_dict()
        self.assertEqual(len(data['decision_plans']), 1)
        self.assertEqual(data['key_insight'], 'Betting pays off')
        self.assertEqual(data['hand_number'], 1)

        restored = HandCommentary.from_dict(data)
        self.assertEqual(len(restored.decision_plans), 1)
        self.assertEqual(restored.decision_plans[0].hand_strategy, 'Value bet')


class TestOpponentModelNarrativeObservations(unittest.TestCase):
    """Test narrative observations in OpponentModel."""

    def test_add_observation(self):
        """Test adding narrative observations."""
        model = OpponentModel('Observer', 'Trump')
        model.add_narrative_observation('Folds to aggression on scary boards')
        model.add_narrative_observation('Overvalues top pair')

        self.assertEqual(len(model.narrative_observations), 2)
        self.assertIn('Folds to aggression on scary boards', model.narrative_observations)
        self.assertIn('Overvalues top pair', model.narrative_observations)

    def test_observation_deduplication(self):
        """Test that duplicate observations are not added."""
        model = OpponentModel('Observer', 'Trump')
        model.add_narrative_observation('Folds to pressure')
        model.add_narrative_observation('Folds to pressure')

        self.assertEqual(len(model.narrative_observations), 1)

    def test_observation_sliding_window(self):
        """Test that only last 5 observations are kept."""
        model = OpponentModel('Observer', 'Trump')
        for i in range(7):
            model.add_narrative_observation(f'Observation {i}')

        self.assertEqual(len(model.narrative_observations), 5)
        self.assertEqual(model.narrative_observations[0], 'Observation 2')
        self.assertEqual(model.narrative_observations[-1], 'Observation 6')

    def test_observations_in_prompt_summary(self):
        """Test observations appear in prompt summary."""
        model = OpponentModel('Observer', 'Trump')
        model.add_narrative_observation('Very aggressive postflop')
        model.tendencies.hands_observed = 10  # Need enough hands for summary

        summary = model.get_prompt_summary()
        self.assertIn('Notes:', summary)
        self.assertIn('aggressive postflop', summary)

    def test_serialization_with_observations(self):
        """Test observations survive serialization."""
        model = OpponentModel('Observer', 'Trump')
        model.add_narrative_observation('Bluffs rivers often')
        model.add_narrative_observation('Tight preflop')

        data = model.to_dict()
        self.assertEqual(len(data['narrative_observations']), 2)

        restored = OpponentModel.from_dict(data)
        self.assertEqual(restored.narrative_observations, model.narrative_observations)

    def test_empty_observation_ignored(self):
        """Test empty observations are not added."""
        model = OpponentModel('Observer', 'Trump')
        model.add_narrative_observation('')
        model.add_narrative_observation('   ')
        model.add_narrative_observation(None)

        self.assertEqual(len(model.narrative_observations), 0)


class TestSessionMemoryReflections(unittest.TestCase):
    """Test reflections in SessionMemory."""

    def test_add_reflection(self):
        """Test adding strategic reflections."""
        sm = SessionMemory('TestPlayer')
        sm.add_reflection('Check-raising was effective against tight players.')

        self.assertEqual(len(sm.recent_reflections), 1)
        self.assertIn('Check-raising was effective', sm.recent_reflections[0])

    def test_add_reflection_with_key_insight(self):
        """Test key_insight is preferred over full reflection."""
        sm = SessionMemory('TestPlayer')
        sm.add_reflection(
            'Long strategic reflection about the hand and what happened.',
            key_insight='Bet bigger on wet boards.'
        )

        self.assertEqual(len(sm.recent_reflections), 1)
        self.assertEqual(sm.recent_reflections[0], 'Bet bigger on wet boards.')

    def test_reflection_deduplication(self):
        """Test duplicate reflections are not added."""
        sm = SessionMemory('TestPlayer')
        sm.add_reflection('Same insight.')
        sm.add_reflection('Same insight.')

        self.assertEqual(len(sm.recent_reflections), 1)

    def test_reflection_sliding_window(self):
        """Test only last 5 reflections are kept."""
        sm = SessionMemory('TestPlayer')
        for i in range(7):
            sm.add_reflection(f'Reflection {i}')

        self.assertEqual(len(sm.recent_reflections), 5)
        self.assertIn('Reflection 2', sm.recent_reflections[0])
        self.assertIn('Reflection 6', sm.recent_reflections[-1])

    def test_reflections_in_context_prompt(self):
        """Test reflections appear in context for prompt."""
        sm = SessionMemory('TestPlayer')
        sm.add_reflection('Always value bet the river.')

        context = sm.get_context_for_prompt()
        self.assertIn('Learnings:', context)
        self.assertIn('value bet the river', context)

    def test_serialization_with_reflections(self):
        """Test reflections survive serialization."""
        sm = SessionMemory('TestPlayer')
        sm.add_reflection('Insight one')
        sm.add_reflection('Insight two')

        data = sm.to_dict()
        self.assertEqual(len(data['recent_reflections']), 2)

        restored = SessionMemory.from_dict(data)
        self.assertEqual(restored.recent_reflections, sm.recent_reflections)

    def test_empty_reflection_ignored(self):
        """Test empty reflections are not added."""
        sm = SessionMemory('TestPlayer')
        sm.add_reflection('')
        sm.add_reflection('', key_insight='')

        self.assertEqual(len(sm.recent_reflections), 0)


class TestCommentaryPersistence(unittest.TestCase):
    """Test commentary persistence in database."""

    def setUp(self):
        """Create temporary database."""
        fd, self.db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.persistence = GamePersistence(self.db_path)

    def tearDown(self):
        """Clean up temporary database."""
        os.unlink(self.db_path)

    def test_save_commentary(self):
        """Test saving hand commentary to database."""
        commentary = HandCommentary(
            player_name='TestPlayer',
            emotional_reaction='Feeling great!',
            strategic_reflection='Check-raising was the right play.',
            opponent_observations=['Trump: folds to pressure'],
            table_comment='Nice hand!',
            key_insight='Check-raise works.',
            hand_number=1
        )

        self.persistence.save_hand_commentary('test_game', 1, 'TestPlayer', commentary)

        # Verify saved
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT player_name, strategic_reflection, key_insight
                FROM hand_commentary WHERE game_id = 'test_game'
            ''')
            row = cursor.fetchone()

        self.assertEqual(row[0], 'TestPlayer')
        self.assertIn('Check-raising', row[1])
        self.assertEqual(row[2], 'Check-raise works.')

    def test_save_commentary_with_decision_plans(self):
        """Test saving commentary with decision plans."""
        plan = DecisionPlan(
            hand_number=1,
            phase='FLOP',
            player_name='TestPlayer',
            hand_strategy='Check-raise',
            inner_monologue='Going for value',
            action='raise',
            amount=100,
            pot_size=200
        )

        commentary = HandCommentary(
            player_name='TestPlayer',
            emotional_reaction='Happy',
            strategic_reflection='Good play',
            opponent_observations=[],
            table_comment=None,
            decision_plans=[plan],
            hand_number=1
        )

        self.persistence.save_hand_commentary('test_game', 1, 'TestPlayer', commentary)

        # Verify decision plans saved as JSON
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT decision_plans FROM hand_commentary WHERE game_id = 'test_game'
            ''')
            row = cursor.fetchone()

        self.assertIn('Check-raise', row[0])
        self.assertIn('FLOP', row[0])

    def test_get_recent_reflections(self):
        """Test retrieving recent reflections."""
        # Save multiple commentaries
        for i in range(3):
            commentary = HandCommentary(
                player_name='TestPlayer',
                emotional_reaction='Reaction',
                strategic_reflection=f'Reflection {i}',
                opponent_observations=[],
                table_comment=None,
                key_insight=f'Insight {i}',
                hand_number=i + 1
            )
            self.persistence.save_hand_commentary('test_game', i + 1, 'TestPlayer', commentary)

        reflections = self.persistence.get_recent_reflections('test_game', 'TestPlayer', limit=5)

        self.assertEqual(len(reflections), 3)
        # Should be ordered by hand_number DESC
        self.assertEqual(reflections[0]['hand_number'], 3)

    def test_upsert_commentary(self):
        """Test that saving same hand/player updates existing record."""
        commentary1 = HandCommentary(
            player_name='TestPlayer',
            emotional_reaction='First reaction',
            strategic_reflection='First reflection',
            opponent_observations=[],
            table_comment=None,
            hand_number=1
        )
        self.persistence.save_hand_commentary('test_game', 1, 'TestPlayer', commentary1)

        commentary2 = HandCommentary(
            player_name='TestPlayer',
            emotional_reaction='Updated reaction',
            strategic_reflection='Updated reflection',
            opponent_observations=[],
            table_comment=None,
            hand_number=1
        )
        self.persistence.save_hand_commentary('test_game', 1, 'TestPlayer', commentary2)

        # Should only have one record
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute('''
                SELECT COUNT(*), strategic_reflection
                FROM hand_commentary WHERE game_id = 'test_game' AND player_name = 'TestPlayer'
            ''')
            row = cursor.fetchone()

        self.assertEqual(row[0], 1)
        self.assertIn('Updated reflection', row[1])


class TestPromptConfigMemoryFields(unittest.TestCase):
    """Test PromptConfig memory-related fields."""

    def test_strategic_reflection_default(self):
        """Test strategic_reflection defaults to True."""
        config = PromptConfig()
        self.assertTrue(config.strategic_reflection)

    def test_memory_keep_exchanges_default(self):
        """Test memory_keep_exchanges defaults to 0."""
        config = PromptConfig()
        self.assertEqual(config.memory_keep_exchanges, 0)

    def test_memory_keep_exchanges_custom(self):
        """Test custom memory_keep_exchanges value."""
        config = PromptConfig(memory_keep_exchanges=5)
        self.assertEqual(config.memory_keep_exchanges, 5)

    def test_serialization_preserves_int_field(self):
        """Test serialization preserves non-boolean field."""
        config = PromptConfig(memory_keep_exchanges=10)
        data = config.to_dict()
        restored = PromptConfig.from_dict(data)

        self.assertEqual(restored.memory_keep_exchanges, 10)

    def test_disable_all_preserves_int_field(self):
        """Test disable_all preserves memory_keep_exchanges."""
        config = PromptConfig(memory_keep_exchanges=5)
        disabled = config.disable_all()

        self.assertFalse(disabled.strategic_reflection)
        self.assertEqual(disabled.memory_keep_exchanges, 5)

    def test_enable_all_preserves_int_field(self):
        """Test enable_all preserves memory_keep_exchanges."""
        config = PromptConfig(strategic_reflection=False, memory_keep_exchanges=3)
        enabled = config.enable_all()

        self.assertTrue(enabled.strategic_reflection)
        self.assertEqual(enabled.memory_keep_exchanges, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
