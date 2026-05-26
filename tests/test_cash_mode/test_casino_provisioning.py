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
    CASINO_CLOSE_THRESHOLDS,
    CASINO_CLOSING_HAND_COUNTDOWN,
    CASINO_FISH_BUY_IN_MULTIPLIER,
    CASINO_FISH_MAX,
    CASINO_FISH_MIN,
    CASINO_MIN_HUNGRY_GRINDERS,
    CASINO_SPAWN_THRESHOLDS,
    WHALE_POOL_FLOORS,
    WHALE_POOL_THRESHOLDS,
    WHALE_PREFUND_MAX_MULT,
    WHALE_PREFUND_MIN_MULT,
    CasinoRefill,
    CasinoSpawn,
    WhaleSpawn,
    WhaleTeardown,
    _casino_table_id,
    _shed_excess_fish,
    clear_closing,
    decrement_closing_hands,
    enter_closing,
    get_closing_countdown,
    is_closing,
    resolve_casino_provisioning,
    resolve_whale_provisioning,
)
from cash_mode.closed_economy import (
    GRINDER_COMFORT_ZONES,
    GRINDER_HUNGER_THRESHOLD,
    compute_bank_pool_reserves,
    is_hungry_grinder,
    list_affordable_predators,
    list_hungry_grinders,
    seed_bank_pool,
)
from cash_mode.stakes_ladder import table_buy_in_window
from cash_mode.tables import (
    CashTableState,
    IdlePoolEntry,
    ai_slot,
    ai_slot_fish,
    open_slot,
)
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

    # Fish are real, curated `archetype='fish'` personas. Casino spawn
    # selects them via `list_fish_for_cash_mode` and pool-funds their
    # bankroll on seating — no pre-seeded bankroll rows required.
    fish_pids = [
        'vacation_greg',
        'bachelorette_brenda',
        'cruise_carl',
        'birthday_bobby',
        'after_hours_trent',
        'lucky_mona',
        'slots_linda',
        'golf_trip_brad',
        'freddie_fratboy',
    ]
    for pid in fish_pids:
        # Display name e.g. "Vacation Greg" for `vacation_greg`.
        display = ' '.join(word.capitalize() for word in pid.split('_'))
        personality.save_personality(
            display,
            _fish_config(pid, display),
            personality_id=pid,
        )

    # 3 hungry grinders at the casino tier — well below the hunger threshold.
    grinder_pids = []
    for i in range(3):
        pid = f"hungry_grinder_{i}"
        personality.save_personality(
            f"Grinder{i}",
            _grinder_config(comfort_zone="$2"),
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
            "test_fish_0",
            bankroll_repo=bankroll,
            sandbox_id=SBX,
            now=ANCHOR,
        )

    def test_hungry_grinder_under_threshold(self, db_setup):
        bankroll = db_setup["bankroll"]
        assert is_hungry_grinder(
            "hungry_grinder_0",
            bankroll_repo=bankroll,
            sandbox_id=SBX,
            now=ANCHOR,
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
            "hungry_grinder_0",
            bankroll_repo=bankroll,
            sandbox_id=SBX,
            now=ANCHOR,
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
            "high_stakes",
            bankroll_repo=bankroll,
            sandbox_id=SBX,
            now=ANCHOR,
        )

    def test_list_hungry_grinders_sorted_by_deficit(self, db_setup):
        """Most-desperate grinder comes first."""
        bankroll = db_setup["bankroll"]
        # Three grinders: 4000, 2000, 6000 chips (out of 10000).
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_0",
                chips=4_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_1",
                chips=2_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="hungry_grinder_2",
                chips=6_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        result = list_hungry_grinders(
            bankroll,
            sandbox_id=SBX,
            now=ANCHOR,
        )
        # Most-desperate first: 2000 < 4000 < 6000.
        assert result[:3] == [
            "hungry_grinder_1",
            "hungry_grinder_0",
            "hungry_grinder_2",
        ]


# --- Casino spawn tests ------------------------------------------------------


