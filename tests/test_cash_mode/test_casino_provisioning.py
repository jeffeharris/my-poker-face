"""Casino provisioning + bank-pool seed.

Spec: `docs/plans/CASH_MODE_CLOSED_ECONOMY.md`. Covers the spawn /
teardown lifecycle of `table_type='casino'` tables and the operator-
controlled bank-pool seed for sim cold-start.

Conservation invariant (`drift == 0`) is asserted across the spawn
flow — chips that land in fish seats are matched 1:1 by
`casino_seat_seed` ledger rows, and the sim-seed pair preserves drift.
"""

from __future__ import annotations

import os
import random
import sys
import unittest
from datetime import datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import AIBankrollState
from cash_mode.casino_provisioning import (
    CASINO_FISH_PER_TABLE,
    CASINO_FISH_BUY_IN_MULTIPLIER,
    CASINO_SPAWN_THRESHOLDS,
    CasinoSpawn,
    _casino_table_id,
    resolve_casino_provisioning,
)
from cash_mode.closed_economy import (
    compute_bank_pool_reserves,
    seed_bank_pool,
)
from cash_mode.stakes_ladder import table_buy_in_window
from cash_mode.tables import CashTableState, open_slot
from core.economy.ledger import (
    BANK_POOL_DEPOSIT_REASONS,
    BANK_POOL_DRAW_REASONS,
    LEDGER_REASONS,
    record_bank_pool_sim_seed_pair,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


ANCHOR = datetime(2026, 5, 23, 12, 0, 0)
SBX = "test-casino"


# --- Fixtures ----------------------------------------------------------------


def _fish_config(personality_id: str, name: str) -> dict:
    """Reusable fish config — minimum bankroll-knobs + archetype tag."""
    return {
        "archetype": "fish",
        "rule_strategy": "fish",
        "play_style": "test fish",
        "anchors": {
            "baseline_aggression": 0.15,
            "baseline_looseness": 0.85,
            "ego": 0.2,
            "poise": 0.15,
            "expressiveness": 0.5,
            "risk_identity": 0.5,
            "adaptation_bias": 0.0,
            "baseline_energy": 0.5,
            "recovery_rate": 0.0,
        },
        "bankroll_knobs": {
            "starting_bankroll": 2_500,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
    }


@pytest.fixture
def db_setup(tmp_path):
    """Tempdb with N fish personalities, no active casinos."""
    db = str(tmp_path / "casino_provisioning.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    tables = CashTableRepository(db)
    ledger = ChipLedgerRepository(db)
    personality = PersonalityRepository(db)

    # Seed enough fish to fill a casino table (4 by default).
    fish_pids = []
    for i in range(CASINO_FISH_PER_TABLE + 1):  # +1 spare
        pid = f"test_fish_{i}"
        personality.save_personality(
            f"TestFish{i}",
            _fish_config(pid, f"TestFish{i}"),
            personality_id=pid,
        )
        # Fish need a bankroll row to appear in `load_fish_ids` discovery.
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id=pid, chips=0, last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        fish_pids.append(pid)

    return {
        "bankroll": bankroll,
        "tables": tables,
        "ledger": ledger,
        "personality": personality,
        "fish_pids": fish_pids,
    }


# --- Bank-pool seed tests ----------------------------------------------------


class TestBankPoolSeed:
    def test_seed_increases_pool_depth(self, db_setup):
        ledger = db_setup["ledger"]
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 0
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 10_000

    def test_seed_is_drift_neutral(self, db_setup):
        """Seed creates + destroys equal amounts → ledger_outstanding stays."""
        ledger = db_setup["ledger"]
        before_creations = ledger.sum_creations_by_reason(sandbox_id=SBX)
        before_destructions = ledger.sum_destructions_by_reason(sandbox_id=SBX)
        seed_bank_pool(ledger, sandbox_id=SBX, amount=5_000)
        after_creations = ledger.sum_creations_by_reason(sandbox_id=SBX)
        after_destructions = ledger.sum_destructions_by_reason(sandbox_id=SBX)
        # Each side gained 5000 (seed creation + bank_pool_deposit destruction).
        net_creation = sum(after_creations.values()) - sum(before_creations.values())
        net_destruction = sum(after_destructions.values()) - sum(before_destructions.values())
        assert net_creation == 5_000
        assert net_destruction == 5_000
        # Ledger outstanding (creations - destructions) is unchanged.
        assert net_creation - net_destruction == 0

    def test_zero_amount_no_op(self, db_setup):
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=0)
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 0


# --- Casino spawn tests ------------------------------------------------------


class TestCasinoSpawn:
    def test_no_spawn_without_pool(self, db_setup):
        """Pool below threshold → no spawn even if fish are available."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        assert batch.spawns == []
        assert tables.list_all_tables(sandbox_id=SBX) == []

    def test_spawn_with_seeded_pool(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Seed enough for a $2 casino.
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        assert len(batch.spawns) >= 1
        casino_2 = next(s for s in batch.spawns if s.stake_label == "$2")
        assert casino_2.table_id == _casino_table_id("$2")
        assert len(casino_2.fish_seated) == CASINO_FISH_PER_TABLE

        # Buy-in math: $2 = 2 BB × 40 BB minimum × 4 fish = 320 chips.
        _, min_buy_in, _ = table_buy_in_window("$2")
        expected = CASINO_FISH_PER_TABLE * int(
            min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER
        )
        assert casino_2.bank_pool_drawn == expected

        # Pool decreased by the spawn cost.
        pool_after = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        assert pool_after == 10_000 - expected

        # Table row exists with casino type.
        all_tables = tables.list_all_tables(sandbox_id=SBX)
        casino_rows = [t for t in all_tables if t.table_type == "casino"]
        assert len(casino_rows) >= 1
        casino_row = next(t for t in casino_rows if t.stake_label == "$2")
        seated_fish = {
            slot.get("personality_id")
            for slot in casino_row.seats
            if slot.get("kind") == "ai"
        }
        assert seated_fish == set(casino_2.fish_seated)

    def test_idempotent_across_ticks(self, db_setup):
        """Re-running the resolver doesn't double-spawn the same casino."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        first = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        second = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        # Spawned once; second tick is a no-op.
        assert len(first.spawns) >= 1
        assert second.spawns == []

    def test_dollar_10_threshold_requires_more_pool(self, db_setup):
        """$10 casino needs $10 threshold pool, not just $2's threshold."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Seed enough for $2 but not enough for $10.
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        stakes = [s.stake_label for s in batch.spawns]
        assert "$2" in stakes
        assert "$10" not in stakes

    def test_dollar_10_spawn_with_enough_pool(self, db_setup):
        """When pool ≥ $10 threshold, both $2 and $10 casinos spawn."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Need enough fish for BOTH casinos — re-seed sandbox with more.
        # The fixture has CASINO_FISH_PER_TABLE + 1 fish; need 2N for two
        # casinos. Seed additional fish here.
        personality = db_setup["personality"]
        for i in range(CASINO_FISH_PER_TABLE + 1, 2 * CASINO_FISH_PER_TABLE + 2):
            pid = f"test_fish_{i}"
            personality.save_personality(
                f"TestFish{i}", _fish_config(pid, f"TestFish{i}"),
                personality_id=pid,
            )
            bankroll.save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid, chips=0, last_regen_tick=ANCHOR,
                ),
                sandbox_id=SBX,
            )

        seed_bank_pool(ledger, sandbox_id=SBX, amount=80_000)

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        stakes = sorted(s.stake_label for s in batch.spawns)
        assert stakes == ["$10", "$2"] or stakes == ["$2", "$10"]


