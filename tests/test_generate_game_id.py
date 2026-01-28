"""Tests for T1-16: Secure game ID generation.

Tests the actual production generate_game_id function from flask_app.routes.game_routes.
"""
import re
import os
import pytest

# Set testing environment before importing flask modules
os.environ['TESTING'] = '1'


@pytest.fixture(scope='module')
def generate_game_id():
    """Import the real generate_game_id function once for all tests.

    The function itself is a pure function (just secrets.token_urlsafe),
    so it doesn't need app context to run - only to import the module.
    """
    from flask_app import create_app
    app = create_app()
    app.config['TESTING'] = True

    with app.app_context():
        from flask_app.routes.game_routes import generate_game_id as fn
        return fn


def test_generate_game_id_returns_nonempty_string(generate_game_id):
    game_id = generate_game_id()
    assert isinstance(game_id, str)
    assert len(game_id) > 0


def test_generate_game_id_uniqueness(generate_game_id):
    """Generate 100 IDs and verify all are unique."""
    ids = [generate_game_id() for _ in range(100)]
    assert len(set(ids)) == 100


def test_generate_game_id_not_numeric(generate_game_id):
    """IDs should not be purely numeric (old time-based pattern)."""
    ids = [generate_game_id() for _ in range(20)]
    # With token_urlsafe(16), it's astronomically unlikely all 20 would be purely digits
    numeric_count = sum(1 for gid in ids if gid.isdigit())
    assert numeric_count < 20, "IDs appear to still be time-based numeric strings"


def test_generate_game_id_url_safe(generate_game_id):
    """IDs should only contain URL-safe characters."""
    url_safe_pattern = re.compile(r'^[A-Za-z0-9_-]+$')
    for _ in range(20):
        game_id = generate_game_id()
        assert url_safe_pattern.match(game_id), f"ID contains non-URL-safe characters: {game_id}"


def test_generate_game_id_length(generate_game_id):
    """token_urlsafe(16) produces a 22-character string."""
    game_id = generate_game_id()
    assert len(game_id) == 22
