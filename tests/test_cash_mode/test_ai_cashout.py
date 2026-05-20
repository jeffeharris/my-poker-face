"""Tests for the leave-time AI cash-out loop in /api/cash/leave.

When the player leaves a cash table, every seated AI's current
`Player.stack` credits back to that AI's persistent bankroll. Without
this loop, AI table winnings evaporate at session end and AI bankrolls
drift monotonically downward — sit-down debits never get matched by
cash-out credits. This is the load-bearing v1 economic-loop fix and
the foundation for Path B (AI sponsorship), which reads
`load_ai_bankroll_current` to gate lender eligibility.

Tests target `credit_ai_cash_out`, the pure-ish helper that does the
per-AI credit (projection-on-read + clamp-to-cap + write). Driving
the full `/api/cash/leave` route end-to-end would require spinning
up the state machine, controllers, and Socket.IO; the helper's math
is the actual surface that needs verification.

Mirrors `tests/test_repositories/test_bankroll_repository.py` shape:
tempdb backed by SchemaManager, BankrollRepository instance, seeded
personality rows for knob lookup.
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
    credit_ai_cash_out,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.schema_manager import SchemaManager


def _insert_personality(
    db_path: str,
    personality_id: str,
    *,
    name: str = None,
    bankroll_knobs: dict = None,
) -> None:
    """Insert a personality row with optional bankroll_knobs in config_json."""
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id) "
            "VALUES (?, ?, ?)",
            (name or f"Personality {personality_id}", json.dumps(config), personality_id),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "ai_cashout.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repo(db_path):
    r = BankrollRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0)


# --- Happy path: stack credits into bankroll ---


class TestSingleAICashOut:
    def test_credits_stack_to_bankroll(self, repo, db_path, now):
        # Napoleon: bankroll 5_000 at last_regen_tick=now. Cash-out
        # 3_000 chips from the table → bankroll becomes 8_000 (no
        # projection time elapsed, no clamp).
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "bankroll_cap": 50_000,
            "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "napoleon", 3_000, sandbox_id="test-sandbox-1", now=now)

        assert result is not None
        assert result.chips == 8_000
        assert result.last_regen_tick == now
        # Persisted.
        stored = repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.chips == 8_000

    def test_projection_applied_before_credit(self, repo, db_path, now):
        # last_regen_tick is one day ago, rate=500/day → projection
        # adds 500 before the table credit lands.
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "bankroll_cap": 50_000,
            "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        one_day_ago = now - timedelta(days=1)
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon",
            chips=5_000,
            last_regen_tick=one_day_ago,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "napoleon", 3_000, sandbox_id="test-sandbox-1", now=now)

        # 5_000 + 500 (regen) + 3_000 (table) = 8_500
        assert result.chips == 8_500
        assert result.last_regen_tick == now


# --- Cap clamp: extras evaporate at the ceiling ---


class TestCapClamp:
    def test_cap_clamp_eats_excess(self, repo, db_path, now):
        # Bankroll at 49_000, cap 50_000, table stack 5_000 → winnings
        # clamp to 50_000. 4_000 chips evaporate (intentional v1 rule).
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "bankroll_cap": 50_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=49_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "napoleon", 5_000, sandbox_id="test-sandbox-1", now=now)

        assert result.chips == 50_000

    def test_at_cap_stays_at_cap(self, repo, db_path, now):
        # Already at cap; cash-out is a no-growth event. The bankroll
        # *write still fires* (last_regen_tick refreshes) but chips
        # don't change.
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "bankroll_cap": 50_000,
            "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=50_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=now)

        assert result.chips == 50_000

    def test_uses_personality_specific_cap(self, repo, db_path, now):
        # Different cap per personality: zeus cap=200k, napoleon cap=10k.
        # Cash-out respects each personality's own knob.
        _insert_personality(db_path, "zeus", bankroll_knobs={
            "bankroll_cap": 200_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$100",
        })
        _insert_personality(db_path, "napoleon", bankroll_knobs={
            "bankroll_cap": 10_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="zeus", chips=150_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=8_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        zeus_after = credit_ai_cash_out(repo, "zeus", 30_000, sandbox_id="test-sandbox-1", now=now)
        napoleon_after = credit_ai_cash_out(repo, "napoleon", 5_000, sandbox_id="test-sandbox-1", now=now)

        assert zeus_after.chips == 180_000  # well under 200k cap
        assert napoleon_after.chips == 10_000  # clamped to 10k cap


# --- Skip conditions ---


class TestSkipConditions:
    def test_zero_stack_no_op(self, repo, db_path, now):
        # Busted AI: stack=0 → no write happens, function returns None.
        _insert_personality(db_path, "napoleon")
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "napoleon", 0, sandbox_id="test-sandbox-1", now=now)

        assert result is None
        # No spurious write — last_regen_tick preserved.
        stored = repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.chips == 5_000
        assert stored.last_regen_tick == now

    def test_negative_stack_no_op(self, repo, db_path, now):
        # Defensive: callers shouldn't pass negative, but if they do,
        # we don't burn the bankroll.
        _insert_personality(db_path, "napoleon")
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000, last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "napoleon", -100, sandbox_id="test-sandbox-1", now=now)

        assert result is None

    def test_no_bankroll_row_skips(self, repo, db_path, now):
        # Personality has no `ai_bankroll_state` row yet (shouldn't
        # happen for a seated AI — sit_down writes the row — but the
        # function should not crash if it does).
        _insert_personality(db_path, "napoleon")
        # (No save_ai_bankroll call.)

        result = credit_ai_cash_out(repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=now)

        assert result is None
        # Still no row.
        assert repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1") is None


# --- Multiple AIs ---


class TestMultipleAIs:
    def test_independent_credits(self, repo, db_path, now):
        # Three AIs with different stacks — each credited
        # independently. No cross-contamination between bankrolls.
        for pid in ("napoleon", "zeus", "athena"):
            _insert_personality(db_path, pid, bankroll_knobs={
                "bankroll_cap": 50_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            })
            repo.save_ai_bankroll(AIBankrollState(
                personality_id=pid, chips=5_000, last_regen_tick=now,
            ), sandbox_id="test-sandbox-1")

        credit_ai_cash_out(repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=now)
        credit_ai_cash_out(repo, "zeus", 2_500, sandbox_id="test-sandbox-1", now=now)
        credit_ai_cash_out(repo, "athena", 0, sandbox_id="test-sandbox-1", now=now)  # busted

        assert repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1").chips == 6_000
        assert repo.load_ai_bankroll("zeus", sandbox_id="test-sandbox-1").chips == 7_500
        assert repo.load_ai_bankroll("athena", sandbox_id="test-sandbox-1").chips == 5_000  # unchanged


# --- Defaults fallback ---


class TestDefaultKnobs:
    def test_personality_without_knobs_uses_defaults(self, repo, db_path, now):
        # Personality has no bankroll_knobs sub-dict → load_personality_knobs
        # returns BANKROLL_KNOB_DEFAULTS (cap=10_000). Stack credit
        # clamps at the default cap.
        _insert_personality(db_path, "rookie")  # no knobs
        repo.save_ai_bankroll(AIBankrollState(
            personality_id="rookie",
            chips=BANKROLL_KNOB_DEFAULTS.bankroll_cap - 1_000,
            last_regen_tick=now,
        ), sandbox_id="test-sandbox-1")

        result = credit_ai_cash_out(repo, "rookie", 5_000, sandbox_id="test-sandbox-1", now=now)

        # Default cap is 10_000; we started at 9_000 + 5_000 → clamp to 10_000
        assert result.chips == BANKROLL_KNOB_DEFAULTS.bankroll_cap
