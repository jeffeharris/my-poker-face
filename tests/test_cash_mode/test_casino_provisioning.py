"""Casino provisioning + bank-pool seed.

Spec: `docs/plans/CASH_MODE_CLOSED_ECONOMY.md`. Covers the three-pass
lifecycle (refill / teardown-with-closing / spawn) and the operator-
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
    CASINO_CLOSING_HAND_COUNTDOWN,
    CASINO_FISH_BUY_IN_MULTIPLIER,
    CASINO_FISH_MAX,
    CASINO_FISH_MIN,
    CASINO_MIN_HUNGRY_GRINDERS,
    CASINO_SPAWN_THRESHOLDS,
    CasinoRefill,
    CasinoSpawn,
    _casino_table_id,
    clear_closing,
    decrement_closing_hands,
    enter_closing,
    get_closing_countdown,
    is_closing,
    resolve_casino_provisioning,
)
from cash_mode.closed_economy import (
    GRINDER_COMFORT_ZONES,
    GRINDER_HUNGER_THRESHOLD,
    compute_bank_pool_reserves,
    is_hungry_grinder,
    list_hungry_grinders,
    seed_bank_pool,
)
from cash_mode.stakes_ladder import table_buy_in_window
from cash_mode.tables import CashTableState, open_slot
from core.economy.ledger import (
    BANK_POOL_DEPOSIT_REASONS,
    BANK_POOL_DRAW_REASONS,
    LEDGER_REASONS,
    record_tourist_injection,
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


def _grinder_config(comfort_zone: str = "$2") -> dict:
    return {
        "play_style": "tight aggressive grinder",
        "anchors": {
            "baseline_aggression": 0.6,
            "baseline_looseness": 0.25,
            "ego": 0.5,
            "poise": 0.8,
            "expressiveness": 0.3,
            "risk_identity": 0.5,
            "adaptation_bias": 0.5,
            "baseline_energy": 0.5,
            "recovery_rate": 0.2,
        },
        "bankroll_knobs": {
            "starting_bankroll": 10_000,
            "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": comfort_zone,
        },
    }


@pytest.fixture
def db_setup(tmp_path):
    """Tempdb with N fish + M hungry grinders, no active casinos."""
    db = str(tmp_path / "casino_provisioning.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    tables = CashTableRepository(db)
    ledger = ChipLedgerRepository(db)
    personality = PersonalityRepository(db)

    # Fish are now generated ephemerally from the four named templates
    # at casino spawn / refill time. Seed the templates so
    # `spawn_ephemeral_fish` can clone them. No bankroll rows — the
    # templates themselves never seat; their clones do.
    from cash_mode.closed_economy import EPHEMERAL_FISH_TEMPLATES
    fish_pids = list(EPHEMERAL_FISH_TEMPLATES)
    for pid in fish_pids:
        # Display name e.g. "Vacation Greg" for `vacation_greg`.
        display = ' '.join(word.capitalize() for word in pid.split('_'))
        personality.save_personality(
            display, _fish_config(pid, display),
            personality_id=pid,
        )

    # 3 hungry grinders at the casino tier — well below the hunger threshold.
    grinder_pids = []
    for i in range(3):
        pid = f"hungry_grinder_{i}"
        personality.save_personality(
            f"Grinder{i}", _grinder_config(comfort_zone="$2"),
            personality_id=pid,
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id=pid,
                chips=4_000,  # 40% of starting → hungry
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        grinder_pids.append(pid)

    # Closing state lives in the DB (cash_tables.closing_hand_countdown).
    # Each test fixture starts with a fresh tempdb, so no cross-test
    # contamination — nothing to clear.

    return {
        "bankroll": bankroll,
        "tables": tables,
        "ledger": ledger,
        "personality": personality,
        "fish_pids": fish_pids,
        "grinder_pids": grinder_pids,
    }


# --- Bank-pool seed tests ----------------------------------------------------


class TestBankPoolSeed:
    def test_seed_increases_pool_depth(self, db_setup):
        ledger = db_setup["ledger"]
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 0
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 10_000

    def test_seed_is_drift_neutral(self, db_setup):
        ledger = db_setup["ledger"]
        before_c = ledger.sum_creations_by_reason(sandbox_id=SBX)
        before_d = ledger.sum_destructions_by_reason(sandbox_id=SBX)
        seed_bank_pool(ledger, sandbox_id=SBX, amount=5_000)
        after_c = ledger.sum_creations_by_reason(sandbox_id=SBX)
        after_d = ledger.sum_destructions_by_reason(sandbox_id=SBX)
        net_c = sum(after_c.values()) - sum(before_c.values())
        net_d = sum(after_d.values()) - sum(before_d.values())
        assert net_c == 5_000
        assert net_d == 5_000
        assert net_c - net_d == 0

    def test_zero_amount_no_op(self, db_setup):
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=0)
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 0


# --- Grinder definition ------------------------------------------------------


class TestGrinderDefinition:
    def test_fish_is_not_grinder(self, db_setup):
        bankroll = db_setup["bankroll"]
        # Fish bankroll is 0, below threshold, but fish archetype excludes them.
        assert not is_hungry_grinder(
            "test_fish_0", bankroll_repo=bankroll, sandbox_id=SBX, now=ANCHOR,
        )

    def test_hungry_grinder_under_threshold(self, db_setup):
        bankroll = db_setup["bankroll"]
        assert is_hungry_grinder(
            "hungry_grinder_0", bankroll_repo=bankroll, sandbox_id=SBX, now=ANCHOR,
        )

    def test_full_bankroll_not_hungry(self, db_setup):
        """Grinder at 100% of starting is not 'hungry.'"""
        bankroll = db_setup["bankroll"]
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_0",
                chips=10_000,  # 100% of starting
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        assert not is_hungry_grinder(
            "hungry_grinder_0", bankroll_repo=bankroll, sandbox_id=SBX, now=ANCHOR,
        )

    def test_wrong_comfort_zone(self, db_setup):
        """A high-stakes grinder isn't in the casino tier — excluded."""
        bankroll = db_setup["bankroll"]
        personality = db_setup["personality"]
        personality.save_personality(
            "HighStakes",
            _grinder_config(comfort_zone="$1000"),  # not in casino tier
            personality_id="high_stakes",
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="high_stakes",
                chips=4_000,  # would qualify on hunger
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        assert not is_hungry_grinder(
            "high_stakes", bankroll_repo=bankroll, sandbox_id=SBX, now=ANCHOR,
        )

    def test_list_hungry_grinders_sorted_by_deficit(self, db_setup):
        """Most-desperate grinder comes first."""
        bankroll = db_setup["bankroll"]
        # Three grinders: 4000, 2000, 6000 chips (out of 10000).
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_0", chips=4_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_1", chips=2_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_2", chips=6_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        result = list_hungry_grinders(
            bankroll, sandbox_id=SBX, now=ANCHOR,
        )
        # Most-desperate first: 2000 < 4000 < 6000.
        assert result[:3] == [
            "hungry_grinder_1", "hungry_grinder_0", "hungry_grinder_2",
        ]


