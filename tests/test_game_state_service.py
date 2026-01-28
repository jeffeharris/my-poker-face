"""Tests for game state service TTL eviction (T2-19)."""

from datetime import datetime, timedelta
from unittest.mock import patch

from flask_app.services import game_state_service


def _clear_state():
    """Reset module-level state between tests."""
    game_state_service.games.clear()
    game_state_service.game_locks.clear()
    game_state_service.game_last_access.clear()


class TestGameStateTTLEviction:
    """Test TTL-based eviction for in-memory game state."""

    def setup_method(self):
        _clear_state()

    def teardown_method(self):
        _clear_state()

    def test_set_game_tracks_access_time(self):
        game_state_service.set_game("game1", {"data": "test"})
        assert "game1" in game_state_service.game_last_access
        assert isinstance(game_state_service.game_last_access["game1"], datetime)

    def test_get_game_updates_access_time(self):
        game_state_service.set_game("game1", {"data": "test"})
        old_time = game_state_service.game_last_access["game1"]

        # Access again slightly later
        with patch("flask_app.services.game_state_service.datetime") as mock_dt:
            mock_dt.now.return_value = old_time + timedelta(seconds=10)
            mock_dt.side_effect = datetime
            result = game_state_service.get_game("game1")

        assert result is not None
        assert result["data"] == "test"

    def test_stale_game_evicted_after_ttl(self):
        """Game not accessed within GAME_TTL_HOURS is evicted."""
        game_state_service.set_game("stale_game", {"data": "old"})
        game_state_service.get_game_lock("stale_game")  # create a lock too

        # Manually set last access to 3 hours ago
        game_state_service.game_last_access["stale_game"] = (
            datetime.now() - timedelta(hours=3)
        )

        # Accessing a different game triggers cleanup
        game_state_service.set_game("fresh_game", {"data": "new"})

        # The stale game should have been evicted by cleanup in set_game
        assert game_state_service.get_game("stale_game") is None
        assert "stale_game" not in game_state_service.games
        assert "stale_game" not in game_state_service.game_last_access
        assert "stale_game" not in game_state_service.game_locks

    def test_fresh_game_not_evicted(self):
        """Game accessed within GAME_TTL_HOURS is kept."""
        game_state_service.set_game("fresh_game", {"data": "keep"})

        # Trigger cleanup
        game_state_service._cleanup_stale_games()

        assert game_state_service.get_game("fresh_game") is not None
        assert "fresh_game" in game_state_service.game_last_access

    def test_delete_game_cleans_up_tracking(self):
        """delete_game removes game_last_access and game_locks entries."""
        game_state_service.set_game("game1", {"data": "test"})
        game_state_service.get_game_lock("game1")

        assert "game1" in game_state_service.games
        assert "game1" in game_state_service.game_last_access
        assert "game1" in game_state_service.game_locks

        game_state_service.delete_game("game1")

        assert "game1" not in game_state_service.games
        assert "game1" not in game_state_service.game_last_access
        assert "game1" not in game_state_service.game_locks

    def test_get_game_returns_none_for_evicted(self):
        """Evicted game returns None, enabling lazy reload from DB."""
        game_state_service.set_game("evicted", {"data": "gone"})
        game_state_service.game_last_access["evicted"] = (
            datetime.now() - timedelta(hours=3)
        )

        result = game_state_service.get_game("evicted")
        assert result is None

    def test_multiple_stale_games_evicted(self):
        """Multiple stale games are all evicted in one cleanup pass."""
        for i in range(5):
            game_state_service.set_game(f"stale_{i}", {"data": i})
            game_state_service.game_last_access[f"stale_{i}"] = (
                datetime.now() - timedelta(hours=3)
            )

        game_state_service.set_game("fresh", {"data": "new"})

        # Trigger cleanup
        game_state_service._cleanup_stale_games()

        for i in range(5):
            assert f"stale_{i}" not in game_state_service.games
        assert "fresh" in game_state_service.games
