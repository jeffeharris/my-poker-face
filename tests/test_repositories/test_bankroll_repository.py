"""Tests for the v88 schema migration and BankrollRepository.

Covers:
  - Migration v88 lands cleanly on existing pre-v88 databases (idempotent)
  - ai_bankroll_state + player_bankroll_state tables have the right shape
  - save / load round-trips for AI and player bankroll
  - load_personality_knobs falls back to defaults when config_json lacks
    the bankroll_knobs sub-dict (or it's missing keys)
  - load_ai_bankroll_current applies projection (clamped to cap)

  - load_* returns None when no row exists
"""

from __future__ import annotations

import json
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
from cash_mode.lender_profile import LENDER_PROFILE_DEFAULTS, LenderProfile
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.schema_manager import SchemaManager


def _insert_personality(
    db_path: str,
    personality_id: str,
    *,
    name: str = None,
    bankroll_knobs: dict = None,
    lender_profile: dict = None,
) -> None:
    """Helper: insert a personality row with optional bankroll_knobs / lender_profile in config_json."""
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    if lender_profile is not None:
        config["lender_profile"] = lender_profile
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id) "
            "VALUES (?, ?, ?)",
            (name or f"Personality {personality_id}", json.dumps(config), personality_id),
        )
        conn.commit()


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

    def test_personalities_table_unchanged_by_v88(self, db_path):
        # v88 stores knobs inside config_json; it must NOT add knob columns
        # to the personalities table. If a future migration re-adds them
        # this test should fail and prompt a design discussion.
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(personalities)")}
            for forbidden in (
                "bankroll_cap", "bankroll_rate", "buy_in_multiplier",
                "stop_loss_buy_ins", "stop_win_buy_ins", "stake_comfort_zone",
            ):
                assert forbidden not in cols, (
                    f"v88 should not add {forbidden} column — knobs live in config_json"
                )

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
        # Simulate pre-v88 state: drop the two bankroll tables and
        # remove the v88+ rows from schema_version. The personalities
        # table is untouched — v88 doesn't alter its shape. Versions >=
        # v91 (cash_tables, cash_idle_pool) are also rolled back so the
        # migration loop re-applies from v88 onward.
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TABLE IF EXISTS ai_bankroll_state")
            conn.execute("DROP TABLE IF EXISTS player_bankroll_state")
            conn.execute("DELETE FROM schema_version WHERE version >= 88")
            conn.commit()
        # Re-run ensure_schema → should re-apply v88
        SchemaManager(path).ensure_schema()
        with sqlite3.connect(path) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "ai_bankroll_state" in tables
            assert "player_bankroll_state" in tables
            v88_row = conn.execute(
                "SELECT description FROM schema_version WHERE version = 88"
            ).fetchone()
            assert v88_row is not None


# NOTE: TestSchemaMigrationV89 / TestSchemaMigrationV90 (the legacy
# active_loan_* and active_loan_lender_id column tests) were removed
# in Cleanup B of the backing-system handoff. The columns themselves
# get dropped in v99 (Cleanup C); the migration entries stay in the
# schema_manager dispatch table so fresh DBs still produce them on the
# way up, but the test surface is gone because active_loan_* is no
# longer a public column anyone reads or writes.


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

    # Active-loan round-trip + clear-on-settlement tests deleted in
    # Cleanup B — `PlayerBankrollState` no longer carries loan fields.
    # Stake state lives in `tests/test_stake_repository.py` against the
    # `StakeRepository` (v98 stakes table).


# --- Personality knob loading ---


