"""Tests for the AI vice spending mechanic.

Pure-math tests for the trigger / amount / recovery formulas, plus
end-to-end tests that fire `resolve_ai_vice_spending` and
`tick_vice_expirations` against tempdb-backed repos.

See `docs/plans/CASH_MODE_AI_VICE_SPENDING.md` for the design.
"""

from __future__ import annotations

import json
import os
import random
import sys
import unittest
from datetime import datetime, timedelta

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.ai_vice_spending import (
    BASE_FRACTION,
    BASE_RECOVERY,
    CONCENTRATION_FLOOR,
    DURATION_RANGES,
    EXCESS_WEIGHT,
    FLOOR_PROTECTION_FRACTION,
    MAX_PROB,
    MAX_RECOVERY,
    MAX_VICE_FRACTION,
    MIN_CAST_MEDIAN_FOR_VICE,
    MIN_VICE_AMOUNT,
    PRESSURE_BOOST,
    VICE_BUCKET_WEIGHTS,
    VICE_STARTS_PER_REFRESH,
    ViceEndResult,
    ViceStartResult,
    compute_cast_median,
    compute_excess_ratio,
    compute_pressure,
    compute_recovered_axes,
    compute_recovery_factor,
    compute_vice_amount,
    compute_vice_probability,
    duration_for_bucket,
    pick_duration_bucket,
    reserve_vice_multiplier,
    resolve_ai_vice_spending,
    tick_vice_expirations,
)
from cash_mode.bankroll import AIBankrollState
from cash_mode.closed_economy import seed_bank_pool
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.vice_state_repository import ViceState, ViceStateRepository

ANCHOR = datetime(2026, 5, 23, 12, 0, 0)
SBX = "test-sandbox-vice"


# --- Pure-math tests --------------------------------------------------------


class TestCastMedian(unittest.TestCase):
    def test_empty_returns_zero(self):
        self.assertEqual(compute_cast_median([]), 0)

    def test_odd_count(self):
        self.assertEqual(compute_cast_median([1, 5, 9]), 5)

    def test_even_count_averages_middle_two(self):
        self.assertEqual(compute_cast_median([1, 5, 9, 13]), 7)

    def test_robust_to_outlier(self):
        # One $2M outlier in an otherwise-modest cast — median ignores it
        chips = [14_000] * 79 + [2_170_000]
        self.assertEqual(compute_cast_median(chips), 14_000)


class TestExcessRatio(unittest.TestCase):
    def test_at_threshold_returns_zero(self):
        # Concentration = exactly CONCENTRATION_FLOOR → zero (just at)
        bankroll = int(14_000 * CONCENTRATION_FLOOR)
        self.assertEqual(compute_excess_ratio(bankroll, 14_000), 0.0)

    def test_below_threshold_returns_zero(self):
        # Concentration < CONCENTRATION_FLOOR → no excess
        self.assertEqual(compute_excess_ratio(20_000, 14_000), 0.0)

    def test_above_threshold_linear(self):
        # bankroll = 5x median; concentration = 5; excess = 5 - 2.5 = 2.5
        self.assertAlmostEqual(
            compute_excess_ratio(70_000, 14_000),
            5.0 - CONCENTRATION_FLOOR,
            places=3,
        )

    def test_extreme_outlier_unbounded(self):
        # $2M against a $14K median produces a very large excess —
        # caller's job to cap via MAX_PROB.
        excess = compute_excess_ratio(2_170_000, 14_000)
        self.assertGreater(excess, 100)

    def test_zero_median_returns_zero(self):
        # Defensive: never divide by zero
        self.assertEqual(compute_excess_ratio(100_000, 0), 0.0)


class TestPressure(unittest.TestCase):
    def test_min_axis_drives_pressure(self):
        # composure is the worst → pressure = 1 - composure
        self.assertAlmostEqual(
            compute_pressure(0.8, 0.3, 0.5),
            1.0 - 0.3,
        )

    def test_calm_ai_has_floor_pressure(self):
        # All axes near baseline (0.7) → pressure ~ 0.3
        self.assertAlmostEqual(
            compute_pressure(0.7, 0.7, 0.7),
            0.3,
        )

    def test_perfectly_calm_axes_zero_pressure(self):
        # All axes at max → pressure = 0
        self.assertEqual(compute_pressure(1.0, 1.0, 1.0), 0.0)

    def test_pressure_never_negative(self):
        # Defensive: even invalid inputs above 1 don't produce negative
        self.assertGreaterEqual(compute_pressure(1.5, 1.0, 1.0), 0.0)


