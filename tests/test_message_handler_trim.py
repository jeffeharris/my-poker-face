"""Tests for message handler in-memory trim (T2-20)."""

from unittest.mock import patch

from flask_app.handlers.message_handler import send_message, MAX_MESSAGES_IN_MEMORY
from flask_app.services import game_state_service


def _reset_state():
    game_state_service.games.clear()
    game_state_service.game_locks.clear()
    game_state_service.game_last_access.clear()


def _make_game_data(num_messages=0):
    """Create a game_data dict with a given number of pre-existing messages."""
    messages = [
        {"id": str(i), "sender": "test", "content": f"msg_{i}",
         "timestamp": "12:00", "message_type": "table"}
        for i in range(num_messages)
    ]
    return {"messages": messages}


class TestMessageTrim:
    def setup_method(self):
        _reset_state()

    def teardown_method(self):
        _reset_state()

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.persistence")
    def test_messages_under_limit_not_trimmed(self, mock_persistence, mock_socketio):
        """Messages under MAX_MESSAGES_IN_MEMORY are all retained."""
        game_data = _make_game_data(num_messages=10)
        game_state_service.set_game("g1", game_data)

        send_message("g1", "player", "hello", "table")

        result = game_state_service.get_game("g1")
        assert len(result["messages"]) == 11

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.persistence")
    def test_messages_trimmed_when_exceeding_limit(self, mock_persistence, mock_socketio):
        """Messages are trimmed to MAX_MESSAGES_IN_MEMORY when limit is exceeded."""
        game_data = _make_game_data(num_messages=MAX_MESSAGES_IN_MEMORY)
        game_state_service.set_game("g1", game_data)

        # This append makes it 201, should trim to 200
        send_message("g1", "player", "new_message", "table")

        result = game_state_service.get_game("g1")
        assert len(result["messages"]) == MAX_MESSAGES_IN_MEMORY

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.persistence")
    def test_most_recent_messages_retained(self, mock_persistence, mock_socketio):
        """After trimming, the most recent messages are kept (oldest dropped)."""
        game_data = _make_game_data(num_messages=MAX_MESSAGES_IN_MEMORY)
        game_state_service.set_game("g1", game_data)

        send_message("g1", "player", "latest_message", "table")

        result = game_state_service.get_game("g1")
        messages = result["messages"]

        # The latest message should be the last one
        assert messages[-1]["content"] == "latest_message"
        # The oldest message (msg_0) should have been dropped
        contents = [m["content"] for m in messages]
        assert "msg_0" not in contents
        # msg_1 should now be the oldest
        assert messages[0]["content"] == "msg_1"

    @patch("flask_app.handlers.message_handler.socketio")
    @patch("flask_app.handlers.message_handler.persistence")
    def test_bulk_append_trims_correctly(self, mock_persistence, mock_socketio):
        """Adding many messages one at a time keeps list at limit."""
        game_data = _make_game_data(num_messages=0)
        game_state_service.set_game("g1", game_data)

        for i in range(250):
            send_message("g1", "player", f"bulk_{i}", "table")

        result = game_state_service.get_game("g1")
        assert len(result["messages"]) == MAX_MESSAGES_IN_MEMORY
        # Last message should be the most recent
        assert result["messages"][-1]["content"] == "bulk_249"

    def test_max_messages_constant_is_200(self):
        """Verify the constant is set to 200."""
        assert MAX_MESSAGES_IN_MEMORY == 200
