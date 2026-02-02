"""
Shared pytest fixtures for the my-poker-face test suite.

These fixtures are available to all tests under tests/.
Existing unittest.TestCase classes are NOT affected -- they continue
to use their own setUp/tearDown.  New pytest-style tests should
prefer these fixtures.

The make_openai_response() helper is a plain function that can also
be imported by unittest.TestCase classes:

    from tests.conftest import make_openai_response
"""
import os

import pytest
from unittest.mock import Mock, patch

from poker.persistence import GamePersistence
from poker.poker_game import initialize_game_state


# ---------------------------------------------------------------------------
# Temporary database + GamePersistence
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Yield a path to a fresh temporary SQLite database file.

    Uses pytest's built-in tmp_path so cleanup is automatic.
    """
    return str(tmp_path / "test_poker.db")


@pytest.fixture
def persistence(db_path):
    """Yield a GamePersistence instance backed by a temporary database."""
    return GamePersistence(db_path)


# ---------------------------------------------------------------------------
# Game state initialization
# ---------------------------------------------------------------------------

@pytest.fixture
def game_state():
    """Return a standard 3-player game state (Alice, Bob, Charlie)."""
    return initialize_game_state(["Alice", "Bob", "Charlie"])


@pytest.fixture
def game_state_factory():
    """Return a factory for creating game states with custom players.

    Usage::

        def test_heads_up(game_state_factory):
            gs = game_state_factory(["P1", "P2"], starting_stack=5000)
    """
    def _make(player_names=None, **kwargs):
        names = player_names or ["Alice", "Bob", "Charlie"]
        return initialize_game_state(names, **kwargs)
    return _make


# ---------------------------------------------------------------------------
# UsageTracker with singleton reset
# ---------------------------------------------------------------------------

@pytest.fixture
def usage_tracker(db_path):
    """Yield a UsageTracker backed by a temp database.

    Resets the UsageTracker singleton before and after the test so
    state does not leak between tests.
    """
    from core.llm import UsageTracker

    # GamePersistence must be created first to initialize tables
    GamePersistence(db_path)

    UsageTracker._instance = None
    tracker = UsageTracker(db_path=db_path)
    yield tracker
    UsageTracker._instance = None


# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture
def flask_app(persistence):
    """Create a Flask app with persistence patched to a temp database."""
    from flask_app import create_app

    app = create_app()
    app.testing = True

    with patch("flask_app.extensions.persistence", persistence):
        yield app


@pytest.fixture
def flask_client(flask_app):
    """Yield a Flask test client."""
    with flask_app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Mock OpenAI response helper
# ---------------------------------------------------------------------------

def make_openai_response(
    content="Hello!",
    finish_reason="stop",
    prompt_tokens=10,
    completion_tokens=5,
):
    """Build a mock OpenAI chat completion response.

    This is a plain function (not a fixture) so it can be used from both
    pytest-style tests and unittest.TestCase methods::

        from tests.conftest import make_openai_response

        resp = make_openai_response(content='{"action":"fold"}')

    Returns a Mock matching the OpenAI ChatCompletion response shape.
    """
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = content
    mock_response.choices[0].finish_reason = finish_reason
    mock_response.usage = Mock()
    mock_response.usage.prompt_tokens = prompt_tokens
    mock_response.usage.completion_tokens = completion_tokens
    mock_response.usage.completion_tokens_details = None
    mock_response.usage.prompt_tokens_details = None
    return mock_response


@pytest.fixture
def mock_openai_response():
    """Fixture wrapper returning the make_openai_response factory.

    Usage::

        def test_llm(mock_openai_response):
            resp = mock_openai_response(content='{"action":"fold"}')
    """
    return make_openai_response