class TestViceProbability(unittest.TestCase):
    def test_zero_excess_means_zero_prob(self):
        # Broke AIs never vice regardless of pressure
        self.assertEqual(compute_vice_probability(0.0, 1.0), 0.0)

    def test_capped_at_max_prob(self):
        # Extremely flush + maxed pressure → MAX_PROB
        self.assertAlmostEqual(
            compute_vice_probability(50.0, 1.0),
            MAX_PROB,
        )

    def test_doc_bezos_calm(self):
        # Worked example: Bezos calm with excess 3.8 and pressure 0.4
        # → 0.152 * 1.24 ≈ 0.19
        p = compute_vice_probability(3.8, 0.4)
        self.assertAlmostEqual(p, 0.188, places=2)

    def test_pressure_amplifies_wealth(self):
        # Same wealth, more pressure → strictly higher probability
        low = compute_vice_probability(3.0, 0.0)
        high = compute_vice_probability(3.0, 0.8)
        self.assertGreater(high, low)


class TestViceAmount(unittest.TestCase):
    def _rng(self):
        # Random.Random(0) gives a deterministic sequence; uniform(0.5, 1.5)
        # returns a value in that range.
        return random.Random(0)

    def test_zero_excess_skips_event(self):
        amt = compute_vice_amount(8_000, 10_000, 0.0, self._rng())
        self.assertEqual(amt, 0)

    def test_normal_fire_produces_positive_amount(self):
        amt = compute_vice_amount(50_000, 10_000, 3.8, self._rng())
        self.assertGreater(amt, MIN_VICE_AMOUNT)

    def test_max_fraction_cap(self):
        # Force jitter to max → ensure the cap kicks in
        rng = random.Random()
        # Stub uniform to always return AMOUNT_JITTER_HIGH
        rng.uniform = lambda a, b: b  # type: ignore
        amt = compute_vice_amount(50_000, 10_000, 3.8, rng)
        cap = int(50_000 * MAX_VICE_FRACTION)
        self.assertLessEqual(amt, cap)

    def test_floor_protection_skips_event(self):
        # Bankroll just above the 50% floor — any meaningful spend
        # would breach it.
        floor = int(10_000 * FLOOR_PROTECTION_FRACTION)
        bankroll = floor + 100  # 5_100 — barely above floor
        # excess_ratio big enough to fire, but the floor guard catches it
        amt = compute_vice_amount(bankroll, 10_000, 0.5, self._rng())
        self.assertEqual(amt, 0)


class TestRecoveryFactor(unittest.TestCase):
    def test_min_amount_yields_base(self):
        self.assertAlmostEqual(
            compute_recovery_factor(MIN_VICE_AMOUNT),
            BASE_RECOVERY,
        )

    def test_logarithmic_scaling(self):
        # 10x amount adds 0.05 (AMOUNT_BONUS), so 500 → 0.30
        self.assertAlmostEqual(compute_recovery_factor(500), 0.30, places=3)

    def test_capped_at_max(self):
        # Even huge amounts cap at MAX_RECOVERY
        self.assertEqual(compute_recovery_factor(10**9), MAX_RECOVERY)

    def test_zero_amount_is_zero(self):
        self.assertEqual(compute_recovery_factor(0), 0.0)


class TestRecoveredAxes(unittest.TestCase):
    def test_pulls_toward_baseline(self):
        # current below baseline → moves up by factor of the gap
        new_conf, _, _ = compute_recovered_axes(
            confidence=0.2,
            composure=0.7,
            energy=0.5,
            baseline_confidence=0.6,
            baseline_composure=0.7,
            baseline_energy=0.5,
            recovery_factor=0.5,
        )
        # 0.2 + (0.6 - 0.2) * 0.5 = 0.4
        self.assertAlmostEqual(new_conf, 0.4, places=4)

    def test_no_pull_when_factor_zero(self):
        out = compute_recovered_axes(
            confidence=0.2,
            composure=0.3,
            energy=0.4,
            baseline_confidence=0.7,
            baseline_composure=0.7,
            baseline_energy=0.5,
            recovery_factor=0.0,
        )
        self.assertEqual(out, (0.2, 0.3, 0.4))

    def test_clamps_to_unit_range(self):
        out = compute_recovered_axes(
            confidence=0.95,
            composure=0.5,
            energy=0.5,
            baseline_confidence=1.5,  # bad baseline > 1
            baseline_composure=0.5,
            baseline_energy=0.5,
            recovery_factor=1.0,
        )
        # Output should still be clamped to [0, 1]
        for v in out:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)


