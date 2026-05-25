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
    BANKROLL_KNOB_DEFAULTS,
    AIBankrollState,
    credit_ai_cash_out,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture(autouse=True)
def _enable_regen():
    """This file tests the regen-projection-on-cash-out mechanism.

    Passive regen is retired as a *default* (`REGEN_ENABLED=False`) per
    CASH_MODE_SIDE_HUSTLE.md, but the projection machinery is kept and
    still supported. Enable it for these tests so the projection-before-
    credit / accrual assertions exercise the mechanism; the "off"
    behaviour is covered by tests/test_economy_flags.py.
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
) -> None:
    """Insert a personality row with optional bankroll_knobs in config_json."""
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id) " "VALUES (?, ?, ?)",
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
        _insert_personality(
            db_path,
            "napoleon",
            bankroll_knobs={
                "starting_bankroll": 50_000,
                "bankroll_rate": 500,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="napoleon",
                chips=5_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )

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
        _insert_personality(
            db_path,
            "napoleon",
            bankroll_knobs={
                "starting_bankroll": 50_000,
                "bankroll_rate": 500,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        one_day_ago = now - timedelta(days=1)
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="napoleon",
                chips=5_000,
                last_regen_tick=one_day_ago,
            ),
            sandbox_id="test-sandbox-1",
        )

        result = credit_ai_cash_out(repo, "napoleon", 3_000, sandbox_id="test-sandbox-1", now=now)

        # 5_000 + 500 (regen) + 3_000 (table) = 8_500
        assert result.chips == 8_500
        assert result.last_regen_tick == now


# --- Winnings above starting_bankroll are kept (regen target, not cap) ---


class TestWinningsAboveTarget:
    def test_winnings_pass_through_starting_bankroll(self, repo, db_path, now):
        # Bankroll at 49_000, starting_bankroll 50_000, table stack
        # 5_000 → final bankroll 54_000. starting_bankroll is the
        # regen *target*, not a ceiling — winnings above it are kept.
        _insert_personality(
            db_path,
            "napoleon",
            bankroll_knobs={
                "starting_bankroll": 50_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="napoleon",
                chips=49_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )

        result = credit_ai_cash_out(repo, "napoleon", 5_000, sandbox_id="test-sandbox-1", now=now)

        assert result.chips == 54_000

    def test_above_target_stays_above(self, repo, db_path, now):
        # Already above starting_bankroll; cash-out adds winnings and
        # regen is dormant (project_bankroll early-returns when chips
        # are already at or above target).
        _insert_personality(
            db_path,
            "napoleon",
            bankroll_knobs={
                "starting_bankroll": 50_000,
                "bankroll_rate": 500,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        )
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="napoleon",
                chips=50_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )

        result = credit_ai_cash_out(repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=now)

        # 50_000 (no regen, already at target) + 1_000 winnings = 51_000.
        assert result.chips == 51_000

    def test_low_starter_can_climb_past_target(self, repo, db_path, now):
        # A_mime archetype: starting_bankroll=200 (street-performer
        # tier), wins big at the $2 table. The cash-out must let the
        # winnings stack — without this, the character can never
        # afford a higher stake.
        _insert_personality(
            db_path,
            "a_mime",
            bankroll_knobs={
                "starting_bankroll": 200,
                "bankroll_rate": 100,  # 100 chips/day regen toward target
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$2",
            },
        )
        # Bankroll at 120 (below target), tick 1 day ago — regen
        # should pull it up toward target (200) before winnings land.
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="a_mime",
                chips=120,
                last_regen_tick=now - timedelta(days=1),
            ),
            sandbox_id="test-sandbox-1",
        )

        # Wins $400 at the seat — far above the $200 starting target.
        result = credit_ai_cash_out(repo, "a_mime", 400, sandbox_id="test-sandbox-1", now=now)

        # Regen pulls 120 → min(200, 120+100) = 200. Winnings stack
        # on top: 200 + 400 = 600. Enough to buy into $10 next session.
        # The pre-target regen still caps at the target — only winnings
        # above target stack uncapped, not regen.
        assert result.chips == 600


# --- Edge cases (busted stacks, missing rows) ---


class TestEdgeCases:
    def test_zero_stack_advances_tick(self, repo, db_path, now):
        # Busted AI: stack=0 still commits a write so the regen clock
        # advances. Otherwise an AI that loses everything sits at
        # chips=0 with a stale tick and never recovers.
        _insert_personality(db_path, "napoleon")
        old_tick = now - timedelta(days=1)
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="napoleon",
                chips=5_000,
                last_regen_tick=old_tick,
            ),
            sandbox_id="test-sandbox-1",
        )

        result = credit_ai_cash_out(repo, "napoleon", 0, sandbox_id="test-sandbox-1", now=now)

        assert result is not None
        # Regen of one day at the default 500/day applied; no table
        # chips added on top because the stack was 0.
        assert result.chips == 5_500
        assert result.last_regen_tick == now
        stored = repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored.chips == 5_500
        assert stored.last_regen_tick == now

    def test_negative_stack_treated_as_zero(self, repo, db_path, now):
        # Defensive: callers shouldn't pass negative, but if they do,
        # clamp to 0 rather than debiting the bankroll. Tick still
        # advances so regen continues.
        _insert_personality(db_path, "napoleon")
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="napoleon",
                chips=5_000,
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )

        result = credit_ai_cash_out(repo, "napoleon", -100, sandbox_id="test-sandbox-1", now=now)

        assert result is not None
        assert result.chips == 5_000

    def test_no_bankroll_row_creates_row(self, repo, db_path, now):
        # Personality has no `ai_bankroll_state` row yet. The credit
        # path is the defensive seam — it writes a fresh row so the
        # regen clock can begin. Previously this case silently
        # skipped, stranding the AI.
        _insert_personality(db_path, "napoleon")
        # (No save_ai_bankroll call.)

        result = credit_ai_cash_out(repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=now)

        assert result is not None
        assert result.chips == 1_000
        assert result.last_regen_tick == now
        stored = repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1")
        assert stored is not None
        assert stored.chips == 1_000

    def test_no_bankroll_row_with_zero_stack_creates_chip_zero_row(self, repo, db_path, now):
        # No row + bust-out (stack=0) is the worst case of the old
        # bug: the AI was stranded forever. Now we create a row at
        # chips=0 with a live tick so regen can start accruing.
        _insert_personality(db_path, "napoleon")

        result = credit_ai_cash_out(repo, "napoleon", 0, sandbox_id="test-sandbox-1", now=now)

        assert result is not None
        assert result.chips == 0
        assert result.last_regen_tick == now


# --- Multiple AIs ---


class TestMultipleAIs:
    def test_independent_credits(self, repo, db_path, now):
        # Three AIs with different stacks — each credited
        # independently. No cross-contamination between bankrolls.
        for pid in ("napoleon", "zeus", "athena"):
            _insert_personality(
                db_path,
                pid,
                bankroll_knobs={
                    "starting_bankroll": 50_000,
                    "bankroll_rate": 0,
                    "buy_in_multiplier": 1.0,
                    "stake_comfort_zone": "$10",
                },
            )
            repo.save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid,
                    chips=5_000,
                    last_regen_tick=now,
                ),
                sandbox_id="test-sandbox-1",
            )

        credit_ai_cash_out(repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=now)
        credit_ai_cash_out(repo, "zeus", 2_500, sandbox_id="test-sandbox-1", now=now)
        credit_ai_cash_out(repo, "athena", 0, sandbox_id="test-sandbox-1", now=now)  # busted

        assert repo.load_ai_bankroll("napoleon", sandbox_id="test-sandbox-1").chips == 6_000
        assert repo.load_ai_bankroll("zeus", sandbox_id="test-sandbox-1").chips == 7_500
        assert (
            repo.load_ai_bankroll("athena", sandbox_id="test-sandbox-1").chips == 5_000
        )  # unchanged


# --- Defaults fallback ---


class TestDefaultKnobs:
    def test_personality_without_knobs_uses_defaults(self, repo, db_path, now):
        # Personality has no bankroll_knobs sub-dict → load_personality_knobs
        # returns BANKROLL_KNOB_DEFAULTS. The default starting_bankroll
        # is just the regen target — winnings stack uncapped on top.
        _insert_personality(db_path, "rookie")  # no knobs
        starting = BANKROLL_KNOB_DEFAULTS.starting_bankroll
        repo.save_ai_bankroll(
            AIBankrollState(
                personality_id="rookie",
                chips=starting - 1_000,  # one buy-in below default target
                last_regen_tick=now,
            ),
            sandbox_id="test-sandbox-1",
        )

        result = credit_ai_cash_out(repo, "rookie", 5_000, sandbox_id="test-sandbox-1", now=now)

        # No regen elapsed (same `now` used for save + credit), so
        # bankroll = (starting - 1000) + 5000 = starting + 4000.
        assert result.chips == starting + 4_000
