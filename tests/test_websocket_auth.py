"""Tests for T1-18: Owner + is_human auth checks on WebSocket handlers.

Verifies that on_join and handle_player_action reject non-owners
and unauthenticated users silently.
"""
import pytest
from unittest.mock import MagicMock, patch

import flask_app.extensions as ext

pytestmark = pytest.mark.flask


@pytest.fixture(autouse=True)
def _mock_extensions():
    """Mock flask_app.extensions attributes needed for module import.

    game_routes uses `from ..extensions import auth_manager` which copies the
    reference at import time. We must also patch the module-level name in
    game_routes for tests that need a working auth_manager.
    """
    mock_limiter = MagicMock()
    old_limiter = ext.limiter
    ext.limiter = mock_limiter
    yield
    ext.limiter = old_limiter


def _make_game_data(owner_id='owner-123', game_started=False, is_human=True):
    """Create mock game data dict matching GameStateService format."""
    state_machine = MagicMock()
    state_machine.game_state.current_player.is_human = is_human
    state_machine.game_state.current_player.name = 'Player1'
    state_machine.game_state.current_player.bet = 0
    state_machine.game_state.current_player_options = ['fold', 'call', 'raise', 'all_in']
    state_machine.game_state.highest_bet = 20
    data = {
        'owner_id': owner_id,
        'game_started': game_started,
        'state_machine': state_machine,
    }
    return data


def _mock_admin_authz(is_admin: bool):
    """Create a mock authorization service for admin override tests."""
    if not is_admin:
        return None
    authz = MagicMock()
    authz.has_permission.return_value = True
    return authz


def _register_and_get_handlers():
    """Register socket events on a mock sio and return captured handlers."""
    from flask_app.routes.game_routes import register_socket_events

    sio = MagicMock()
    handlers = {}

    def capture_handler(event):
        def decorator(fn):
            handlers[event] = fn
            return fn
        return decorator

    sio.on = capture_handler
    register_socket_events(sio)
    return handlers