class TestDurationForBucket(unittest.TestCase):
    def test_short_in_range(self):
        rng = random.Random(0)
        d = duration_for_bucket('short', rng)
        low, high = DURATION_RANGES['short']
        self.assertGreaterEqual(d, low)
        self.assertLessEqual(d, high)

    def test_unknown_bucket_falls_back_medium(self):
        rng = random.Random(0)
        d = duration_for_bucket('xxx', rng)
        low, high = DURATION_RANGES['medium']
        self.assertGreaterEqual(d, low)
        self.assertLessEqual(d, high)


class TestPickDurationBucket(unittest.TestCase):
    """The system-side duration picker — duration is no longer the LLM's call.

    It must be deterministic (seeded rng → reproducible), only ever return a
    valid bucket, follow the base weight distribution at zero pressure, and
    skew toward `long` as pressure rises.
    """

    def test_only_valid_buckets(self):
        rng = random.Random(123)
        for _ in range(500):
            self.assertIn(pick_duration_bucket(0.5, rng), DURATION_RANGES)

    def test_deterministic_for_seed(self):
        seq_a = [pick_duration_bucket(0.5, random.Random(7)) for _ in range(1)]
        seq_b = [pick_duration_bucket(0.5, random.Random(7)) for _ in range(1)]
        self.assertEqual(seq_a, seq_b)
        # A shared rng instance produces a reproducible stream too.
        rng1, rng2 = random.Random(99), random.Random(99)
        stream_a = [pick_duration_bucket(0.3, rng1) for _ in range(50)]
        stream_b = [pick_duration_bucket(0.3, rng2) for _ in range(50)]
        self.assertEqual(stream_a, stream_b)

    def test_zero_pressure_follows_base_weights(self):
        rng = random.Random(2026)
        n = 20_000
        counts = {'short': 0, 'medium': 0, 'long': 0}
        for _ in range(n):
            counts[pick_duration_bucket(0.0, rng)] += 1
        for bucket, weight in VICE_BUCKET_WEIGHTS.items():
            self.assertAlmostEqual(counts[bucket] / n, weight, delta=0.03)

    def test_pressure_skews_toward_long(self):
        n = 20_000
        rng_low = random.Random(2026)
        rng_high = random.Random(2026)
        long_at_zero = sum(pick_duration_bucket(0.0, rng_low) == 'long' for _ in range(n))
        long_at_full = sum(pick_duration_bucket(1.0, rng_high) == 'long' for _ in range(n))
        # Full pressure moves VICE_PRESSURE_SKEW of short's mass onto long.
        self.assertGreater(long_at_full, long_at_zero)

    def test_pressure_clamped(self):
        # Out-of-range pressure must not blow up the distribution.
        rng = random.Random(1)
        for _ in range(200):
            self.assertIn(pick_duration_bucket(5.0, rng), DURATION_RANGES)
            self.assertIn(pick_duration_bucket(-3.0, rng), DURATION_RANGES)

    def test_degenerate_weights_fall_back_to_default(self):
        rng = random.Random(1)
        out = pick_duration_bucket(0.5, rng, weights={'short': 0.0, 'medium': 0.0, 'long': 0.0})
        self.assertIn(out, DURATION_RANGES)


# --- Integration tests against tempdb ---------------------------------------


