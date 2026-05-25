"""Tests for SandboxRepository (v100) and resolve_default_sandbox_for.

Round-trips CRUD on the `sandboxes` table, verifies the opaque-UUID
contract, archive semantics, and the in-process resolver cache.
Schema/migration shape is tested by schema_manager's general test
suite; this file covers the data + caching layer.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

import pytest

from flask_app.services import sandbox_resolver
from poker.repositories.sandbox_repository import (
    SandboxRepository,
    SandboxState,
)
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sandbox.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = SandboxRepository(db_path)
    yield r
    r.close()


@pytest.fixture(autouse=True)
def reset_resolver_cache():
    """Each test gets a clean resolver cache.

    Resolver caches at module scope, so tests that don't clear can
    cross-contaminate ("owner_alice → sandbox_X" hangs around even
    after the DB row was dropped between tests).
    """
    sandbox_resolver.clear_cache()
    yield
    sandbox_resolver.clear_cache()


# --- Repo CRUD ----------------------------------------------------------


class TestCreate:
    def test_creates_with_opaque_uuid4(self, repo):
        state = repo.create("owner_alice")
        # Match canonical uuid4 representation; allows the repo to
        # swap libraries without breaking the contract as long as the
        # format stays standard hex+hyphen.
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            state.sandbox_id,
        )
        assert state.owner_id == "owner_alice"

    def test_creates_distinct_ids_on_repeated_calls(self, repo):
        a = repo.create("owner_alice")
        b = repo.create("owner_alice")
        assert a.sandbox_id != b.sandbox_id

    def test_persists_default_name(self, repo):
        state = repo.create("owner_alice")
        assert state.name == "My Casino"

    def test_persists_custom_name(self, repo):
        state = repo.create("owner_alice", name="Experimental Pit")
        assert state.name == "Experimental Pit"

    def test_created_at_is_recent(self, repo):
        before = datetime.utcnow()
        state = repo.create("owner_alice")
        after = datetime.utcnow()
        assert before - timedelta(seconds=1) <= state.created_at <= after + timedelta(seconds=1)
        assert state.archived_at is None


class TestLoad:
    def test_returns_none_for_missing_id(self, repo):
        assert repo.load("does-not-exist") is None

    def test_round_trips_created_state(self, repo):
        created = repo.create("owner_alice", name="My Casino")
        loaded = repo.load(created.sandbox_id)
        assert loaded == created


class TestListForOwner:
    def test_empty_when_no_sandboxes(self, repo):
        assert repo.list_for_owner("owner_alice") == []

    def test_lists_in_creation_order(self, repo):
        a = repo.create("owner_alice", name="First")
        # Force a measurable timestamp gap so created_at ordering is
        # stable even on fast systems where two creates would land
        # in the same microsecond.
        import time

        time.sleep(0.01)
        b = repo.create("owner_alice", name="Second")
        listed = repo.list_for_owner("owner_alice")
        assert [s.sandbox_id for s in listed] == [a.sandbox_id, b.sandbox_id]

    def test_scopes_by_owner(self, repo):
        a = repo.create("owner_alice")
        b = repo.create("owner_bob")
        alice = repo.list_for_owner("owner_alice")
        bob = repo.list_for_owner("owner_bob")
        assert [s.sandbox_id for s in alice] == [a.sandbox_id]
        assert [s.sandbox_id for s in bob] == [b.sandbox_id]

    def test_excludes_archived_by_default(self, repo):
        a = repo.create("owner_alice")
        repo.archive(a.sandbox_id)
        assert repo.list_for_owner("owner_alice") == []

    def test_include_archived_returns_all(self, repo):
        a = repo.create("owner_alice")
        repo.archive(a.sandbox_id)
        listed = repo.list_for_owner("owner_alice", include_archived=True)
        assert len(listed) == 1
        assert listed[0].archived_at is not None


class TestListAll:
    def test_empty_when_no_sandboxes(self, repo):
        assert repo.list_all() == []

    def test_returns_every_owner_in_creation_order(self, repo):
        a = repo.create("owner_alice", name="First")
        import time

        time.sleep(0.01)
        b = repo.create("owner_bob", name="Second")
        listed = repo.list_all()
        assert [s.sandbox_id for s in listed] == [a.sandbox_id, b.sandbox_id]

    def test_excludes_archived_by_default(self, repo):
        a = repo.create("owner_alice")
        b = repo.create("owner_bob")
        repo.archive(a.sandbox_id)
        listed = repo.list_all()
        assert [s.sandbox_id for s in listed] == [b.sandbox_id]

    def test_include_archived_returns_all(self, repo):
        a = repo.create("owner_alice")
        repo.archive(a.sandbox_id)
        listed = repo.list_all(include_archived=True)
        assert len(listed) == 1
        assert listed[0].archived_at is not None


class TestArchive:
    def test_returns_true_when_row_updated(self, repo):
        state = repo.create("owner_alice")
        assert repo.archive(state.sandbox_id) is True

    def test_returns_false_for_missing_row(self, repo):
        assert repo.archive("does-not-exist") is False

    def test_stamps_archived_at(self, repo):
        state = repo.create("owner_alice")
        now = datetime(2026, 5, 20, 12, 0, 0)
        repo.archive(state.sandbox_id, now=now)
        reloaded = repo.load(state.sandbox_id)
        assert reloaded.archived_at == now

    def test_idempotent_under_repeat(self, repo):
        state = repo.create("owner_alice")
        repo.archive(state.sandbox_id, now=datetime(2026, 5, 20, 12, 0, 0))
        # Second archive bumps the timestamp; doesn't crash.
        repo.archive(state.sandbox_id, now=datetime(2026, 5, 20, 13, 0, 0))
        reloaded = repo.load(state.sandbox_id)
        assert reloaded.archived_at == datetime(2026, 5, 20, 13, 0, 0)


# --- Resolver -----------------------------------------------------------


class TestResolver:
    def test_first_access_creates_default_sandbox(self, repo):
        sandbox_id = sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )
        # The created sandbox is the only one owned by alice.
        owned = repo.list_for_owner("owner_alice")
        assert len(owned) == 1
        assert owned[0].sandbox_id == sandbox_id
        assert owned[0].name == "My Casino"

    def test_second_access_returns_existing(self, repo):
        first = sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )
        second = sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )
        assert first == second
        # And no extra row created.
        assert len(repo.list_for_owner("owner_alice")) == 1

    def test_owner_scoped(self, repo):
        a = sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )
        b = sandbox_resolver.resolve_default_sandbox_for(
            "owner_bob",
            sandbox_repo=repo,
        )
        assert a != b

    def test_cache_short_circuits_repo_after_warmup(self, repo, monkeypatch):
        sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )

        # Replace `list_for_owner` so any cache-miss call would raise.
        # The second resolve should hit the cache and never call it.
        def boom(*args, **kwargs):
            raise AssertionError("resolver should have hit the cache")

        monkeypatch.setattr(repo, "list_for_owner", boom)
        sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )

    def test_invalidate_cache_forces_relookup(self, repo):
        first = sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )
        sandbox_resolver.invalidate_cache_for_owner("owner_alice")
        # Archive the first sandbox, manually create a replacement,
        # and confirm the resolver returns the new id rather than the
        # cached stale one.
        repo.archive(first)
        replacement = repo.create("owner_alice", name="My Casino")
        resolved = sandbox_resolver.resolve_default_sandbox_for(
            "owner_alice",
            sandbox_repo=repo,
        )
        assert resolved == replacement.sandbox_id
