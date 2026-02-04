"""Tests for T1-16: Secure game ID generation.

Tests the actual production generate_game_id function from flask_app.routes.game_routes.
"""
import re
import os
import tempfile
import pytest
from unittest.mock import patch

# Set testing environment before importing flask modules
os.environ['TESTING'] = '1'


@pytest.fixture(scope='module')
def generate_game_id():
    """Import the real generate_game_id function once for all tests.

    The function itself is a pure function (just secrets.token_urlsafe),
    so it doesn't need app context to run - only to import the module.
    """
    from flask_app import create_app
    from poker.repositories import create_repos

    # Create a temporary database for this test module
    test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    test_db.close()
    repos = create_repos(test_db.name)

    # Patch init_persistence to use our test DB repos
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