# --- Casino teardown tests ---------------------------------------------------


class TestCasinoTeardown:
    def test_teardown_when_no_fish_and_pool_empty(self, db_setup):
        """Casino with no fish + empty pool → torn down."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]

        # Spawn a casino, then drain it manually by replacing the seats.
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        # Find the casino, drain all seats (simulate fish all busted).
        casino_row = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        empty_state = CashTableState(
            table_id=casino_row.table_id,
            stake_label=casino_row.stake_label,
            seats=[open_slot() for _ in range(6)],
            created_at=casino_row.created_at,
            last_activity_at=casino_row.last_activity_at,
            name=casino_row.name,
            table_type='casino',
        )
        tables.save_table(empty_state, sandbox_id=SBX, now=ANCHOR)

        # Pool is partly drained from the spawn but not zero. Drain it
        # below the refill cost so teardown actually fires.
        # Spawn cost was 320; we seeded 10000 → pool = 9680. Need it
        # below the refill cost (4 × 80 = 320). Hard to do without
        # writing arbitrary ledger entries — instead, force teardown
        # by draining via tourist_injection ledger row.
        from core.economy.ledger import record_tourist_injection
        # Drain pool to 0 — single injection of the remaining balance.
        current_pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        record_tourist_injection(
            ledger,
            personality_id="test_fish_0",
            amount=current_pool,
            sandbox_id=SBX,
        )
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 0

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert any(t.table_id == casino_row.table_id for t in batch.teardowns)
        # Table row gone.
        remaining = tables.list_all_tables(sandbox_id=SBX)
        assert casino_row.table_id not in {t.table_id for t in remaining}

    def test_no_teardown_when_fish_still_seated(self, db_setup):
        """Active casino with fish in seats is not torn down."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        # Don't drain — fish are still seated.
        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert batch.teardowns == []


