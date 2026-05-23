"""Closed-economy testbed — fake-vice deposits + tourist injection.

Spec: `docs/plans/CASH_MODE_CLOSED_ECONOMY.md`. This module asserts
two things together: (a) the pure formulas behave per the spec, and
(b) wired against tempdb-backed repos, the resolution functions
correctly write paired ledger + bankroll updates so the conservation
invariant (`drift == 0`) holds.

Wider integration with the lobby refresh is covered by the existing
chip-ledger conservation tests — if these resolvers introduce
unbalanced flows, the drift assertion would surface it.
"""

from __future__ import annotations

import json
import os
import random
import sys
import unittest
from datetime import datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.bankroll import AIBankrollState
from cash_mode.closed_economy import (
    CASINO_TIER_STAKE_LABELS,
    FAKE_VICE_COMFORT_FLOOR,
    FAKE_VICE_DEPOSITS_PER_REFRESH,
    FAKE_VICE_MAX_PROB,
    MIN_VICE_AMOUNT,
    compute_bank_pool_reserves,
    compute_excess_ratio,
    compute_vice_amount,
    compute_vice_probability,
    load_fish_ids,
    resolve_closed_economy,
    resolve_fake_vice_deposits,
)
from core.economy.ledger import (
    BANK_POOL_DEPOSIT_REASONS,
    LEDGER_REASONS,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


ANCHOR = datetime(2026, 5, 23, 12, 0, 0)
SBX = "test-closed-economy"


# --- Pure-formula tests ------------------------------------------------------


class TestExcessRatio(unittest.TestCase):
    def test_broke_returns_zero(self):
        self.assertEqual(compute_excess_ratio(5000, 10000), 0.0)

    def test_at_floor_returns_zero(self):
        # bankroll == 1.2 × starting → on the floor exactly
        self.assertEqual(compute_excess_ratio(12000, 10000), 0.0)

    def test_above_floor_returns_excess(self):
        # 20000 chips vs 10000 starting (floor=12000) → 0.8
        self.assertAlmostEqual(compute_excess_ratio(20000, 10000), 0.8, places=4)

    def test_unbounded_above(self):
        # 100000 chips vs 10000 starting (floor=12000) → 8.8
        self.assertAlmostEqual(compute_excess_ratio(100_000, 10000), 8.8, places=4)

    def test_zero_starting_bankroll(self):
        self.assertEqual(compute_excess_ratio(50000, 0), 0.0)


class TestViceProbability(unittest.TestCase):
    def test_zero_excess_returns_zero(self):
        self.assertEqual(compute_vice_probability(0.0), 0.0)

    def test_negative_excess_returns_zero(self):
        self.assertEqual(compute_vice_probability(-0.5), 0.0)

    def test_capped_at_max_prob(self):
        # Huge excess hits the cap
        self.assertEqual(compute_vice_probability(100.0), FAKE_VICE_MAX_PROB)

    def test_monotonic(self):
        # Probability strictly increases with excess up to the cap.
        probs = [compute_vice_probability(x) for x in (0.5, 1.0, 2.0, 4.0)]
        for a, b in zip(probs, probs[1:]):
            self.assertLessEqual(a, b)


class TestViceAmount(unittest.TestCase):
    def test_zero_bankroll_returns_zero(self):
        self.assertEqual(compute_vice_amount(0, 1.0, random.Random(0)), 0)

    def test_zero_excess_returns_zero(self):
        self.assertEqual(compute_vice_amount(50000, 0.0, random.Random(0)), 0)

    def test_capped_at_max_fraction(self):
        # With huge excess + max rng multiplier, amount caps at 15% of bankroll.
        class _MaxRng:
            def uniform(self, lo, hi):
                return hi

            def random(self):
                return 0.0

        amount = compute_vice_amount(100_000, 50.0, _MaxRng())
        self.assertLessEqual(amount, 15_000)  # 15% of 100k


# --- Bank-pool / fish discovery ---------------------------------------------


@pytest.fixture
def db_setup(tmp_path):
    """Fresh tempdb with one rich AI + one fish + a no-archetype baseline."""
    db = str(tmp_path / "closed_economy.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    ledger = ChipLedgerRepository(db)
    personality = PersonalityRepository(db)

    # Rich non-fish personality — sits well above the comfort floor.
    personality.save_personality(
        "RichGrinder",
        {
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
                "stake_comfort_zone": "$10",
            },
        },
        personality_id="rich_grinder",
    )
    # Fish personality — archetype=fish, small starting bankroll.
    personality.save_personality(
        "TestFish",
        {
            "archetype": "fish",
            "rule_strategy": "fish",
            "play_style": "calling station tourist",
            "anchors": {
                "baseline_aggression": 0.15,
                "baseline_looseness": 0.85,
                "ego": 0.2,
                "poise": 0.15,
                "expressiveness": 0.8,
                "risk_identity": 0.6,
                "adaptation_bias": 0.0,
                "baseline_energy": 0.7,
                "recovery_rate": 0.0,
            },
            "bankroll_knobs": {
                "starting_bankroll": 2_500,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$2",
            },
        },
        personality_id="test_fish",
    )

    # Rich AI starts at 5× starting → excess ratio ~3.8, well above floor.
    bankroll.save_ai_bankroll(
        AIBankrollState(
            personality_id="rich_grinder",
            chips=50_000,
            last_regen_tick=ANCHOR,
        ),
        sandbox_id=SBX,
    )
    # Fish AI starts at 200 chips (8% of starting) — well below the
    # tourist injection threshold (40%).
    bankroll.save_ai_bankroll(
        AIBankrollState(
            personality_id="test_fish",
            chips=200,
            last_regen_tick=ANCHOR,
        ),
        sandbox_id=SBX,
    )

    return {"bankroll": bankroll, "ledger": ledger, "personality": personality}


class _AlwaysFiresRng:
    """rng that always returns 0.0 → vice_prob.random() < prob always true.

    Also makes `uniform` return the midpoint so amount calculations
    stay deterministic.
    """

    def random(self):
        return 0.0

    def uniform(self, lo, hi):
        return (lo + hi) / 2.0


class _NeverFiresRng:
    def random(self):
        return 1.0

    def uniform(self, lo, hi):
        return (lo + hi) / 2.0


class TestFishDiscovery:
    def test_load_fish_ids_finds_archetype_fish(self, db_setup):
        bankroll = db_setup["bankroll"]
        assert load_fish_ids(bankroll, sandbox_id=SBX) == {"test_fish"}

    def test_load_fish_ids_excludes_non_fish(self, db_setup):
        bankroll = db_setup["bankroll"]
        fish = load_fish_ids(bankroll, sandbox_id=SBX)
        assert "rich_grinder" not in fish


class TestBankPoolReserves:
    def test_empty_pool_returns_zero(self, db_setup):
        ledger = db_setup["ledger"]
        assert compute_bank_pool_reserves(ledger, sandbox_id=SBX) == 0


# --- Resolver integration ----------------------------------------------------


class TestFakeViceDeposit:
    def test_rich_ai_deposits_to_pool(self, db_setup):
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        fish_ids = load_fish_ids(bankroll, sandbox_id=SBX)

        deposits = resolve_fake_vice_deposits(
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_AlwaysFiresRng(),
            now=ANCHOR,
            fish_ids=fish_ids,
        )
        assert len(deposits) == 1
        d = deposits[0]
        assert d.personality_id == "rich_grinder"
        assert d.amount >= MIN_VICE_AMOUNT
        assert d.excess_ratio > 0

        # Bankroll decreased by amount; ledger has a `bank_pool_deposit` row.
        state = bankroll.load_ai_bankroll("rich_grinder", sandbox_id=SBX)
        assert state.chips == 50_000 - d.amount

        # Pool depth equals the deposit amount.
        pool = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
        assert pool == d.amount

    def test_fish_excluded_from_vice(self, db_setup):
        """Fish never deposit — they're recipients, not contributors."""
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        # Seed the fish artificially flush so they'd otherwise vice.
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="test_fish",
                chips=20_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        deposits = resolve_fake_vice_deposits(
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_AlwaysFiresRng(),
            now=ANCHOR,
            fish_ids={"test_fish"},
        )
        pids = {d.personality_id for d in deposits}
        assert "test_fish" not in pids

    def test_broke_ai_skipped(self, db_setup):
        """No vice when bankroll is below the comfort floor."""
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id="rich_grinder",
                chips=8_000,  # below 1.2× starting = 12000
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
        )
        deposits = resolve_fake_vice_deposits(
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_AlwaysFiresRng(),
            now=ANCHOR,
            fish_ids=set(),
        )
        assert len(deposits) == 0

    def test_rng_never_fires(self, db_setup):
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        deposits = resolve_fake_vice_deposits(
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_NeverFiresRng(),
            now=ANCHOR,
            fish_ids=set(),
        )
        assert deposits == []