# --- Casino spawn tests ------------------------------------------------------


class TestCasinoSpawn:
    def test_no_spawn_without_pool(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert batch.spawns == []
        assert tables.list_all_tables(sandbox_id=SBX) == []

    def test_no_spawn_without_hungry_grinders(self, db_setup):
        """Demand signal: no spawn when no grinders are hungry."""
        bankroll = db_setup["bankroll"]
        tables = db_setup["tables"]
        ledger = db_setup["ledger"]
        # Bump all grinders to full bankroll — no longer hungry.
        for pid in db_setup["grinder_pids"]:
            bankroll.save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid, chips=10_000, last_regen_tick=ANCHOR,
                ),
                sandbox_id=SBX,
            )
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert batch.spawns == []

    def test_spawn_with_full_economic_signals(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert len(batch.spawns) >= 1
        casino_2 = next(s for s in batch.spawns if s.stake_label == "$2")
        # Variable fish count between MIN and MAX.
        assert CASINO_FISH_MIN <= len(casino_2.fish_seated) <= CASINO_FISH_MAX
        # Buy-in math is correct.
        _, min_buy_in, _ = table_buy_in_window("$2")
        per_fish = int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER)
        assert casino_2.bank_pool_drawn == len(casino_2.fish_seated) * per_fish

    def test_idempotent_across_ticks(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        first = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        # Second tick should NOT re-spawn (refill at most).
        second = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        assert len(first.spawns) >= 1
        assert second.spawns == []


# --- Refill pass -------------------------------------------------------------


class TestCasinoRefill:
    def test_refill_seeds_one_fish_per_tick(self, db_setup):
        """Open seat at active casino → one fish added (not many)."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        # Spawn with rng pinned so we know how many fish landed initially.
        spawn_batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        if not spawn_batch.spawns:
            pytest.skip("rng did not produce a spawn")
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        seated_count_before = sum(
            1 for s in casino.seats if s.get("kind") == "ai"
        )
        # Only test refill if there were open seats AND fewer than MAX
        # fish seated (room for at least one refill).
        if seated_count_before >= CASINO_FISH_MAX:
            pytest.skip("spawn already at max fish; no refill possible")

        # Manually empty one fish seat (simulates a bust).
        for i, slot in enumerate(casino.seats):
            if slot.get("kind") == "ai":
                casino.seats[i] = open_slot()
                break
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)

        refill_batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(2), now=ANCHOR,
        )
        # One refill = exactly one CasinoRefill entry.
        assert len(refill_batch.refills) == 1
        assert refill_batch.refills[0].table_id == casino.table_id

    def test_no_refill_when_pool_empty(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Spawn first.
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        # Drain the pool by injecting all remaining chips to a fish.
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        if pool > 0:
            record_tourist_injection(
                ledger, personality_id="test_fish_0", amount=pool, sandbox_id=SBX,
            )
        # Empty a casino seat → potential refill candidate.
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        for i, slot in enumerate(casino.seats):
            if slot.get("kind") == "ai":
                casino.seats[i] = open_slot()
                break
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)

        # Now resolve — pool is empty, so no refill should happen.
        result = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        assert result.refills == []


# --- Closing-state lifecycle -------------------------------------------------


class TestClosingState:
    def test_teardown_enters_closing_state(self, db_setup):
        """No fish + pool can't refill → casino enters 'closing', not deleted."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        # Empty all seats (simulate all fish busting).
        for i in range(len(casino.seats)):
            casino.seats[i] = open_slot()
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)
        # Drain the pool so no refill is possible.
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        if pool > 0:
            record_tourist_injection(
                ledger, personality_id="test_fish_0", amount=pool, sandbox_id=SBX,
            )

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        # Teardown event recorded as 'closing_announced'.
        assert any(
            t.table_id == casino.table_id
            and t.reason.startswith('closing_announced')
            for t in batch.teardowns
        )
        # Table still exists in DB (not deleted yet).
        assert any(
            t.table_id == casino.table_id
            for t in tables.list_all_tables(sandbox_id=SBX)
        )
        # And it's marked as closing.
        assert is_closing(tables, SBX, casino.table_id)
        assert get_closing_countdown(tables, SBX, casino.table_id) == CASINO_CLOSING_HAND_COUNTDOWN

    def test_countdown_elapsed_deletes_table(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        # Drain pool so the same-tick spawn pass can't immediately
        # re-open at the same stake after we delete this casino. (When
        # the pool has chips, the post-teardown spawn pass will create
        # a fresh casino with the same table_id — the right product
        # behavior for steady state, but it masks the delete in the
        # narrow assertion below.)
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        if pool > 0:
            record_tourist_injection(
                ledger, personality_id="test_fish_0",
                amount=pool, sandbox_id=SBX,
            )
        # Empty + save first, THEN enter closing. With DB-backed state,
        # save_table writes the whole row including `closing_hand_countdown`
        # from the (stale) CashTableState — so calling enter_closing
        # AFTER the save is the correct ordering.
        for i in range(len(casino.seats)):
            casino.seats[i] = open_slot()
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)
        enter_closing(tables, SBX, casino.table_id, 0)

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        assert any(
            t.table_id == casino.table_id
            and t.reason == 'closing_countdown_elapsed'
            for t in batch.teardowns
        )
        assert not any(
            t.table_id == casino.table_id
            for t in tables.list_all_tables(sandbox_id=SBX)
        )
        assert not is_closing(tables, SBX, casino.table_id)

    def test_no_refill_at_closing_casino(self, db_setup):
        """Closing casinos don't take new fish."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        # Empty one seat.
        for i, slot in enumerate(casino.seats):
            if slot.get("kind") == "ai":
                casino.seats[i] = open_slot()
                break
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)
        # Mark as closing.
        enter_closing(tables, SBX, casino.table_id, 5)
        # Pool still has chips — but refill should NOT fire.
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) > 0

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        assert batch.refills == []

    def test_no_spawn_while_closing(self, db_setup):
        """Spawn pass skips stakes with a closing casino."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Seed a closing casino directly.
        seed_bank_pool(ledger, sandbox_id=SBX, amount=20_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino" and t.stake_label == "$2"
        )
        enter_closing(tables, SBX, casino.table_id, 5)

        # Pool still healthy. Spawn should NOT fire at $2 because one is closing.
        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(1), now=ANCHOR,
        )
        new_2 = [s for s in batch.spawns if s.stake_label == "$2"]
        assert new_2 == []


