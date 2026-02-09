"""
Shared pytest fixtures for the my-poker-face test suite.

These fixtures are available to all tests under tests/.
Existing unittest.TestCase classes are NOT affected -- they continue
to use their own setUp/tearDown.  New pytest-style tests should
prefer these fixtures.

The make_openai_response() helper is a plain function that can also
be imported by unittest.TestCase classes:

    from tests.conftest import make_openai_response

The load_personality_from_json() helper loads personality data directly
from personalities.json, bypassing the database and LLM generation:

    from tests.conftest import load_personality_from_json
"""
import json
import os
from pathlib import Path

# Raise rate limits for tests to prevent 429s when multiple test modules
# share a pytest-xdist worker and hit rate-limited endpoints.
os.environ.setdefault('RATE_LIMIT_NEW_GAME', '10000 per minute')
os.environ.setdefault('RATE_LIMIT_GAME_ACTION', '10000 per minute')

import pytest
from unittest.mock import Mock, patch

from poker.repositories import create_repos
from poker.poker_game import initialize_game_state


# ---------------------------------------------------------------------------
# Personality loading from JSON (bypasses DB and LLM)
# ---------------------------------------------------------------------------

_PERSONALITIES_JSON_PATH = Path(__file__).parent.parent / "poker" / "personalities.json"
_personalities_cache = None


def _load_all_personalities():
    """Load and cache all personalities from personalities.json."""
    global _personalities_cache
    if _personalities_cache is None:
        with open(_PERSONALITIES_JSON_PATH) as f:
            data = json.load(f)
        _personalities_cache = data.get("personalities", data)
    return _personalities_cache


def load_personality_from_json(name):
    """Load a single personality config from personalities.json.

    Returns the personality dict, or a default config if not found.
    Usable from both pytest fixtures and unittest.TestCase methods::

        from tests.conftest import load_personality_from_json

    Can be used as a side_effect for mocking _load_personality_config::

        @patch.object(AIPokerPlayer, '_load_personality_config',
                      side_effect=lambda self: load_personality_from_json(self.name))
    """
    all_personalities = _load_all_personalities()
    return all_personalities.get(name, {
        "play_style": "balanced",
        "default_confidence": "Unsure",
        "default_attitude": "Distracted",
        "personality_traits": {
            "bluff_tendency": 0.5,
            "aggression": 0.5,
            "chattiness": 0.5,
            "emoji_usage": 0.3,
        },
    })


# ---------------------------------------------------------------------------
# Temporary database + repositories
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Yield a path to a fresh temporary SQLite database file.

    Uses pytest's built-in tmp_path so cleanup is automatic.
    """
    return str(tmp_path / "test_poker.db")


@pytest.fixture
def repos(db_path):
    """Yield a dict of all repository instances backed by a temporary database."""
    return create_repos(db_path)


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

    # create_repos ensures schema is initialized
    create_repos(db_path)

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
