"""Tests for `SideHustleStateRepository` (schema v114).

Mirror of the vice-state coverage: insert / list_active / list_expired /
load / delete / is_on_hustle / active_pids round-trip correctly and stay
per-sandbox scoped. See `docs/plans/CASH_MODE_SIDE_HUSTLE.md`.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.side_hustle_state_repository import (
    SideHustleState,
    SideHustleStateRepository,
)

SBX = "test-sandbox"
NOW = datetime(2026, 5, 24, 12, 0, 0)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "side_hustle.db")
        SchemaManager(db_path).ensure_schema()
        r = SideHustleStateRepository(db_path)
        yield r
        r.close()


def _state(pid="napoleon", *, sandbox_id=SBX, started=NOW, ends=None,
           amount=500, bucket="medium", narration="off to flip a small business"):
    return SideHustleState(
        personality_id=pid,
        sandbox_id=sandbox_id,
        started_at=started,
        ends_at=ends if ends is not None else NOW + timedelta(hours=1),
        amount=amount,
        duration_bucket=bucket,
        narration=narration,
    )


class TestInsertAndLoad:
    def test_insert_round_trips(self, repo):
        repo.insert_side_hustle_state(_state())
        loaded = repo.load("napoleon", sandbox_id=SBX)
        assert loaded is not None
        assert loaded.personality_id == "napoleon"
        assert loaded.amount == 500
        assert loaded.duration_bucket == "medium"
        assert loaded.narration == "off to flip a small business"
        assert loaded.started_at == NOW
        assert loaded.ends_at == NOW + timedelta(hours=1)

    def test_load_missing_returns_none(self, repo):
        assert repo.load("nobody", sandbox_id=SBX) is None

    def test_reinsert_is_idempotent(self, repo):
        repo.insert_side_hustle_state(_state(amount=500))
        repo.insert_side_hustle_state(_state(amount=900))  # same key, new target
        loaded = repo.load("napoleon", sandbox_id=SBX)
        assert loaded.amount == 900
        # Still exactly one row for the key.
        assert len(repo.list_active(sandbox_id=SBX, now=NOW)) == 1


class TestActiveVsExpired:
    def test_active_and_expired_split_on_ends_at(self, repo):
        repo.insert_side_hustle_state(
            _state("active_guy", ends=NOW + timedelta(minutes=30)))
        repo.insert_side_hustle_state(
            _state("expired_guy", ends=NOW - timedelta(minutes=1)))

        active = repo.list_active(sandbox_id=SBX, now=NOW)
        expired = repo.list_expired(sandbox_id=SBX, now=NOW)

        assert [s.personality_id for s in active] == ["active_guy"]
        assert [s.personality_id for s in expired] == ["expired_guy"]

    def test_ends_at_exactly_now_is_expired(self, repo):
        # ends_at <= now is expired; ends_at > now is active.
        repo.insert_side_hustle_state(_state("boundary", ends=NOW))
        assert repo.list_active(sandbox_id=SBX, now=NOW) == []
        assert len(repo.list_expired(sandbox_id=SBX, now=NOW)) == 1

    def test_lists_ordered_by_ends_at(self, repo):
        repo.insert_side_hustle_state(_state("late", ends=NOW + timedelta(hours=3)))
        repo.insert_side_hustle_state(_state("soon", ends=NOW + timedelta(minutes=10)))
        active = repo.list_active(sandbox_id=SBX, now=NOW)
        assert [s.personality_id for s in active] == ["soon", "late"]


class TestDelete:
    def test_delete_removes_row(self, repo):
        repo.insert_side_hustle_state(_state())
        assert repo.delete("napoleon", sandbox_id=SBX) is True
        assert repo.load("napoleon", sandbox_id=SBX) is None

    def test_delete_missing_returns_false(self, repo):
        assert repo.delete("nobody", sandbox_id=SBX) is False


class TestPredicates:
    def test_is_on_hustle_true_when_active(self, repo):
        repo.insert_side_hustle_state(_state(ends=NOW + timedelta(minutes=5)))
        assert repo.is_on_hustle("napoleon", sandbox_id=SBX, now=NOW) is True

    def test_is_on_hustle_false_when_expired(self, repo):
        repo.insert_side_hustle_state(_state(ends=NOW - timedelta(minutes=5)))
        assert repo.is_on_hustle("napoleon", sandbox_id=SBX, now=NOW) is False

    def test_active_pids_returns_only_unexpired(self, repo):
        repo.insert_side_hustle_state(_state("a", ends=NOW + timedelta(minutes=5)))
        repo.insert_side_hustle_state(_state("b", ends=NOW - timedelta(minutes=5)))
        assert repo.active_pids(sandbox_id=SBX, now=NOW) == {"a"}


class TestSandboxIsolation:
    def test_state_is_scoped_per_sandbox(self, repo):
        repo.insert_side_hustle_state(_state("napoleon", sandbox_id="sb-a"))
        # Same AI, different sandbox — independent.
        assert repo.load("napoleon", sandbox_id="sb-a") is not None
        assert repo.load("napoleon", sandbox_id="sb-b") is None
        assert repo.active_pids(sandbox_id="sb-b", now=NOW) == set()

    def test_delete_only_affects_target_sandbox(self, repo):
        repo.insert_side_hustle_state(_state("napoleon", sandbox_id="sb-a"))
        repo.insert_side_hustle_state(_state("napoleon", sandbox_id="sb-b"))
        repo.delete("napoleon", sandbox_id="sb-a")
        assert repo.load("napoleon", sandbox_id="sb-a") is None
        assert repo.load("napoleon", sandbox_id="sb-b") is not None