@pytest.fixture
def db_setup(tmp_path):
    """Schema + repos + cast distribution that produces a usable median.

    Seeds:
      - `flush_ai` at $50K — should vice (concentration >> 2.5)
      - `broke_ai` at $5K — never vices
      - 7 "background" AIs at $14K each so the cast median lands at
        $14K. flush_ai concentration = 3.57 → above the 2.5 floor.
    """
    db = str(tmp_path / "vice.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    ledger = ChipLedgerRepository(db)
    vice_repo = ViceStateRepository(db)
    personality = PersonalityRepository(db)

    bankroll.save_ai_bankroll(
        AIBankrollState(
            personality_id="flush_ai",
            chips=50_000,
            last_regen_tick=ANCHOR,
        ),
        sandbox_id=SBX,
        chip_ledger_repo=ledger,
    )
    bankroll.save_ai_bankroll(
        AIBankrollState(
            personality_id="broke_ai",
            chips=5_000,
            last_regen_tick=ANCHOR,
        ),
        sandbox_id=SBX,
        chip_ledger_repo=ledger,
    )
    # 7 background AIs at $14K each — anchors the cast median there.
    for i in range(7):
        bankroll.save_ai_bankroll(
            AIBankrollState(
                personality_id=f"bg_ai_{i}",
                chips=14_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
            chip_ledger_repo=ledger,
        )

    # Seed an emotional state for the flush AI — pressure 0.3-ish.
    flush_psych = {
        'axes': {'confidence': 0.7, 'composure': 0.7, 'energy': 0.6},
        'anchors': {'baseline_aggression': 0.5},
    }
    bankroll.save_emotional_state_json(
        "flush_ai",
        json.dumps(flush_psych),
        sandbox_id=SBX,
    )

    return {
        "db": db,
        "bankroll": bankroll,
        "ledger": ledger,
        "vice": vice_repo,
        "personality": personality,
    }


class _AlwaysLowRng:
    """rng.random() always returns 0 → probability check always passes."""

    def random(self):
        return 0.0

    def uniform(self, a, b):
        return (a + b) / 2.0


class _AlwaysHighRng:
    """rng.random() always returns 0.99 → probability check fails."""

    def random(self):
        return 0.99

    def uniform(self, a, b):
        return (a + b) / 2.0


class TestViceStateRepository:
    """CRUD round-trips against the vice_state table."""

    def test_insert_and_load(self, db_setup):
        repo = db_setup["vice"]
        state = ViceState(
            personality_id="napoleon",
            sandbox_id=SBX,
            started_at=ANCHOR,
            ends_at=ANCHOR + timedelta(hours=1),
            amount=1500,
            duration_bucket='medium',
            narration='Napoleon commissioned a thing',
        )
        repo.insert_vice_state(state)
        loaded = repo.load("napoleon", sandbox_id=SBX)
        assert loaded is not None
        assert loaded.amount == 1500
        assert loaded.duration_bucket == 'medium'

    def test_list_active_excludes_expired(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(
            ViceState(
                personality_id="active",
                sandbox_id=SBX,
                started_at=ANCHOR,
                ends_at=ANCHOR + timedelta(hours=2),
                amount=500,
                duration_bucket='medium',
                narration='x',
            )
        )
        repo.insert_vice_state(
            ViceState(
                personality_id="expired",
                sandbox_id=SBX,
                started_at=ANCHOR - timedelta(hours=2),
                ends_at=ANCHOR - timedelta(hours=1),
                amount=500,
                duration_bucket='medium',
                narration='y',
            )
        )
        active = repo.list_active(sandbox_id=SBX, now=ANCHOR)
        assert {v.personality_id for v in active} == {"active"}
        expired = repo.list_expired(sandbox_id=SBX, now=ANCHOR)
        assert {v.personality_id for v in expired} == {"expired"}

    def test_is_on_vice(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(
            ViceState(
                personality_id="on",
                sandbox_id=SBX,
                started_at=ANCHOR,
                ends_at=ANCHOR + timedelta(hours=1),
                amount=100,
                duration_bucket='short',
                narration='x',
            )
        )
        assert repo.is_on_vice("on", sandbox_id=SBX, now=ANCHOR) is True
        assert repo.is_on_vice("off", sandbox_id=SBX, now=ANCHOR) is False
        # After expiry passes, no longer on vice
        assert (
            repo.is_on_vice(
                "on",
                sandbox_id=SBX,
                now=ANCHOR + timedelta(hours=2),
            )
            is False
        )

    def test_delete(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(
            ViceState(
                personality_id="x",
                sandbox_id=SBX,
                started_at=ANCHOR,
                ends_at=ANCHOR + timedelta(hours=1),
                amount=100,
                duration_bucket='short',
                narration='x',
            )
        )
        assert repo.delete("x", sandbox_id=SBX) is True
        assert repo.load("x", sandbox_id=SBX) is None
        # Second delete returns False
        assert repo.delete("x", sandbox_id=SBX) is False

    def test_sandbox_isolation(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(
            ViceState(
                personality_id="shared",
                sandbox_id="sbx-A",
                started_at=ANCHOR,
                ends_at=ANCHOR + timedelta(hours=1),
                amount=100,
                duration_bucket='short',
                narration='x',
            )
        )
        # Same personality_id in a different sandbox is invisible
        assert repo.load("shared", sandbox_id="sbx-B") is None
        assert repo.is_on_vice("shared", sandbox_id="sbx-B", now=ANCHOR) is False


class TestReserveViceMultiplier(unittest.TestCase):
    """The reserve scaler: vice refills full at the floor, tapering to off at the
    CEILING above the trigger — so it's still ~half-on AT the trigger (crosses it)
    and brakes above it.
    """

    def test_still_on_at_the_trigger(self):
        # The key fix: at RESERVE_TRIGGER (0.12), vice is ~0.5 (default ceiling
        # 0.18), NOT 0 — it pushes reserves ACROSS the trigger.
        self.assertAlmostEqual(reserve_vice_multiplier(0.12), 0.5)

    def test_off_at_ceiling_braked(self):
        # at/above RESERVE_VICE_CEILING (0.18) → vice off (hot bank, braked)
        self.assertEqual(reserve_vice_multiplier(0.18), 0.0)
        self.assertEqual(reserve_vice_multiplier(0.30), 0.0)

    def test_below_healthy_full(self):
        # at/below RESERVE_HEALTHY (0.06) → vice full refill (incl. critical)
        self.assertEqual(reserve_vice_multiplier(0.06), 1.0)
        self.assertEqual(reserve_vice_multiplier(0.03), 1.0)
        self.assertEqual(reserve_vice_multiplier(0.0), 1.0)

    def test_tapers_above_trigger_as_brake(self):
        # past the trigger vice keeps easing toward off (the brake): 0.15 < 0.12.
        assert reserve_vice_multiplier(0.15) < reserve_vice_multiplier(0.12)
        assert reserve_vice_multiplier(0.15) > 0.0

    def test_monotonic_decreasing(self):
        assert reserve_vice_multiplier(0.07) > reserve_vice_multiplier(0.13)


class TestViceReserveGate:
    """Integration: VICE_RESERVE_GATED scales the whole pass by pool depth."""

    def test_flush_reserves_suppress_vice_when_gated(self, db_setup, monkeypatch):
        # Flag ON + a flush bank pool → reserves/holdings well above the
        # healthy floor → the whole vice pass is suppressed.
        monkeypatch.setattr("cash_mode.economy_flags.VICE_RESERVE_GATED", True)
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=80_000)
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result == []
        # Bankroll untouched — no chips drained.
        assert db_setup["bankroll"].load_ai_bankroll("flush_ai", sandbox_id=SBX).chips == 50_000

    def test_low_reserves_allow_vice_when_gated(self, db_setup, monkeypatch):
        # Flag ON but the pool is empty (reserves ~0, below critical) → vice
        # fires at full intensity, same as ungated.
        monkeypatch.setattr("cash_mode.economy_flags.VICE_RESERVE_GATED", True)
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert len(result) == 1
        assert result[0].personality_id == "flush_ai"

    def test_flag_off_ignores_reserves(self, db_setup, monkeypatch):
        # Flag OFF (default) → a flush pool does NOT suppress vice; the gate
        # is inert until explicitly flipped on.
        monkeypatch.setattr("cash_mode.economy_flags.VICE_RESERVE_GATED", False)
        seed_bank_pool(db_setup["ledger"], sandbox_id=SBX, amount=80_000)
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert len(result) == 1


class TestResolveAiViceSpending:
    """End-to-end: fire vice for a flush AI and check chip / state effects."""

    def test_flush_ai_vices_on_low_roll(self, db_setup):
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert len(result) == 1
        r = result[0]
        assert r.personality_id == "flush_ai"
        assert r.amount > 0
        assert r.duration_bucket in DURATION_RANGES
        assert r.ends_at > r.started_at

        # Bankroll debited
        state = db_setup["bankroll"].load_ai_bankroll("flush_ai", sandbox_id=SBX)
        assert state.chips == 50_000 - r.amount

        # Vice state row written
        vstate = db_setup["vice"].load("flush_ai", sandbox_id=SBX)
        assert vstate is not None
        assert vstate.amount == r.amount

    def test_broke_ai_does_not_vice(self, db_setup):
        result = resolve_ai_vice_spending(
            candidates={"broke_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result == []

    def test_high_rng_blocks_fire(self, db_setup):
        # Probability check fails for everyone
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysHighRng(),
            now=ANCHOR,
        )
        assert result == []

    def test_starts_per_refresh_cap(self, db_setup):
        # Seed multiple flush AIs; only `max_starts` fire
        for i in range(5):
            pid = f"flush_{i}"
            db_setup["bankroll"].save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid,
                    chips=50_000,
                    last_regen_tick=ANCHOR,
                ),
                sandbox_id=SBX,
                chip_ledger_repo=db_setup["ledger"],
            )

        # Use a low concentration_floor override so we're testing the
        # cap mechanic, not the median-shift effect from adding 5
        # flush AIs to the cast.
        result = resolve_ai_vice_spending(
            candidates={f"flush_{i}" for i in range(5)},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
            max_starts=2,
            concentration_floor=0.5,
        )
        assert len(result) == 2

    def test_ledger_audit_invariant(self, db_setup):
        """vice_spending fire must leave the audit drift at zero.

        ai_bankrolls_stored shrinks by `amount`; ledger destruction
        side grows by the same amount (via record_vice_spending).
        Both shrink the universe equally, so drift stays zero.
        """
        ledger = db_setup["ledger"]
        before_destructions = ledger.sum_destructions_by_reason()
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=ledger,
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert len(result) == 1
        after_destructions = ledger.sum_destructions_by_reason()
        vice_destroyed = after_destructions.get('vice_spending', 0) - before_destructions.get(
            'vice_spending', 0
        )
        assert vice_destroyed == result[0].amount

    def test_short_circuits_when_cast_too_poor(self, db_setup):
        """If the cast median is below MIN_CAST_MEDIAN_FOR_VICE, vice
        suppresses entirely — even a flush AI shouldn't fire when
        "everybody is broke." Regression for the design intent: drain
        the rich, not the relatively-richer-than-broke."""
        # Drop everyone (including flush_ai) to near-zero so median collapses
        for pid in ["flush_ai", "broke_ai"] + [f"bg_ai_{i}" for i in range(7)]:
            db_setup["bankroll"].save_ai_bankroll(
                AIBankrollState(
                    personality_id=pid,
                    chips=500,
                    last_regen_tick=ANCHOR,
                ),
                sandbox_id=SBX,
                chip_ledger_repo=db_setup["ledger"],
            )
        # Now make one AI "relatively rich" by cast standards: $5K
        # against a median of $500. Concentration = 10 (above floor).
        # But cast median ($500) is below MIN_CAST_MEDIAN_FOR_VICE, so
        # the pass short-circuits.
        db_setup["bankroll"].save_ai_bankroll(
            AIBankrollState(
                personality_id="flush_ai",
                chips=5_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
            chip_ledger_repo=db_setup["ledger"],
        )

        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result == []

    def test_concentration_gate_excludes_modestly_above_average(self, db_setup):
        """Reported bug: Ace at $24K vicing while median is $14K.
        Under the concentration gate (2.5× median = $35K threshold),
        an AI at $24K is only 1.71× — below the floor — so doesn't vice.
        """
        # Seed an "Ace-like" AI at $24K against the standing $14K median
        db_setup["bankroll"].save_ai_bankroll(
            AIBankrollState(
                personality_id="ace_like",
                chips=24_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
            chip_ledger_repo=db_setup["ledger"],
        )

        result = resolve_ai_vice_spending(
            candidates={"ace_like"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert result == []

    def test_no_chip_ledger_repo_still_fires(self, db_setup):
        # vice should fire even when the ledger is unavailable —
        # consistent with how other paths degrade gracefully.
        result = resolve_ai_vice_spending(
            candidates={"flush_ai"},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=None,
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
        )
        assert len(result) == 1


class TestTickViceExpirations:
    """Expiry: psych recovery applies, row deleted, end event returned."""

    def test_expires_past_due_row(self, db_setup):
        # Insert a vice that already ended
        db_setup["vice"].insert_vice_state(
            ViceState(
                personality_id="flush_ai",
                sandbox_id=SBX,
                started_at=ANCHOR - timedelta(hours=2),
                ends_at=ANCHOR - timedelta(minutes=5),
                amount=1500,
                duration_bucket='medium',
                narration='cosmic foundation gift',
            )
        )
        ends = tick_vice_expirations(
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            sandbox_id=SBX,
            now=ANCHOR,
        )
        assert len(ends) == 1
        assert ends[0].personality_id == "flush_ai"
        # Row deleted
        assert db_setup["vice"].load("flush_ai", sandbox_id=SBX) is None

    def test_does_not_expire_active_row(self, db_setup):
        db_setup["vice"].insert_vice_state(
            ViceState(
                personality_id="flush_ai",
                sandbox_id=SBX,
                started_at=ANCHOR,
                ends_at=ANCHOR + timedelta(hours=1),
                amount=500,
                duration_bucket='medium',
                narration='x',
            )
        )
        ends = tick_vice_expirations(
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            sandbox_id=SBX,
            now=ANCHOR,
        )
        assert ends == []
        # Row still exists
        assert db_setup["vice"].load("flush_ai", sandbox_id=SBX) is not None

    def test_recovery_pulls_axes_toward_baseline(self, db_setup):
        # Drop flush_ai's composure way down
        tilted_psych = {
            'axes': {'confidence': 0.7, 'composure': 0.2, 'energy': 0.7},
            'anchors': {},
        }
        db_setup["bankroll"].save_emotional_state_json(
            "flush_ai",
            json.dumps(tilted_psych),
            sandbox_id=SBX,
        )
        # Insert a vice that just ended; amount large enough to push
        # recovery factor past BASE_RECOVERY.
        db_setup["vice"].insert_vice_state(
            ViceState(
                personality_id="flush_ai",
                sandbox_id=SBX,
                started_at=ANCHOR - timedelta(hours=2),
                ends_at=ANCHOR - timedelta(minutes=5),
                amount=5000,
                duration_bucket='medium',
                narration='x',
            )
        )
        ends = tick_vice_expirations(
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            sandbox_id=SBX,
            now=ANCHOR,
        )
        assert len(ends) == 1
        assert ends[0].recovery_applied is True

        # Re-load and check composure moved up
        blob = db_setup["bankroll"].load_emotional_state_json(
            "flush_ai",
            sandbox_id=SBX,
        )
        data = json.loads(blob)
        new_composure = data['axes']['composure']
        assert new_composure > 0.2  # pulled up from the tilted starting state

    def test_recovery_skipped_when_no_psych_state(self, db_setup):
        # Insert vice for an AI with no emotional_state_json row
        db_setup["bankroll"].save_ai_bankroll(
            AIBankrollState(
                personality_id="no_psych",
                chips=10_000,
                last_regen_tick=ANCHOR,
            ),
            sandbox_id=SBX,
            chip_ledger_repo=db_setup["ledger"],
        )
        db_setup["vice"].insert_vice_state(
            ViceState(
                personality_id="no_psych",
                sandbox_id=SBX,
                started_at=ANCHOR - timedelta(hours=2),
                ends_at=ANCHOR - timedelta(minutes=5),
                amount=500,
                duration_bucket='medium',
                narration='x',
            )
        )
        ends = tick_vice_expirations(
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            personality_repo=db_setup["personality"],
            sandbox_id=SBX,
            now=ANCHOR,
        )
        # End event still fires; only recovery is skipped
        assert len(ends) == 1
        assert ends[0].recovery_applied is False
        # Row still deleted
        assert db_setup["vice"].load("no_psych", sandbox_id=SBX) is None


class TestFormatViceStartMessage:
    """Defensive prefix logic — never let the ticker show an unattributed line."""

    def test_name_led_narration_passes_through(self):
        from cash_mode.activity import format_vice_start_message

        out = format_vice_start_message(
            "Napoleon",
            "Napoleon commissioned an oversized bronze bust",
        )
        assert out == "Napoleon commissioned an oversized bronze bust"

    def test_case_insensitive_name_lead(self):
        from cash_mode.activity import format_vice_start_message

        # LLM lowercased the name — still counts as name-led.
        out = format_vice_start_message(
            "Buddha",
            "buddha donated to the silent retreat fund",
        )
        assert out == "buddha donated to the silent retreat fund"

    def test_possessive_name_lead(self):
        from cash_mode.activity import format_vice_start_message

        out = format_vice_start_message(
            "Napoleon",
            "Napoleon's tailor delivered a new uniform",
        )
        assert out == "Napoleon's tailor delivered a new uniform"

    def test_missing_name_gets_prepended(self):
        """The reported UX bug: LLM dropped the name → ticker reads as
        unattributed. The formatter prepends the name as a fallback."""
        from cash_mode.activity import format_vice_start_message

        out = format_vice_start_message(
            "Bezos",
            "Pre-ordered a private flight he won't be on for two years",
        )
        assert out.startswith("Bezos — ")
        assert "private flight" in out

    def test_empty_narration_falls_back_to_name(self):
        from cash_mode.activity import format_vice_start_message

        out = format_vice_start_message("Hemingway", "")
        assert "Hemingway" in out
        assert "stepped out" in out

    def test_whitespace_only_narration_falls_back(self):
        from cash_mode.activity import format_vice_start_message

        out = format_vice_start_message("Hemingway", "   \n  ")
        assert "Hemingway" in out

    def test_no_double_prepend_when_name_already_there(self):
        """Don't produce 'Napoleon — Napoleon did X' on name-led inputs."""
        from cash_mode.activity import format_vice_start_message

        out = format_vice_start_message(
            "Napoleon",
            "Napoleon did the thing",
        )
        # Exactly one occurrence of the name at the start
        assert out.count("Napoleon") == 1


class TestEmitViceSpendingEvents:
    """Vice start / end events emit to the lobby ticker correctly."""

    def _setup_personality_repo(self, db_setup, pid: str, display_name: str):
        """Insert a minimal personality row so name lookups work."""
        import sqlite3

        with sqlite3.connect(db_setup["db"]) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO personalities
                    (name, config_json, personality_id)
                VALUES (?, ?, ?)
                """,
                (
                    display_name,
                    json.dumps({"name": display_name, "id": pid}),
                    pid,
                ),
            )

    def test_start_emits_ticker_event(self, db_setup):
        from cash_mode import activity
        from cash_mode.lobby import _emit_vice_spending_events

        # Reset the ring buffer so prior tests don't pollute
        with activity._events_lock:
            activity._events.clear()

        self._setup_personality_repo(db_setup, "napoleon_id", "Napoleon")

        start = ViceStartResult(
            personality_id="napoleon_id",
            amount=2500,
            duration_bucket='long',
            started_at=ANCHOR,
            ends_at=ANCHOR + timedelta(hours=2),
            narration="Napoleon commissioned an oversized bronze bust",
            excess_ratio=3.8,
            pressure=0.4,
        )
        _emit_vice_spending_events(
            starts=[start],
            ends=[],
            personality_repo=db_setup["personality"],
            now=ANCHOR,
            sandbox_id=SBX,
        )
        events = activity.recent_events(limit=10, sandbox_id=SBX)
        vice_events = [e for e in events if e.type == "vice_start"]
        assert len(vice_events) == 1
        assert vice_events[0].name == "Napoleon"
        assert "bronze bust" in vice_events[0].message
        assert vice_events[0].reason == 'long'

    def test_end_emits_ticker_event(self, db_setup):
        from cash_mode import activity
        from cash_mode.lobby import _emit_vice_spending_events

        with activity._events_lock:
            activity._events.clear()

        self._setup_personality_repo(db_setup, "buddha_id", "Buddha")

        end = ViceEndResult(
            personality_id="buddha_id",
            started_at=ANCHOR - timedelta(hours=2),
            ends_at=ANCHOR,
            amount=1500,
            duration_bucket='long',
            narration="Buddha donated to the silent retreat fund",
            recovery_applied=True,
        )
        _emit_vice_spending_events(
            starts=[],
            ends=[end],
            personality_repo=db_setup["personality"],
            now=ANCHOR,
            sandbox_id=SBX,
        )
        events = activity.recent_events(limit=10, sandbox_id=SBX)
        vice_events = [e for e in events if e.type == "vice_end"]
        assert len(vice_events) == 1
        assert "Buddha is back" in vice_events[0].message

    def test_unattributed_narration_gets_name_prepended(self, db_setup):
        """Regression for the reported UX bug: LLM returned a narration
        without the character's name, ticker rendered as an
        unattributed quote. The emit helper now resolves the name and
        the formatter prepends it."""
        from cash_mode import activity
        from cash_mode.lobby import _emit_vice_spending_events

        with activity._events_lock:
            activity._events.clear()

        self._setup_personality_repo(db_setup, "bezos_id", "Bezos")

        start = ViceStartResult(
            personality_id="bezos_id",
            amount=4500,
            duration_bucket='long',
            started_at=ANCHOR,
            ends_at=ANCHOR + timedelta(hours=3),
            # LLM dropped the name in this case — the bug condition.
            narration="Pre-ordered a private flight he won't be on for two years",
            excess_ratio=4.0,
            pressure=0.4,
        )
        _emit_vice_spending_events(
            starts=[start],
            ends=[],
            personality_repo=db_setup["personality"],
            now=ANCHOR,
            sandbox_id=SBX,
        )
        events = activity.recent_events(limit=10, sandbox_id=SBX)
        vice_events = [e for e in events if e.type == "vice_start"]
        assert len(vice_events) == 1
        # The displayed message identifies WHO is spending.
        assert vice_events[0].message.startswith("Bezos")
        assert "private flight" in vice_events[0].message

    def test_no_events_when_starts_and_ends_empty(self, db_setup):
        from cash_mode import activity
        from cash_mode.lobby import _emit_vice_spending_events

        with activity._events_lock:
            activity._events.clear()

        _emit_vice_spending_events(
            starts=[],
            ends=[],
            personality_repo=db_setup["personality"],
            now=ANCHOR,
            sandbox_id=SBX,
        )
        events = activity.recent_events(limit=10, sandbox_id=SBX)
        assert events == []

    def test_sandbox_filter_on_ticker(self, db_setup):
        """Events emitted under sandbox A don't surface under sandbox B."""
        from cash_mode import activity
        from cash_mode.lobby import _emit_vice_spending_events

        with activity._events_lock:
            activity._events.clear()

        self._setup_personality_repo(db_setup, "p_id", "X")
        start = ViceStartResult(
            personality_id="p_id",
            amount=500,
            duration_bucket='medium',
            started_at=ANCHOR,
            ends_at=ANCHOR + timedelta(hours=1),
            narration="X did something",
            excess_ratio=1.0,
            pressure=0.4,
        )
        _emit_vice_spending_events(
            starts=[start],
            ends=[],
            personality_repo=db_setup["personality"],
            now=ANCHOR,
            sandbox_id="sbx-A",
        )
        # Other sandbox sees nothing
        events_b = activity.recent_events(limit=10, sandbox_id="sbx-B")
        assert events_b == []
        # Origin sandbox sees the event
        events_a = activity.recent_events(limit=10, sandbox_id="sbx-A")
        assert any(e.type == "vice_start" for e in events_a)


if __name__ == "__main__":
    unittest.main()
