"""Tests for T1-27 — chat session ownership enforcement.

Previously `get_chat_session(session_id)` and `archive_chat_session(session_id)`
were owner-blind, so anyone who knew or guessed a session_id could
read or archive another user's chat. The fix adds an `expected_owner_id`
parameter to both helpers; the route layer passes the caller's
authenticated owner so the ownership check happens at the data layer.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(tmp_path):
    from poker.repositories.experiment_repository import ExperimentRepository
    from poker.repositories.game_repository import GameRepository
    from poker.repositories.schema_manager import SchemaManager

    db_path = str(tmp_path / "chat_owner.db")
    SchemaManager(db_path).ensure_schema()
    game_repo = GameRepository(db_path)
    r = ExperimentRepository(db_path, game_repo)
    yield r
    r.close()
    game_repo.close()


@pytest.fixture
def two_sessions(repo):
    """Seed two chat sessions belonging to different owners."""
    repo.save_chat_session(
        session_id="alice_session",
        owner_id="user_alice",
        messages=[{"role": "user", "content": "alice's secret"}],
        config_snapshot={"by": "alice"},
        config_versions=[],
    )
    repo.save_chat_session(
        session_id="bob_session",
        owner_id="user_bob",
        messages=[{"role": "user", "content": "bob's secret"}],
        config_snapshot={"by": "bob"},
        config_versions=[],
    )
    return repo


class TestGetChatSessionOwnership:
    def test_legacy_unscoped_lookup_still_works(self, two_sessions):
        """Internal callers without an authenticated owner context
        (admin tools, migrations) can omit expected_owner_id and get
        the legacy unscoped behavior."""
        result = two_sessions.get_chat_session("alice_session")
        assert result is not None
        assert result["session_id"] == "alice_session"

    def test_correct_owner_can_read(self, two_sessions):
        result = two_sessions.get_chat_session("alice_session", expected_owner_id="user_alice")
        assert result is not None
        assert result["messages"][0]["content"] == "alice's secret"

    def test_wrong_owner_returns_none(self, two_sessions):
        """T1-27: an attacker who knows a session_id but isn't the owner
        gets None — same shape as a missing session, no information leak."""
        result = two_sessions.get_chat_session("alice_session", expected_owner_id="user_bob")
        assert result is None

    def test_missing_session_with_owner_returns_none(self, two_sessions):
        result = two_sessions.get_chat_session("nonexistent", expected_owner_id="user_alice")
        assert result is None


class TestArchiveChatSessionOwnership:
    def test_legacy_unscoped_archive_still_works(self, two_sessions):
        """Internal callers can omit expected_owner_id."""
        archived = two_sessions.archive_chat_session("alice_session")
        assert archived is True

    def test_correct_owner_can_archive(self, two_sessions):
        archived = two_sessions.archive_chat_session(
            "alice_session",
            expected_owner_id="user_alice",
        )
        assert archived is True

    def test_wrong_owner_cannot_archive(self, two_sessions):
        archived = two_sessions.archive_chat_session(
            "alice_session",
            expected_owner_id="user_bob",
        )
        assert archived is False

        # Verify alice's session is NOT archived — still retrievable
        result = two_sessions.get_chat_session(
            "alice_session",
            expected_owner_id="user_alice",
        )
        assert result is not None

    def test_missing_session_returns_false(self, two_sessions):
        archived = two_sessions.archive_chat_session(
            "nonexistent",
            expected_owner_id="user_alice",
        )
        assert archived is False