class TestCasinoSpawn:
    def test_no_spawn_without_pool(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
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
                    personality_id=pid,
                    chips=10_000,
                    last_regen_tick=ANCHOR,
                ),
                sandbox_id=SBX,
            )
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        assert batch.spawns == []

    def test_spawn_with_full_economic_signals(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        assert len(batch.spawns) >= 1
        casino_2 = next(s for s in batch.spawns if s.stake_label == "$2")
        # Variable fish count between MIN and MAX.
        assert CASINO_FISH_MIN <= len(casino_2.fish_seated) <= CASINO_FISH_MAX
        # Each fish is pool-funded with a prefunded bankroll (~3x buy-in,
        # jittered, capped by pool depth) — so the draw covers at least one
        # buy-in per fish.
        _, min_buy_in, _ = table_buy_in_window("$2")
        per_fish = int(min_buy_in * CASINO_FISH_BUY_IN_MULTIPLIER)
        assert casino_2.bank_pool_drawn >= len(casino_2.fish_seated) * per_fish

    def test_idempotent_across_ticks(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        first = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        # Second tick should NOT re-spawn (refill at most).
        second = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )
        if not spawn_batch.spawns:
            pytest.skip("rng did not produce a spawn")
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
        seated_count_before = sum(1 for s in casino.seats if s.get("kind") == "ai")
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(2),
            now=ANCHOR,
        )
        # One refill = exactly one CasinoRefill entry.
        assert len(refill_batch.refills) == 1
        assert refill_batch.refills[0].table_id == casino.table_id

    def test_refill_clears_seated_fish_idle_row(self, db_setup):
        """Regression: a fish that left a casino on `take_break` keeps an
        idle-pool row; when refill re-seats it, that row must be cleared.
        Casino provisioning seats straight into `cash_tables` (not via the
        lobby live-fill path), so without an explicit clear the fish ends
        up both seated and idle — the `seated_and_idle` split-brain seen
        live for the casino fish cluster.
        """
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)

        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )
        casino = next(
            (t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino"),
            None,
        )
        if casino is None:
            pytest.skip("rng did not produce a spawn")
        seated_count = sum(1 for s in casino.seats if s.get("kind") == "ai")
        if seated_count >= CASINO_FISH_MAX:
            pytest.skip("spawn already at max fish; no refill possible")

        # Open one seat (simulate a bust) so refill has somewhere to seat.
        for i, slot in enumerate(casino.seats):
            if slot.get("kind") == "ai":
                casino.seats[i] = open_slot()
                break
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)

        # Every fish carries a stale `take_break` idle row — whichever one
        # refill picks, its row should be gone afterward.
        for pid in db_setup["fish_pids"]:
            tables.save_idle(
                IdlePoolEntry(
                    personality_id=pid,
                    left_at=ANCHOR,
                    reason="take_break",
                ),
                sandbox_id=SBX,
            )
        idle_before = {e.personality_id for e in tables.list_idle(sandbox_id=SBX)}

        refill_batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(2),
            now=ANCHOR,
        )
        assert len(refill_batch.refills) == 1
        refilled_pid = refill_batch.refills[0].fish_id
        idle_after = {e.personality_id for e in tables.list_idle(sandbox_id=SBX)}
        # The re-seated fish's idle row is cleared; the others remain.
        assert refilled_pid in idle_before
        assert refilled_pid not in idle_after
        assert (idle_before - idle_after) == {refilled_pid}

    def test_no_refill_when_pool_empty(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Spawn first.
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        # Drain the pool by injecting all remaining chips to a fish.
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        if pool > 0:
            record_tourist_injection(
                ledger,
                personality_id="test_fish_0",
                amount=pool,
                sandbox_id=SBX,
            )
        # Also zero every fish bankroll. Otherwise the resolver's
        # drain-on-exit sweep returns a departed fish's pool-funded
        # bankroll to the pool and that funds a refill — only with no
        # recoverable liquidity anywhere does the pool gate block refill.
        for pid in db_setup["fish_pids"]:
            bankroll.save_ai_bankroll(
                AIBankrollState(personality_id=pid, chips=0, last_regen_tick=ANCHOR),
                sandbox_id=SBX,
            )
        # Empty a casino seat → potential refill candidate.
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
        for i, slot in enumerate(casino.seats):
            if slot.get("kind") == "ai":
                casino.seats[i] = open_slot()
                break
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)

        # Now resolve — pool is empty, so no refill should happen.
        result = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )
        assert result.refills == []


# --- Closing-state lifecycle -------------------------------------------------


class TestShedExcessFish:
    """Casinos over the fish cap shed the excess (chips back to pool)."""

    def _seat_excess_fish(self, db_setup, n_fish, buy_in=80):
        """Build a $2 casino seating `n_fish` stamped fish + open seats."""
        tables = db_setup["tables"]
        fish = db_setup["fish_pids"][:n_fish]
        seats = [ai_slot_fish(pid, buy_in) for pid in fish]
        while len(seats) < 6:
            seats.append(open_slot())
        casino = CashTableState(
            table_id="cash-casino-2-001",
            stake_label="$2",
            seats=seats,
            created_at=ANCHOR,
            last_activity_at=ANCHOR,
            name="Casino — $2",
            table_type="casino",
        )
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)
        return tables

    def test_sheds_down_to_max_and_returns_chips(self, db_setup):
        ledger = db_setup["ledger"]
        buy_in = 80
        n_fish = CASINO_FISH_MAX + 2  # over the cap
        tables = self._seat_excess_fish(db_setup, n_fish, buy_in)
        pool_before = compute_bank_pool_reserves(ledger, sandbox_id=SBX)

        shed = _shed_excess_fish(tables, ledger, sandbox_id=SBX, now=ANCHOR)

        assert shed == n_fish - CASINO_FISH_MAX
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
        seated_fish = sum(1 for s in casino.seats if s.get("archetype") == "fish")
        assert seated_fish == CASINO_FISH_MAX
        # Conservation: every shed fish's seat chips returned to the pool.
        pool_after = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        assert pool_after - pool_before == shed * buy_in

    def test_noop_at_or_below_cap(self, db_setup):
        ledger = db_setup["ledger"]
        tables = self._seat_excess_fish(db_setup, CASINO_FISH_MAX)
        pool_before = compute_bank_pool_reserves(ledger, sandbox_id=SBX)

        shed = _shed_excess_fish(tables, ledger, sandbox_id=SBX, now=ANCHOR)

        assert shed == 0
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == pool_before