class TestOnJoinAuth:
    """Tests for ownership checks in on_join handler."""

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.socketio')
    @patch('flask_app.routes.game_routes.join_room')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_non_owner_cannot_join(self, mock_auth, mock_gss, mock_join_room,
                                    mock_socketio, mock_progress):
        """Non-owner user is rejected silently — no room join, no game start."""
        mock_auth.get_current_user.return_value = {'id': 'other-user'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['join_game']('game-abc')

        mock_join_room.assert_not_called()
        mock_progress.assert_not_called()

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.socketio')
    @patch('flask_app.routes.game_routes.join_room')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager', None)
    def test_unauthenticated_user_cannot_join(self, mock_gss, mock_join_room,
                                               mock_socketio, mock_progress):
        """When auth_manager is None, user is rejected silently."""
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['join_game']('game-abc')

        mock_join_room.assert_not_called()
        mock_progress.assert_not_called()

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.socketio')
    @patch('flask_app.routes.game_routes.join_room')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_auth_returns_none_user_cannot_join(self, mock_auth, mock_gss,
                                                 mock_join_room, mock_socketio,
                                                 mock_progress):
        """Unauthenticated user (get_current_user returns None) is rejected."""
        mock_auth.get_current_user.return_value = None
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['join_game']('game-abc')

        mock_join_room.assert_not_called()
        mock_progress.assert_not_called()

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.socketio')
    @patch('flask_app.routes.game_routes.join_room')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_owner_can_join_and_start(self, mock_auth, mock_gss, mock_join_room,
                                      mock_socketio, mock_progress):
        """Owner is allowed to join the room and trigger game start."""
        mock_auth.get_current_user.return_value = {'id': 'owner-123'}
        game_data = _make_game_data(owner_id='owner-123', game_started=False)
        mock_gss.get_game.return_value = game_data

        handlers = _register_and_get_handlers()
        handlers['join_game']('game-abc')

        mock_join_room.assert_called_once_with('game-abc')
        mock_progress.assert_called_once_with('game-abc')

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.socketio')
    @patch('flask_app.routes.game_routes.join_room')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_nonexistent_game_rejected(self, mock_auth, mock_gss, mock_join_room,
                                        mock_socketio, mock_progress):
        """Non-existent game returns silently."""
        mock_auth.get_current_user.return_value = {'id': 'owner-123'}
        mock_gss.get_game.return_value = None

        handlers = _register_and_get_handlers()
        handlers['join_game']('nonexistent')

        mock_join_room.assert_not_called()


class TestHandlePlayerActionAuth:
    """Tests for ownership + is_human checks in handle_player_action."""

    @patch('flask_app.routes.game_routes.play_turn')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_non_owner_cannot_act(self, mock_auth, mock_gss, mock_play_turn):
        """Non-owner user's action is rejected — play_turn never called."""
        mock_auth.get_current_user.return_value = {'id': 'attacker'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['player_action']({
            'game_id': 'game-abc',
            'action': 'call',
            'amount': 0,
        })

        mock_play_turn.assert_not_called()

    @patch('flask_app.routes.game_routes.play_turn')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_unauthenticated_user_cannot_act(self, mock_auth, mock_gss,
                                              mock_play_turn):
        """Unauthenticated user's action is rejected."""
        mock_auth.get_current_user.return_value = None
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['player_action']({
            'game_id': 'game-abc',
            'action': 'call',
            'amount': 0,
        })

        mock_play_turn.assert_not_called()

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.update_and_emit_game_state')
    @patch('flask_app.routes.game_routes.game_repo')
    @patch('flask_app.routes.game_routes.send_message')
    @patch('flask_app.routes.game_routes.format_action_message')
    @patch('flask_app.routes.game_routes.analyze_player_decision')
    @patch('flask_app.routes.game_routes.record_action_in_memory')
    @patch('flask_app.routes.game_routes.advance_to_next_active_player')
    @patch('flask_app.routes.game_routes.validate_player_action', return_value=(True, ''))
    @patch('flask_app.routes.game_routes.play_turn')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_owner_can_act_on_human_turn(self, mock_auth, mock_gss, mock_play_turn,
                                          mock_validate, mock_advance,
                                          mock_record, mock_analyze,
                                          mock_format, mock_send_msg,
                                          mock_game_repo, mock_update,
                                          mock_progress):
        """Owner can submit actions when it's a human player's turn."""
        mock_auth.get_current_user.return_value = {'id': 'owner-123'}
        game_data = _make_game_data(owner_id='owner-123', is_human=True)
        mock_gss.get_game.return_value = game_data
        mock_play_turn.return_value = game_data['state_machine'].game_state
        mock_advance.return_value = game_data['state_machine'].game_state
        mock_gss.get_game_owner_info.return_value = ('owner-123', 'Owner')

        handlers = _register_and_get_handlers()
        handlers['player_action']({
            'game_id': 'game-abc',
            'action': 'call',
            'amount': 0,
        })

        mock_play_turn.assert_called_once()

    @patch('flask_app.routes.game_routes.play_turn')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_owner_cannot_act_on_ai_turn(self, mock_auth, mock_gss, mock_play_turn):
        """Owner is rejected when it's an AI player's turn."""
        mock_auth.get_current_user.return_value = {'id': 'owner-123'}
        mock_gss.get_game.return_value = _make_game_data(
            owner_id='owner-123', is_human=False
        )

        handlers = _register_and_get_handlers()
        handlers['player_action']({
            'game_id': 'game-abc',
            'action': 'call',
            'amount': 0,
        })

        mock_play_turn.assert_not_called()


class TestHandleSendMessageAuth:
    """Tests for ownership checks in send_message socket handler."""

    @patch('flask_app.routes.game_routes.send_message')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_non_owner_cannot_send_message(self, mock_auth, mock_gss, mock_send_message):
        mock_auth.get_current_user.return_value = {'id': 'attacker'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['send_message']({
            'game_id': 'game-abc',
            'message': 'hello',
            'sender': 'Player',
            'message_type': 'user',
        })

        mock_send_message.assert_not_called()

    @patch('flask_app.routes.game_routes.send_message')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_owner_can_send_message(self, mock_auth, mock_gss, mock_send_message):
        mock_auth.get_current_user.return_value = {'id': 'owner-123'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['send_message']({
            'game_id': 'game-abc',
            'message': 'hello',
            'sender': 'Player',
            'message_type': 'user',
        })

        mock_send_message.assert_called_once_with('game-abc', 'Player', 'hello', 'user')

    @patch('flask_app.routes.game_routes.send_message')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_admin_override_can_send_message(self, mock_auth, mock_gss, mock_send_message):
        mock_auth.get_current_user.return_value = {'id': 'admin-1'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        with patch('flask_app.routes.game_routes.get_authorization_service', return_value=_mock_admin_authz(True)):
            handlers = _register_and_get_handlers()
            handlers['send_message']({
                'game_id': 'game-abc',
                'message': 'hello',
                'sender': 'Player',
                'message_type': 'user',
            })

        mock_send_message.assert_called_once_with('game-abc', 'Player', 'hello', 'user')


class TestProgressGameSocketAuth:
    """Tests for ownership checks in progress_game socket handler."""

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_non_owner_cannot_progress_game(self, mock_auth, mock_gss, mock_progress):
        mock_auth.get_current_user.return_value = {'id': 'attacker'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['progress_game']('game-abc')

        mock_progress.assert_not_called()

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_owner_can_progress_game(self, mock_auth, mock_gss, mock_progress):
        mock_auth.get_current_user.return_value = {'id': 'owner-123'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        handlers = _register_and_get_handlers()
        handlers['progress_game']('game-abc')

        mock_progress.assert_called_once_with('game-abc')

    @patch('flask_app.routes.game_routes.progress_game')
    @patch('flask_app.routes.game_routes.game_state_service')
    @patch('flask_app.routes.game_routes.auth_manager')
    def test_admin_override_can_progress_game(self, mock_auth, mock_gss, mock_progress):
        mock_auth.get_current_user.return_value = {'id': 'admin-1'}
        mock_gss.get_game.return_value = _make_game_data(owner_id='owner-123')

        with patch('flask_app.routes.game_routes.get_authorization_service', return_value=_mock_admin_authz(True)):
            handlers = _register_and_get_handlers()
            handlers['progress_game']('game-abc')

        mock_progress.assert_called_once_with('game-abc')
