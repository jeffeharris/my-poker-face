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
    BANKROLL_KNOB_DEFAULTS,
    AIBankrollState,
    BankrollKnobs,
    PlayerBankrollState,
    project_bankroll,
)
from cash_mode.staker_profile import (
    BORROWER_PROFILE_DEFAULTS,
    STAKER_PROFILE_DEFAULTS,
    BorrowerProfile,
    StakerProfile,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.schema_manager import SchemaManager

SANDBOX_ID = "test-sandbox-1"


@pytest.fixture(autouse=True)
def _enable_regen():
    """This file exercises the passive-regen projection mechanism.

    Passive regen is retired as a *default* (`REGEN_ENABLED=False`) per
    CASH_MODE_SIDE_HUSTLE.md, but the projection machinery is kept and
    still supported. Force the flag on for these tests so the accrual /
    clamp assertions exercise the mechanism rather than the new default.
    The "off" behaviour is covered by tests/test_economy_flags.py.
    """
    from cash_mode import economy_flags

    saved = economy_flags.REGEN_ENABLED
    economy_flags.REGEN_ENABLED = True
    yield
    economy_flags.REGEN_ENABLED = saved


def _insert_personality(
    db_path: str,
    personality_id: str,
    *,
    name: str = None,
    bankroll_knobs: dict = None,
    staker_profile: dict = None,
    borrower_profile: dict = None,
    anchors: dict = None,
) -> None:
    """Helper: insert a personality row with optional bankroll_knobs / staker_profile / borrower_profile / anchors in config_json."""
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    if staker_profile is not None:
        config["staker_profile"] = staker_profile
    if borrower_profile is not None:
        config["borrower_profile"] = borrower_profile
    if anchors is not None:
        config["anchors"] = anchors
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
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
                "starting_bankroll",
                "bankroll_rate",
                "buy_in_multiplier",
                "stake_comfort_zone",
            ):
                assert (
                    forbidden not in cols
                ), f"v88 should not add {forbidden} column — knobs live in config_json"

    def test_ai_bankroll_pk_enforced(self, db_path):
        # v102: composite PK (personality_id, sandbox_id); a second
        # insert with the same pair raises IntegrityError. A second
        # insert with a different sandbox_id is allowed (each sandbox
        # has its own row for the same personality).
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO ai_bankroll_state "
                "(personality_id, sandbox_id, chips) VALUES (?, ?, ?)",
                ("alice", "sb1", 1000),
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO ai_bankroll_state "
                    "(personality_id, sandbox_id, chips) VALUES (?, ?, ?)",
                    ("alice", "sb1", 2000),
                )
            # Different sandbox_id is fine — separate save-file.
            conn.execute(
                "INSERT INTO ai_bankroll_state "
                "(personality_id, sandbox_id, chips) VALUES (?, ?, ?)",
                ("alice", "sb2", 2000),
            )
            conn.commit()

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
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
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
        repo.save_ai_bankroll(state, sandbox_id=SANDBOX_ID)
        loaded = repo.load_ai_bankroll("napoleon", sandbox_id=SANDBOX_ID)
        assert loaded is not None
        assert loaded.personality_id == "napoleon"
        assert loaded.chips == 4_200
        assert loaded.last_regen_tick == tick

    def test_load_returns_none_for_unknown_personality(self, repo):
        assert repo.load_ai_bankroll("nobody", sandbox_id=SANDBOX_ID) is None

    def test_save_is_upsert(self, repo):
        repo.save_ai_bankroll(AIBankrollState("alice", 1_000), sandbox_id=SANDBOX_ID)
        repo.save_ai_bankroll(AIBankrollState("alice", 2_500), sandbox_id=SANDBOX_ID)
        loaded = repo.load_ai_bankroll("alice", sandbox_id=SANDBOX_ID)
        assert loaded.chips == 2_500

    def test_null_tick_round_trips(self, repo):
        # No-event-yet state — last_regen_tick stays None
        repo.save_ai_bankroll(
            AIBankrollState("seed", 5_000, last_regen_tick=None),
            sandbox_id=SANDBOX_ID,
        )
        loaded = repo.load_ai_bankroll("seed", sandbox_id=SANDBOX_ID)
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
            starting_bankroll=50_000,
            bankroll_rate=1_000,
            buy_in_multiplier=1.5,
            stake_comfort_zone="$200",
        )
        assert repo.save_personality_knobs("big_stack_bob", custom) is True
        loaded = repo.load_personality_knobs("big_stack_bob")
        assert loaded == custom

    def test_save_returns_false_when_no_row(self, repo):
        # The repo doesn't insert new personality rows — knob writes
        # target rows that already exist. Missing personality_id is a
        # no-op, signaled by the False return.
        assert repo.save_personality_knobs("no_such_personality", BANKROLL_KNOB_DEFAULTS) is False

    def test_partial_sub_dict_uses_defaults_per_field(self, db_path, repo):
        # bankroll_knobs has only two keys — the rest fall back to defaults.
        _insert_personality(
            db_path,
            "partial_knobs",
            bankroll_knobs={"starting_bankroll": 25_000, "stake_comfort_zone": "$50"},
        )
        knobs = repo.load_personality_knobs("partial_knobs")
        assert knobs.starting_bankroll == 25_000
        assert knobs.stake_comfort_zone == "$50"
        # Missing keys fall back to defaults
        assert knobs.bankroll_rate == BANKROLL_KNOB_DEFAULTS.bankroll_rate
        assert knobs.buy_in_multiplier == BANKROLL_KNOB_DEFAULTS.buy_in_multiplier

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
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("Preserved Pete", json.dumps(original_config), "preserved_pete"),
            )
            conn.commit()
        custom = BankrollKnobs(50_000, 1_000, 1.5, "$200")
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
        assert config["bankroll_knobs"]["starting_bankroll"] == 50_000

    def test_load_handles_malformed_config_json(self, db_path, repo):
        # If config_json is unparseable, return defaults rather than crashing.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("Broken Bob", "{not valid json", "broken_bob"),
            )
            conn.commit()
        knobs = repo.load_personality_knobs("broken_bob")
        assert knobs == BANKROLL_KNOB_DEFAULTS

    def test_load_handles_non_dict_bankroll_knobs(self, db_path, repo):
        # `bankroll_knobs: "oops"` (string instead of dict) → defaults.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
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
        assert (
            project_bankroll(state, starting_bankroll=10_000, rate=500, now=datetime.utcnow())
            == 3_000
        )

    def test_within_same_day_no_regen(self):
        # Half-second elapsed → floor(rate * 5.78e-6 days) == 0 → no change.
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(seconds=1)
        state = AIBankrollState("a", chips=1_000, last_regen_tick=tick)
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=now) == 1_000

    def test_full_day_adds_rate(self):
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(days=1)
        state = AIBankrollState("a", chips=1_000, last_regen_tick=tick)
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=now) == 1_500

    def test_multiple_days_linear_growth(self):
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(days=4)
        state = AIBankrollState("a", chips=1_000, last_regen_tick=tick)
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=now) == 3_000

    def test_regen_stops_at_target(self):
        # `starting_bankroll` is the regen target. Below it, regen
        # accrues at `rate/day` but doesn't overshoot.
        tick = datetime(2026, 1, 1, 0, 0, 0)
        now = tick + timedelta(days=365)
        state = AIBankrollState("a", chips=8_000, last_regen_tick=tick)
        # Without target: 8_000 + 500 * 365 = 190_500. Target is 10_000.
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=now) == 10_000

    def test_above_target_reads_unchanged(self):
        # `starting_bankroll` is a regen target, NOT a cap. An AI who
        # has won past their natural-wealth tier reads back at their
        # stored value — chips earned above the target are kept.
        tick = datetime(2026, 5, 17, 12, 0, 0)
        now = tick + timedelta(days=1)
        state = AIBankrollState("a", chips=15_000, last_regen_tick=tick)
        # Stored 15_000 > target 10_000 → no regen, no clamp.
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=now) == 15_000