class TestDamLadder:
    """High-stakes gates open in ladder order — no leapfrogging a tier."""

    def _resolve(self, db_setup, seed, rng_seed=1):
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=seed)
        resolve_casino_provisioning(
            cash_table_repo=db_setup["tables"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(rng_seed),
            now=ANCHOR,
        )
        return {
            t.stake_label
            for t in db_setup["tables"].list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        }

    def test_shallow_pool_opens_only_low_tiers(self, db_setup):
        # 60k covers $2 (5k) and $10 (50k) but not the $50 gate (100k).
        stakes = self._resolve(db_setup, seed=60_000)
        assert "$2" in stakes
        assert "$10" in stakes
        assert "$50" not in stakes

    def test_deep_pool_cascades_without_gaps(self, db_setup):
        stakes = self._resolve(db_setup, seed=2_000_000)
        # No gaps: present tiers must be a prefix of the ladder (you can't
        # have $50 without $10, $10 without $2). Casinos cap at $50 — the
        # $200+ band is whale-only now (see TestWhaleProvisioning).
        order = ["$2", "$10", "$50"]
        present = [s for s in order if s in stakes]
        assert present == order[: len(present)]
        # A deep pool reaches the top casino gate ($50).
        assert "$50" in stakes


class TestDamWindDown:
    """The high-stakes gate ($50) closes as the pool drains below its floor."""

    def _make_50_casino(self, db_setup, n_fish=2, buy_in=2000):
        fish = db_setup["fish_pids"][:n_fish]
        seats = [ai_slot_fish(p, buy_in) for p in fish]
        while len(seats) < 6:
            seats.append(open_slot())
        casino = CashTableState(
            table_id="cash-casino-50-001",
            stake_label="$50",
            seats=seats,
            created_at=ANCHOR,
            last_activity_at=ANCHOR,
            name="Casino — $50",
            table_type="casino",
        )
        db_setup["tables"].save_table(casino, sandbox_id=SBX, now=ANCHOR)

    def _resolve(self, db_setup, seed):
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=seed)
        resolve_casino_provisioning(
            cash_table_repo=db_setup["tables"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )

    def test_winds_down_below_floor(self, db_setup):
        self._make_50_casino(db_setup)
        # Pool below the $50 floor (45k) → wind down even with fish seated.
        self._resolve(db_setup, seed=CASINO_CLOSE_THRESHOLDS["$50"] - 10_000)
        assert get_closing_countdown(db_setup["tables"], SBX, "cash-casino-50-001") is not None

    def test_stays_open_above_floor(self, db_setup):
        self._make_50_casino(db_setup)
        # Pool comfortably above the floor → stays open (not closing).
        self._resolve(db_setup, seed=CASINO_CLOSE_THRESHOLDS["$50"] + 120_000)
        assert get_closing_countdown(db_setup["tables"], SBX, "cash-casino-50-001") is None


class TestRetiredTierWindDown:
    """A casino at a retired stake ($200 — now whale-only) winds down even
    with fish seated and a fat pool. Pass 1 won't refill it; Pass 2 closes
    it; the closing countdown plays out and teardown deletes it. Covers a
    pre-existing $200 casino in a DB created before the tier was retired."""

    def _make_200_casino(self, db_setup, n_fish=2, buy_in=8000):
        fish = db_setup["fish_pids"][:n_fish]
        seats = [ai_slot_fish(p, buy_in) for p in fish]
        while len(seats) < 6:
            seats.append(open_slot())
        casino = CashTableState(
            table_id="cash-casino-200-001",
            stake_label="$200",
            seats=seats,
            created_at=ANCHOR,
            last_activity_at=ANCHOR,
            name="Casino — $200",
            table_type="casino",
        )
        db_setup["tables"].save_table(casino, sandbox_id=SBX, now=ANCHOR)

    def _resolve(self, db_setup, rng_seed=1):
        return resolve_casino_provisioning(
            cash_table_repo=db_setup["tables"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(rng_seed),
            now=ANCHOR,
        )

    def test_enters_closing_despite_fat_pool_and_fish(self, db_setup):
        self._make_200_casino(db_setup)
        # A fat pool would normally keep a high-stakes casino open — but the
        # retired tier winds down regardless.
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=1_000_000)
        self._resolve(db_setup)
        assert get_closing_countdown(db_setup["tables"], SBX, "cash-casino-200-001") is not None
        # And it was NOT refilled past its existing fish (Pass 1 skips it).
        casino = next(
            t
            for t in db_setup["tables"].list_all_tables(sandbox_id=SBX)
            if t.table_id == "cash-casino-200-001"
        )
        assert sum(1 for s in casino.seats if s.get("archetype") == "fish") == 2

    def test_tears_down_after_countdown(self, db_setup):
        self._make_200_casino(db_setup)
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=1_000_000)
        # Resolve enough times for the closing countdown to elapse + delete.
        for _ in range(CASINO_CLOSING_HAND_COUNTDOWN + 3):
            self._resolve(db_setup)
        gone = all(
            t.table_id != "cash-casino-200-001"
            for t in db_setup["tables"].list_all_tables(sandbox_id=SBX)
        )
        assert gone