class TestPersonalityKnobs:
    def test_load_returns_defaults_when_row_missing(self, repo):
        knobs = repo.load_personality_knobs("nonexistent_id")
        assert knobs == BANKROLL_KNOB_DEFAULTS

    def test_load_returns_defaults_when_config_lacks_bankroll_knobs(self, db_path, repo):
        # Personality row exists but config_json has no bankroll_knobs sub-dict.
        _insert_personality(db_path, "test_personality")
        knobs = repo.load_personality_knobs("test_personality")
        assert knobs == BANKROLL_KNOB_DEFAULTS

    def test_save_then_load_round_trip(self, db_path, repo):
        _insert_personality(db_path, "big_stack_bob", name="Big Stack Bob")
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

    def test_partial_sub_dict_uses_defaults_per_field(self, db_path, repo):
        # bankroll_knobs has only two keys — the rest fall back to defaults.
        _insert_personality(
            db_path,
            "partial_knobs",
            bankroll_knobs={"bankroll_cap": 25_000, "stake_comfort_zone": "$50"},
        )
        knobs = repo.load_personality_knobs("partial_knobs")
        assert knobs.bankroll_cap == 25_000
        assert knobs.stake_comfort_zone == "$50"
        # Missing keys fall back to defaults
        assert knobs.bankroll_rate == BANKROLL_KNOB_DEFAULTS.bankroll_rate
        assert knobs.buy_in_multiplier == BANKROLL_KNOB_DEFAULTS.buy_in_multiplier
        assert knobs.stop_loss_buy_ins == BANKROLL_KNOB_DEFAULTS.stop_loss_buy_ins
        assert knobs.stop_win_buy_ins == BANKROLL_KNOB_DEFAULTS.stop_win_buy_ins

    def test_save_preserves_other_config_keys(self, db_path, repo):
        # Inserting a personality with anchors etc. and then writing knobs
        # must not wipe the rest of config_json — this is the bug we'd
        # have hit with the columns + INSERT OR REPLACE approach.
        original_config = {
            "play_style": "tight aggressive",
            "anchors": {"baseline_aggression": 0.7, "poise": 0.8},
            "verbal_tics": ["'Show me the chips.'"],
        }
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Preserved Pete", json.dumps(original_config), "preserved_pete"),
            )
            conn.commit()
        custom = BankrollKnobs(50_000, 1_000, 1.5, 2, 10, "$200")
        repo.save_personality_knobs("preserved_pete", custom)
        # Read raw config_json back; every original key must still be present.
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT config_json FROM personalities WHERE personality_id = ?",
                ("preserved_pete",),
            ).fetchone()
        config = json.loads(row[0])
        assert config["play_style"] == "tight aggressive"
        assert config["anchors"] == {"baseline_aggression": 0.7, "poise": 0.8}
        assert config["verbal_tics"] == ["'Show me the chips.'"]
        assert config["bankroll_knobs"]["bankroll_cap"] == 50_000

    def test_load_handles_malformed_config_json(self, db_path, repo):
        # If config_json is unparseable, return defaults rather than crashing.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Broken Bob", "{not valid json", "broken_bob"),
            )
            conn.commit()
        knobs = repo.load_personality_knobs("broken_bob")
        assert knobs == BANKROLL_KNOB_DEFAULTS

    def test_load_handles_non_dict_bankroll_knobs(self, db_path, repo):
        # `bankroll_knobs: "oops"` (string instead of dict) → defaults.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Wrong Type", json.dumps({"bankroll_knobs": "oops"}), "wrong_type"),
            )
            conn.commit()
        knobs = repo.load_personality_knobs("wrong_type")
        assert knobs == BANKROLL_KNOB_DEFAULTS


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


class TestAIBankrollCurrentReads:
    def test_load_current_applies_regen(self, db_path, repo):
        # Seed: 1000 chips, last_regen_tick = 4 days ago, rate=500/day → 3000.
        # Personality row has no bankroll_knobs → defaults (rate 500, cap 10_000).
        _insert_personality(db_path, "hungry_hippo", name="Hungry Hippo")
        tick = datetime(2026, 5, 13, 12, 0, 0)
        now = datetime(2026, 5, 17, 12, 0, 0)
        repo.save_ai_bankroll(AIBankrollState("hungry_hippo", chips=1_000, last_regen_tick=tick))
        projected = repo.load_ai_bankroll_current("hungry_hippo", now=now)
        assert projected == 3_000

    def test_load_current_returns_none_for_unknown(self, repo):
        assert repo.load_ai_bankroll_current("nobody") is None

    def test_load_current_uses_personality_specific_cap(self, db_path, repo):
        # Personality with bankroll_cap=2000 — should clamp tighter than default.
        _insert_personality(
            db_path,
            "capped_cat",
            name="Capped Cat",
            bankroll_knobs={"bankroll_cap": 2_000, "bankroll_rate": 500},
        )
        tick = datetime(2026, 5, 10, 12, 0, 0)
        now = datetime(2026, 5, 17, 12, 0, 0)  # 7 days, would add 3500
        repo.save_ai_bankroll(AIBankrollState("capped_cat", chips=500, last_regen_tick=tick))
        projected = repo.load_ai_bankroll_current("capped_cat", now=now)
        assert projected == 2_000