class TestAIBankrollCurrentReads:
    def test_load_current_applies_regen(self, db_path, repo):
        # Seed: 1000 chips, last_regen_tick = 4 days ago, rate=500/day → 3000.
        # Personality row has no bankroll_knobs → defaults (rate 500, cap 10_000).
        _insert_personality(db_path, "hungry_hippo", name="Hungry Hippo")
        tick = datetime(2026, 5, 13, 12, 0, 0)
        now = datetime(2026, 5, 17, 12, 0, 0)
        repo.save_ai_bankroll(
            AIBankrollState("hungry_hippo", chips=1_000, last_regen_tick=tick),
            sandbox_id=SANDBOX_ID,
        )
        projected = repo.load_ai_bankroll_current(
            "hungry_hippo",
            sandbox_id=SANDBOX_ID,
            now=now,
        )
        assert projected == 3_000

    def test_load_current_returns_none_for_unknown(self, repo):
        assert repo.load_ai_bankroll_current("nobody", sandbox_id=SANDBOX_ID) is None

    def test_load_current_uses_personality_specific_cap(self, db_path, repo):
        # Personality with starting_bankroll=2000 — should clamp tighter than default.
        _insert_personality(
            db_path,
            "capped_cat",
            name="Capped Cat",
            bankroll_knobs={"starting_bankroll": 2_000, "bankroll_rate": 500},
        )
        tick = datetime(2026, 5, 10, 12, 0, 0)
        now = datetime(2026, 5, 17, 12, 0, 0)  # 7 days, would add 3500
        repo.save_ai_bankroll(
            AIBankrollState("capped_cat", chips=500, last_regen_tick=tick),
            sandbox_id=SANDBOX_ID,
        )
        projected = repo.load_ai_bankroll_current(
            "capped_cat",
            sandbox_id=SANDBOX_ID,
            now=now,
        )
        assert projected == 2_000


