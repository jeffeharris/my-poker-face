"""Tests for the v97 emotional_state_json column + repo methods.

Schema v97 added `ai_bankroll_state.emotional_state_json TEXT NULL`
so sim-hand psychology survives cache evictions and backend restarts.
This test file pins the column + repo round-trip; the cache
discipline that uses it (hydrate-on-miss, serialize-on-evict) lives
under cash_mode/controller_cache + its own tests in full-sim Commit 3.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from cash_mode.bankroll import AIBankrollState
from poker.repositories import create_repos


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        repos = create_repos(db_path)
        yield repos["bankroll_repo"]
    finally:
        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass


class TestEmotionalStateRoundTrip:
    def test_save_then_load_returns_same_blob(self, repo):
        blob = '{"state": "tilted", "severity": "moderate", "intensity": 0.6}'
        repo.save_emotional_state_json("napoleon", blob, sandbox_id="test-sandbox-1")
        assert repo.load_emotional_state_json("napoleon", sandbox_id="test-sandbox-1") == blob

    def test_load_missing_personality_returns_none(self, repo):
        assert repo.load_emotional_state_json("nonexistent", sandbox_id="test-sandbox-1") is None

    def test_save_with_none_clears_the_column(self, repo):
        blob = '{"state": "tilted"}'
        repo.save_emotional_state_json("napoleon", blob, sandbox_id="test-sandbox-1")
        assert repo.load_emotional_state_json("napoleon", sandbox_id="test-sandbox-1") == blob
        # Clear:
        repo.save_emotional_state_json("napoleon", None, sandbox_id="test-sandbox-1")
        assert repo.load_emotional_state_json("napoleon", sandbox_id="test-sandbox-1") is None

    def test_save_creates_bankroll_row_if_missing(self, repo):
        """Sim might touch a personality's psychology before any
        chip-event has written a bankroll row. Save must still
        succeed (inserting a placeholder row) rather than silently
        dropping the state."""
        repo.save_emotional_state_json("brand_new_pid", '{"state": "confident"}', sandbox_id="test-sandbox-1")
        bankroll = repo.load_ai_bankroll("brand_new_pid", sandbox_id="test-sandbox-1")
        assert bankroll is not None
        assert bankroll.chips == 0   # placeholder
        assert repo.load_emotional_state_json("brand_new_pid", sandbox_id="test-sandbox-1") == (
            '{"state": "confident"}'
        )

    def test_save_does_not_clobber_existing_chips(self, repo):
        """Writing the emotional-state column must leave chips +
        last_regen_tick untouched — these are written by different
        cadences (chip events vs sim hands)."""
        from datetime import datetime
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon",
            chips=12_345,
            last_regen_tick=datetime(2026, 5, 19, 0, 0, 0),
        ), sandbox_id="test-sandbox-1")

        repo.save_emotional_state_json("napoleon", '{"state": "tilted"}', sandbox_id="test-sandbox-1")

        bankroll = repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert bankroll.chips == 12_345
        assert bankroll.last_regen_tick == datetime(2026, 5, 19, 0, 0, 0)
        assert repo.load_emotional_state_json("napoleon", sandbox_id="test-sandbox-1") == '{"state": "tilted"}'


class TestBatchEmotionalStateLoad:
    """The lobby route uses load_emotional_state_json_for_pids to
    resolve unseated-AI emotions in one query instead of N queries."""

    def test_empty_pid_list_returns_empty_dict(self, repo):
        assert repo.load_emotional_state_json_for_pids([], sandbox_id="test-sandbox-1") == {}

    def test_batch_includes_missing_as_none(self, repo):
        repo.save_emotional_state_json("napoleon", '{"state": "tilted"}', sandbox_id="test-sandbox-1")
        # Buddha doesn't exist; should still appear in result as None.
        result = repo.load_emotional_state_json_for_pids(
            ["napoleon", "buddha"],
            sandbox_id="test-sandbox-1",
        )
        assert result == {
            "napoleon": '{"state": "tilted"}',
            "buddha": None,
        }

    def test_batch_with_null_column_returns_none(self, repo):
        from datetime import datetime
        # Seed a bankroll row without ever flushing psychology.
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="lincoln",
            chips=5000,
            last_regen_tick=datetime(2026, 5, 19, 0, 0, 0),
        ), sandbox_id="test-sandbox-1")
        result = repo.load_emotional_state_json_for_pids(["lincoln"], sandbox_id="test-sandbox-1")
        assert result == {"lincoln": None}