class TestClosingState:
    def test_teardown_enters_closing_state(self, db_setup):
        """No fish + pool can't refill → casino enters 'closing', not deleted."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
        # Empty all seats (simulate all fish busting).
        for i in range(len(casino.seats)):
            casino.seats[i] = open_slot()
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)
        # Drain the pool AND zero fish bankrolls so no refill is possible
        # (the drain-on-exit sweep would otherwise refund a departed fish's
        # pool-funded bankroll and fund a refill instead of closing).
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        if pool > 0:
            record_tourist_injection(
                ledger,
                personality_id="test_fish_0",
                amount=pool,
                sandbox_id=SBX,
            )
        for pid in db_setup["fish_pids"]:
            bankroll.save_ai_bankroll(
                AIBankrollState(personality_id=pid, chips=0, last_regen_tick=ANCHOR),
                sandbox_id=SBX,
            )

        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )
        # Teardown event recorded as 'closing_announced'.
        assert any(
            t.table_id == casino.table_id and t.reason.startswith('closing_announced')
            for t in batch.teardowns
        )
        # Table still exists in DB (not deleted yet).
        assert any(t.table_id == casino.table_id for t in tables.list_all_tables(sandbox_id=SBX))
        # And it's marked as closing.
        assert is_closing(tables, SBX, casino.table_id)
        assert get_closing_countdown(tables, SBX, casino.table_id) == CASINO_CLOSING_HAND_COUNTDOWN

    def test_countdown_elapsed_deletes_table(self, db_setup):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
        # Drain pool so the same-tick spawn pass can't immediately
        # re-open at the same stake after we delete this casino. (When
        # the pool has chips, the post-teardown spawn pass will create
        # a fresh casino with the same table_id — the right product
        # behavior for steady state, but it masks the delete in the
        # narrow assertion below.)
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        if pool > 0:
            record_tourist_injection(
                ledger,
                personality_id="test_fish_0",
                amount=pool,
                sandbox_id=SBX,
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )
        assert any(
            t.table_id == casino.table_id and t.reason == 'closing_countdown_elapsed'
            for t in batch.teardowns
        )
        assert not any(
            t.table_id == casino.table_id for t in tables.list_all_tables(sandbox_id=SBX)
        )
        assert not is_closing(tables, SBX, casino.table_id)

    def test_no_refill_at_closing_casino(self, db_setup):
        """Closing casinos don't take new fish."""
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(
            t
            for t in tables.list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino" and t.stake_label == "$2"
        )
        enter_closing(tables, SBX, casino.table_id, 5)

        # Pool still healthy. Spawn should NOT fire at $2 because one is closing.
        batch = resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
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
        # Prefund lands in fish bankrolls; only the buy-in sits on the seat.
        # Conservation: seat chips + fish bankrolls == total drawn from pool.
        fish_bankroll_total = 0
        for spawn in batch.spawns:
            for pid in spawn.fish_seated:
                st = bankroll.load_ai_bankroll(pid, sandbox_id=SBX)
                if st is not None:
                    fish_bankroll_total += int(st.chips)
        assert casino_seat_total + fish_bankroll_total == total_drawn


class TestPersistence:
    """v113: closing state survives a fresh repo instantiation (DB-backed)."""

    def test_closing_state_persists_across_repo_instances(self, db_setup, tmp_path):
        tables = db_setup["tables"]
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
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
            cash_table_repo=tables,
            bankroll_repo=bankroll,
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )
        casino = next(t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_type == "casino")
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


# --- Fish seat shape (permanent-persona model) -----------------------------


