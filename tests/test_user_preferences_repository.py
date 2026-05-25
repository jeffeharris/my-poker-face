"""Tests for `UserPreferencesRepository` (schema v115).

Round-trips world_pace, defaults gracefully on a missing/invalid row,
and upserts. See `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.user_preferences_repository import (
    DEFAULT_WORLD_PACE,
    UserPreferencesRepository,
)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "prefs.db")
        SchemaManager(db_path).ensure_schema()
        r = UserPreferencesRepository(db_path)
        yield r
        r.close()


def test_missing_row_returns_default(repo):
    assert repo.get_world_pace("nobody") == DEFAULT_WORLD_PACE


def test_set_and_get_round_trips(repo):
    repo.set_world_pace("u1", "bustling")
    assert repo.get_world_pace("u1") == "bustling"


def test_upsert_overwrites(repo):
    repo.set_world_pace("u1", "bustling")
    repo.set_world_pace("u1", "subtle")
    assert repo.get_world_pace("u1") == "subtle"


def test_invalid_pace_rejected(repo):
    with pytest.raises(ValueError):
        repo.set_world_pace("u1", "turbo")


def test_per_user_scoped(repo):
    repo.set_world_pace("u1", "subtle")
    repo.set_world_pace("u2", "bustling")
    assert repo.get_world_pace("u1") == "subtle"
    assert repo.get_world_pace("u2") == "bustling"


def test_unrecognized_stored_value_degrades_to_default(repo):
    # Simulate a future pace rolled back: write a raw value the current
    # code doesn't know, confirm reads don't blow up.
    with repo._get_connection() as conn:
        conn.execute(
            "INSERT INTO user_preferences (user_id, world_pace) VALUES (?, ?)",
            ("u3", "frenetic"),
        )
    assert repo.get_world_pace("u3") == DEFAULT_WORLD_PACE