# --- Staker profile loading (Path B) ---


class TestStakerProfile:
    """`load_staker_profile` reads `config_json.staker_profile` with
    per-field fallback to `STAKER_PROFILE_DEFAULTS`. Same shape as
    `load_personality_knobs`."""

    def test_load_returns_defaults_when_row_missing(self, repo):
        profile = repo.load_staker_profile("nonexistent_id")
        assert profile == STAKER_PROFILE_DEFAULTS

    def test_load_returns_defaults_when_config_lacks_staker_profile(self, db_path, repo):
        # Personality row exists but config_json has no staker_profile sub-dict.
        _insert_personality(db_path, "no_profile_id")
        profile = repo.load_staker_profile("no_profile_id")
        assert profile == STAKER_PROFILE_DEFAULTS

    def test_load_returns_full_profile_when_present(self, db_path, repo):
        _insert_personality(
            db_path,
            "predatory_pete",
            staker_profile={
                "willing": True,
                "max_loan_pct_of_bankroll": 0.08,
                "floor_anchor": 1.40,
                "rate_anchor": 0.45,
                "respect_floor": -0.9,
                "heat_ceiling": 0.95,
            },
        )
        profile = repo.load_staker_profile("predatory_pete")
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
            staker_profile={"willing": False},
        )
        profile = repo.load_staker_profile("mime")
        assert profile.willing is False
        # Missing fields fall back per-field.
        assert profile.max_loan_pct_of_bankroll == STAKER_PROFILE_DEFAULTS.max_loan_pct_of_bankroll

    def test_partial_profile_falls_back_per_field(self, db_path, repo):
        # Only floor_anchor and rate_anchor set; other fields default.
        _insert_personality(
            db_path,
            "partial",
            staker_profile={"floor_anchor": 1.05, "rate_anchor": 0.10},
        )
        profile = repo.load_staker_profile("partial")
        assert profile.floor_anchor == 1.05
        assert profile.rate_anchor == 0.10
        # The rest pulled from defaults.
        assert profile.willing == STAKER_PROFILE_DEFAULTS.willing
        assert profile.max_loan_pct_of_bankroll == STAKER_PROFILE_DEFAULTS.max_loan_pct_of_bankroll
        assert profile.respect_floor == STAKER_PROFILE_DEFAULTS.respect_floor
        assert profile.heat_ceiling == STAKER_PROFILE_DEFAULTS.heat_ceiling

    def test_load_handles_malformed_json(self, db_path, repo):
        # Malformed config_json → defaults (logged warning).
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("Bad JSON", "{not valid", "bad_json"),
            )
            conn.commit()
        profile = repo.load_staker_profile("bad_json")
        assert profile == STAKER_PROFILE_DEFAULTS

    def test_load_handles_non_dict_staker_profile(self, db_path, repo):
        # staker_profile sub-key isn't a dict → defaults.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("Wrong Type", json.dumps({"staker_profile": "oops"}), "wrong_type_lp"),
            )
            conn.commit()
        profile = repo.load_staker_profile("wrong_type_lp")
        assert profile == STAKER_PROFILE_DEFAULTS


