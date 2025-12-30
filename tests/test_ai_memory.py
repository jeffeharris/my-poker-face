#!/usr/bin/env python3
"""
Test suite for the AI Memory and Learning System.

Tests cover:
- Hand history recording and serialization
- Session memory tracking
- Opponent modeling and tendency calculation
- Commentary generation (with mocked LLM)
- Memory manager orchestration
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from poker.memory.hand_history import (
    RecordedAction, RecordedHand, PlayerHandInfo, WinnerInfo,
    HandInProgress, HandHistoryRecorder
)
from poker.memory.session_memory import HandMemory, SessionContext, SessionMemory
from poker.memory.opponent_model import (
    OpponentTendencies, MemorableHand, OpponentModel, OpponentModelManager
)
from poker.memory.commentary_generator import HandCommentary, CommentaryGenerator
from poker.memory.memory_manager import AIMemoryManager


class TestRecordedAction(unittest.TestCase):
    """Test the RecordedAction frozen dataclass."""

    def test_action_creation(self):
        """Test creating an action record."""
        action = RecordedAction(
            player_name="Alice",
            action="raise",
            amount=100,
            phase="PRE_FLOP",
            pot_after=150
        )

        self.assertEqual(action.player_name, "Alice")
        self.assertEqual(action.action, "raise")
        self.assertEqual(action.amount, 100)
        self.assertEqual(action.phase, "PRE_FLOP")
        self.assertEqual(action.pot_after, 150)

    def test_action_immutability(self):
        """Test that RecordedAction is immutable."""
        action = RecordedAction(
            player_name="Alice",
            action="fold",
            amount=0,
            phase="FLOP",
            pot_after=200
        )

        with self.assertRaises(AttributeError):
            action.action = "call"

    def test_action_serialization(self):
        """Test to_dict and from_dict round-trip."""
        original = RecordedAction(
            player_name="Bob",
            action="call",
            amount=50,
            phase="TURN",
            pot_after=300
        )

        data = original.to_dict()
        restored = RecordedAction.from_dict(data)

        self.assertEqual(restored.player_name, original.player_name)
        self.assertEqual(restored.action, original.action)
        self.assertEqual(restored.amount, original.amount)
        self.assertEqual(restored.phase, original.phase)


class TestRecordedHand(unittest.TestCase):
    """Test the RecordedHand frozen dataclass."""

    def setUp(self):
        """Create a sample recorded hand."""
        self.hand = RecordedHand(
            game_id="game_001",
            hand_number=1,
            timestamp=datetime.now(),
            players=(
                PlayerHandInfo("Alice", 1000, "BTN", False),
                PlayerHandInfo("Bob", 1000, "SB", False),
                PlayerHandInfo("Human", 1000, "BB", True),
            ),
            hole_cards={"Alice": ["Ah", "Kh"], "Bob": ["7c", "2d"]},
            community_cards=("Qh", "Jh", "Th", "2s", "3c"),
            actions=(
                RecordedAction("Bob", "call", 10, "PRE_FLOP", 30),
                RecordedAction("Human", "check", 0, "PRE_FLOP", 30),
                RecordedAction("Alice", "raise", 50, "PRE_FLOP", 80),
                RecordedAction("Bob", "fold", 0, "PRE_FLOP", 80),
                RecordedAction("Human", "call", 40, "PRE_FLOP", 120),
            ),
            winners=(WinnerInfo("Alice", 120, "Royal Flush", 1),),
            pot_size=120,
            was_showdown=True
        )

    def test_get_player_outcome_winner(self):
        """Test outcome detection for winner."""
        outcome = self.hand.get_player_outcome("Alice")
        self.assertEqual(outcome, "won")

    def test_get_player_outcome_folded(self):
        """Test outcome detection for player who folded."""
        outcome = self.hand.get_player_outcome("Bob")
        self.assertEqual(outcome, "folded")

    def test_get_player_outcome_lost(self):
        """Test outcome detection for player who lost at showdown."""
        outcome = self.hand.get_player_outcome("Human")
        self.assertEqual(outcome, "lost")

    def test_get_player_actions(self):
        """Test filtering actions by player."""
        alice_actions = self.hand.get_player_actions("Alice")
        self.assertEqual(len(alice_actions), 1)
        self.assertEqual(alice_actions[0].action, "raise")

    def test_serialization_round_trip(self):
        """Test to_dict and from_dict preserve all data."""
        data = self.hand.to_dict()
        restored = RecordedHand.from_dict(data)

        self.assertEqual(restored.game_id, self.hand.game_id)
        self.assertEqual(restored.hand_number, self.hand.hand_number)
        self.assertEqual(len(restored.players), len(self.hand.players))
        self.assertEqual(len(restored.actions), len(self.hand.actions))
        self.assertEqual(restored.pot_size, self.hand.pot_size)
        self.assertEqual(restored.was_showdown, self.hand.was_showdown)


class TestHandHistoryRecorder(unittest.TestCase):
    """Test the HandHistoryRecorder class."""

    def test_record_complete_hand(self):
        """Test recording a full hand from start to completion."""
        recorder = HandHistoryRecorder("game_001")

        # Create mock game state
        mock_state = Mock()
        mock_state.players = [
            Mock(name="Alice", stack=1000, hand=[], is_human=False, is_folded=False),
            Mock(name="Bob", stack=1000, hand=[], is_human=False, is_folded=True),
        ]
        mock_state.table_positions = {"BTN": "Alice", "SB": "Bob"}
        mock_state.pot = {"total": 100}

        # Start hand
        recorder.start_hand(mock_state, hand_number=1)
        self.assertIsNotNone(recorder.current_hand)

        # Record actions
        recorder.record_action("Alice", "raise", 50, "PRE_FLOP", 70)
        recorder.record_action("Bob", "fold", 0, "PRE_FLOP", 70)

        # Record community cards
        recorder.record_community_cards("FLOP", ["Ah", "Kh", "Qh"])

        # Complete the hand
        winner_info = {"winnings": {"Alice": 100}, "hand_name": "High Card", "hand_rank": 10}
        recorded = recorder.complete_hand(winner_info, mock_state)

        self.assertIsInstance(recorded, RecordedHand)
        self.assertEqual(recorded.hand_number, 1)
        self.assertEqual(len(recorded.actions), 2)
        self.assertEqual(recorded.winners[0].name, "Alice")
        self.assertIsNone(recorder.current_hand)
        self.assertEqual(len(recorder.completed_hands), 1)


class TestSessionMemory(unittest.TestCase):
    """Test the SessionMemory class."""

    def setUp(self):
        """Create a session memory instance."""
        self.memory = SessionMemory("TestPlayer", max_hand_memory=5)

    def test_record_hand_outcome_win(self):
        """Test recording a winning hand."""
        self.memory.record_hand_outcome(
            hand_number=1,
            outcome="won",
            pot_size=500,
            amount_won_or_lost=500,
            notable_events=["Hit a flush"]
        )

        self.assertEqual(self.memory.context.hands_played, 1)
        self.assertEqual(self.memory.context.hands_won, 1)
        self.assertEqual(self.memory.context.total_winnings, 500)
        self.assertEqual(len(self.memory.hand_memories), 1)

    def test_record_hand_outcome_loss(self):
        """Test recording a losing hand."""
        self.memory.record_hand_outcome(
            hand_number=1,
            outcome="lost",
            pot_size=300,
            amount_won_or_lost=-150
        )

        self.assertEqual(self.memory.context.hands_played, 1)
        self.assertEqual(self.memory.context.hands_won, 0)
        self.assertEqual(self.memory.context.total_winnings, -150)

    def test_streak_tracking(self):
        """Test win/loss streak detection."""
        # Win 3 in a row
        for i in range(3):
            self.memory.record_hand_outcome(i + 1, "won", 100, 100)

        self.assertEqual(self.memory.context.current_streak, "winning")
        self.assertEqual(self.memory.context.streak_count, 3)

        # Now lose - resets streak
        self.memory.record_hand_outcome(4, "lost", 100, -100)
        self.assertEqual(self.memory.context.current_streak, "losing")
        self.assertEqual(self.memory.context.streak_count, 1)

        # Lose again - streak continues
        self.memory.record_hand_outcome(5, "lost", 100, -100)
        self.assertEqual(self.memory.context.current_streak, "losing")
        self.assertEqual(self.memory.context.streak_count, 2)

    def test_memory_trimming(self):
        """Test that old hands are trimmed when max is exceeded."""
        for i in range(10):
            self.memory.record_hand_outcome(i+1, "won", 100, 100)

        self.assertEqual(len(self.memory.hand_memories), 5)  # max_hand_memory=5
        self.assertEqual(self.memory.hand_memories[0].hand_number, 6)  # Oldest kept

    def test_emotional_state(self):
        """Test emotional state calculation."""
        # Start neutral
        self.assertEqual(self.memory.get_emotional_state(), "neutral")

        # Win big pots
        for i in range(3):
            self.memory.record_hand_outcome(i+1, "won", 1000, 1000)

        self.assertIn(self.memory.get_emotional_state(), ["confident", "positive"])

    def test_context_for_prompt(self):
        """Test generating context string for AI prompts."""
        self.memory.record_hand_outcome(1, "won", 500, 500, ["Big bluff"])
        self.memory.record_hand_outcome(2, "lost", 200, -200)

        context = self.memory.get_context_for_prompt()

        self.assertIsInstance(context, str)
        self.assertIn("Session:", context)

    def test_serialization(self):
        """Test to_dict and from_dict round-trip."""
        self.memory.record_hand_outcome(1, "won", 500, 500)
        self.memory.add_observation("Table is playing tight")

        data = self.memory.to_dict()
        restored = SessionMemory.from_dict(data)

        self.assertEqual(restored.player_name, self.memory.player_name)
        self.assertEqual(restored.context.hands_played, 1)
        self.assertEqual(len(restored.hand_memories), 1)


class TestOpponentTendencies(unittest.TestCase):
    """Test the OpponentTendencies class."""

    def test_initial_values(self):
        """Test default tendency values."""
        tendencies = OpponentTendencies()

        self.assertEqual(tendencies.hands_observed, 0)
        self.assertEqual(tendencies.vpip, 0.5)
        self.assertEqual(tendencies.aggression_factor, 1.0)

    def test_update_from_preflop_raise(self):
        """Test updating stats from a preflop raise."""
        tendencies = OpponentTendencies()
        tendencies.update_from_action("raise", "PRE_FLOP", is_voluntary=True)

        self.assertEqual(tendencies.hands_observed, 1)
        self.assertEqual(tendencies._vpip_count, 1)
        self.assertEqual(tendencies._pfr_count, 1)
        self.assertEqual(tendencies._bet_raise_count, 1)

    def test_update_from_call(self):
        """Test updating stats from a call."""
        tendencies = OpponentTendencies()
        tendencies.update_from_action("call", "FLOP", is_voluntary=True)

        self.assertEqual(tendencies._call_count, 1)
        self.assertEqual(tendencies._bet_raise_count, 0)

    def test_aggression_factor_calculation(self):
        """Test aggression factor calculation."""
        tendencies = OpponentTendencies()

        # 3 bets/raises, 1 call = AF of 3
        for _ in range(3):
            tendencies.update_from_action("raise", "FLOP", count_hand=False)
        tendencies.update_from_action("call", "FLOP", count_hand=False)

        self.assertEqual(tendencies.aggression_factor, 3.0)

    def test_play_style_classification(self):
        """Test play style label generation."""
        tendencies = OpponentTendencies()

        # Not enough data
        self.assertEqual(tendencies.get_play_style_label(), "unknown")

        # Simulate tight-aggressive (low VPIP, high AF)
        tendencies.hands_observed = 10
        tendencies._vpip_count = 2  # 20% VPIP
        tendencies._bet_raise_count = 8
        tendencies._call_count = 2
        tendencies._recalculate_stats()

        self.assertEqual(tendencies.get_play_style_label(), "tight-aggressive")

    def test_serialization(self):
        """Test to_dict and from_dict."""
        tendencies = OpponentTendencies()
        tendencies.update_from_action("raise", "PRE_FLOP")
        tendencies.update_showdown(won=True)

        data = tendencies.to_dict()
        restored = OpponentTendencies.from_dict(data)

        self.assertEqual(restored.hands_observed, tendencies.hands_observed)
        self.assertEqual(restored._showdowns, tendencies._showdowns)


class TestOpponentModel(unittest.TestCase):
    """Test the OpponentModel class."""

    def test_observe_actions_across_hands(self):
        """Test that hands_observed only increments once per hand."""
        model = OpponentModel("Observer", "Target")

        # Multiple actions in same hand
        model.observe_action("raise", "PRE_FLOP", hand_number=1)
        model.observe_action("call", "FLOP", hand_number=1)
        model.observe_action("bet", "TURN", hand_number=1)

        self.assertEqual(model.tendencies.hands_observed, 1)

        # New hand
        model.observe_action("fold", "PRE_FLOP", hand_number=2)
        self.assertEqual(model.tendencies.hands_observed, 2)

    def test_add_memorable_hand(self):
        """Test adding memorable hands with threshold."""
        model = OpponentModel("Observer", "Target")

        # Below threshold - shouldn't be added
        model.add_memorable_hand(1, "bluff_caught", 0.5, "Small bluff", "Hand 1")
        self.assertEqual(len(model.memorable_hands), 0)

        # Above threshold - should be added
        model.add_memorable_hand(2, "big_loss", 0.8, "Lost big pot", "Hand 2")
        self.assertEqual(len(model.memorable_hands), 1)

    def test_memorable_hands_limit(self):
        """Test that memorable hands are limited and sorted by impact."""
        model = OpponentModel("Observer", "Target")

        # Add 7 memorable hands
        for i in range(7):
            model.add_memorable_hand(
                i, "event", 0.7 + (i * 0.02), f"Event {i}", f"Hand {i}"
            )

        # Should only keep top 5
        self.assertEqual(len(model.memorable_hands), 5)
        # Highest impact should be first
        self.assertGreater(
            model.memorable_hands[0].impact_score,
            model.memorable_hands[4].impact_score
        )


class TestOpponentModelManager(unittest.TestCase):
    """Test the OpponentModelManager class."""

    def test_get_or_create_model(self):
        """Test lazy creation of opponent models."""
        manager = OpponentModelManager()

        model = manager.get_model("Alice", "Bob")
        self.assertEqual(model.observer, "Alice")
        self.assertEqual(model.opponent, "Bob")

        # Same call should return same model
        model2 = manager.get_model("Alice", "Bob")
        self.assertIs(model, model2)

    def test_observe_action_skips_self(self):
        """Test that self-observations are skipped."""
        manager = OpponentModelManager()

        manager.observe_action("Alice", "Alice", "raise", "PRE_FLOP")

        # Should not create a model for self-observation
        self.assertEqual(len(manager.models.get("Alice", {})), 0)

    def test_table_summary(self):
        """Test generating table summary for multiple opponents."""
        manager = OpponentModelManager()

        # Build up some observations
        for i in range(5):
            manager.observe_action("Alice", "Bob", "raise", "PRE_FLOP", hand_number=i)
            manager.observe_action("Alice", "Carol", "call", "PRE_FLOP", hand_number=i)

        summary = manager.get_table_summary("Alice", ["Bob", "Carol"])

        self.assertIsInstance(summary, str)
        # Should mention both opponents if they have enough data
        self.assertIn("Bob", summary)

    def test_serialization(self):
        """Test to_dict and from_dict."""
        manager = OpponentModelManager()
        manager.observe_action("Alice", "Bob", "raise", "PRE_FLOP", hand_number=1)

        data = manager.to_dict()
        restored = OpponentModelManager.from_dict(data)

        self.assertIn("Alice", restored.models)
        self.assertIn("Bob", restored.models["Alice"])


class TestCommentaryGenerator(unittest.TestCase):
    """Test the CommentaryGenerator class."""

    def test_quick_reaction_win(self):
        """Test quick reaction generation for wins."""
        generator = CommentaryGenerator()

        reaction = generator.generate_quick_reaction("Alice", "won", 500, chattiness=0.8)

        self.assertIsInstance(reaction, str)
        self.assertGreater(len(reaction), 0)

    def test_quick_reaction_low_chattiness(self):
        """Test that low chattiness suppresses reactions."""
        generator = CommentaryGenerator()

        reaction = generator.generate_quick_reaction("Alice", "folded", 100, chattiness=0.2)

        self.assertIsNone(reaction)

    def test_should_comment_logic(self):
        """Test the should_comment decision logic."""
        generator = CommentaryGenerator()

        # High chattiness, high impact = should speak
        self.assertTrue(generator.should_comment(chattiness=0.9, emotional_impact=0.8))

        # Low chattiness, low impact = probably silent
        self.assertFalse(generator.should_comment(chattiness=0.1, emotional_impact=0.1))

    def test_extract_notable_events(self):
        """Test extraction of notable events from a hand."""
        generator = CommentaryGenerator()

        # Create a hand with notable events
        hand = RecordedHand(
            game_id="test",
            hand_number=1,
            timestamp=datetime.now(),
            players=(PlayerHandInfo("Alice", 1000, "BTN", False),),
            hole_cards={},
            community_cards=(),
            actions=(
                RecordedAction("Alice", "all_in", 1000, "PRE_FLOP", 1000),
            ),
            winners=(WinnerInfo("Alice", 1500, "Flush", 5),),
            pot_size=1500,
            was_showdown=True
        )

        events = generator.extract_notable_events(hand, "Alice")

        self.assertIn("Went all-in", events)
        self.assertTrue(any("Big pot" in e for e in events))

    @patch('poker.memory.commentary_generator.COMMENTARY_ENABLED', False)
    def test_commentary_disabled(self):
        """Test that commentary is skipped when disabled."""
        generator = CommentaryGenerator()

        result = generator.generate_commentary(
            player_name="Alice",
            hand=Mock(),
            player_outcome="won",
            player_cards=["Ah", "Kh"],
            session_memory=None,
            opponent_models=None,
            confidence="high",
            attitude="confident",
            chattiness=0.8,
            assistant=Mock()
        )

        self.assertIsNone(result)


class TestAIMemoryManager(unittest.TestCase):
    """Test the AIMemoryManager orchestrator."""

    def setUp(self):
        """Create a memory manager instance."""
        self.manager = AIMemoryManager("test_game")

    def test_initialize_for_player(self):
        """Test player initialization."""
        self.manager.initialize_for_player("Alice")

        self.assertIn("Alice", self.manager.initialized_players)
        self.assertIn("Alice", self.manager.session_memories)

    def test_initialize_player_idempotent(self):
        """Test that initializing same player twice is safe."""
        self.manager.initialize_for_player("Alice")
        self.manager.initialize_for_player("Alice")

        self.assertEqual(len(self.manager.initialized_players), 1)

    def test_on_hand_start(self):
        """Test hand start recording."""
        mock_state = Mock()
        mock_state.players = [
            Mock(name="Alice", stack=1000, hand=[], is_human=False),
        ]
        mock_state.table_positions = {}

        self.manager.on_hand_start(mock_state, hand_number=5)

        self.assertEqual(self.manager.hand_count, 5)
        self.assertIsNotNone(self.manager.hand_recorder.current_hand)

    def test_on_action_records_and_updates_models(self):
        """Test that actions are recorded and opponent models updated."""
        self.manager.initialize_for_player("Alice")
        self.manager.initialize_for_player("Bob")
        self.manager.hand_count = 1

        # Start a hand first
        mock_state = Mock()
        mock_state.players = [
            Mock(name="Alice", stack=1000, hand=[], is_human=False),
            Mock(name="Bob", stack=1000, hand=[], is_human=False),
        ]
        mock_state.table_positions = {}
        self.manager.on_hand_start(mock_state, 1)

        # Record action
        self.manager.on_action("Alice", "raise", 100, "PRE_FLOP", 150)

        # Check action was recorded
        self.assertEqual(len(self.manager.hand_recorder.current_hand.actions), 1)

        # Check opponent model was updated (Bob observing Alice)
        bob_model = self.manager.opponent_model_manager.get_model("Bob", "Alice")
        self.assertEqual(bob_model.tendencies.hands_observed, 1)

    def test_get_decision_context(self):
        """Test generating decision context for AI prompts."""
        self.manager.initialize_for_player("Alice")
        self.manager.session_memories["Alice"].record_hand_outcome(1, "won", 500, 500)

        context = self.manager.get_decision_context("Alice", ["Bob", "Carol"])

        self.assertIsInstance(context, str)
        self.assertIn("Session", context)

    def test_serialization_round_trip(self):
        """Test full serialization and deserialization."""
        self.manager.initialize_for_player("Alice")
        self.manager.initialize_for_player("Bob")
        self.manager.hand_count = 3
        self.manager.session_memories["Alice"].record_hand_outcome(1, "won", 500, 500)

        # Observe some actions
        self.manager.opponent_model_manager.observe_action(
            "Alice", "Bob", "raise", "PRE_FLOP", hand_number=1
        )

        data = self.manager.to_dict()
        restored = AIMemoryManager.from_dict(data)

        self.assertEqual(restored.game_id, self.manager.game_id)
        self.assertEqual(restored.hand_count, 3)
        self.assertEqual(len(restored.initialized_players), 2)
        self.assertIn("Alice", restored.session_memories)

    def test_thread_safe_commentary_generation(self):
        """Test that commentary generation handles threading correctly."""
        self.manager.initialize_for_player("Alice")

        # Create a mock recorded hand
        mock_hand = RecordedHand(
            game_id="test",
            hand_number=1,
            timestamp=datetime.now(),
            players=(PlayerHandInfo("Alice", 1000, "BTN", False),),
            hole_cards={"Alice": ["Ah", "Kh"]},
            community_cards=(),
            actions=(),
            winners=(WinnerInfo("Alice", 100, "Pair", 9),),
            pot_size=100,
            was_showdown=False
        )

        # Set the recorded hand
        with self.manager._lock:
            self.manager._last_recorded_hand = mock_hand

        # Create mock AI player
        mock_ai_player = Mock()
        mock_ai_player.confidence = "high"
        mock_ai_player.attitude = "confident"
        mock_ai_player.elastic_personality = None
        mock_ai_player.personality_config = {"personality_traits": {"chattiness": 0.5}}
        mock_ai_player.assistant = Mock()
        mock_ai_player.assistant.chat = Mock(return_value='{"emotional_reaction": "test"}')

        # This should not raise - tests thread safety
        with patch('poker.memory.memory_manager.COMMENTARY_ENABLED', False):
            result = self.manager.generate_commentary_for_hand({"Alice": mock_ai_player})

        self.assertEqual(result, {})  # Commentary disabled

        # Verify _last_recorded_hand was cleared to prevent memory leak
        self.assertIsNone(self.manager._last_recorded_hand)


class TestHandMemorySerialization(unittest.TestCase):
    """Test HandMemory serialization edge cases."""

    def test_notable_events_preserved(self):
        """Test that notable events list is preserved through serialization."""
        memory = HandMemory(
            hand_number=1,
            outcome="won",
            pot_size=500,
            amount_won_or_lost=500,
            notable_events=["Caught bluff", "River suckout"],
            emotional_impact=0.8
        )

        data = memory.to_dict()
        restored = HandMemory.from_dict(data)

        self.assertEqual(restored.notable_events, memory.notable_events)

    def test_timestamp_serialization(self):
        """Test that timestamps survive serialization."""
        now = datetime.now()
        memory = HandMemory(
            hand_number=1,
            outcome="folded",
            pot_size=100,
            amount_won_or_lost=0,
            notable_events=[],
            emotional_impact=-0.1,
            timestamp=now
        )

        data = memory.to_dict()
        restored = HandMemory.from_dict(data)

        # Should be within a second (accounting for serialization precision)
        time_diff = abs((restored.timestamp - now).total_seconds())
        self.assertLess(time_diff, 1)


class TestIntegrationScenario(unittest.TestCase):
    """Integration test simulating a multi-hand game session."""

    def test_full_game_session(self):
        """Simulate a 5-hand game session and verify memory state."""
        manager = AIMemoryManager("integration_test")

        # Initialize players
        manager.initialize_for_player("Alice")
        manager.initialize_for_player("Bob")

        # Simulate 5 hands
        for hand_num in range(1, 6):
            # Create mock game state
            mock_state = Mock()
            mock_state.players = [
                Mock(name="Alice", stack=1000, hand=[], is_human=False, is_folded=False),
                Mock(name="Bob", stack=1000, hand=[], is_human=False, is_folded=False),
            ]
            mock_state.table_positions = {"BTN": "Alice", "BB": "Bob"}
            mock_state.pot = {"total": 100}

            # Start hand
            manager.on_hand_start(mock_state, hand_num)

            # Record some actions
            manager.on_action("Alice", "raise", 30, "PRE_FLOP", 50)
            manager.on_action("Bob", "call", 20, "PRE_FLOP", 70)

            # Complete hand (alternating winners)
            winner = "Alice" if hand_num % 2 == 1 else "Bob"
            winner_info = {"winnings": {winner: 100}, "hand_name": "Pair", "hand_rank": 9}

            manager.on_hand_complete(winner_info, mock_state, skip_commentary=True)

        # Verify final state
        self.assertEqual(manager.hand_count, 5)
        self.assertEqual(len(manager.hand_recorder.completed_hands), 5)

        # Check session memories were updated
        alice_session = manager.session_memories["Alice"]
        self.assertEqual(alice_session.context.hands_played, 5)
        self.assertEqual(alice_session.context.hands_won, 3)  # Hands 1, 3, 5

        bob_session = manager.session_memories["Bob"]
        self.assertEqual(bob_session.context.hands_won, 2)  # Hands 2, 4

        # Check opponent models have observations
        alice_model_of_bob = manager.opponent_model_manager.get_model("Alice", "Bob")
        self.assertEqual(alice_model_of_bob.tendencies.hands_observed, 5)


if __name__ == '__main__':
    unittest.main()