# --- Conservation invariant --------------------------------------------------


class TestSpawnConservation:
    """Spawn writes are drift-safe: chips at fish seats match ledger rows."""

    def test_drift_neutral_after_spawn(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        before_creations = sum(
            ledger.sum_creations_by_reason(sandbox_id=SBX).values()
        )
        before_destructions = sum(
            ledger.sum_destructions_by_reason(sandbox_id=SBX).values()
        )
        before_ledger_outstanding = before_creations - before_destructions

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert len(batch.spawns) >= 1
        total_drawn = sum(s.bank_pool_drawn for s in batch.spawns)

        after_creations = sum(
            ledger.sum_creations_by_reason(sandbox_id=SBX).values()
        )
        after_destructions = sum(
            ledger.sum_destructions_by_reason(sandbox_id=SBX).values()
        )
        after_ledger_outstanding = after_creations - after_destructions

        # casino_seat_seed are creations; nothing else was destroyed.
        assert after_ledger_outstanding - before_ledger_outstanding == total_drawn

        # Actual seat chips = creation amount.
        casino_seat_total = 0
        for t in tables.list_all_tables(sandbox_id=SBX):
            if t.table_type != "casino":
                continue
            for slot in t.seats:
                if slot.get("kind") == "ai":
                    casino_seat_total += int(slot.get("chips", 0))
        assert casino_seat_total == total_drawn


class TestConstants:
    def test_thresholds_defined(self):
        assert "$2" in CASINO_SPAWN_THRESHOLDS
        assert "$10" in CASINO_SPAWN_THRESHOLDS
        assert CASINO_SPAWN_THRESHOLDS["$10"] > CASINO_SPAWN_THRESHOLDS["$2"]

    def test_casino_seat_seed_in_draw_reasons(self):
        assert "casino_seat_seed" in BANK_POOL_DRAW_REASONS

    def test_bank_pool_sim_seed_registered(self):
        assert "bank_pool_sim_seed" in LEDGER_REASONS


# --- Ephemeral-tourist behavior (post-EPHEMERAL_TOURISTS spec) -------------


class TestEphemeralTourists:
    """Verify the new on-demand tourist behavior: seats carry inline
    personality, ledger context records template_key + leak, and
    teardown returns residual chips to the pool."""

    def test_seats_carry_ephemeral_personality(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        ai_seats = [s for s in casino.seats if s.get("kind") == "ai"]
        assert ai_seats, "expected at least one tourist seated"
        for seat in ai_seats:
            # Inline personality dict mirrors the fish JSON shape
            inline = seat.get("ephemeral_personality")
            assert inline is not None, "tourist seat missing inline personality"
            assert inline["archetype"] == "fish"
            assert inline["ephemeral"] is True
            assert inline["rule_strategy"] == "fish"
            assert inline.get("fish_leak"), "tourist missing designated leak"
            # Synthetic pid format
            assert seat["personality_id"].startswith("tourist-")
            # display_name set on the seat for UI consumption
            assert seat.get("display_name")

    def test_ledger_context_records_template_and_leak(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        # Walk recent ledger entries for casino_seat_seed rows; assert
        # template_key + fish_leak present in context.
        entries = ledger.recent_entries(limit=50)
        seeds = [e for e in entries if e["reason"] == "casino_seat_seed"]
        assert len(seeds) == CASINO_FISH_PER_TABLE
        for e in seeds:
            ctx = e.get("context") or {}
            assert ctx.get("template_key"), f"missing template_key: {e}"
            assert ctx.get("fish_leak"), f"missing fish_leak: {e}"
            assert ctx.get("display_name"), f"missing display_name: {e}"

    def test_teardown_returns_residual_chips_to_pool(self, db_setup):
        """Tourists with chips remaining when their casino tears down
        must return those chips to the pool via casino_seat_return."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        # Measure pool depth before teardown; expect it to grow by the
        # total residual chips on tourist seats.
        residual_total = sum(
            int(s.get("chips", 0)) for s in casino.seats
            if s.get("kind") == "ai" and s.get("ephemeral_personality")
        )
        pool_before_teardown = compute_bank_pool_reserves(ledger, sandbox_id=SBX)

        # Force teardown by draining the pool below refill cost AND
        # vacating all tourist seats (so _casino_has_seated_tourists
        # returns False).
        for i, slot in enumerate(casino.seats):
            if slot.get("kind") == "ai":
                # Leave the chips on the seat to verify they get returned.
                # Just clear the ephemeral marker by replacing with an
                # ai_slot that has chips but no ephemeral_personality —
                # actually we want to preserve chips so the return fires.
                pass
        # Simpler: directly call _return_seat_residuals_to_pool and
        # then delete the table to mimic the resolver's teardown branch.
        from cash_mode.casino_provisioning import _return_seat_residuals_to_pool
        returned, stranded = _return_seat_residuals_to_pool(
            casino, chip_ledger_repo=ledger,
            sandbox_id=SBX, reason_detail="test_forced_teardown",
        )
        assert stranded == 0
        assert returned == residual_total

        pool_after = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        assert pool_after - pool_before_teardown == residual_total

        # Verify the ledger rows exist with the right reason
        return_entries = [
            e for e in ledger.recent_entries(limit=50)
            if e["reason"] == "casino_seat_return"
        ]
        assert len(return_entries) == len([
            s for s in casino.seats
            if s.get("kind") == "ai" and s.get("ephemeral_personality")
            and int(s.get("chips", 0)) > 0
        ])

    def test_partial_return_failure_strands_chips_not_silent(self, db_setup):
        """If a single `record_casino_seat_return` write fails, the helper
        reports the stranded amount via the second return value — the
        caller MUST NOT delete_table in that case (would break drift)."""
        from unittest.mock import patch
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        from cash_mode.casino_provisioning import _return_seat_residuals_to_pool

        # Force every ledger write to raise — simulates DB lock / IO
        # failure mid-teardown. Helper should report all chips stranded.
        with patch(
            'cash_mode.casino_provisioning.record_casino_seat_return',
            side_effect=RuntimeError("simulated DB failure"),
        ):
            returned, stranded = _return_seat_residuals_to_pool(
                casino, chip_ledger_repo=ledger,
                sandbox_id=SBX, reason_detail="test_partial_fail",
            )
        assert returned == 0
        assert stranded > 0, "expected stranded chips when writes fail"

    def test_spawn_teardown_roundtrip_is_drift_neutral(self, db_setup):
        """Full conservation property: seed pool → spawn casino → tear
        down with all tourists still holding chips → pool returns to
        ~original depth (minus only chips that have actually moved)."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        initial_pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)

        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        # Force teardown by returning all residual + deleting
        from cash_mode.casino_provisioning import _return_seat_residuals_to_pool
        _returned, stranded = _return_seat_residuals_to_pool(
            casino, chip_ledger_repo=ledger,
            sandbox_id=SBX, reason_detail="test_roundtrip",
        )
        assert stranded == 0
        tables.delete_table(casino.table_id, sandbox_id=SBX)

        # Pool should be back to ~initial. The seed cost out + return in
        # should net to zero for tourist seats that never lost chips.
        final_pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        assert final_pool == initial_pool, (
            f"drift: initial {initial_pool}, final {final_pool}")


if __name__ == "__main__":
    unittest.main()