class TestConservationInvariant:
    """The whole closed-economy loop must preserve the conservation invariant.

    Per `CASH_MODE_ECONOMY.md`, every chip movement either (a) goes through
    central_bank (gets a ledger row) or (b) is a pure transfer between
    non-bank entities (no ledger row). The audit endpoint computes:

        drift = ledger.creations − ledger.destructions − actual_non_bank_chips

    For the closed-economy flow:
      - fake-vice deposit destroys chips from an AI → central_bank
      - tourist injection creates chips from central_bank → fish AI

    Both go through the bank, both write ledger rows. So the closed-economy
    flow preserves drift.
    """

    def test_drift_zero_after_full_cycle(self, db_setup):
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]

        # Capture initial total (all AIs)
        def total_ai_chips():
            return sum(
                bankroll.load_ai_bankroll(pid, sandbox_id=SBX).chips
                for pid in bankroll.iter_personality_ids_with_bankrolls(sandbox_id=SBX)
            )

        initial = total_ai_chips()

        # Run a full cycle: vice deposits (post-EPHEMERAL_TOURISTS,
        # tourist injection no longer fires).
        batch = resolve_closed_economy(
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_AlwaysFiresRng(),
            now=ANCHOR,
        )

        final = total_ai_chips()

        # Net chip movement: only vice destructions matter now.
        # initial - vice = final
        total_vice = sum(d.amount for d in batch.deposits)
        assert final == initial - total_vice

        # Bank pool delta: deposits ONLY add (no withdrawals to fish).
        pool_delta = batch.bank_pool_after - batch.bank_pool_before
        assert pool_delta == total_vice


