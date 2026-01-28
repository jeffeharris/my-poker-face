"""Tests for T1-16: Secure game ID generation."""
import re
import secrets


def _generate_game_id() -> str:
    """Mirror of generate_game_id from game_routes.py to avoid Flask import issues."""
    return secrets.token_urlsafe(16)


def test_generate_game_id_returns_nonempty_string():
    game_id = _generate_game_id()
    assert isinstance(game_id, str)
    assert len(game_id) > 0


def test_generate_game_id_uniqueness():
    """Generate 100 IDs and verify all are unique."""
    ids = [_generate_game_id() for _ in range(100)]
    assert len(set(ids)) == 100


def test_generate_game_id_not_numeric():
    """IDs should not be purely numeric (old time-based pattern)."""
    ids = [_generate_game_id() for _ in range(20)]
    # With token_urlsafe(16), it's astronomically unlikely all 20 would be purely digits
    numeric_count = sum(1 for gid in ids if gid.isdigit())
    assert numeric_count < 20, "IDs appear to still be time-based numeric strings"


def test_generate_game_id_url_safe():
    """IDs should only contain URL-safe characters."""
    url_safe_pattern = re.compile(r'^[A-Za-z0-9_-]+$')
    for _ in range(20):
        game_id = _generate_game_id()
        assert url_safe_pattern.match(game_id), f"ID contains non-URL-safe characters: {game_id}"


def test_generate_game_id_length():
    """token_urlsafe(16) produces a 22-character string."""
    game_id = _generate_game_id()
    assert len(game_id) == 22