class TestFishSeats:
    """Casino fish are real curated personas, pool-funded, archetype-stamped."""

    def _spawn(self, db_setup):
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=60_000)
        return resolve_casino_provisioning(
            cash_table_repo=db_setup["tables"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(0),
            now=ANCHOR,
        )

    def test_fish_seats_are_real_personas_archetype_stamped(self, db_setup):
        batch = self._spawn(db_setup)
        assert batch.spawns, "expected a casino spawn"
        fish_set = set(db_setup["fish_pids"])
        seen = 0
        for t in db_setup["tables"].list_all_tables(sandbox_id=SBX):
            if t.table_type != "casino":
                continue
            for s in t.seats:
                if s.get("kind") == "ai" and s.get("archetype") == "fish":
                    seen += 1
                    assert s["personality_id"] in fish_set
                    assert "ephemeral_personality" not in s
                    assert s["chips"] > 0
        assert seen >= CASINO_FISH_MIN

    def test_fish_get_pool_funded_bankroll(self, db_setup):
        batch = self._spawn(db_setup)
        bankroll = db_setup["bankroll"]
        for spawn in batch.spawns:
            for pid in spawn.fish_seated:
                state = bankroll.load_ai_bankroll(pid, sandbox_id=SBX)
                assert state is not None  # prefunded from the pool
                assert state.chips >= 0  # bankroll = prefund - buy_in

    def test_no_synthetic_tourist_pids(self, db_setup):
        batch = self._spawn(db_setup)
        for spawn in batch.spawns:
            for pid in spawn.fish_seated:
                assert not pid.startswith("tourist-")
                assert not pid.startswith("_tourist_")


class TestZombieSeatReclaim:
    """AI seats whose persona no longer resolves (old-model
    `tourist-<uuid>` seats, or any deleted persona) are self-healed:
    opened, with chips returned to the pool, so a dead seat can never
    permanently block the human or a live-filling grinder from sitting.
    """

    def _spawn_casino(self, db_setup, *, seed=60_000, rng_seed=0):
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=seed)
        resolve_casino_provisioning(
            cash_table_repo=db_setup["tables"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(rng_seed),
            now=ANCHOR,
        )
        return next(
            t
            for t in db_setup["tables"].list_all_tables(sandbox_id=SBX)
            if t.table_type == "casino"
        )

    def test_helper_opens_zombie_returns_chips_keeps_valid(self, db_setup):
        from cash_mode.casino_provisioning import _reclaim_zombie_casino_seats

        tables, ledger = db_setup["tables"], db_setup["ledger"]
        casino = self._spawn_casino(db_setup)
        opens = [i for i, s in enumerate(casino.seats) if s.get("kind") == "open"]
        assert opens, "spawn should leave open seats for grinders/human"
        valid_fish_before = [s["personality_id"] for s in casino.seats if s.get("kind") == "ai"]
        # Plant an old-model zombie tourist seat with residual chips.
        casino.seats[opens[0]] = {
            "kind": "ai",
            "personality_id": "tourist-deadbeef",
            "chips": 150,
        }
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)
        pool_before = compute_bank_pool_reserves(ledger, sandbox_id=SBX)

        reclaimed = _reclaim_zombie_casino_seats(
            tables,
            ledger,
            sandbox_id=SBX,
            valid_pids=db_setup["personality"].list_all_personality_ids(),
            fish_ids={
                f["personality_id"] for f in db_setup["personality"].list_fish_for_cash_mode()
            },
            now=ANCHOR,
        )

        assert reclaimed == 1
        reloaded = next(
            t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_id == casino.table_id
        )
        live = [s.get("personality_id") for s in reloaded.seats if s.get("kind") == "ai"]
        assert "tourist-deadbeef" not in live  # zombie seat opened
        for pid in valid_fish_before:
            assert pid in live  # real fish untouched
        # Chips return to the pool exactly (helper runs without a refill).
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == pool_before + 150

    def test_resolver_clears_planted_zombie(self, db_setup):
        """The full provisioning resolve wires the reclaim in: a planted
        zombie seat is gone after one normal pass."""
        tables, ledger = db_setup["tables"], db_setup["ledger"]
        casino = self._spawn_casino(db_setup)
        opens = [i for i, s in enumerate(casino.seats) if s.get("kind") == "open"]
        if not opens:
            pytest.skip("spawn left no open seat to plant a zombie")
        casino.seats[opens[0]] = {
            "kind": "ai",
            "personality_id": "tourist-deadbeef",
            "chips": 90,
        }
        tables.save_table(casino, sandbox_id=SBX, now=ANCHOR)

        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(3),
            now=ANCHOR,
        )

        reloaded = next(
            t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_id == casino.table_id
        )
        live = [s.get("personality_id") for s in reloaded.seats if s.get("kind") == "ai"]
        assert "tourist-deadbeef" not in live