# --- Borrower profile loading (Phase 4 of the backing system) ---


class TestBorrowerProfile:
    """`load_borrower_profile` reads `config_json.borrower_profile` with
    per-field fallback to `BORROWER_PROFILE_DEFAULTS`. Mirror of
    `load_staker_profile` for "does this AI accept stakes when bust?"."""

    def test_load_returns_defaults_when_row_missing(self, repo):
        profile = repo.load_borrower_profile("nonexistent_id")
        assert profile == BORROWER_PROFILE_DEFAULTS
        # Defaults to willing — most AIs accept a stake to avoid bust.
        assert profile.willing is True

    def test_load_returns_defaults_when_config_lacks_borrower_profile(self, db_path, repo):
        _insert_personality(db_path, "no_bp_id")
        profile = repo.load_borrower_profile("no_bp_id")
        assert profile == BORROWER_PROFILE_DEFAULTS

    def test_load_returns_stoic_unwilling_borrower(self, db_path, repo):
        # Stoic personalities (Lincoln, Buddha) refuse stakes.
        _insert_personality(
            db_path,
            "buddha",
            borrower_profile={"willing": False},
        )
        profile = repo.load_borrower_profile("buddha")
        assert profile.willing is False

    def test_load_explicit_willing_true_round_trips(self, db_path, repo):
        # Most personalities default to willing=True; an explicit override
        # is preserved through the read path.
        _insert_personality(
            db_path,
            "napoleon",
            borrower_profile={"willing": True},
        )
        profile = repo.load_borrower_profile("napoleon")
        assert profile.willing is True

    def test_load_handles_malformed_json(self, db_path, repo):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("Bad JSON", "{not valid", "bad_json_bp"),
            )
            conn.commit()
        profile = repo.load_borrower_profile("bad_json_bp")
        assert profile == BORROWER_PROFILE_DEFAULTS

    def test_load_handles_non_dict_borrower_profile(self, db_path, repo):
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
                ("Wrong Type", json.dumps({"borrower_profile": "oops"}), "wrong_type_bp"),
            )
            conn.commit()
        profile = repo.load_borrower_profile("wrong_type_bp")
        assert profile == BORROWER_PROFILE_DEFAULTS

    def test_lender_and_borrower_profiles_independent(self, db_path, repo):
        # A personality can be a willing lender AND unwilling borrower
        # (Lincoln-like: principled about giving help but not asking).
        _insert_personality(
            db_path,
            "principled_lincoln",
            staker_profile={"willing": True, "max_loan_pct_of_bankroll": 0.15},
            borrower_profile={"willing": False},
        )
        lender = repo.load_staker_profile("principled_lincoln")
        borrower = repo.load_borrower_profile("principled_lincoln")
        assert lender.willing is True
        assert borrower.willing is False


