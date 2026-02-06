"""Tests for coach feedback functionality in SessionMemory and API endpoints."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

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


def _make_session_memory_with_prompt():
    """Create a SessionMemory with a pending feedback prompt including context."""
    memory = SessionMemory()
    memory.set_feedback_prompt({
        'hand': 'AKo',
        'position': 'UTG',
        'range_target': 0.08,
        'hand_number': 1,
        'context': {
            'phase': 'PRE_FLOP',
            'pot_total': 150,
            'cost_to_call': 50,
            'equity': 0.65,
        },
    })
    return memory


def _make_game_data(session_memory=None):
    """Create minimal game_data dict for coach feedback tests."""
    return {
        'coach_session_memory': session_memory or _make_session_memory_with_prompt(),
        'state_machine': MagicMock(),
    }


def _patch_auth():
    """Patch authorization to bypass permission checks."""
    mock_auth_service = MagicMock()
    mock_auth_service.auth_manager.get_current_user.return_value = {'id': 'test-user'}
    mock_auth_service.has_permission.return_value = True
    return patch('poker.authorization.authorization_service', mock_auth_service)


@patch('flask_app.routes.coach_routes._get_current_user_id', return_value='test-user')
class TestCoachFeedbackRoutes(unittest.TestCase):
    """Test coach feedback API endpoints."""

    @classmethod
    def setUpClass(cls):
        """Create a test Flask app once for all tests."""
        from poker.repositories import create_repos
        from flask_app import create_app

        cls._test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls._test_db.close()

        repos = create_repos(cls._test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key, repo in repos.items():
                if hasattr(ext, key):
                    setattr(ext, key, repo)
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls._app = create_app()
        cls._app.testing = True

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls._test_db.name)

    def setUp(self):
        self.client = self._app.test_client()
        self.session_memory = _make_session_memory_with_prompt()
        self.game_data = _make_game_data(self.session_memory)

    # --- /api/coach/<game_id>/feedback ---

    @patch('flask_app.routes.coach_routes.game_state_service')
    def test_feedback_canned_reason(self, mock_gss, _mock_uid):
        """Canned reason should return predefined response."""
        mock_gss.get_game.return_value = self.game_data

        with self._app.app_context(), _patch_auth():
            res = self.client.post(
                '/api/coach/test-game/feedback',
                json={'reason': 'read', 'hand': 'AKo', 'position': 'UTG', 'hand_number': 1},
            )

        assert res.status_code == 200
        data = res.get_json()
        assert data['status'] == 'ok'
        assert 'Trust your instincts' in data['response']
        assert data['feedback_stored'] is True

    @patch('flask_app.routes.coach_routes._generate_feedback_response',
           return_value='Great thinking!')
    @patch('flask_app.routes.coach_routes.game_state_service')
    def test_feedback_custom_reason(self, mock_gss, _mock_gen, _mock_uid):
        """Custom reason should call LLM response generator."""
        mock_gss.get_game.return_value = self.game_data

        with self._app.app_context(), _patch_auth():
            res = self.client.post(
                '/api/coach/test-game/feedback',
                json={'reason': 'Big raise scared me', 'hand': 'AKo',
                      'position': 'UTG', 'hand_number': 1},
            )

        assert res.status_code == 200
        data = res.get_json()
        assert data['response'] == 'Great thinking!'

    @patch('flask_app.routes.coach_routes.game_state_service')
    def test_feedback_empty_reason_returns_400(self, mock_gss, _mock_uid):
        """Empty reason should return 400."""
        mock_gss.get_game.return_value = self.game_data

        with self._app.app_context(), _patch_auth():
            res = self.client.post(
                '/api/coach/test-game/feedback',
                json={'reason': '', 'hand': 'AKo'},
            )

        assert res.status_code == 400
        assert 'No reason provided' in res.get_json()['error']

    @patch('flask_app.routes.coach_routes.game_state_service')
    def test_feedback_oversized_reason_returns_400(self, mock_gss, _mock_uid):
        """Reason over 500 chars should return 400."""
        mock_gss.get_game.return_value = self.game_data

        with self._app.app_context(), _patch_auth():
            res = self.client.post(
                '/api/coach/test-game/feedback',
                json={'reason': 'x' * 501, 'hand': 'AKo'},
            )

        assert res.status_code == 400
        assert 'too long' in res.get_json()['error']

    @patch('flask_app.routes.coach_routes.game_state_service')
    def test_feedback_context_preserved_for_llm(self, mock_gss, _mock_uid):
        """Context from pending prompt should be read before recording clears it."""
        mock_gss.get_game.return_value = self.game_data

        with self._app.app_context(), _patch_auth(), \
             patch('flask_app.routes.coach_routes._generate_feedback_response') as mock_gen:
            mock_gen.return_value = 'Good reasoning!'
            res = self.client.post(
                '/api/coach/test-game/feedback',
                json={'reason': 'Opponent was aggressive', 'hand': 'AKo',
                      'position': 'UTG', 'hand_number': 1},
            )

        assert res.status_code == 200
        # Verify _generate_feedback_response was called with hand_context
        mock_gen.assert_called_once()
        call_args = mock_gen.call_args
        hand_context = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get('context')
        assert hand_context is not None
        assert hand_context['phase'] == 'PRE_FLOP'
        assert hand_context['pot_total'] == 150

    # --- /api/coach/<game_id>/feedback/dismiss ---

    @patch('flask_app.routes.coach_routes.game_state_service')
    def test_dismiss_feedback(self, mock_gss, _mock_uid):
        """Dismiss should clear the pending prompt and return ok."""
        mock_gss.get_game.return_value = self.game_data

        with self._app.app_context(), _patch_auth():
            res = self.client.post('/api/coach/test-game/feedback/dismiss')

        assert res.status_code == 200
        assert res.get_json()['status'] == 'ok'
        assert self.session_memory.get_feedback_prompt() is None