class TestUnstampedFishSeatHealing:
    """Old-model `<fish>__eph_<hash>` seats hold a fish *persona* but were
    placed via `ai_slot` (no `archetype='fish'` seat stamp). They must not
    count as fish — otherwise provisioning sees the casino as full while the
    player sees none ("no tourists") — and must be reclaimable so refill can
    reseat properly-stamped fish.
    """

    def _make_casino(self, seats, *, table_id="cash-casino-2-001"):
        return CashTableState(
            table_id=table_id,
            stake_label="$2",
            seats=seats,
            created_at=ANCHOR,
            last_activity_at=ANCHOR,
            name="Casino — $2",
            table_type="casino",
        )

    def test_count_seated_fish_uses_seat_stamp_not_persona(self, db_setup):
        """Only the `archetype='fish'` seat stamp counts — a fish persona
        seated without the stamp does not inflate the fish count."""
        from cash_mode.casino_provisioning import _count_seated_fish
        from cash_mode.tables import ai_slot, ai_slot_fish

        table = self._make_casino(
            [
                ai_slot_fish("vacation_greg", 80),  # stamped fish -> counts
                ai_slot("birthday_bobby", 80),  # fish persona, NO stamp -> not counted
                ai_slot("hungry_grinder_0", 80),  # grinder -> not counted
                open_slot(),
                open_slot(),
                open_slot(),
            ]
        )
        assert _count_seated_fish(table) == 1

    def test_reclaim_opens_unstamped_fish_keeps_stamped_and_grinder(self, db_setup):
        from cash_mode.casino_provisioning import _reclaim_zombie_casino_seats
        from cash_mode.tables import ai_slot, ai_slot_fish

        tables, ledger, personality = (
            db_setup["tables"],
            db_setup["ledger"],
            db_setup["personality"],
        )
        table = self._make_casino(
            [
                ai_slot_fish("vacation_greg", 80),  # stamped fish — keep
                ai_slot("birthday_bobby", 120),  # un-stamped fish persona — reclaim
                ai_slot("hungry_grinder_0", 80),  # grinder — keep
                open_slot(),
                open_slot(),
                open_slot(),
            ]
        )
        tables.save_table(table, sandbox_id=SBX, now=ANCHOR)
        pool_before = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        fish_ids = {f["personality_id"] for f in personality.list_fish_for_cash_mode()}

        reclaimed = _reclaim_zombie_casino_seats(
            tables,
            ledger,
            sandbox_id=SBX,
            valid_pids=personality.list_all_personality_ids(),
            fish_ids=fish_ids,
            now=ANCHOR,
        )

        assert reclaimed == 1
        reloaded = next(
            t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_id == table.table_id
        )
        live = [s.get("personality_id") for s in reloaded.seats if s.get("kind") == "ai"]
        assert "birthday_bobby" not in live  # un-stamped fish seat opened
        assert "vacation_greg" in live  # stamped fish untouched
        assert "hungry_grinder_0" in live  # grinder untouched
        # Its residual chips return to the pool exactly (conservation).
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == pool_before + 120

    def test_resolver_unwedges_casino_full_of_unstamped_fish(self, db_setup):
        """The wedge: a casino whose every seat is an un-stamped fish persona
        reads 0 fish (not "full"), so the full resolve reclaims those seats
        and refills with a properly-stamped fish."""
        from cash_mode.casino_provisioning import _count_seated_fish
        from cash_mode.tables import ai_slot

        tables, ledger = db_setup["tables"], db_setup["ledger"]
        seed_bank_pool(ledger, sandbox_id=SBX, amount=10_000)
        # Six un-stamped fish-persona seats, no open seats — the wedge.
        wedged = self._make_casino([ai_slot(pid, 80) for pid in db_setup["fish_pids"][:6]])
        assert _count_seated_fish(wedged) == 0  # none stamped -> reads empty
        tables.save_table(wedged, sandbox_id=SBX, now=ANCHOR)

        resolve_casino_provisioning(
            cash_table_repo=tables,
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=random.Random(1),
            now=ANCHOR,
        )

        reloaded = next(
            t for t in tables.list_all_tables(sandbox_id=SBX) if t.table_id == wedged.table_id
        )
        fish_ids = {f["personality_id"] for f in db_setup["personality"].list_fish_for_cash_mode()}
        # No seat holds an un-stamped fish persona anymore.
        unstamped = [
            s
            for s in reloaded.seats
            if s.get("kind") == "ai"
            and s.get("personality_id") in fish_ids
            and s.get("archetype") != "fish"
        ]
        assert not unstamped
        # And refill seated at least one properly-stamped fish.
        assert _count_seated_fish(reloaded) >= 1


# --- Whale provisioning (the $200+ relief gate) ------------------------------


def _seated_fish_at_lobby(tables, *, stake_label=None):
    """Return (table, seat_idx, pid) for the whale (a fish seat at a lobby
    table), or None. The whale's single source of truth — regular fish are
    casino-only, so a fish stamp at a cardroom table is the whale."""
    for table in tables.list_all_tables(sandbox_id=SBX):
        if table.table_type != "lobby":
            continue
        if stake_label is not None and table.stake_label != stake_label:
            continue
        for idx, slot in enumerate(table.seats):
            if slot.get("kind") == "ai" and slot.get("archetype") == "fish":
                return table, idx, slot.get("personality_id")
    return None


