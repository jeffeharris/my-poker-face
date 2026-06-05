"""AI chat messages carry the speaker's avatar URL so the chat bubble can
render their face even after they've left the table.

The in-character farewell an AI emits when it leaves a cash table fires
asynchronously — a beat after the player is removed from the live game state
and `ai_controllers`. By then the frontend's seat-derived avatar cache no
longer has them, so the bubble would go blank. Stamping the avatar URL on the
message itself (here) makes the comment self-describing.
"""

from types import SimpleNamespace
from unittest.mock import patch

from flask_app.handlers.message_handler import send_message
from flask_app.services import game_state_service


def _reset_state():
    game_state_service.games.clear()
    game_state_service.game_locks.clear()
    game_state_service.game_last_access.clear()


class _FakeEmotionalState:
    def __init__(self, emotion):
        self._emotion = emotion

    def get_display_emotion(self):
        return self._emotion


class TestAiMessageAvatar:
    def setup_method(self):
        _reset_state()

    def teardown_method(self):
        _reset_state()

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.game_repo")
    @patch("flask_app.handlers.avatar_handler.get_avatar_url_with_fallback")
    def test_seated_ai_message_uses_controller_emotion(
        self, mock_avatar, mock_game_repo, mock_socketio
    ):
        mock_avatar.return_value = "/api/avatar/Batman/angry"
        controller = SimpleNamespace(emotional_state=_FakeEmotionalState("angry"))
        game_state_service.set_game(
            "g1", {"messages": [], "ai_controllers": {"Batman": controller}}
        )

        send_message("g1", "Batman", "Your move.", "ai")

        mock_avatar.assert_called_once_with("g1", "Batman", "angry")
        msg = game_state_service.get_game("g1")["messages"][-1]
        assert msg["avatar_url"] == "/api/avatar/Batman/angry"

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.game_repo")
    @patch("flask_app.handlers.avatar_handler.get_avatar_url_with_fallback")
    def test_departed_ai_farewell_falls_back_to_confident(
        self, mock_avatar, mock_game_repo, mock_socketio
    ):
        # Sender is gone from ai_controllers (already left the table).
        mock_avatar.return_value = "/api/avatar/Batman/confident"
        game_state_service.set_game("g1", {"messages": [], "ai_controllers": {}})

        send_message("g1", "Batman", "Cashing out, good game.", "ai")

        mock_avatar.assert_called_once_with("g1", "Batman", "confident")
        msg = game_state_service.get_game("g1")["messages"][-1]
        assert msg["avatar_url"] == "/api/avatar/Batman/confident"

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.game_repo")
    @patch("flask_app.handlers.avatar_handler.get_avatar_url_with_fallback")
    def test_non_ai_message_has_no_avatar(self, mock_avatar, mock_game_repo, mock_socketio):
        game_state_service.set_game("g1", {"messages": [], "ai_controllers": {}})

        send_message("g1", "Table", "Batman left with $500", "system")

        mock_avatar.assert_not_called()
        msg = game_state_service.get_game("g1")["messages"][-1]
        assert "avatar_url" not in msg

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.game_repo")
    @patch("flask_app.handlers.avatar_handler.get_avatar_url_with_fallback")
    def test_avatar_lookup_failure_does_not_break_message(
        self, mock_avatar, mock_game_repo, mock_socketio
    ):
        mock_avatar.side_effect = RuntimeError("db down")
        game_state_service.set_game("g1", {"messages": [], "ai_controllers": {}})

        send_message("g1", "Batman", "Still here.", "ai")

        msg = game_state_service.get_game("g1")["messages"][-1]
        assert msg["content"] == "Still here."
        assert "avatar_url" not in msg
