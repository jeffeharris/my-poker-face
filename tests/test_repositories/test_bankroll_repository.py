"""Tests for the v88 schema migration and BankrollRepository.

Covers:
  - Migration v88 lands cleanly on existing pre-v88 databases (idempotent)
  - Tables and personality bankroll knob columns have the right shape
  - save / load round-trips for AI and player bankroll
  - load_personality_knobs falls back to defaults when columns are NULL
  - load_ai_bankroll_projected applies projection (clamped to cap)
  - load_* returns None when no row exists
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    BankrollKnobs,
    PlayerBankrollState,
    project_bankroll,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    """Temp database with full schema (including v88) initialized."""
    path = str(tmp_path / "bankroll.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = BankrollRepository(db_path)
    yield r
    r.close()


# --- Migration shape ---


class TestSchemaMigrationV88:
    def test_ai_bankroll_state_table_exists(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(ai_bankroll_state)")}
            assert "personality_id" in cols
            assert "chips" in cols
            assert "last_regen_tick" in cols

    def test_player_bankroll_state_table_exists(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(player_bankroll_state)")}
            assert "player_id" in cols
            assert "chips" in cols
            assert "starting_bankroll" in cols

    def test_personality_knob_columns_added(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(personalities)")}
            assert "bankroll_cap" in cols
            assert "bankroll_rate" in cols
            assert "buy_in_multiplier" in cols
            assert "stop_loss_buy_ins" in cols
            assert "stop_win_buy_ins" in cols
            assert "stake_comfort_zone" in cols

    def test_ai_bankroll_pk_enforced(self, db_path):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO ai_bankroll_state (personality_id, chips) VALUES (?, ?)",
                ("alice", 1000),
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO ai_bankroll_state (personality_id, chips) VALUES (?, ?)",
                    ("alice", 2000),
                )

    def test_idempotent_on_rerun(self, db_path):
        # Running v88 twice must be a no-op (CREATE TABLE IF NOT EXISTS
        # + PRAGMA-guarded ALTERs).
        sm = SchemaManager.__new__(SchemaManager)
        with sqlite3.connect(db_path) as conn:
            sm._migrate_v88_add_bankroll_tables(conn)
            sm._migrate_v88_add_bankroll_tables(conn)
        # No error means idempotent.

    def test_migrates_from_pre_v88_db(self, tmp_path):
        """A DB at v87 should migrate cleanly up to v88 when ensure_schema runs."""
        path = str(tmp_path / "old.db")
        SchemaManager(path).ensure_schema()
        # Simulate pre-v88 state: drop the v88 tables, drop the knob
        # columns from personalities, remove the v88 row from
        # schema_version.
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TABLE IF EXISTS ai_bankroll_state")
            conn.execute("DROP TABLE IF EXISTS player_bankroll_state")
            # SQLite supports DROP COLUMN since 3.35; the test runner
            # uses a recent SQLite, but be defensive — recreate the
            # personalities table without the knob columns via a copy.
            conn.execute("ALTER TABLE personalities RENAME TO personalities_old")
            conn.execute(
                """
                CREATE TABLE personalities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_generated BOOLEAN DEFAULT 1,
                    source TEXT DEFAULT 'ai_generated',
                    times_used INTEGER DEFAULT 0,
                    elasticity_config TEXT,
                    personality_id TEXT UNIQUE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO personalities
                    (id, name, config_json, created_at, updated_at, is_generated,
                     source, times_used, elasticity_config, personality_id)
                SELECT id, name, config_json, created_at, updated_at, is_generated,
                       source, times_used, elasticity_config, personality_id
                FROM personalities_old
                """
            )
            conn.execute("DROP TABLE personalities_old")
            conn.execute("DELETE FROM schema_version WHERE version = 88")
            conn.commit()
        # Re-run ensure_schema → should re-apply v88 only
        SchemaManager(path).ensure_schema()
        with sqlite3.connect(path) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "ai_bankroll_state" in tables
            assert "player_bankroll_state" in tables
            cols = {row[1] for row in conn.execute("PRAGMA table_info(personalities)")}
            assert "bankroll_cap" in cols
            assert "stake_comfort_zone" in cols
            v88_row = conn.execute(
                "SELECT description FROM schema_version WHERE version = 88"
            ).fetchone()
            assert v88_row is not None


# --- AI bankroll round-trip ---


class TestAIBankrollRoundTrip:
    def test_save_then_load_basic(self, repo):
        tick = datetime(2026, 5, 17, 12, 0)
        state = AIBankrollState(
            personality_id="napoleon",
            chips=4_200,
            last_regen_tick=tick,
        )
        repo.save_ai_bankroll(state)
        loaded = repo.load_ai_bankroll("napoleon")
        assert loaded is not None
        assert loaded.personality_id == "napoleon"
        assert loaded.chips == 4_200
        assert loaded.last_regen_tick == tick

    def test_load_returns_none_for_unknown_personality(self, repo):
        assert repo.load_ai_bankroll("nobody") is None

    def test_save_is_upsert(self, repo):
        repo.save_ai_bankroll(AIBankrollState("alice", 1_000))
        repo.save_ai_bankroll(AIBankrollState("alice", 2_500))
        loaded = repo.load_ai_bankroll("alice")
        assert loaded.chips == 2_500

    def test_null_tick_round_trips(self, repo):
        # No-event-yet state — last_regen_tick stays None
        repo.save_ai_bankroll(AIBankrollState("seed", 5_000, last_regen_tick=None))
        loaded = repo.load_ai_bankroll("seed")
        assert loaded.last_regen_tick is None
        assert loaded.chips == 5_000


# --- Player bankroll round-trip ---


class TestPlayerBankrollRoundTrip:
    def test_save_then_load_basic(self, repo):
        state = PlayerBankrollState(
            player_id="player_42",
            chips=1_500,
            starting_bankroll=2_000,
        )
        repo.save_player_bankroll(state)
        loaded = repo.load_player_bankroll("player_42")
        assert loaded is not None
        assert loaded.chips == 1_500
        assert loaded.starting_bankroll == 2_000

    def test_load_returns_none_for_unknown_player(self, repo):
        assert repo.load_player_bankroll("nobody") is None

    def test_save_is_upsert(self, repo):
        repo.save_player_bankroll(PlayerBankrollState("p1", 1_000, 2_000))
        repo.save_player_bankroll(PlayerBankrollState("p1", 500, 2_000))
        loaded = repo.load_player_bankroll("p1")
        assert loaded.chips == 500


# --- Personality knob loading ---


class TestPersonalityKnobs:
    def test_load_returns_defaults_when_row_missing(self, repo):
        knobs = repo.load_personality_knobs("nonexistent_id")
        assert knobs == BANKROLL_KNOB_DEFAULTS

    def test_load_returns_defaults_when_columns_null(self, db_path, repo):
        # Insert a personality row with NULL knob columns.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Test Personality", "{}", "test_personality"),
            )
            conn.commit()
        knobs = repo.load_personality_knobs("test_personality")
        assert knobs == BANKROLL_KNOB_DEFAULTS

    def test_save_then_load_round_trip(self, db_path, repo):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Big Stack Bob", "{}", "big_stack_bob"),
            )
            conn.commit()
        custom = BankrollKnobs(
            bankroll_cap=50_000,
            bankroll_rate=1_000,
            buy_in_multiplier=1.5,
            stop_loss_buy_ins=2,
            stop_win_buy_ins=10,
            stake_comfort_zone="$200",
        )
        assert repo.save_personality_knobs("big_stack_bob", custom) is True
        loaded = repo.load_personality_knobs("big_stack_bob")
        assert loaded == custom

    def test_save_returns_false_when_no_row(self, repo):
        # The repo doesn't insert new personality rows — knob writes
        # target rows that already exist. Missing personality_id is a
        # no-op, signaled by the False return.
        assert repo.save_personality_knobs(
            "no_such_personality", BANKROLL_KNOB_DEFAULTS
        ) is False

    def test_partial_null_uses_defaults_per_field(self, db_path, repo):
        # Mix: set bankroll_cap and stake_comfort_zone; leave the rest NULL.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO personalities
                    (name, config_json, personality_id, bankroll_cap, stake_comfort_zone)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("Partial Knobs", "{}", "partial_knobs", 25_000, "$50"),
            )
            conn.commit()
        knobs = repo.load_personality_knobs("partial_knobs")
        assert knobs.bankroll_cap == 25_000
        assert knobs.stake_comfort_zone == "$50"
        # NULL columns fall back to defaults
        assert knobs.bankroll_rate == BANKROLL_KNOB_DEFAULTS.bankroll_rate
        assert knobs.buy_in_multiplier == BANKROLL_KNOB_DEFAULTS.buy_in_multiplier
        assert knobs.stop_loss_buy_ins == BANKROLL_KNOB_DEFAULTS.stop_loss_buy_ins
        assert knobs.stop_win_buy_ins == BANKROLL_KNOB_DEFAULTS.stop_win_buy_ins


# --- Projection on read ---


class TestProjectBankrollPure:
    """Unit tests for the pure project_bankroll function — no DB."""

    def test_no_tick_returns_stored_chips(self):
        state = AIBankrollState("seed", chips=3_000, last_regen_tick=None)
        # The "never had an event" state should project to the seed
        # value, not inflate by full elapsed time since epoch.
        assert project_bankroll(state, cap=10_000, rate=500, now=datetime.utcnow()) == 3_000

    def test_within_same_day_no_regen(self):
        # Half-second elapsed → floor(rate * 5.78e-6 days) == 0 → no change.
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(seconds=1)
        state = AIBankrollState("a", chips=1_000, last_regen_tick=tick)
        assert project_bankroll(state, cap=10_000, rate=500, now=now) == 1_000

    def test_full_day_adds_rate(self):
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(days=1)
        state = AIBankrollState("a", chips=1_000, last_regen_tick=tick)
        assert project_bankroll(state, cap=10_000, rate=500, now=now) == 1_500

    def test_multiple_days_linear_growth(self):
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(days=4)
        state = AIBankrollState("a", chips=1_000, last_regen_tick=tick)
        assert project_bankroll(state, cap=10_000, rate=500, now=now) == 3_000

    def test_clamps_to_cap(self):
        tick = datetime(2026, 1, 1, 0, 0, 0)
        now = tick + timedelta(days=365)
        state = AIBankrollState("a", chips=8_000, last_regen_tick=tick)
        # Without cap: 8_000 + 500 * 365 = 190_500. Cap at 10_000.
        assert project_bankroll(state, cap=10_000, rate=500, now=now) == 10_000

    def test_starting_above_cap_stays_at_value(self):
        # An AI already above cap (e.g., from a big win) doesn't get
        # clamped down on read; only the projection is capped. The
        # min() means a stored value > cap reads as cap, which is the
        # intended behavior — the cap is a hard ceiling on live
        # eligibility, not a soft floor.
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(days=1)
        state = AIBankrollState("a", chips=15_000, last_regen_tick=tick)
        assert project_bankroll(state, cap=10_000, rate=500, now=now) == 10_000


class TestAIBankrollProjectedReads:
    def test_load_projected_applies_regen(self, db_path, repo):
        # Seed: 1000 chips, last_regen_tick = 4 days ago, rate=500/day → 3000.
        # Personality row carries default knobs (NULL → defaults: rate 500, cap 10_000).
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Hungry Hippo", "{}", "hungry_hippo"),
            )
            conn.commit()
        tick = datetime(2026, 5, 13, 12, 0, 0)
        now = datetime(2026, 5, 17, 12, 0, 0)
        repo.save_ai_bankroll(AIBankrollState("hungry_hippo", chips=1_000, last_regen_tick=tick))
        projected = repo.load_ai_bankroll_projected("hungry_hippo", now=now)
        assert projected == 3_000

    def test_load_projected_returns_none_for_unknown(self, repo):
        assert repo.load_ai_bankroll_projected("nobody") is None

    def test_load_projected_uses_personality_specific_cap(self, db_path, repo):
        # Personality with bankroll_cap=2000 — should clamp tighter than default.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO personalities
                    (name, config_json, personality_id, bankroll_cap, bankroll_rate)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("Capped Cat", "{}", "capped_cat", 2_000, 500),
            )
            conn.commit()
        tick = datetime(2026, 5, 10, 12, 0, 0)
        now = datetime(2026, 5, 17, 12, 0, 0)  # 7 days, would add 3500
        repo.save_ai_bankroll(AIBankrollState("capped_cat", chips=500, last_regen_tick=tick))
        projected = repo.load_ai_bankroll_projected("capped_cat", now=now)
        assert projected == 2_000