class TestWhaleProvisioning:
    """A whale = a fish persona at a LOBBY table with a deep prefund."""

    def _make_lobby_table(self, db_setup, stake_label="$200", suffix="001", seats=None):
        slug = stake_label[1:]
        table = CashTableState(
            table_id=f"cash-table-{slug}-{suffix}",
            stake_label=stake_label,
            seats=seats if seats is not None else [open_slot() for _ in range(6)],
            created_at=ANCHOR,
            last_activity_at=ANCHOR,
            name=f"Cardroom — {stake_label}",
            table_type="lobby",
        )
        db_setup["tables"].save_table(table, sandbox_id=SBX, now=ANCHOR)
        return table.table_id

    def _resolve_whale(self, db_setup, rng_seed=1):
        return resolve_whale_provisioning(
            cash_table_repo=db_setup["tables"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=random.Random(rng_seed),
            now=ANCHOR,
        )

    def test_spawns_when_pool_clears_threshold_and_seat_open(self, db_setup):
        table_id = self._make_lobby_table(db_setup, "$200")
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)

        batch = self._resolve_whale(db_setup)

        assert batch.spawn is not None
        assert batch.spawn.stake_label == "$200"
        _, _, max_buy_in = table_buy_in_window("$200")
        assert batch.spawn.buy_in == max_buy_in  # deep stack on the felt
        # A fish-stamped seat now sits at the cardroom table.
        seated = _seated_fish_at_lobby(db_setup["tables"], stake_label="$200")
        assert seated is not None
        table, _, pid = seated
        assert table.table_id == table_id
        assert pid == batch.spawn.whale_id
        assert batch.spawn.name  # display name resolved for the ticker

    def test_no_spawn_below_threshold(self, db_setup):
        self._make_lobby_table(db_setup, "$200")
        # 400k < $200 threshold (500k); no $50 cardroom table exists to
        # absorb the lower gate, so nothing spawns.
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=400_000)

        batch = self._resolve_whale(db_setup)

        assert batch.spawn is None
        assert _seated_fish_at_lobby(db_setup["tables"]) is None

    def test_no_spawn_without_open_lobby_seat(self, db_setup):
        # All seats taken by grinders — no open cardroom seat for the whale.
        full = [ai_slot(f"reg_{i}", 20_000) for i in range(6)]
        self._make_lobby_table(db_setup, "$200", seats=full)
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)

        batch = self._resolve_whale(db_setup)

        assert batch.spawn is None

    def test_prefers_higher_stake_when_both_eligible(self, db_setup):
        self._make_lobby_table(db_setup, "$50", suffix="001")
        self._make_lobby_table(db_setup, "$200", suffix="001")
        # Clears both $50 (150k) and $200 (500k) — prefer the bigger release.
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)

        batch = self._resolve_whale(db_setup)

        assert batch.spawn is not None
        assert batch.spawn.stake_label == "$200"

    def test_falls_back_to_lower_stake_when_top_seat_full(self, db_setup):
        # $200 cardroom full, $50 cardroom open. Pool clears $200, but with
        # no open $200 seat the whale takes the open $50 cardroom instead.
        full = [ai_slot(f"reg_{i}", 20_000) for i in range(6)]
        self._make_lobby_table(db_setup, "$200", seats=full)
        self._make_lobby_table(db_setup, "$50", suffix="001")
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)

        batch = self._resolve_whale(db_setup)

        assert batch.spawn is not None
        assert batch.spawn.stake_label == "$50"

    def test_prefund_is_deep(self, db_setup):
        self._make_lobby_table(db_setup, "$200")
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)

        batch = self._resolve_whale(db_setup)

        _, _, max_buy_in = table_buy_in_window("$200")
        drawn = batch.spawn.bank_pool_drawn
        # 10-18x the max buy-in (the dormant whale prefund band).
        assert WHALE_PREFUND_MIN_MULT * max_buy_in <= drawn <= WHALE_PREFUND_MAX_MULT * max_buy_in
        # Buy-in sits on the felt; the rest is rebuy reserve in the bankroll.
        whale_bk = db_setup["bankroll"].load_ai_bankroll(batch.spawn.whale_id, sandbox_id=SBX)
        assert whale_bk is not None
        assert whale_bk.chips == drawn - batch.spawn.buy_in

    def test_one_whale_at_a_time(self, db_setup):
        self._make_lobby_table(db_setup, "$200")
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)

        first = self._resolve_whale(db_setup)
        assert first.spawn is not None

        # Second resolve: whale already live, pool still well above floor →
        # no new whale, no wind-down.
        second = self._resolve_whale(db_setup)
        assert second.spawn is None
        assert second.teardown is None
        # Exactly one fish seat across all cardroom tables.
        n_whale_seats = sum(
            1
            for t in db_setup["tables"].list_all_tables(sandbox_id=SBX)
            if t.table_type == "lobby"
            for s in t.seats
            if s.get("kind") == "ai" and s.get("archetype") == "fish"
        )
        assert n_whale_seats == 1

    def test_spawn_conservation(self, db_setup):
        self._make_lobby_table(db_setup, "$200")
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)
        ledger = db_setup["ledger"]

        before_c = sum(ledger.sum_creations_by_reason(sandbox_id=SBX).values())
        before_d = sum(ledger.sum_destructions_by_reason(sandbox_id=SBX).values())
        before_outstanding = before_c - before_d

        batch = self._resolve_whale(db_setup)
        assert batch.spawn is not None
        drawn = batch.spawn.bank_pool_drawn

        after_c = sum(ledger.sum_creations_by_reason(sandbox_id=SBX).values())
        after_d = sum(ledger.sum_destructions_by_reason(sandbox_id=SBX).values())
        # Outstanding chips grew by exactly the pool draw.
        assert (after_c - after_d) - before_outstanding == drawn
        # And those chips are split between the whale's felt seat and its
        # (deep) bankroll reserve — nothing minted or lost.
        seated = _seated_fish_at_lobby(db_setup["tables"], stake_label="$200")
        _, seat_idx, pid = seated
        seat_chips = int(seated[0].seats[seat_idx].get("chips", 0))
        whale_bk = db_setup["bankroll"].load_ai_bankroll(pid, sandbox_id=SBX)
        assert seat_chips + int(whale_bk.chips) == drawn

    def test_wind_down_below_floor_returns_chips(self, db_setup, monkeypatch):
        self._make_lobby_table(db_setup, "$200")
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=600_000)
        ledger = db_setup["ledger"]

        spawn = self._resolve_whale(db_setup).spawn
        assert spawn is not None
        pid = spawn.whale_id
        drawn = spawn.bank_pool_drawn

        outstanding_after_spawn = sum(
            ledger.sum_creations_by_reason(sandbox_id=SBX).values()
        ) - sum(ledger.sum_destructions_by_reason(sandbox_id=SBX).values())
        pool_after_spawn = compute_bank_pool_reserves(ledger, sandbox_id=SBX)

        # Force the dam below the floor: raise the floor above the current
        # pool so the live whale is recalled on the next resolve.
        monkeypatch.setitem(WHALE_POOL_FLOORS, "$200", pool_after_spawn + 1)

        batch = self._resolve_whale(db_setup)

        assert batch.teardown is not None
        assert batch.teardown.whale_id == pid
        # Seat vacated; whale bankroll emptied.
        assert _seated_fish_at_lobby(db_setup["tables"], stake_label="$200") is None
        whale_bk = db_setup["bankroll"].load_ai_bankroll(pid, sandbox_id=SBX)
        assert whale_bk is None or int(whale_bk.chips) == 0
        # The whale never played, so its full draw returns to the pool:
        # outstanding falls back by exactly `drawn`, pool recovers it.
        outstanding_after_windown = sum(
            ledger.sum_creations_by_reason(sandbox_id=SBX).values()
        ) - sum(ledger.sum_destructions_by_reason(sandbox_id=SBX).values())
        assert outstanding_after_spawn - outstanding_after_windown == drawn
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) - pool_after_spawn == drawn