# --- Conservation invariant --------------------------------------------------


class TestSpawnConservation:
    def test_drift_neutral_after_spawn(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        before_c = sum(ledger.sum_creations_by_reason(sandbox_id=SBX).values())
        before_d = sum(ledger.sum_destructions_by_reason(sandbox_id=SBX).values())
        before_outstanding = before_c - before_d

        batch = resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        assert len(batch.spawns) >= 1
        total_drawn = sum(s.bank_pool_drawn for s in batch.spawns)

        after_c = sum(ledger.sum_creations_by_reason(sandbox_id=SBX).values())
        after_d = sum(ledger.sum_destructions_by_reason(sandbox_id=SBX).values())
        after_outstanding = after_c - after_d

        # Spawn creations = total chips at casino seats.
        assert after_outstanding - before_outstanding == total_drawn

        casino_seat_total = 0
        for t in tables.list_all_tables(sandbox_id=SBX):
            if t.table_type != "casino":
                continue
            for slot in t.seats:
                if slot.get("kind") == "ai":
                    casino_seat_total += int(slot.get("chips", 0))
        assert casino_seat_total == total_drawn


class TestPersistence:
    """v113: closing state survives a fresh repo instantiation (DB-backed)."""

    def test_closing_state_persists_across_repo_instances(self, db_setup, tmp_path):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        enter_closing(tables, SBX, casino.table_id, 7)

        # Spin up a NEW repo instance against the same DB and verify
        # the closing state survives (would fail with the in-memory
        # implementation since module-level dicts don't share across
        # instances).
        # Use the underlying _db_path to wire a sibling repo.
        from poker.repositories.cash_table_repository import CashTableRepository
        fresh = CashTableRepository(tables.db_path)
        assert is_closing(fresh, SBX, casino.table_id)
        assert get_closing_countdown(fresh, SBX, casino.table_id) == 7

    def test_decrement_persists(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        enter_closing(tables, SBX, casino.table_id, 5)
        decrement_closing_hands(tables, SBX, casino.table_id)
        decrement_closing_hands(tables, SBX, casino.table_id)
        # DB now holds countdown=3.
        assert get_closing_countdown(tables, SBX, casino.table_id) == 3

    def test_decrement_floors_at_zero(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        enter_closing(tables, SBX, casino.table_id, 1)
        decrement_closing_hands(tables, SBX, casino.table_id)
        decrement_closing_hands(tables, SBX, casino.table_id)  # over-decrement
        assert get_closing_countdown(tables, SBX, casino.table_id) == 0
        # Casino is still in closing state (countdown == 0, not None).
        assert is_closing(tables, SBX, casino.table_id)

    def test_save_table_preserves_closing_state(self, db_setup):
        """save_table calls from movement / sync flows must not clobber
        the closing column — pre-existing rows' value must round-trip."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables, bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger, sandbox_id=SBX,
            rng=random.Random(0), now=ANCHOR,
        )
        casino = next(
            t for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )
        enter_closing(tables, SBX, casino.table_id, 9)

        # Re-load the row (now carries closing_hand_countdown=9), mutate
        # something unrelated, save back. The countdown must survive.
        reloaded = tables.load_table(casino.table_id, sandbox_id=SBX)
        assert reloaded.closing_hand_countdown == 9
        # Touch last_activity_at to trigger a save.
        tables.save_table(reloaded, sandbox_id=SBX, now=ANCHOR)
        after = tables.load_table(casino.table_id, sandbox_id=SBX)
        assert after.closing_hand_countdown == 9


class TestConstants:
    def test_thresholds_defined(self):
        assert "$2" in CASINO_SPAWN_THRESHOLDS
        assert "$10" in CASINO_SPAWN_THRESHOLDS
        assert CASINO_SPAWN_THRESHOLDS["$10"] > CASINO_SPAWN_THRESHOLDS["$2"]

    def test_fish_range_sensible(self):
        assert CASINO_FISH_MIN >= 1
        assert CASINO_FISH_MAX >= CASINO_FISH_MIN
        assert CASINO_FISH_MAX <= 6  # bounded by TABLE_SEAT_COUNT

    def test_closing_countdown_positive(self):
        assert CASINO_CLOSING_HAND_COUNTDOWN > 0

    def test_ledger_reasons_registered(self):
        assert "casino_seat_seed" in LEDGER_REASONS
        assert "bank_pool_sim_seed" in LEDGER_REASONS
        assert "casino_seat_seed" in BANK_POOL_DRAW_REASONS


if __name__ == "__main__":
    unittest.main()
