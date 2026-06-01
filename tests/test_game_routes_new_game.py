#!/usr/bin/env python3
"""
Test suite for duplicate player name validation in the /api/new-game route.
"""

import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from poker.repositories import create_repos


class TestNewGameDuplicatePlayerName(unittest.TestCase):
    """Test duplicate player name detection when creating a new game."""

    def setUp(self):
        """Create a test Flask app and temporary database."""
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()

        repos = create_repos(self.test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext

            ext.game_repo = repos['game_repo']
            ext.user_repo = repos['user_repo']
            ext.settings_repo = repos['settings_repo']
            ext.personality_repo = repos['personality_repo']
            ext.experiment_repo = repos['experiment_repo']
            ext.prompt_capture_repo = repos['prompt_capture_repo']
            ext.decision_analysis_repo = repos['decision_analysis_repo']
            ext.prompt_preset_repo = repos['prompt_preset_repo']
            ext.capture_label_repo = repos['capture_label_repo']
            ext.replay_experiment_repo = repos['replay_experiment_repo']
            ext.llm_repo = repos['llm_repo']
            ext.guest_tracking_repo = repos['guest_tracking_repo']
            ext.hand_history_repo = repos['hand_history_repo']
            ext.tournament_repo = repos['tournament_repo']
            ext.coach_repo = repos['coach_repo']
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        # The rate limiter is a single module-global bound across every
        # create_app(), and PRH-41 keys authenticated users per-id — so neither
        # flipping RATELIMIT_ENABLED here nor a unique REMOTE_ADDR escapes the
        # `user:test-user-1` bucket that sibling suites in the run already
        # filled (→ 429 instead of the 400 we're asserting). Reset the shared
        # storage so each test starts with an empty quota.
        from flask_app.extensions import limiter

        with self.app.app_context():
            try:
                limiter.reset()
            except Exception:
                pass
        self.client = self.app.test_client()

        # Patch game_routes module-level repo globals to this test's fresh repos.
        # game_routes imports these by value at module import time, so without this
        # tests can hit stale/closed repo objects from other test modules.
        self._route_patchers = [
            patch('flask_app.extensions.game_repo', repos['game_repo']),
            patch('flask_app.extensions.user_repo', repos['user_repo']),
            patch('flask_app.extensions.prompt_preset_repo', repos['prompt_preset_repo']),
            patch('flask_app.extensions.guest_tracking_repo', repos['guest_tracking_repo']),
            patch('flask_app.extensions.hand_history_repo', repos['hand_history_repo']),
            patch('flask_app.extensions.tournament_repo', repos['tournament_repo']),
            patch('flask_app.extensions.llm_repo', repos['llm_repo']),
            patch(
                'flask_app.extensions.decision_analysis_repo',
                repos['decision_analysis_repo'],
            ),
            patch('flask_app.extensions.capture_label_repo', repos['capture_label_repo']),
            patch('flask_app.extensions.coach_repo', repos['coach_repo']),
            patch('flask_app.extensions.persistence_db_path', repos['db_path']),
        ]
        for patcher in self._route_patchers:
            patcher.start()

    def tearDown(self):
        """Clean up temporary database."""
        for patcher in self._route_patchers:
            patcher.stop()
        os.unlink(self.test_db.name)

    def _mock_auth(self):
        """Return a patch that provides a fake authenticated user.

        The user id is unique per test: PRH-41 keys the rate limiter per
        authenticated user, so a shared id would let sibling suites in an xdist
        worker fill the `user:<id>` new-game bucket (10/hr) and this test would
        429 instead of asserting its real status. A per-test id gives each test
        its own empty bucket (the unique REMOTE_ADDR below covers the IP fallback,
        and setUp also resets the limiter — this is the belt-and-suspenders that
        survives an unreliable reset under xdist)."""
        mock_auth = unittest.mock.MagicMock()
        mock_auth.get_current_user.return_value = {
            'id': f'test-user-{self.id()}',
            'name': 'TestUser',
        }
        return patch('flask_app.extensions.auth_manager', mock_auth)

    def test_duplicate_player_name_returns_400(self):
        """POST /api/new-game with player name matching an AI personality returns 400."""
        with self._mock_auth():
            response = self.client.post(
                '/api/new-game',
                json={
                    'playerName': 'Batman',
                    'personalities': ['Batman', 'Yoda'],
                },
                # Unique REMOTE_ADDR avoids the shared limiter bucket that
                # other suites in the run have already exhausted.
                environ_overrides={'REMOTE_ADDR': '10.99.0.1'},
            )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data['code'], 'DUPLICATE_PLAYER_NAME')

    def test_duplicate_player_name_case_insensitive(self):
        """Duplicate name check is case-insensitive."""
        with self._mock_auth():
            response = self.client.post(
                '/api/new-game',
                json={
                    'playerName': 'batman',
                    'personalities': ['Batman', 'Yoda'],
                },
                environ_overrides={'REMOTE_ADDR': '10.99.0.2'},
            )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data['code'], 'DUPLICATE_PLAYER_NAME')

    def test_non_matching_name_does_not_error(self):
        """Player name that doesn't match any AI personality proceeds past validation."""
        with self._mock_auth():
            response = self.client.post(
                '/api/new-game',
                json={
                    'playerName': 'UniquePlayer',
                    'personalities': ['Batman', 'Yoda'],
                },
            )
        # Should not be a 400 with DUPLICATE_PLAYER_NAME
        # (may fail later in game init, but that's fine — we only test the name check)
        if response.status_code == 400:
            data = response.get_json()
            self.assertNotEqual(data.get('code'), 'DUPLICATE_PLAYER_NAME')


if __name__ == '__main__':
    unittest.main()
