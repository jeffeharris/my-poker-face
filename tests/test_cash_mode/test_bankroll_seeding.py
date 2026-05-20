"""Tests for `ensure_ai_bankrolls_seeded` and the refresh-path bankroll fallback.

The seed helper covers two failure modes that previously stranded
personalities in the lobby:

  1. A personality eligible for cash mode that had no
     `ai_bankroll_state` row was rejected by the live-fill path
     (`load_ai_bankroll_current` returned None → treated as 0 chips).
  2. A row created via `save_emotional_state_json`'s implicit
     placeholder (chips=0, last_regen_tick=NULL) had no regen clock,
     so the AI could never recover.

Tests run against a tempdb, mirroring `test_lobby_seeding.py`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import ensure_ai_bankrolls_seeded
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


def _insert_personality(
    db_path: str,
    personality_id: str,
    *,
    name: str = None,
    bankroll_knobs: dict = None,
) -> None:
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name or f"Personality {personality_id}", json.dumps(config), personality_id),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "bankroll_seed.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def bankroll_repo(db_path):
    r = BankrollRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def personality_repo(db_path):
    r = PersonalityRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 20, 18, 0, 0)


class TestEnsureAIBankrollsSeeded:
    def test_creates_row_for_eligible_personality_without_one(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        # Personality with explicit knobs; no bankroll row yet.
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "starting_bankroll": 25_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
        })

        actions = ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now,
        )

        assert actions == {"napoleon": "created"}
        stored = bankroll_repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.chips == 25_000
        assert stored.last_regen_tick == now

    def test_repairs_placeholder_row_with_null_tick(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        # The `save_emotional_state_json` placeholder pattern —
        # row exists at chips=0, tick=NULL. The seed helper repairs
        # it to a real starting bankroll.
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "starting_bankroll": 25_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
        })
        # Insert the placeholder pattern directly so we exercise the
        # repair branch without depending on save_emotional_state_json.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO ai_bankroll_state (personality_id, sandbox_id, chips, last_regen_tick) "
                "VALUES ('napoleon', 'test-sandbox-1', 0, NULL)"
            )
            conn.commit()

        actions = ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now,
        )

        assert actions == {"napoleon": "repaired"}
        stored = bankroll_repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.chips == 25_000
        assert stored.last_regen_tick == now

    def test_skips_healthy_row(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        # Row already has chips and a tick — live state, leave alone.
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "starting_bankroll": 25_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
        })
        existing_tick = now - timedelta(hours=2)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=12_345, last_regen_tick=existing_tick,
        ), sandbox_id="test-sandbox-1")

        actions = ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now,
        )

        assert actions == {"napoleon": "skipped"}
        stored = bankroll_repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.chips == 12_345
        assert stored.last_regen_tick == existing_tick

    def test_idempotent_second_call_no_op(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "starting_bankroll": 25_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
        })

        first = ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now,
        )
        second = ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now + timedelta(minutes=5),
        )

        assert first == {"napoleon": "created"}
        assert second == {"napoleon": "skipped"}
        # Tick from the first call is preserved — second call doesn't
        # bump it.
        stored = bankroll_repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.last_regen_tick == now

    def test_seeds_all_eligible_personalities(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        for i in range(10):
            _insert_personality(db_path, f"p{i}", bankroll_knobs={
                "starting_bankroll": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
            })

        actions = ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now,
        )

        assert len(actions) == 10
        assert all(v == "created" for v in actions.values())
        for i in range(10):
            stored = bankroll_repo.load_ai_bankroll(f"p{i}", sandbox_id="test-sandbox-1")
            assert stored.chips == 10_000

    def test_seeding_unblocks_live_fill(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        # End-to-end check: a personality with no bankroll row gets a
        # row written by the seed helper, and then becomes pickable
        # for live-fill via the refresh path. Without the seed, the
        # fallback in `_bankroll_lookup` would still cover this case,
        # but the seeded row is the steady-state shape.
        import random
        from cash_mode.lobby import ensure_lobby_seeded, refresh_unseated_tables
        from poker.repositories.cash_table_repository import CashTableRepository

        # Need a personality_id starting with 'a' so list_eligible_for_cash_mode
        # orders it first deterministically.
        _insert_personality(db_path, "aaa_napoleon", bankroll_knobs={
            "starting_bankroll": 25_000, "bankroll_rate": 0,
            "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
        })

        # Step 1: bankroll seed creates the row.
        ensure_ai_bankrolls_seeded(
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            sandbox_id="test-sandbox-1",
            now=now,
        )

        # Step 2: lobby seed builds tables. With only one personality
        # eligible, it gets placed at one table — but not all 5.
        cash_table_repo = CashTableRepository(db_path)
        try:
            ensure_lobby_seeded(
                cash_table_repo=cash_table_repo,
                personality_repo=personality_repo,
                bankroll_repo=bankroll_repo,
                sandbox_id="test-sandbox-1",
                now=now,
            )

            # Step 3: the seeded personality has a real bankroll row
            # now, so `load_ai_bankroll_current` returns a positive
            # number — the fix's whole point.
            current = bankroll_repo.load_ai_bankroll_current(
                "aaa_napoleon", sandbox_id="test-sandbox-1", now=now,
            )
            assert current is not None
            assert current > 0
        finally:
            cash_table_repo.close()

    def test_repair_emits_ai_seed_for_chip_diff(
        self, bankroll_repo, personality_repo, db_path, now,
    ):
        # Repair adds chips (0 → cap). Audit must record this as a
        # mint via ai_seed; save_ai_bankroll's first-write hook only
        # fires for genuinely new rows, so the helper emits manually.
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "starting_bankroll": 25_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
        })
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO ai_bankroll_state (personality_id, sandbox_id, chips, last_regen_tick) "
                "VALUES ('napoleon', 'test-sandbox-1', 0, NULL)"
            )
            conn.commit()

        seed_calls = []

        class _FakeLedger:
            pass

        # Patch the ledger module's record_ai_seed to capture calls.
        from core.economy import ledger as chip_ledger
        original = chip_ledger.record_ai_seed
        def _spy(*args, **kwargs):
            seed_calls.append(kwargs)
            return original(*args, **kwargs)
        chip_ledger.record_ai_seed = _spy
        try:
            ensure_ai_bankrolls_seeded(
                personality_repo=personality_repo,
                bankroll_repo=bankroll_repo,
                sandbox_id="test-sandbox-1",
                now=now,
                chip_ledger_repo=_FakeLedger(),
            )
        finally:
            chip_ledger.record_ai_seed = original

        assert len(seed_calls) == 1
        assert seed_calls[0]['personality_id'] == 'napoleon'
        assert seed_calls[0]['amount'] == 25_000
        assert seed_calls[0]['context']['reason'] == 'placeholder_repair'