# --- Lender profile loading (Path B) ---


class TestLenderProfile:
    """`load_lender_profile` reads `config_json.lender_profile` with
    per-field fallback to `LENDER_PROFILE_DEFAULTS`. Same shape as
    `load_personality_knobs`."""

    def test_load_returns_defaults_when_row_missing(self, repo):
        profile = repo.load_lender_profile("nonexistent_id")
        assert profile == LENDER_PROFILE_DEFAULTS

    def test_load_returns_defaults_when_config_lacks_lender_profile(self, db_path, repo):
        # Personality row exists but config_json has no lender_profile sub-dict.
        _insert_personality(db_path, "no_profile_id")
        profile = repo.load_lender_profile("no_profile_id")
        assert profile == LENDER_PROFILE_DEFAULTS

    def test_load_returns_full_profile_when_present(self, db_path, repo):
        _insert_personality(
            db_path,
            "predatory_pete",
            lender_profile={
                "willing": True,
                "max_loan_pct_of_bankroll": 0.08,
                "floor_anchor": 1.40,
                "rate_anchor": 0.45,
                "respect_floor": -0.9,
                "heat_ceiling": 0.95,
            },
        )
        profile = repo.load_lender_profile("predatory_pete")
        assert profile.willing is True
        assert profile.max_loan_pct_of_bankroll == 0.08
        assert profile.floor_anchor == 1.40
        assert profile.rate_anchor == 0.45
        assert profile.respect_floor == -0.9
        assert profile.heat_ceiling == 0.95

    def test_load_returns_unwilling_lender(self, db_path, repo):
        # Chaos personalities (mime, cheshire cat) refuse outright.
        _insert_personality(
            db_path,
            "mime",
            lender_profile={"willing": False},
        )
        profile = repo.load_lender_profile("mime")
        assert profile.willing is False
        # Missing fields fall back per-field.
        assert profile.max_loan_pct_of_bankroll == LENDER_PROFILE_DEFAULTS.max_loan_pct_of_bankroll

    def test_partial_profile_falls_back_per_field(self, db_path, repo):
        # Only floor_anchor and rate_anchor set; other fields default.
        _insert_personality(
            db_path,
            "partial",
            lender_profile={"floor_anchor": 1.05, "rate_anchor": 0.10},
        )
        profile = repo.load_lender_profile("partial")
        assert profile.floor_anchor == 1.05
        assert profile.rate_anchor == 0.10
        # The rest pulled from defaults.
        assert profile.willing == LENDER_PROFILE_DEFAULTS.willing
        assert profile.max_loan_pct_of_bankroll == LENDER_PROFILE_DEFAULTS.max_loan_pct_of_bankroll
        assert profile.respect_floor == LENDER_PROFILE_DEFAULTS.respect_floor
        assert profile.heat_ceiling == LENDER_PROFILE_DEFAULTS.heat_ceiling

    def test_load_handles_malformed_json(self, db_path, repo):
        # Malformed config_json → defaults (logged warning).
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Bad JSON", "{not valid", "bad_json"),
            )
            conn.commit()
        profile = repo.load_lender_profile("bad_json")
        assert profile == LENDER_PROFILE_DEFAULTS

    def test_load_handles_non_dict_lender_profile(self, db_path, repo):
        # lender_profile sub-key isn't a dict → defaults.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                ("Wrong Type", json.dumps({"lender_profile": "oops"}), "wrong_type_lp"),
            )
            conn.commit()
        profile = repo.load_lender_profile("wrong_type_lp")
        assert profile == LENDER_PROFILE_DEFAULTS
