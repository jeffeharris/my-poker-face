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
# share a pytest-xdist worker and hit rate-limited endpoints. Use plain
# assignment rather than setdefault — docker compose injects production
# limits (e.g. RATE_LIMIT_NEW_GAME='10 per hour') into the container env,
# which would otherwise win over setdefault and trip 429s across tests.
os.environ['RATE_LIMIT_NEW_GAME'] = '10000 per minute'
os.environ['RATE_LIMIT_GAME_ACTION'] = '10000 per minute'

# Disable cash-mode movement-narrative LLM calls across the WHOLE suite.
# The seated-table chat path (`flask_app/handlers/game_handler.py:
# _emit_seated_movement_chat`) and any lobby route exercising the worker
# queue would otherwise fire real FAST-tier calls during integration
# tests — burning tokens without test signal. Lives in the top-level
# conftest (not just `tests/test_cash_mode/conftest.py`) because many
# cash-mode integration tests live OUTSIDE that subdirectory
# (`tests/test_cash_lobby_route.py`, `tests/test_cash_sit_route.py`,
# etc.) and don't see the per-package conftest.
os.environ.setdefault('CASH_LEAVE_NARRATIVE_ENABLED', '0')

# Disable the realtime world ticker across the suite. The ticker is a
# background daemon started in create_app(); left on, it runs against
# torn-down per-test DBs (noisy "tick failed" logs, latent flake) and,
# more importantly, makes `GET /api/cash/lobby` a pure read — moving
# world-advancement off the request path onto an async thread, which
# breaks tests that expect a lobby read to advance movement synchronously.
# Off in tests, `get_lobby` keeps its synchronous read-driven refresh
# fallback, preserving prior behavior. Plain assignment so a compose-
# injected value can't flip it on. The ticker's own unit tests
# (`test_ticker_service.py`) override this var explicitly per-test.
os.environ['WORLD_TICKER_ENABLED'] = 'false'

# Disable AI avatar (character image) generation across the WHOLE suite.
# Integration tests that build/play a real game hit the on-demand avatar path
# (game-state serialization -> avatar_handler.start_single_emotion_generation
# -> poker.character_images.generate_character_images), which makes a real
# image-provider HTTP call. Without a key that 401s (noise); WITH a key on a
# dev box it would actually generate images and burn credits. Set before
# flask_app.config is imported (config reads this at import). Plain assignment
# so a compose-injected value can't flip it back on; an avatar-gen test can
# monkeypatch flask_app.config.ENABLE_AVATAR_GENERATION explicitly.
os.environ['ENABLE_AVATAR_GENERATION'] = 'false'

from unittest.mock import Mock, patch

import pytest

from poker.poker_game import initialize_game_state
from poker.repositories import create_repos

# Activate the SchemaManager schema-template fast-path for the whole suite.
# create_repos()/SchemaManager.ensure_schema() builds a fresh DB by running the
# full migration chain (~5.2s/call) -- the dominant per-test cost. With this flag
# set, the first empty-DB build per process is snapshotted and every subsequent
# fresh build is seeded from that snapshot (~10ms). Identical resulting schema;
# only set in tests, so production behavior is unchanged. See
# poker/repositories/schema_manager.py:_maybe_seed_from_template.
# Default on; allow an explicit override (e.g. POKER_TEST_SCHEMA_TEMPLATE=0) for A/B.
os.environ['POKER_TEST_SCHEMA_TEMPLATE'] = os.environ.get('POKER_TEST_SCHEMA_TEMPLATE') or '1'


# ---------------------------------------------------------------------------
# Marker backfill for legacy (mostly unittest) modules
# ---------------------------------------------------------------------------
# Several slow/simulation modules predate the marker scheme and are
# unittest.TestCase based (no `import pytest` to hang a module `pytestmark` on).
# Tag them by filename here so `-m "not slow and not simulation"` (the quick
# loop) deselects them, without per-file import churn. Explicit in-file
# @pytest.mark.* still applies on top of this. Keep these lists short and exact;
# prefer in-file markers for new modules.
_SLOW_BY_FILENAME = {
    "test_ai_memory.py",
    "test_message_history_impact.py",
    "test_personality_responses.py",
    "test_reflection_system.py",
}
_SIMULATION_BY_FILENAME = {
    "test_sng_runner.py",
}


def pytest_collection_modifyitems(items):
    for item in items:
        name = os.path.basename(str(item.fspath))
        if name in _SLOW_BY_FILENAME:
            item.add_marker(pytest.mark.slow)
        if name in _SIMULATION_BY_FILENAME:
            item.add_marker(pytest.mark.simulation)


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
    return all_personalities.get(
        name,
        {
            "play_style": "balanced",
            "default_confidence": "Unsure",
            "default_attitude": "Distracted",
            "personality_traits": {
                "bluff_tendency": 0.5,
                "aggression": 0.5,
                "chattiness": 0.5,
                "emoji_usage": 0.3,
            },
        },
    )


# ---------------------------------------------------------------------------
# Economy-flag isolation (suite-wide)
# ---------------------------------------------------------------------------