class TestPerRefreshCaps:
    def test_deposit_cap_respected(self, db_setup):
        """No more than FAKE_VICE_DEPOSITS_PER_REFRESH fire per call."""
        bankroll = db_setup["bankroll"]
        ledger = db_setup["ledger"]
        personality = db_setup["personality"]
        # Seed more rich AIs than the cap so we'd exceed it without the limit.
        for i in range(FAKE_VICE_DEPOSITS_PER_REFRESH + 3):
            pid = f"rich_extra_{i}"
            personality.save_personality(
                f"RichExtra{i}",
                {
                    "play_style": "rich extra",
                    "anchors": {
                        "baseline_aggression": 0.5, "baseline_looseness": 0.5,
                        "ego": 0.5, "poise": 0.5, "expressiveness": 0.5,
                        "risk_identity": 0.5, "adaptation_bias": 0.5,
                        "baseline_energy": 0.5, "recovery_rate": 0.5,
                    },
                    "bankroll_knobs": {
                        "starting_bankroll": 10_000,
                        "bankroll_rate": 0,
                        "buy_in_multiplier": 1.0,
                        "stake_comfort_zone": "$10",
                    },
                },
                personality_id=pid,
            )
            bankroll.save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid, chips=50_000, last_regen_tick=ANCHOR,
                ),
                sandbox_id=SBX,
            )

        deposits = resolve_fake_vice_deposits(
            bankroll_repo=bankroll,
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_AlwaysFiresRng(),
            now=ANCHOR,
            fish_ids={"test_fish"},
        )
        assert len(deposits) <= FAKE_VICE_DEPOSITS_PER_REFRESH


class TestConstants:
    """Sanity-check constants are wired correctly."""

    def test_casino_tier_includes_smallest_stake(self):
        assert "$2" in CASINO_TIER_STAKE_LABELS

    def test_ledger_reasons_registered(self):
        assert "bank_pool_deposit" in LEDGER_REASONS
        assert "tourist_injection" in LEDGER_REASONS
        assert "bank_pool_deposit" in BANK_POOL_DEPOSIT_REASONS


if __name__ == "__main__":
    unittest.main()
