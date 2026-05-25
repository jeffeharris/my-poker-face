"""Tests for PersonalityRepository.list_eligible_for_cash_mode.

Verifies the cash-mode candidate query: returns personality_id +
name for public personalities (plus user-private when user_id is
passed), deterministic ordering by personality_id, NULL ids
excluded.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = pytest.mark.integration

from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "personalities.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = PersonalityRepository(db_path)
    yield r
    r.close()


def _insert(db_path, *, name, personality_id, visibility="public", owner_id=None):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personalities (name, config_json, personality_id, visibility, owner_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, "{}", personality_id, visibility, owner_id),
        )
        conn.commit()


class TestListEligibleForCashMode:
    def test_empty_db_returns_empty_list(self, repo):
        assert repo.list_eligible_for_cash_mode() == []

    def test_returns_public_personalities(self, db_path, repo):
        _insert(db_path, name="Bob Ross", personality_id="bob_ross")
        _insert(db_path, name="Napoleon", personality_id="napoleon")
        result = repo.list_eligible_for_cash_mode()
        assert len(result) == 2
        ids = {r["personality_id"] for r in result}
        assert ids == {"bob_ross", "napoleon"}

    def test_excludes_private_when_no_user_id(self, db_path, repo):
        _insert(db_path, name="Public Pat", personality_id="public_pat")
        _insert(
            db_path,
            name="Private Pete",
            personality_id="private_pete",
            visibility="private",
            owner_id="user_42",
        )
        result = repo.list_eligible_for_cash_mode()
        ids = {r["personality_id"] for r in result}
        assert ids == {"public_pat"}

    def test_includes_user_private_when_user_id_passed(self, db_path, repo):
        _insert(db_path, name="Public Pat", personality_id="public_pat")
        _insert(
            db_path,
            name="My Custom",
            personality_id="my_custom",
            visibility="private",
            owner_id="user_42",
        )
        _insert(
            db_path,
            name="Other User's",
            personality_id="other_users",
            visibility="private",
            owner_id="user_999",
        )
        result = repo.list_eligible_for_cash_mode(user_id="user_42")
        ids = {r["personality_id"] for r in result}
        assert ids == {"public_pat", "my_custom"}
        # user_42's own private is included; user_999's is not.

    def test_excludes_disabled(self, db_path, repo):
        _insert(db_path, name="Public Pat", personality_id="public_pat")
        _insert(
            db_path,
            name="Banned Bob",
            personality_id="banned_bob",
            visibility="disabled",
        )
        result = repo.list_eligible_for_cash_mode()
        ids = {r["personality_id"] for r in result}
        assert ids == {"public_pat"}

    def test_excludes_null_personality_id(self, db_path, repo):
        # Insert a row with NULL personality_id (pre-v85 leftover state)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, visibility) " "VALUES (?, ?, ?)",
                ("No ID", "{}", "public"),
            )
            conn.commit()
        _insert(db_path, name="Public Pat", personality_id="public_pat")
        result = repo.list_eligible_for_cash_mode()
        ids = {r["personality_id"] for r in result}
        assert ids == {"public_pat"}

    def test_ordering_is_deterministic_by_personality_id(self, db_path, repo):
        # Insert in non-sorted order
        for pid in ["zeus", "abraham_lincoln", "napoleon", "bob_ross"]:
            _insert(db_path, name=pid.title(), personality_id=pid)
        result = repo.list_eligible_for_cash_mode()
        ids = [r["personality_id"] for r in result]
        # Sorted ascending by personality_id (alphabetical)
        assert ids == ["abraham_lincoln", "bob_ross", "napoleon", "zeus"]

    def test_returns_name_alongside_id(self, db_path, repo):
        _insert(db_path, name="Abraham Lincoln", personality_id="abraham_lincoln")
        result = repo.list_eligible_for_cash_mode()
        assert result == [{"personality_id": "abraham_lincoln", "name": "Abraham Lincoln"}]

    def test_excludes_rule_bot_stand_ins(self, db_path, repo):
        # CaseBot / GTO-Lite / BaselineSolver are seeded for tournament-
        # mode picker symmetry but should not appear as cash opponents.
        _insert(db_path, name="Abraham Lincoln", personality_id="abraham_lincoln")
        _insert(db_path, name="CaseBot", personality_id="casebot")
        _insert(db_path, name="GTO-Lite", personality_id="gto_lite")
        _insert(db_path, name="BaselineSolver", personality_id="baselinesolver")
        result = repo.list_eligible_for_cash_mode()
        ids = {r["personality_id"] for r in result}
        assert ids == {"abraham_lincoln"}