class TestAspirationCooldown:
    """`load_aspiration_cooldown_until` / `save_aspiration_cooldown_until`
    on the `ai_bankroll_state` v107 column. Per-AI rate limiting for
    aspiration_ask triggers. Spec:
    `docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md` Commit 3.
    """

    def test_default_is_none(self, repo):
        # No row → no cooldown.
        assert (
            repo.load_aspiration_cooldown_until(
                "missing",
                sandbox_id="sb",
            )
            is None
        )

    def test_round_trips_timestamp(self, repo):
        # Seed a bankroll row, stamp cooldown, read it back.
        from cash_mode.bankroll import AIBankrollState

        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="zeus",
                chips=10_000,
                last_regen_tick=datetime(2026, 5, 22, 10, 0, 0),
            ),
            sandbox_id="sb1",
        )
        target = datetime(2026, 5, 22, 12, 30, 45)
        ok = repo.save_aspiration_cooldown_until(
            "zeus",
            sandbox_id="sb1",
            until=target,
        )
        assert ok is True
        loaded = repo.load_aspiration_cooldown_until("zeus", sandbox_id="sb1")
        assert loaded == target

    def test_save_none_clears_cooldown(self, repo):
        from cash_mode.bankroll import AIBankrollState

        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="zeus",
                chips=10_000,
                last_regen_tick=datetime(2026, 5, 22, 10, 0, 0),
            ),
            sandbox_id="sb1",
        )
        repo.save_aspiration_cooldown_until(
            "zeus",
            sandbox_id="sb1",
            until=datetime(2026, 5, 22, 11, 0, 0),
        )
        ok = repo.save_aspiration_cooldown_until(
            "zeus",
            sandbox_id="sb1",
            until=None,
        )
        assert ok is True
        assert (
            repo.load_aspiration_cooldown_until(
                "zeus",
                sandbox_id="sb1",
            )
            is None
        )

    def test_save_returns_false_without_row(self, repo):
        # No bankroll row → cannot stamp cooldown.
        assert (
            repo.save_aspiration_cooldown_until(
                "ghost",
                sandbox_id="sb1",
                until=datetime(2026, 5, 22),
            )
            is False
        )

    def test_sandbox_scoped(self, repo):
        # Same pid in two sandboxes has independent cooldowns.
        from cash_mode.bankroll import AIBankrollState

        for sandbox_id in ("sb_a", "sb_b"):
            repo.save_ai_bankroll(
                AIBankrollState(
                    personality_id="zeus",
                    chips=10_000,
                    last_regen_tick=datetime(2026, 5, 22, 10, 0, 0),
                ),
                sandbox_id=sandbox_id,
            )
        repo.save_aspiration_cooldown_until(
            "zeus",
            sandbox_id="sb_a",
            until=datetime(2026, 5, 22, 12, 0, 0),
        )
        assert repo.load_aspiration_cooldown_until(
            "zeus",
            sandbox_id="sb_a",
        ) == datetime(2026, 5, 22, 12, 0, 0)
        assert (
            repo.load_aspiration_cooldown_until(
                "zeus",
                sandbox_id="sb_b",
            )
            is None
        )


class TestBorrowerProfileAspirationBias:
    """`load_borrower_profile.aspiration_bias` — anchor-derived with
    explicit-override and willing=False suppression. Spec:
    `docs/plans/CASH_MODE_AI_ASPIRATION_ASK.md` Commit 1.
    """

    def test_defaults_when_no_anchors_or_override(self, db_path, repo):
        _insert_personality(db_path, "plain")
        profile = repo.load_borrower_profile("plain")
        assert profile.aspiration_bias == 0.5

    def test_derived_from_ego_and_risk_identity(self, db_path, repo):
        # 0.6 × 0.86 + 0.4 × 0.90 = 0.876 — Napoleon-class climber.
        _insert_personality(
            db_path,
            "napoleon",
            anchors={"ego": 0.86, "risk_identity": 0.90},
        )
        profile = repo.load_borrower_profile("napoleon")
        assert profile.aspiration_bias == pytest.approx(0.876)

    def test_derived_low_for_humble_and_cautious(self, db_path, repo):
        # 0.6 × 0.36 + 0.4 × 0.38 = 0.368 — Lincoln-class grinder.
        _insert_personality(
            db_path,
            "lincoln",
            anchors={"ego": 0.36, "risk_identity": 0.38},
        )
        profile = repo.load_borrower_profile("lincoln")
        assert profile.aspiration_bias == pytest.approx(0.368)

    def test_explicit_override_wins(self, db_path, repo):
        # Explicit JSON override beats the anchor-derived value.
        _insert_personality(
            db_path,
            "fixed_climber",
            anchors={"ego": 0.20, "risk_identity": 0.20},  # would derive low
            borrower_profile={"aspiration_bias": 0.95},
        )
        profile = repo.load_borrower_profile("fixed_climber")
        assert profile.aspiration_bias == 0.95

    def test_override_clamped_to_unit_interval(self, db_path, repo):
        # Out-of-range overrides clamp rather than crash.
        _insert_personality(
            db_path,
            "loud_one",
            borrower_profile={"aspiration_bias": 1.5},
        )
        assert repo.load_borrower_profile("loud_one").aspiration_bias == 1.0
        _insert_personality(
            db_path,
            "quiet_one",
            borrower_profile={"aspiration_bias": -0.4},
        )
        assert repo.load_borrower_profile("quiet_one").aspiration_bias == 0.0

    def test_willing_false_forces_zero(self, db_path, repo):
        # Locked decision: refusing stakes ⟹ never aspires.
        # The anchor values would otherwise derive a high bias.
        _insert_personality(
            db_path,
            "buddha",
            anchors={"ego": 0.90, "risk_identity": 0.90},
            borrower_profile={"willing": False, "aspiration_bias": 0.95},
        )
        profile = repo.load_borrower_profile("buddha")
        assert profile.willing is False
        assert profile.aspiration_bias == 0.0

    def test_partial_anchors_fall_back_to_default(self, db_path, repo):
        # Anchors dict present but missing risk_identity → default.
        _insert_personality(
            db_path,
            "ego_only",
            anchors={"ego": 0.86},
        )
        profile = repo.load_borrower_profile("ego_only")
        assert profile.aspiration_bias == 0.5

    def test_non_numeric_override_falls_back_to_default(self, db_path, repo):
        _insert_personality(
            db_path,
            "bad_override",
            borrower_profile={"aspiration_bias": "high"},
        )
        profile = repo.load_borrower_profile("bad_override")
        assert profile.aspiration_bias == 0.5

    def test_save_round_trips_explicit_value(self, db_path, repo):
        _insert_personality(db_path, "saveme")
        ok = repo.save_borrower_profile(
            "saveme",
            willing=True,
            willingness_threshold=None,
            aspiration_bias=0.77,
        )
        assert ok is True
        assert repo.load_borrower_profile("saveme").aspiration_bias == 0.77

    def test_save_none_clears_override(self, db_path, repo):
        # Storing an explicit value then clearing it returns to default.
        _insert_personality(
            db_path,
            "togglable",
            borrower_profile={"aspiration_bias": 0.9},
        )
        repo.save_borrower_profile(
            "togglable",
            willing=True,
            willingness_threshold=None,
            aspiration_bias=None,
        )
        # No anchors → falls through to default.
        assert repo.load_borrower_profile("togglable").aspiration_bias == 0.5

    def test_save_clamps_out_of_range(self, db_path, repo):
        _insert_personality(db_path, "extreme")
        repo.save_borrower_profile(
            "extreme",
            willing=True,
            willingness_threshold=None,
            aspiration_bias=2.0,
        )
        assert repo.load_borrower_profile("extreme").aspiration_bias == 1.0