# Economy flags forced to the OFF test baseline. These read the environment at
# import, and `flask_app.config` calls `load_dotenv(override=True)`, so a
# developer's local `.env` (or a prod container's env) — which ARMS flags like
# CASINO_RESEED_ON_SPENT, GENESIS_RESERVE_ENABLED, CHIP_CUSTODY_ENABLED,
# TOURNAMENT_* — leaks into the pytest process and silently flips behaviour under
# tests that assert the OFF baseline. That broke 6 casino_provisioning + 3
# economy_flags tests locally while CI stayed green. Forcing them OFF reproduces a
# deterministic baseline regardless of ambient env / flag stage.
#
# NOTE: this is the TEST baseline, decoupled from a flag's production stage — many
# of these are STABLE (on in prod) but tests still want the simpler OFF path
# unless a test exercises the feature explicitly (which runs after this fixture →
# wins). The complementary set (economy flags intentionally left ON in tests) is
# TEST_BASELINE_ON_ECONOMY_FLAGS below; together they must cover every non-locked
# economy flag.
#
# DEPRECATION CONTROL POINT: keep this complete. `test_economy_flag_defaults.py`
# fails if RESET ∪ TEST_BASELINE_ON doesn't partition the registry's non-locked
# economy flags — a NEW flag missing from both would re-open the .env-pollution
# hole.
RESET_ECONOMY_FLAGS = (
    "REGEN_ENABLED",
    "VICE_RESERVE_GATED",
    "GENESIS_RESERVE_ENABLED",
    "RAKE_RESERVE_GATED",
    "DIRECTOR_INEQUALITY_RAKE",
    "DIRECTOR_POLICY_HOLD",
    "CASINO_RELATIVE_THRESHOLDS",
    "CASINO_RESEED_ON_SPENT",
    # PRESENCE_AUTHORITY_ENABLED is no longer an `_env_flag(...)` read (the
    # Presence cutover hardwired it True; the old shadow flag was removed), so
    # it's intentionally absent here — `test_economy_flag_defaults` only tracks
    # env-driven flags. The fixture pins PRESENCE_AUTHORITY_ENABLED = True below.
    "CHIP_CUSTODY_ENABLED",
    "CHIP_CUSTODY_DERIVE_READS",
    "TOURNAMENT_CIRCUIT_ENABLED",
    "TOURNAMENT_DRAW_ENABLED",
    "TABLE_AFFINITY_ENABLED",
    "RENOWN_V2_ENABLED",
    "RENOWN_V2_PERSIST_AI",
    "PRESTIGE_SEEKING_ENABLED",
    "CAREER_PROGRESSION_ENABLED",
    "CAREER_VOUCH_ENABLED",
    "INTAKE_WORLD_WARMUP_ENABLED",
)

# Economy flags intentionally left ON in the test baseline — the long-shipped
# "always on" features (formerly hardcoded `True` in economy_flags.py). They are
# not force-reset; their registry default (STABLE, on) carries through. Together
# with RESET_ECONOMY_FLAGS this must cover every non-locked economy flag, so a new
# flag can't be silently forgotten (see test_economy_flag_defaults.py).
TEST_BASELINE_ON_ECONOMY_FLAGS = (
    "SIDE_HUSTLE_ENABLED",
    "RAKE_PLAYER_TABLES",
    "REPUTATION_DEMEANOR_ENABLED",
    "DOSSIER_SCOUTING_GATE_ENABLED",
)


@pytest.fixture(autouse=True)
def _reset_cutover_flags():
    """Reset the cash-mode economy flags to their test baseline for EVERY test
    (see RESET_ECONOMY_FLAGS).

    Makes the suite deterministic regardless of the ambient env / dev `.env`: the
    flags read the environment at import, so a dev/prod container with e.g.
    `CHIP_CUSTODY_ENABLED=1` would otherwise leak into pytest. A test that
    exercises a mode sets the flag explicitly (runs after this fixture, so it
    wins). `test_cash_mode/conftest.py` has its own narrower copy.

    Every flag resets to its code default (False) EXCEPT
    `PRESENCE_AUTHORITY_ENABLED`: the Presence cutover is complete, so it is
    hardwired True in production and reset to True here — forcing it off would
    break every cash-seating test (the dropped `cash_idle_pool` fallback is gone).
    Pinning it to the production value also restores isolation for the few tests
    that mutate the module global directly (vs monkeypatch)."""
    import cash_mode.economy_flags as ef

    prior = {n: getattr(ef, n) for n in RESET_ECONOMY_FLAGS}
    for n in RESET_ECONOMY_FLAGS:
        setattr(ef, n, False)
    ef.PRESENCE_AUTHORITY_ENABLED = True
    try:
        yield
    finally:
        for n, v in prior.items():
            setattr(ef, n, v)


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


@pytest.fixture
def seed_idle():
    """Stage an AI as idle the authoritative way (Presence cutover complete).

    The legacy `cash_idle_pool` cache was dropped (schema v152): an idle AI now
    lives as an `entity_presence` row (state='idle') plus a `cash_idle_metadata`
    satellite row (reason / target_stake / left_at). This helper writes both,
    mirroring exactly what the `save_table` chokepoint emits on a LEAVE→IDLE, so
    tests can stage idle state without a full seat-then-vacate dance.

    Accepts either a repository (anything with a `.db_path`) or a raw db-path
    string. `left_at` may be a datetime or ISO string.
    """
    import sqlite3 as _sqlite3
    from datetime import datetime as _dt

    def _seed(
        repo_or_path,
        personality_id,
        *,
        sandbox_id,
        reason="forced_leave",
        left_at,
        target_stake=None,
    ):
        path = getattr(repo_or_path, "db_path", repo_or_path)
        iso = left_at.isoformat() if isinstance(left_at, _dt) else str(left_at)
        with _sqlite3.connect(path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO entity_presence "
                "(entity_id, sandbox_id, state, table_id, seat_index, updated_at) "
                "VALUES (?, ?, 'idle', NULL, NULL, ?)",
                (f"ai:{personality_id}", sandbox_id, iso),
            )
            conn.execute(
                "INSERT OR REPLACE INTO cash_idle_metadata "
                "(personality_id, sandbox_id, reason, target_stake, left_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (personality_id, sandbox_id, reason, target_stake, iso),
            )

    return _seed


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
