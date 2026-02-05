"""Tests for coach feedback functionality in SessionMemory."""

import pytest
from flask_app.services.coach_progression import SessionMemory


class TestSessionMemoryFeedback:
    """Test SessionMemory feedback methods."""

    def test_record_player_feedback(self):
        """record_player_feedback should store feedback."""
        memory = SessionMemory()
        memory.record_player_feedback(
            hand_number=3,
            feedback={
                'hand': 'KQs',
                'position': 'CO',
                'action': 'fold',
                'reason': 'Too many players',
            }
        )

        assert 3 in memory.player_feedback
        assert len(memory.player_feedback[3]) == 1
        assert memory.player_feedback[3][0]['hand'] == 'KQs'
        assert memory.player_feedback[3][0]['reason'] == 'Too many players'

    def test_record_feedback_clears_pending_prompt(self):
        """Recording feedback should clear pending prompt."""
        memory = SessionMemory()
        memory.set_feedback_prompt({
            'hand': 'AQo',
            'position': 'UTG',
            'range_target': 0.10,
            'hand_number': 2,
        })

        assert memory.pending_feedback_prompt is not None

        memory.record_player_feedback(
            hand_number=2,
            feedback={'hand': 'AQo', 'reason': 'Too tight'}
        )

        assert memory.pending_feedback_prompt is None

    def test_set_and_get_feedback_prompt(self):
        """set_feedback_prompt and get_feedback_prompt should work."""
        memory = SessionMemory()

        assert memory.get_feedback_prompt() is None

        prompt = {
            'hand': 'JTs',
            'position': 'BTN',
            'range_target': 0.25,
            'hand_number': 7,
        }
        memory.set_feedback_prompt(prompt)

        assert memory.get_feedback_prompt() == prompt

    def test_clear_feedback_prompt(self):
        """clear_feedback_prompt should remove the prompt."""
        memory = SessionMemory()
        memory.set_feedback_prompt({'hand': 'test'})

        assert memory.get_feedback_prompt() is not None

        memory.clear_feedback_prompt()

        assert memory.get_feedback_prompt() is None

    def test_multiple_feedbacks_per_hand(self):
        """Should allow multiple feedbacks for the same hand."""
        memory = SessionMemory()

        memory.record_player_feedback(5, {'reason': 'first'})
        memory.record_player_feedback(5, {'reason': 'second'})

        assert len(memory.player_feedback[5]) == 2
        assert memory.player_feedback[5][0]['reason'] == 'first'
        assert memory.player_feedback[5][1]['reason'] == 'second'

    def test_feedbacks_across_different_hands(self):
        """Should track feedbacks for different hands separately."""
        memory = SessionMemory()

        memory.record_player_feedback(1, {'hand': 'AA', 'reason': 'testing'})
        memory.record_player_feedback(2, {'hand': 'KK', 'reason': 'different'})
        memory.record_player_feedback(3, {'hand': 'QQ', 'reason': 'another'})

        assert len(memory.player_feedback) == 3
        assert memory.player_feedback[1][0]['hand'] == 'AA'
        assert memory.player_feedback[2][0]['hand'] == 'KK'
        assert memory.player_feedback[3][0]['hand'] == 'QQ'

    def test_feedback_prompt_workflow(self):
        """Test the full workflow: set prompt -> record feedback -> prompt cleared."""
        memory = SessionMemory()

        # Initially no prompt
        assert memory.get_feedback_prompt() is None

        # Coach sets a feedback prompt
        memory.set_feedback_prompt({
            'hand': 'AKo',
            'position': 'UTG',
            'range_target': 0.08,
            'hand_number': 10,
        })

        # Prompt is available
        prompt = memory.get_feedback_prompt()
        assert prompt is not None
        assert prompt['hand'] == 'AKo'

        # Player submits feedback
        memory.record_player_feedback(10, {
            'hand': 'AKo',
            'position': 'UTG',
            'action': 'fold',
            'reason': 'Had a read on opponent',
        })

        # Prompt is cleared
        assert memory.get_feedback_prompt() is None

        # Feedback is stored
        assert 10 in memory.player_feedback
        assert memory.player_feedback[10][0]['reason'] == 'Had a read on opponent'

    def test_dismiss_without_feedback(self):
        """User can dismiss prompt without providing feedback."""
        memory = SessionMemory()

        memory.set_feedback_prompt({
            'hand': 'QJs',
            'position': 'CO',
            'range_target': 0.18,
            'hand_number': 5,
        })

        assert memory.get_feedback_prompt() is not None

        # User dismisses without feedback
        memory.clear_feedback_prompt()

        assert memory.get_feedback_prompt() is None
        # No feedback recorded for hand 5
        assert 5 not in memory.player_feedback or len(memory.player_feedback[5]) == 0