class TestAffordablePredators:
    """`list_affordable_predators` — the whale's predator pool (by wealth)."""

    def test_orders_affordable_non_fish_richest_first(self, db_setup):
        bankroll = db_setup["bankroll"]
        personality = db_setup["personality"]
        # Three non-fish AIs of varying wealth + one too-poor to afford $200.
        wealth = {"rich_rita": 300_000, "mid_max": 40_000, "broke_bob": 1_000}
        for pid, chips in wealth.items():
            personality.save_personality(pid.title(), _grinder_config("$200"), personality_id=pid)
            bankroll.save_ai_bankroll(
                AIBankrollState(personality_id=pid, chips=chips, last_regen_tick=ANCHOR),
                sandbox_id=SBX,
            )

        _, min_buy_in, _ = table_buy_in_window("$200")  # 8_000
        predators = list_affordable_predators(
            bankroll,
            sandbox_id=SBX,
            min_buy_in=min_buy_in,
            now=ANCHOR,
        )

        # Richest affordable first; the broke AI and all fish excluded.
        assert predators[0] == "rich_rita"
        assert "mid_max" in predators
        assert "broke_bob" not in predators
        for fish_pid in db_setup["fish_pids"]:
            assert fish_pid not in predators

    def test_excludes_set(self, db_setup):
        bankroll = db_setup["bankroll"]
        personality = db_setup["personality"]
        personality.save_personality(
            "Rich Rita", _grinder_config("$200"), personality_id="rich_rita"
        )
        bankroll.save_ai_bankroll(
            AIBankrollState(personality_id="rich_rita", chips=300_000, last_regen_tick=ANCHOR),
            sandbox_id=SBX,
        )
        _, min_buy_in, _ = table_buy_in_window("$200")
        predators = list_affordable_predators(
            bankroll,
            sandbox_id=SBX,
            min_buy_in=min_buy_in,
            now=ANCHOR,
            exclude={"rich_rita"},
        )
        assert "rich_rita" not in predators
