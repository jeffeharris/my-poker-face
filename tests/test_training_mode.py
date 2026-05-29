#!/usr/bin/env python3
"""Phase 1 tests for Training / Coaching mode (docs/plans/TRAINING_MODE.md).

Covers the non-counting contract that's easy to regress:
- /api/training/start creates a `train-` game with training_mode + auto-coach
- the game wires NO relationship repo and NO tournament tracker (the only safe
  suppression — relationship_states is not cash_mode-gated)
- saved bot_types round-trip so cold-load rebuilds identical controllers
- training games are excluded from the "continue games" list
- the difficulty roster maps/cycles as specified
"""

import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from poker.repositories import create_repos
from training.opponent_roster import (
    DEFAULT_DIFFICULTY,
    DIFFICULTY_ROSTERS,
    resolve_opponents,
)

pytestmark = [pytest.mark.flask, pytest.mark.integration]


class TestOpponentRoster(unittest.TestCase):
    """Pure unit tests for difficulty → roster resolution (no DB/app)."""

    def test_each_tier_returns_requested_count(self):
        for tier in DIFFICULTY_ROSTERS:
            self.assertEqual(len(resolve_opponents(tier, 5)), 5)

    def test_easy_is_loose_passive_rule_bots(self):
        self.assertEqual(resolve_opponents('easy', 2), ['fish', 'foldy'])

    def test_roster_cycles_when_seats_exceed_roster(self):
        # easy has 2 entries; 5 seats cycle fish/foldy/fish/foldy/fish.
        self.assertEqual(
            resolve_opponents('easy', 5), ['fish', 'foldy', 'fish', 'foldy', 'fish']
        )

    def test_hard_is_the_sharp_solver(self):
        self.assertEqual(resolve_opponents('hard', 3), ['sharp', 'sharp', 'sharp'])

    def test_unknown_difficulty_falls_back_to_default(self):
        self.assertEqual(
            resolve_opponents('impossible', 3),
            resolve_opponents(DEFAULT_DIFFICULTY, 3),
        )

    def test_zero_or_negative_seats_is_empty(self):
        self.assertEqual(resolve_opponents('medium', 0), [])
        self.assertEqual(resolve_opponents('medium', -1), [])


class TestTrainingStartRoute(unittest.TestCase):
    """End-to-end tests of /api/training/start and the resulting game_data."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        repos = create_repos(self.test_db.name)
        self._repos = repos

        def mock_init_persistence():
            import flask_app.extensions as ext

            ext.game_repo = repos['game_repo']
            ext.user_repo = repos['user_repo']
            ext.settings_repo = repos['settings_repo']
            ext.personality_repo = repos['personality_repo']
            ext.decision_analysis_repo = repos['decision_analysis_repo']
            ext.capture_label_repo = repos['capture_label_repo']
            ext.hand_history_repo = repos['hand_history_repo']
            ext.coach_repo = repos['coach_repo']
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True

        from flask_app.extensions import limiter

        with self.app.app_context():
            try:
                limiter.reset()
            except Exception:
                pass
        self.client = self.app.test_client()

        self._route_patchers = [
            patch('flask_app.extensions.game_repo', repos['game_repo']),
            patch('flask_app.extensions.user_repo', repos['user_repo']),
            patch('flask_app.extensions.personality_repo', repos['personality_repo']),
            patch('flask_app.extensions.hand_history_repo', repos['hand_history_repo']),
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
        # Evict any in-memory training games this test created.
        from flask_app.services import game_state_service

        for gid in list(game_state_service.list_game_ids()):
            if gid.startswith('train-'):
                game_state_service.delete_game(gid)
        for patcher in self._route_patchers:
            patcher.stop()
        os.unlink(self.test_db.name)

    def _mock_auth(self):
        mock_auth = unittest.mock.MagicMock()
        mock_auth.get_current_user.return_value = {
            'id': f'test-user-{self.id()}',
            'name': 'TestUser',
        }
        return patch('flask_app.extensions.auth_manager', mock_auth)

    def _start(self, **body):
        with self._mock_auth():
            return self.client.post(
                '/api/training/start',
                json=body,
                environ_overrides={'REMOTE_ADDR': '10.77.0.1'},
            )

    def test_requires_auth(self):
        mock_auth = unittest.mock.MagicMock()
        mock_auth.get_current_user.return_value = None
        with patch('flask_app.extensions.auth_manager', mock_auth):
            resp = self.client.post('/api/training/start', json={'difficulty': 'easy'})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_difficulty_returns_400(self):
        resp = self._start(difficulty='nightmare')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('valid_difficulties', resp.get_json())

    def test_creates_non_counting_training_game(self):
        from flask_app.services import game_state_service

        resp = self._start(difficulty='easy', opponent_count=3)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        gid = data['game_id']

        # Identified by the train- prefix, flagged training, auto-coach on.
        self.assertTrue(gid.startswith('train-'))
        self.assertTrue(data['training_mode'])

        gd = game_state_service.get_game(gid)
        self.assertIsNotNone(gd)
        self.assertTrue(gd['training_mode'])

        # Non-counting via wiring-absence: no tournament tracker, no
        # relationship repo (relationship_states is NOT cash_mode-gated).
        self.assertNotIn('tournament_tracker', gd)
        mm = gd['memory_manager']
        self.assertIsNone(
            getattr(mm, '_relationship_repo', None),
            'training games must never wire a relationship repo',
        )

        # Coach is forced on (persisted on the games row → survives cold-load).
        self.assertEqual(self._repos['game_repo'].load_coach_mode(gid), 'proactive')

        # Easy roster → loose-passive rule bots.
        from poker.rule_bot_controller import RuleBotController

        ctrls = gd['ai_controllers']
        self.assertEqual(len(ctrls), 3)
        for c in ctrls.values():
            self.assertIsInstance(c, RuleBotController)

    def test_saved_bot_types_roundtrip_for_coldload(self):
        # Cold-load rebuilds controllers from the persisted bot_types via
        # restore_ai_controllers; assert they were saved.
        resp = self._start(difficulty='easy', opponent_count=2)
        gid = resp.get_json()['game_id']
        cfgs = self._repos['game_repo'].load_llm_configs(gid) or {}
        bot_types = cfgs.get('bot_types', {})
        self.assertEqual(len(bot_types), 2)
        self.assertTrue(set(bot_types.values()) <= {'fish', 'foldy'})

    def test_elimination_flow_suppressed_without_tracker(self):
        # The non-counting guarantee for placement/elimination: with no
        # tournament_tracker on game_data, handle_eliminations no-ops
        # (mirrors the cash-mode contract).
        from flask_app.handlers.game_handler import handle_eliminations

        resp = self._start(difficulty='easy', opponent_count=2)
        gid = resp.get_json()['game_id']
        from flask_app.services import game_state_service

        gd = game_state_service.get_game(gid)
        result = handle_eliminations(gid, gd, unittest.mock.MagicMock(), ['TestUser'], 100)
        # No tracker → the elimination/placement flow is skipped entirely.
        self.assertIsNone(result)

    def test_excluded_from_continue_games_list(self):
        start = self._start(difficulty='medium', opponent_count=3)
        gid = start.get_json()['game_id']
        with self._mock_auth():
            resp = self.client.get(
                '/api/games', environ_overrides={'REMOTE_ADDR': '10.77.0.1'}
            )
        self.assertEqual(resp.status_code, 200)
        listed = {g['game_id'] for g in resp.get_json()['games']}
        self.assertNotIn(gid, listed)


if __name__ == '__main__':
    unittest.main()