class TestBankruptcyHistory:
    """v149 — bankruptcy_count + last_bankruptcy_at round-trip and the
    decay-driven loan-term penalty derived from them."""

    def test_load_defaults_when_never_bankrupt(self, repo):
        repo.save_ai_bankroll(AIBankrollState("clean", 8_000), sandbox_id=SANDBOX_ID)
        count, last_at = repo.load_bankruptcy_state("clean", sandbox_id=SANDBOX_ID)
        assert count == 0
        assert last_at is None

    def test_load_defaults_for_missing_row(self, repo):
        count, last_at = repo.load_bankruptcy_state("ghost", sandbox_id=SANDBOX_ID)
        assert count == 0
        assert last_at is None

    def test_record_increments_and_stamps(self, repo):
        repo.save_ai_bankroll(AIBankrollState("broke", 0), sandbox_id=SANDBOX_ID)
        t1 = datetime(2026, 6, 1, 9, 0)
        new_count = repo.record_bankruptcy("broke", sandbox_id=SANDBOX_ID, now=t1)
        assert new_count == 1
        count, last_at = repo.load_bankruptcy_state("broke", sandbox_id=SANDBOX_ID)
        assert count == 1
        assert last_at == t1

    def test_record_accumulates(self, repo):
        repo.save_ai_bankroll(AIBankrollState("serial", 0), sandbox_id=SANDBOX_ID)
        repo.record_bankruptcy("serial", sandbox_id=SANDBOX_ID, now=datetime(2026, 6, 1))
        t2 = datetime(2026, 6, 20)
        assert repo.record_bankruptcy("serial", sandbox_id=SANDBOX_ID, now=t2) == 2
        count, last_at = repo.load_bankruptcy_state("serial", sandbox_id=SANDBOX_ID)
        assert count == 2
        assert last_at == t2  # latest stamp wins

    def test_record_returns_zero_for_missing_row(self, repo):
        # No bankroll row → nothing to stamp; defensive 0 (shouldn't hit
        # on the live path, which loads+zeroes the row first).
        assert repo.record_bankruptcy("ghost", sandbox_id=SANDBOX_ID, now=datetime(2026, 6, 1)) == 0
