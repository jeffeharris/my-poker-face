"""Tests for the v87 schema migration and RelationshipRepository.

Covers:
  - Migration creates both tables with the right column shape
  - Schema v87 lands cleanly on existing pre-v87 databases (idempotent)
  - save / load round-trips relationship_states
  - load_relationship_state applies projection (heat decays on read)
  - load_raw_relationship_state does NOT project (snapshot view)
  - load_all_relationships projects every row
  - save / load round-trips cash_pair_stats
  - load_* returns None when no row exists
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from poker.memory.opponent_model import (
    CashPairStats,
    HEAT_DECAY_HALF_LIFE_DAYS,
    HEAT_DECAY_PLATEAU_DAYS,
    RelationshipState,
)
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    """Temp database with full schema (including v87) initialized."""
    path = str(tmp_path / "rel.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = RelationshipRepository(db_path)
    yield r
    r.close()


# --- Migration shape ---


class TestSchemaMigrationV87:
    def test_relationship_states_table_exists(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(relationship_states)")}
            assert "observer_id" in cols
            assert "opponent_id" in cols
            assert "heat" in cols
            assert "respect" in cols
            assert "likability" in cols
            assert "last_seen" in cols
            assert "last_decay_tick" in cols

    def test_cash_pair_stats_table_exists(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(cash_pair_stats)")}
            assert "observer_id" in cols
            assert "opponent_id" in cols
            assert "cumulative_pnl" in cols
            assert "hands_played_cash" in cols

    def test_relationship_states_pk_enforced(self, db_path):
        # Inserting two rows with the same (observer_id, opponent_id) PK
        # must fail.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO relationship_states (observer_id, opponent_id) VALUES (?, ?)",
                ("alice", "bob"),
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO relationship_states (observer_id, opponent_id) VALUES (?, ?)",
                    ("alice", "bob"),
                )

    def test_idempotent_on_rerun(self, db_path):
        # Running the v87 migration on a DB that already has v87 must
        # be a no-op (CREATE TABLE IF NOT EXISTS).
        from poker.repositories.schema_manager import SchemaManager
        sm = SchemaManager.__new__(SchemaManager)
        with sqlite3.connect(db_path) as conn:
            sm._migrate_v87_add_relationship_tables(conn)
            sm._migrate_v87_add_relationship_tables(conn)  # second time
        # No error means idempotent.

    def test_migrates_from_pre_v87_db(self, tmp_path):
        """A DB at v86 should migrate cleanly up to v87 when
        ensure_schema is invoked."""
        path = str(tmp_path / "old.db")
        # First, init full schema (so all earlier migrations apply)
        SchemaManager(path).ensure_schema()
        # Simulate pre-v87 state: drop the v87 tables and remove the
        # v87 row from schema_version (which is an append log keyed by
        # version, NOT a single-row settings table — DELETE the row
        # rather than UPDATE). Also delete any rows for versions >87
        # so MAX(version) drops below 87 and the migration loop will
        # re-apply v87 (and any later migrations).
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TABLE IF EXISTS relationship_states")
            conn.execute("DROP TABLE IF EXISTS cash_pair_stats")
            conn.execute("DELETE FROM schema_version WHERE version >= 87")
            conn.commit()
        # Re-run ensure_schema → should re-apply v87 (and anything later) cleanly
        SchemaManager(path).ensure_schema()
        with sqlite3.connect(path) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "relationship_states" in tables
            assert "cash_pair_stats" in tables
            # v87 row should be back in schema_version
            v87_row = conn.execute(
                "SELECT description FROM schema_version WHERE version = 87"
            ).fetchone()
            assert v87_row is not None


# --- relationship_states repository ---


class TestRelationshipStateRoundTrip:
    def test_save_then_load_basic_round_trip(self, repo):
        state = RelationshipState(
            heat=0.6,
            respect=0.8,
            likability=0.4,
            last_seen=datetime(2026, 5, 17, 12, 0),
            last_decay_tick=datetime(2026, 5, 17, 12, 0),
        )
        repo.save_relationship_state("alice", "bob", state)
        # Use raw load — round-trip should preserve all axes verbatim
        loaded = repo.load_raw_relationship_state("alice", "bob")
        assert loaded is not None
        assert loaded.heat == 0.6
        assert loaded.respect == 0.8
        assert loaded.likability == 0.4
        assert loaded.last_seen == datetime(2026, 5, 17, 12, 0)
        assert loaded.last_decay_tick == datetime(2026, 5, 17, 12, 0)

    def test_load_returns_none_for_unknown_pair(self, repo):
        assert repo.load_relationship_state("nobody", "stranger") is None
        assert repo.load_raw_relationship_state("nobody", "stranger") is None

    def test_save_is_upsert(self, repo):
        # Two saves with the same key → second wins, no IntegrityError
        repo.save_relationship_state("alice", "bob", RelationshipState(heat=0.3))
        repo.save_relationship_state("alice", "bob", RelationshipState(heat=0.7))
        loaded = repo.load_raw_relationship_state("alice", "bob")
        assert loaded.heat == 0.7

    def test_null_timestamps_round_trip(self, repo):
        state = RelationshipState(heat=0.0, last_seen=None, last_decay_tick=None)
        repo.save_relationship_state("alice", "bob", state)
        loaded = repo.load_raw_relationship_state("alice", "bob")
        assert loaded.last_seen is None
        assert loaded.last_decay_tick is None


class TestProjectionOnRead:
    """The whole point of separate load / load_raw methods: production
    callers see the projected heat, never the stale snapshot."""

    def test_load_applies_projection(self, repo):
        # Store a hot rivalry 30 days ago; live heat should have decayed.
        tick = datetime(2026, 4, 17, 12, 0)
        now = datetime(2026, 5, 17, 12, 0)  # 30 days later
        state = RelationshipState(
            heat=0.8,
            last_decay_tick=tick,
            last_seen=tick,
        )
        repo.save_relationship_state("alice", "bob", state)

        # Live read: heat is decayed
        live = repo.load_relationship_state("alice", "bob", now=now)
        assert live is not None
        # 30 days elapsed = 7 plateau + 23 decay days = ~1.64 half-lives
        # 0.8 * 0.5^(23/14) ≈ 0.255
        assert live.heat < 0.8
        assert 0.20 < live.heat < 0.30  # roughly

    def test_load_raw_does_not_project(self, repo):
        tick = datetime(2026, 4, 17, 12, 0)
        state = RelationshipState(heat=0.8, last_decay_tick=tick)
        repo.save_relationship_state("alice", "bob", state)

        raw = repo.load_raw_relationship_state("alice", "bob")
        assert raw is not None
        assert raw.heat == 0.8  # exact stored snapshot

    def test_load_projects_through_plateau(self, repo):
        tick = datetime(2026, 5, 1, 12, 0)
        # 3 days elapsed — still in plateau, heat stays at 0.6
        now = tick + timedelta(days=3)
        repo.save_relationship_state(
            "alice", "bob",
            RelationshipState(heat=0.6, last_decay_tick=tick),
        )
        loaded = repo.load_relationship_state("alice", "bob", now=now)
        assert loaded.heat == 0.6

    def test_load_returns_zero_for_far_future(self, repo):
        tick = datetime(2026, 5, 1, 12, 0)
        now = tick + timedelta(days=365)  # one year — should snap
        repo.save_relationship_state(
            "alice", "bob",
            RelationshipState(heat=0.95, last_decay_tick=tick),
        )
        loaded = repo.load_relationship_state("alice", "bob", now=now)
        assert loaded.heat == 0.0


class TestLoadAllRelationships:
    def test_returns_every_pair_for_observer(self, repo):
        repo.save_relationship_state("alice", "bob", RelationshipState(heat=0.3))
        repo.save_relationship_state("alice", "carol", RelationshipState(heat=0.5))
        repo.save_relationship_state("alice", "dan", RelationshipState(heat=0.0))
        # Other observer — must not show up in alice's read
        repo.save_relationship_state("zeke", "bob", RelationshipState(heat=0.9))

        result = repo.load_all_relationships("alice")
        assert set(result.keys()) == {"bob", "carol", "dan"}
        assert "zeke" not in result

    def test_empty_when_no_relationships(self, repo):
        assert repo.load_all_relationships("nobody") == {}

    def test_applies_projection_to_all_rows(self, repo):
        tick = datetime(2026, 4, 17, 12, 0)
        now = datetime(2026, 5, 17, 12, 0)
        for opp in ("bob", "carol"):
            repo.save_relationship_state(
                "alice", opp,
                RelationshipState(heat=0.8, last_decay_tick=tick),
            )

        result = repo.load_all_relationships("alice", now=now)
        for state in result.values():
            assert state.heat < 0.8  # all decayed


# --- cash_pair_stats repository ---


class TestCashPairStatsRoundTrip:
    def test_save_then_load_basic_round_trip(self, repo):
        stats = CashPairStats(
            observer_id="alice",
            opponent_id="bob",
            cumulative_pnl=15000,
            hands_played_cash=437,
        )
        repo.save_cash_pair_stats("alice", "bob", stats)
        loaded = repo.load_cash_pair_stats("alice", "bob")
        assert loaded is not None
        assert loaded.cumulative_pnl == 15000
        assert loaded.hands_played_cash == 437
        assert loaded.observer_id == "alice"
        assert loaded.opponent_id == "bob"

    def test_load_returns_none_for_unknown_pair(self, repo):
        assert repo.load_cash_pair_stats("nobody", "stranger") is None

    def test_save_is_upsert(self, repo):
        repo.save_cash_pair_stats(
            "alice", "bob",
            CashPairStats("alice", "bob", cumulative_pnl=1000, hands_played_cash=10),
        )
        repo.save_cash_pair_stats(
            "alice", "bob",
            CashPairStats("alice", "bob", cumulative_pnl=-500, hands_played_cash=22),
        )
        loaded = repo.load_cash_pair_stats("alice", "bob")
        assert loaded.cumulative_pnl == -500  # negative — observer lost net
        assert loaded.hands_played_cash == 22

    def test_negative_cumulative_pnl_round_trips(self, repo):
        # Observer-POV PnL is negative when they've lost net to opponent.
        # Mirror pair (opponent, observer) has the positive value.
        repo.save_cash_pair_stats(
            "alice", "bob",
            CashPairStats("alice", "bob", cumulative_pnl=-7500, hands_played_cash=100),
        )
        loaded = repo.load_cash_pair_stats("alice", "bob")
        assert loaded.cumulative_pnl == -7500


# --- nickname_override (v101) ---


class TestNicknameOverrideColumn:
    def test_column_exists_on_fresh_schema(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(relationship_states)")}
            assert "nickname_override" in cols


class TestNicknameOverrideRoundTrip:
    def test_load_returns_none_when_no_row(self, repo):
        assert repo.load_nickname_override("alice", "bob") is None

    def test_load_returns_none_when_row_has_null_override(self, repo):
        # An affinity-only row (created by save_relationship_state)
        # has no override yet — load must distinguish "no row" from
        # "row exists but override is NULL" only via the same None.
        repo.save_relationship_state("alice", "bob", RelationshipState(heat=0.2))
        assert repo.load_nickname_override("alice", "bob") is None

    def test_save_then_load_round_trip(self, repo):
        repo.save_nickname_override("alice", "bob", "tight guy in red")
        assert repo.load_nickname_override("alice", "bob") == "tight guy in red"

    def test_save_is_upsert(self, repo):
        repo.save_nickname_override("alice", "bob", "first label")
        repo.save_nickname_override("alice", "bob", "second label")
        assert repo.load_nickname_override("alice", "bob") == "second label"

    def test_save_strips_whitespace(self, repo):
        repo.save_nickname_override("alice", "bob", "   spaced   ")
        assert repo.load_nickname_override("alice", "bob") == "spaced"

    def test_empty_string_clears_override(self, repo):
        repo.save_nickname_override("alice", "bob", "label")
        repo.save_nickname_override("alice", "bob", "")
        assert repo.load_nickname_override("alice", "bob") is None

    def test_whitespace_only_clears_override(self, repo):
        repo.save_nickname_override("alice", "bob", "label")
        repo.save_nickname_override("alice", "bob", "   \t \n  ")
        assert repo.load_nickname_override("alice", "bob") is None

    def test_none_clears_override(self, repo):
        repo.save_nickname_override("alice", "bob", "label")
        repo.save_nickname_override("alice", "bob", None)
        assert repo.load_nickname_override("alice", "bob") is None

    def test_override_is_per_observer(self, repo):
        # alice and zeke both file overrides on bob; reads must
        # never cross-contaminate.
        repo.save_nickname_override("alice", "bob", "alice's view")
        repo.save_nickname_override("zeke", "bob", "zeke's view")
        assert repo.load_nickname_override("alice", "bob") == "alice's view"
        assert repo.load_nickname_override("zeke", "bob") == "zeke's view"

    def test_override_is_per_opponent(self, repo):
        repo.save_nickname_override("alice", "bob", "bob's label")
        repo.save_nickname_override("alice", "carol", "carol's label")
        assert repo.load_nickname_override("alice", "bob") == "bob's label"
        assert repo.load_nickname_override("alice", "carol") == "carol's label"

    def test_override_independent_of_notes(self, repo):
        # Notes and overrides live in the same row but must be
        # writable independently — touching one mustn't blank the
        # other.
        repo.save_note("alice", "bob", "calls light on the turn")
        repo.save_nickname_override("alice", "bob", "tight one")
        assert repo.load_note("alice", "bob") == "calls light on the turn"
        assert repo.load_nickname_override("alice", "bob") == "tight one"

    def test_override_independent_of_affinity_axes(self, repo):
        # Saving an override on a row with existing affinity axes
        # must leave those axes intact.
        repo.save_relationship_state(
            "alice", "bob",
            RelationshipState(heat=0.6, respect=0.7, likability=0.4),
        )
        repo.save_nickname_override("alice", "bob", "label")
        loaded = repo.load_relationship_state("alice", "bob")
        assert loaded is not None
        assert loaded.respect == 0.7
        assert loaded.likability == 0.4


class TestLoadAllNicknameOverrides:
    def test_empty_when_observer_has_no_overrides(self, repo):
        assert repo.load_all_nickname_overrides("alice") == {}

    def test_returns_only_observer_rows(self, repo):
        repo.save_nickname_override("alice", "bob", "alice's label for bob")
        repo.save_nickname_override("alice", "carol", "alice's label for carol")
        repo.save_nickname_override("zeke", "bob", "zeke's label")
        result = repo.load_all_nickname_overrides("alice")
        assert result == {
            "bob":   "alice's label for bob",
            "carol": "alice's label for carol",
        }

    def test_excludes_null_overrides(self, repo):
        # Rows with non-null other fields but NULL override must
        # not leak into the bulk map — callers treat presence in
        # the dict as "viewer has explicitly renamed this opponent."
        repo.save_relationship_state("alice", "bob", RelationshipState(heat=0.3))
        repo.save_nickname_override("alice", "carol", "labelled")
        result = repo.load_all_nickname_overrides("alice")
        assert "bob" not in result
        assert result == {"carol": "labelled"}

    def test_excludes_empty_string_overrides(self, repo):
        # Empty string is also "no real override" — defensive
        # against rows that might predate the trim-to-NULL behaviour.
        repo.save_nickname_override("alice", "bob", "set")
        repo.save_nickname_override("alice", "bob", "")  # clears via trim
        repo.save_nickname_override("alice", "carol", "kept")
        result = repo.load_all_nickname_overrides("alice")
        assert result == {"carol": "kept"}
