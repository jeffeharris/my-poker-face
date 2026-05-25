"""Tests for the fast-forward (FF) feature.

FF lets the player skip LLM-driven AI deliberation for the rest of the
orbit. Mechanics under test:

  1. `_get_or_build_ff_controller` returns a tiered controller with the
     LLM expression layer disabled, and caches per-game so each AI seat
     builds the strategy table at most once.

  2. `POST /api/game/<id>/fast-forward` toggles the `fast_forward` flag
     on game_data. Body `{enabled: bool}` (defaults to True), 400 on
     non-bool, 404 when the game is missing.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from flask_app.handlers.game_handler import (
    _ff_aware_sleep,
    _get_or_build_ff_controller,
)
from poker.poker_player import AIPokerPlayer
from poker.repositories import create_repos
from poker.tiered_bot_controller import TieredBotController


def _stub_personality_generator():
    """A MagicMock standing in for AIPokerPlayer's class-level singleton.

    Without this, every test that builds a controller transitively
    constructs AIPokerPlayer(name) → _load_personality_config() →
    self.__class__._personality_generator (auto-init pointed at
    /app/data/poker_games.db) → get_personality(name) → LLM auto-create
    in the **production** DB. That's how zombie personalities like
    "A" and "Villain" got seeded earlier. The stub returns a minimal
    valid personality dict so the controller wiring proceeds, but no
    DB writes happen.
    """
    stub = MagicMock()
    stub.get_personality.return_value = {
        'play_style': 'balanced',
        'default_confidence': 'steady',
        'default_attitude': 'friendly',
        'personality_traits': {
            'bluff_tendency': 0.3,
            'aggression': 0.5,
            'chattiness': 0.5,
            'emoji_usage': 0.2,
        },
    }
    return stub


pytestmark = [pytest.mark.flask, pytest.mark.integration]


class TestFFControllerBuilder(unittest.TestCase):
    """`_get_or_build_ff_controller` builds the no-LLM tiered controller
    and caches it per game_data."""

    def setUp(self):
        # Pin the AIPokerPlayer singleton to a stub so no test ever
        # auto-creates a personality in /app/data/poker_games.db.
        self._prior_singleton = AIPokerPlayer._personality_generator
        AIPokerPlayer._personality_generator = _stub_personality_generator()

    def tearDown(self):
        AIPokerPlayer._personality_generator = self._prior_singleton

    def test_builds_tiered_controller_with_no_expression_layer(self):
        state_machine = MagicMock()
        game_data: dict = {'owner_id': 'u1'}

        controller = _get_or_build_ff_controller(
            game_data,
            'Villain',
            state_machine,
            'g-1',
        )

        # Tiered class, but expression layer (the LLM call) explicitly off.
        # Without this, FF would still issue LLM calls for narration.
        assert isinstance(controller, TieredBotController)
        assert getattr(controller, 'expression_generator', None) is None

    def test_caches_per_player(self):
        state_machine = MagicMock()
        game_data: dict = {'owner_id': 'u1'}

        a1 = _get_or_build_ff_controller(game_data, 'A', state_machine, 'g-1')
        a2 = _get_or_build_ff_controller(game_data, 'A', state_machine, 'g-1')
        assert a1 is a2  # cache hit

        b = _get_or_build_ff_controller(game_data, 'B', state_machine, 'g-1')
        assert b is not a1  # distinct seat → distinct controller

        # Cache lives on game_data so it dies with the session, not globally.
        assert set(game_data['ff_controllers'].keys()) == {'A', 'B'}


class TestFFAwareSleep(unittest.TestCase):
    """`_ff_aware_sleep` compresses pacing to ~10% when FF is on."""

    def setUp(self):
        # game_state_service is module-bound — set/reset a game directly.
        from flask_app.services import game_state_service

        self._service = game_state_service
        self._service.set_game('ff-sleep-test', {'fast_forward': False})

    def tearDown(self):
        for gid in list(self._service.games.keys()):
            self._service.delete_game(gid)

    def test_passes_through_when_ff_off(self):
        with patch('flask_app.handlers.game_handler.socketio.sleep') as mock_sleep:
            _ff_aware_sleep('ff-sleep-test', 2.0)
            mock_sleep.assert_called_once_with(2.0)

    def test_compresses_when_ff_on(self):
        self._service.set_game('ff-sleep-test', {'fast_forward': True})
        with patch('flask_app.handlers.game_handler.socketio.sleep') as mock_sleep:
            _ff_aware_sleep('ff-sleep-test', 2.0)
            # 10% scaling: 2.0 → 0.2
            assert mock_sleep.call_count == 1
            assert abs(mock_sleep.call_args.args[0] - 0.2) < 1e-9

    def test_short_circuits_on_zero_or_negative(self):
        with patch('flask_app.handlers.game_handler.socketio.sleep') as mock_sleep:
            _ff_aware_sleep('ff-sleep-test', 0)
            _ff_aware_sleep('ff-sleep-test', -1)
            mock_sleep.assert_not_called()

    def test_passes_through_when_game_missing(self):
        # No game_data → can't read flag → behave as if FF is off.
        with patch('flask_app.handlers.game_handler.socketio.sleep') as mock_sleep:
            _ff_aware_sleep('no-such-game', 1.5)
            mock_sleep.assert_called_once_with(1.5)


class TestHandBoundaryReset(unittest.TestCase):
    """`handle_evaluating_hand_phase` clears the FF flag at the hand boundary.

    FF is a single-hand affordance — if the human triggered it while
    folded on hand N, the next hand should resume with normal pacing
    and personality-aware controllers. Without this reset, FF would
    persist into hand N+1 (and beyond) until action lands on the human.
    """

    def _build_game_state(self):
        from core.card import Card
        from poker.poker_game import Player, PokerGameState

        alice = Player(
            name='Alice',
            stack=970,
            is_human=True,
            bet=30,
            hand=(Card('A', 'Spades'), Card('K', 'Hearts')),
            is_folded=False,
        )
        bob = Player(
            name='Bob',
            stack=970,
            is_human=False,
            bet=30,
            hand=(Card('2', 'Clubs'), Card('3', 'Diamonds')),
            is_folded=False,
        )
        return PokerGameState(
            deck=(),
            players=(alice, bob),
            community_cards=(
                Card('7', 'Diamonds'),
                Card('8', 'Clubs'),
                Card('9', 'Spades'),
                Card('Q', 'Hearts'),
                Card('2', 'Spades'),
            ),
            pot={'total': 60, 'Alice': 30, 'Bob': 30},
            current_ante=10,
        )

    def test_clears_fast_forward_on_evaluating_hand_phase(self):
        from flask_app.handlers import game_handler
        from poker.memory.memory_manager import AIMemoryManager

        game_id = 'ff-boundary-test'
        game_state = self._build_game_state()
        mm = AIMemoryManager(game_id)
        mm.on_hand_start(game_state, hand_number=1)

        state_machine = MagicMock()
        state_machine.game_state = game_state
        state_machine.current_phase = None
        state_machine._state_machine = MagicMock()

        game_data = {
            'memory_manager': mm,
            'ai_controllers': {},
            'state_machine': state_machine,
            'owner_id': '',
            'hand_start_stacks': {},
            'short_stack_players': set(),
            'last_announced_phase': None,
            'fast_forward': True,  # the precondition
        }

        patches = [
            patch.object(
                game_handler,
                'socketio',
                MagicMock(
                    emit=MagicMock(),
                    start_background_task=MagicMock(),
                    sleep=MagicMock(),
                ),
            ),
            patch.object(game_handler, 'send_message', MagicMock()),
            patch.object(game_handler, 'hand_history_repo', MagicMock()),
            patch.object(game_handler, 'game_repo', MagicMock()),
            patch.object(game_handler, 'event_repository', MagicMock()),
            patch.object(game_handler, 'coach_repo', MagicMock()),
            patch.object(game_handler, 'handle_eliminations', MagicMock(return_value=False)),
            patch.object(game_handler, 'check_tournament_complete', MagicMock(return_value=False)),
            patch.object(game_handler, 'update_and_emit_game_state', MagicMock()),
            patch.object(game_handler.game_state_service, 'set_game', MagicMock()),
            patch.object(
                game_handler.game_state_service,
                'get_game_owner_info',
                MagicMock(return_value=('', '')),
            ),
            patch.object(
                game_handler,
                'config',
                MagicMock(
                    ENABLE_AI_COMMENTARY=False,
                    ANIMATION_SPEED=0,
                ),
            ),
        ]
        for p in patches:
            p.start()
        try:
            game_handler.handle_evaluating_hand_phase(
                game_id,
                game_data,
                state_machine,
                game_state,
            )
        finally:
            for p in patches:
                p.stop()

        assert game_data['fast_forward'] is False


class _FastForwardRouteBase(unittest.TestCase):
    """Shared tempdb + app instance for the FF route tests.

    Mirrors the pattern in test_cash_sit_route.py — module-level repo
    binding in game_routes captures the first create_app's tempdb, so
    we share one app across all tests in the class.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        cls.test_db.close()
        repos = create_repos(cls.test_db.name)
        cls.repos = repos

        def mock_init_persistence():
            import flask_app.extensions as ext

            for key in (
                'game_repo',
                'user_repo',
                'settings_repo',
                'personality_repo',
                'experiment_repo',
                'prompt_capture_repo',
                'decision_analysis_repo',
                'prompt_preset_repo',
                'capture_label_repo',
                'replay_experiment_repo',
                'llm_repo',
                'guest_tracking_repo',
                'hand_history_repo',
                'tournament_repo',
                'coach_repo',
                'relationship_repo',
                'bankroll_repo',
                'cash_table_repo',
                'chip_ledger_repo',
                'stake_repo',
            ):
                if key in repos:
                    setattr(ext, key, repos[key])
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            cls.app = create_app()
        cls.app.testing = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        try:
            os.unlink(cls.test_db.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        # Authorize as a stub user. `_authorize_game_access` reads
        # `auth_manager` bound at module-import time inside game_routes,
        # so patch THAT binding (patching flask_app.extensions is too late).
        user = {'id': 'u1', 'name': 'Tester', 'tracking_id': None}
        auth_stub = MagicMock(get_current_user=MagicMock(return_value=user))
        self._auth_patcher = patch(
            'flask_app.routes.game_routes.auth_manager',
            auth_stub,
        )
        self._auth_patcher.start()
        self._authz_patcher = patch(
            'poker.authorization.authorization_service',
            MagicMock(
                auth_manager=MagicMock(
                    get_current_user=MagicMock(return_value=user),
                ),
                has_permission=MagicMock(return_value=True),
            ),
        )
        self._authz_patcher.start()

        # Stub game_data in game_state_service — handle_ai_action /
        # progress_game aren't exercised here; we only care that the
        # endpoint flips the flag.
        from flask_app.services import game_state_service

        self._service = game_state_service
        self._service.set_game(
            'test-game',
            {
                'owner_id': 'u1',
                'state_machine': MagicMock(),
                'messages': [],
                'ai_controllers': {},
            },
        )

        # Don't actually drive the game loop from the endpoint — we just
        # want to inspect the flag after the call.
        self._progress_patcher = patch(
            'flask_app.routes.game_routes.progress_game',
            return_value=None,
        )
        self._progress_patcher.start()

    def tearDown(self):
        self._progress_patcher.stop()
        self._authz_patcher.stop()
        self._auth_patcher.stop()
        for gid in list(self._service.games.keys()):
            self._service.delete_game(gid)


class TestFastForwardEndpoint(_FastForwardRouteBase):
    def test_enables_flag_by_default(self):
        resp = self.client.post('/api/game/test-game/fast-forward', json={})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['fast_forward'] is True
        assert self._service.get_game('test-game')['fast_forward'] is True

    def test_explicit_disable(self):
        # First enable, then disable.
        self.client.post('/api/game/test-game/fast-forward', json={'enabled': True})
        resp = self.client.post(
            '/api/game/test-game/fast-forward',
            json={'enabled': False},
        )
        assert resp.status_code == 200
        assert resp.get_json()['fast_forward'] is False
        assert self._service.get_game('test-game')['fast_forward'] is False

    def test_rejects_non_bool_enabled(self):
        resp = self.client.post(
            '/api/game/test-game/fast-forward',
            json={'enabled': 'yes'},
        )
        assert resp.status_code == 400

    def test_404_when_game_missing(self):
        resp = self.client.post('/api/game/no-such-game/fast-forward', json={})
        assert resp.status_code == 404
