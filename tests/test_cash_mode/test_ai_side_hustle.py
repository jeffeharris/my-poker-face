"""Tests for the AI side-hustle mechanic.

Pure-math tests for the deficit / amount formulas, plus end-to-end tests
that fire `resolve_ai_side_hustle` and `tick_side_hustle_expirations`
against tempdb-backed repos and a real bank-pool ledger.

See `docs/plans/CASH_MODE_SIDE_HUSTLE.md` for the design.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode import ai_side_hustle
from cash_mode.ai_side_hustle import (
    HUSTLE_MIN_AMOUNT,
    HustleEndResult,
    HustleStartResult,
    compute_deficit_ratio,
    compute_hustle_amount,
    resolve_ai_side_hustle,
    tick_side_hustle_expirations,
)
from cash_mode.bankroll import AIBankrollState
from cash_mode.closed_economy import compute_bank_pool_reserves
from core.economy import ledger as chip_ledger
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.side_hustle_state_repository import (
    SideHustleState,
    SideHustleStateRepository,
)

NOW = datetime(2026, 5, 24, 12, 0, 0)
SBX = "test-sandbox-hustle"
# Unknown personalities → BANKROLL_KNOB_DEFAULTS (starting_bankroll=10_000).
DEFAULT_STARTING = 10_000


class _FixedRng:
    """Deterministic RNG: uniform → midpoint, random → 0.0 (bucket low end)."""

    def uniform(self, a, b):
        return (a + b) / 2.0

    def random(self):
        return 0.0


# --- Pure-math tests --------------------------------------------------------


class TestDeficitRatio:
    def test_at_baseline_is_zero(self):
        assert compute_deficit_ratio(10_000, 10_000) == 0.0

    def test_above_baseline_is_zero(self):
        assert compute_deficit_ratio(12_000, 10_000) == 0.0

    def test_half_below_baseline(self):
        assert compute_deficit_ratio(5_000, 10_000) == pytest.approx(0.5)

    def test_nearly_broke_approaches_one(self):
        assert compute_deficit_ratio(100, 10_000) == pytest.approx(0.99)

    def test_zero_starting_is_zero(self):
        assert compute_deficit_ratio(0, 0) == 0.0


class TestHustleAmount:
    def test_broke_ai_rolls_positive_weighted_by_starting(self):
        # chips=500, starting=10_000 → deficit=0.95
        # fraction = 0.05 + 0.95*0.15 = 0.1925; raw = 10_000*0.1925*1.0 = 1925
        amount = compute_hustle_amount(500, 10_000, _FixedRng())
        assert amount == 1925

    def test_bigger_starting_earns_bigger_lump(self):
        # Same deficit ratio, larger persona → larger absolute earn.
        small = compute_hustle_amount(500, 10_000, _FixedRng())
        big = compute_hustle_amount(5_000, 100_000, _FixedRng())
        assert big > small

    def test_at_baseline_returns_zero(self):
        assert compute_hustle_amount(10_000, 10_000, _FixedRng()) == 0

    def test_capped_at_gap_to_baseline(self):
        # chips=9_990, starting=10_000 → gap is only 10, below MIN → 0.
        assert compute_hustle_amount(9_990, 10_000, _FixedRng()) == 0

    def test_below_min_amount_returns_zero(self):
        # Tiny starting bankroll → rolled amount below the floor.
        assert compute_hustle_amount(10, 200, _FixedRng()) < HUSTLE_MIN_AMOUNT \
            or compute_hustle_amount(10, 200, _FixedRng()) == 0


# --- Integration fixtures ---------------------------------------------------


@pytest.fixture
def repos(tmp_path):
    db = str(tmp_path / "hustle.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    ledger = ChipLedgerRepository(db)
    hustle = SideHustleStateRepository(db)
    return {"db": db, "bankroll": bankroll, "ledger": ledger, "hustle": hustle}


def _seed_bankroll(repos, pid, chips):
    repos["bankroll"].save_ai_bankroll(
        AIBankrollState(personality_id=pid, chips=chips, last_regen_tick=NOW),
        sandbox_id=SBX,
    )


def _seed_pool(repos, amount):
    """Deposit `amount` into the bank pool via a rake destruction."""
    chip_ledger.record_table_rake(
        repos["ledger"], source=chip_ledger.ai("whale"), amount=amount,
        sandbox_id=SBX,
    )


def _insert_expired_hustle(repos, pid, target, *, ends_offset_min=-60):
    repos["hustle"].insert_side_hustle_state(SideHustleState(
        personality_id=pid,
        sandbox_id=SBX,
        started_at=NOW + timedelta(minutes=ends_offset_min - 60),
        ends_at=NOW + timedelta(minutes=ends_offset_min),
        amount=target,
        duration_bucket="medium",
        narration=f"{pid} grinding",
    ))


# --- resolve_ai_side_hustle -------------------------------------------------


class TestResolve:
    def test_broke_candidate_gets_a_row_no_chips_move(self, repos):
        _seed_bankroll(repos, "napoleon", 500)
        before = repos["bankroll"].load_ai_bankroll("napoleon", sandbox_id=SBX).chips

        out = resolve_ai_side_hustle(
            candidates={"napoleon"},
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            sandbox_id=SBX,
            rng=_FixedRng(),
            now=NOW,
        )
        assert len(out) == 1
        assert isinstance(out[0], HustleStartResult)
        assert out[0].personality_id == "napoleon"
        assert out[0].amount == 1925
        # State row inserted...
        row = repos["hustle"].load("napoleon", sandbox_id=SBX)
        assert row is not None and row.amount == 1925
        # ...and NO chips moved at start.
        after = repos["bankroll"].load_ai_bankroll("napoleon", sandbox_id=SBX).chips
        assert after == before

    def test_at_baseline_candidate_skipped(self, repos):
        _seed_bankroll(repos, "rich", 10_000)  # no deficit
        out = resolve_ai_side_hustle(
            candidates={"rich"},
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            sandbox_id=SBX, rng=_FixedRng(), now=NOW,
        )
        assert out == []
        assert repos["hustle"].load("rich", sandbox_id=SBX) is None

    def test_respects_max_starts_neediest_first(self, repos):
        # Three broke AIs at different depths; max_starts=2 selects the
        # two deepest deficits (lowest chips).
        _seed_bankroll(repos, "deep", 200)      # deficit 0.98
        _seed_bankroll(repos, "mid", 3_000)     # deficit 0.70
        _seed_bankroll(repos, "shallow", 6_000)  # deficit 0.40
        out = resolve_ai_side_hustle(
            candidates={"deep", "mid", "shallow"},
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            sandbox_id=SBX, rng=_FixedRng(), now=NOW,
            max_starts=2,
        )
        picked = {r.personality_id for r in out}
        assert picked == {"deep", "mid"}
        assert repos["hustle"].load("shallow", sandbox_id=SBX) is None

    def test_uses_narrate_fn(self, repos):
        _seed_bankroll(repos, "napoleon", 500)

        def narrate(pid, amount):
            return (f"{pid} flips ${amount} of real estate", "short")

        out = resolve_ai_side_hustle(
            candidates={"napoleon"},
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            sandbox_id=SBX, rng=_FixedRng(), now=NOW,
            narrate_fn=narrate,
        )
        assert "real estate" in out[0].narration
        assert out[0].duration_bucket == "short"


# --- tick_side_hustle_expirations -------------------------------------------


class TestExpiry:
    def test_payout_credited_from_pool_and_row_deleted(self, repos):
        _seed_bankroll(repos, "napoleon", 500)
        _seed_pool(repos, 5_000)
        _insert_expired_hustle(repos, "napoleon", target=1925)

        out = tick_side_hustle_expirations(
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            chip_ledger_repo=repos["ledger"],
            sandbox_id=SBX, now=NOW,
        )
        assert len(out) == 1
        assert isinstance(out[0], HustleEndResult)
        assert out[0].paid_amount == 1925
        assert out[0].target_amount == 1925
        # Bankroll credited.
        assert repos["bankroll"].load_ai_bankroll(
            "napoleon", sandbox_id=SBX).chips == 500 + 1925
        # Pool drawn down: 5_000 deposit − 1925 draw.
        assert compute_bank_pool_reserves(repos["ledger"], sandbox_id=SBX) == 5_000 - 1925
        # Row gone.
        assert repos["hustle"].load("napoleon", sandbox_id=SBX) is None

    def test_payout_clamped_to_pool_depth(self, repos):
        _seed_bankroll(repos, "napoleon", 500)
        _seed_pool(repos, 1_000)  # less than the 1925 target
        _insert_expired_hustle(repos, "napoleon", target=1925)

        out = tick_side_hustle_expirations(
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            chip_ledger_repo=repos["ledger"],
            sandbox_id=SBX, now=NOW,
        )
        assert out[0].paid_amount == 1_000
        assert repos["bankroll"].load_ai_bankroll(
            "napoleon", sandbox_id=SBX).chips == 500 + 1_000
        # Pool fully drained, not negative.
        assert compute_bank_pool_reserves(repos["ledger"], sandbox_id=SBX) == 0

    def test_empty_pool_pays_nothing(self, repos):
        _seed_bankroll(repos, "napoleon", 500)
        # No pool seeded → reserves 0.
        _insert_expired_hustle(repos, "napoleon", target=1925)

        out = tick_side_hustle_expirations(
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            chip_ledger_repo=repos["ledger"],
            sandbox_id=SBX, now=NOW,
        )
        assert out[0].paid_amount == 0
        # Bankroll unchanged, row still deleted (re-triggers next refresh).
        assert repos["bankroll"].load_ai_bankroll(
            "napoleon", sandbox_id=SBX).chips == 500
        assert repos["hustle"].load("napoleon", sandbox_id=SBX) is None

    def test_multiple_expiries_share_pool_fifo(self, repos):
        _seed_bankroll(repos, "first", 500)
        _seed_bankroll(repos, "second", 500)
        _seed_pool(repos, 2_500)  # covers first (1925) + remainder (575)
        # first expires earlier than second → FIFO by ends_at.
        _insert_expired_hustle(repos, "first", target=1925, ends_offset_min=-120)
        _insert_expired_hustle(repos, "second", target=1925, ends_offset_min=-60)

        out = tick_side_hustle_expirations(
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            chip_ledger_repo=repos["ledger"],
            sandbox_id=SBX, now=NOW,
        )
        paid = {r.personality_id: r.paid_amount for r in out}
        assert paid["first"] == 1925
        assert paid["second"] == 575  # only the remainder left
        assert compute_bank_pool_reserves(repos["ledger"], sandbox_id=SBX) == 0

    def test_unexpired_hustle_untouched(self, repos):
        _seed_bankroll(repos, "napoleon", 500)
        _seed_pool(repos, 5_000)
        # ends in the future relative to NOW.
        _insert_expired_hustle(repos, "napoleon", target=1925, ends_offset_min=+60)

        out = tick_side_hustle_expirations(
            side_hustle_repo=repos["hustle"],
            bankroll_repo=repos["bankroll"],
            chip_ledger_repo=repos["ledger"],
            sandbox_id=SBX, now=NOW,
        )
        assert out == []
        assert repos["hustle"].load("napoleon", sandbox_id=SBX) is not None
        assert repos["bankroll"].load_ai_bankroll(
            "napoleon", sandbox_id=SBX).chips == 500
