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
    COMFORT_FLOOR,
    DURATION_RANGES,
    EXCESS_WEIGHT,
    FLOOR_PROTECTION_FRACTION,
    MAX_PROB,
    MAX_RECOVERY,
    MAX_VICE_FRACTION,
    MIN_VICE_AMOUNT,
    PRESSURE_BOOST,
    VICE_STARTS_PER_REFRESH,
    ViceEndResult,
    ViceStartResult,
    compute_excess_ratio,
    compute_pressure,
    compute_recovered_axes,
    compute_recovery_factor,
    compute_vice_amount,
    compute_vice_probability,
    duration_for_bucket,
    resolve_ai_vice_spending,
    tick_vice_expirations,
)
from cash_mode.bankroll import AIBankrollState
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.vice_state_repository import ViceState, ViceStateRepository


ANCHOR = datetime(2026, 5, 23, 12, 0, 0)
SBX = "test-sandbox-vice"


# --- Pure-math tests --------------------------------------------------------


class TestExcessRatio(unittest.TestCase):
    def test_zero_below_comfort_floor(self):
        # 1.2x starting → exactly at the floor → 0
        self.assertEqual(compute_excess_ratio(12_000, 10_000), 0.0)

    def test_zero_below_threshold(self):
        # 0.5x starting → way below → 0
        self.assertEqual(compute_excess_ratio(5_000, 10_000), 0.0)

    def test_5x_starting_gives_38(self):
        # 5x - 1.2 = 3.8 from the worked examples in the design doc
        self.assertAlmostEqual(
            compute_excess_ratio(50_000, 10_000), 3.8, places=3,
        )

    def test_handles_zero_starting(self):
        # Defensive: never divide by zero
        self.assertEqual(compute_excess_ratio(100_000, 0), 0.0)


class TestPressure(unittest.TestCase):
    def test_min_axis_drives_pressure(self):
        # composure is the worst → pressure = 1 - composure
        self.assertAlmostEqual(
            compute_pressure(0.8, 0.3, 0.5), 1.0 - 0.3,
        )

    def test_calm_ai_has_floor_pressure(self):
        # All axes near baseline (0.7) → pressure ~ 0.3
        self.assertAlmostEqual(
            compute_pressure(0.7, 0.7, 0.7), 0.3,
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
            compute_vice_probability(50.0, 1.0), MAX_PROB,
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
            compute_recovery_factor(MIN_VICE_AMOUNT), BASE_RECOVERY,
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
            confidence=0.2, composure=0.7, energy=0.5,
            baseline_confidence=0.6,
            baseline_composure=0.7,
            baseline_energy=0.5,
            recovery_factor=0.5,
        )
        # 0.2 + (0.6 - 0.2) * 0.5 = 0.4
        self.assertAlmostEqual(new_conf, 0.4, places=4)

    def test_no_pull_when_factor_zero(self):
        out = compute_recovered_axes(
            confidence=0.2, composure=0.3, energy=0.4,
            baseline_confidence=0.7, baseline_composure=0.7, baseline_energy=0.5,
            recovery_factor=0.0,
        )
        self.assertEqual(out, (0.2, 0.3, 0.4))

    def test_clamps_to_unit_range(self):
        out = compute_recovered_axes(
            confidence=0.95, composure=0.5, energy=0.5,
            baseline_confidence=1.5,  # bad baseline > 1
            baseline_composure=0.5, baseline_energy=0.5,
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


# --- Integration tests against tempdb ---------------------------------------


@pytest.fixture
def db_setup(tmp_path):
    """Schema + repos + two seeded AIs (one flush, one broke)."""
    db = str(tmp_path / "vice.db")
    SchemaManager(db).ensure_schema()
    bankroll = BankrollRepository(db)
    ledger = ChipLedgerRepository(db)
    vice_repo = ViceStateRepository(db)
    personality = PersonalityRepository(db)

    # Two AI bankrolls: a flush one (vices) and a broke one (doesn't).
    bankroll.save_ai_bankroll(AIBankrollState(
        personality_id="flush_ai",
        chips=50_000,
        last_regen_tick=ANCHOR,
    ), sandbox_id=SBX, chip_ledger_repo=ledger)
    bankroll.save_ai_bankroll(AIBankrollState(
        personality_id="broke_ai",
        chips=5_000,
        last_regen_tick=ANCHOR,
    ), sandbox_id=SBX, chip_ledger_repo=ledger)

    # Seed an emotional state for the flush AI — pressure 0.3-ish.
    flush_psych = {
        'axes': {'confidence': 0.7, 'composure': 0.7, 'energy': 0.6},
        'anchors': {'baseline_aggression': 0.5},
    }
    bankroll.save_emotional_state_json(
        "flush_ai", json.dumps(flush_psych), sandbox_id=SBX,
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
        repo.insert_vice_state(ViceState(
            personality_id="active",
            sandbox_id=SBX,
            started_at=ANCHOR,
            ends_at=ANCHOR + timedelta(hours=2),
            amount=500, duration_bucket='medium', narration='x',
        ))
        repo.insert_vice_state(ViceState(
            personality_id="expired",
            sandbox_id=SBX,
            started_at=ANCHOR - timedelta(hours=2),
            ends_at=ANCHOR - timedelta(hours=1),
            amount=500, duration_bucket='medium', narration='y',
        ))
        active = repo.list_active(sandbox_id=SBX, now=ANCHOR)
        assert {v.personality_id for v in active} == {"active"}
        expired = repo.list_expired(sandbox_id=SBX, now=ANCHOR)
        assert {v.personality_id for v in expired} == {"expired"}

    def test_is_on_vice(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(ViceState(
            personality_id="on", sandbox_id=SBX,
            started_at=ANCHOR, ends_at=ANCHOR + timedelta(hours=1),
            amount=100, duration_bucket='short', narration='x',
        ))
        assert repo.is_on_vice("on", sandbox_id=SBX, now=ANCHOR) is True
        assert repo.is_on_vice("off", sandbox_id=SBX, now=ANCHOR) is False
        # After expiry passes, no longer on vice
        assert repo.is_on_vice(
            "on", sandbox_id=SBX, now=ANCHOR + timedelta(hours=2),
        ) is False

    def test_delete(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(ViceState(
            personality_id="x", sandbox_id=SBX,
            started_at=ANCHOR, ends_at=ANCHOR + timedelta(hours=1),
            amount=100, duration_bucket='short', narration='x',
        ))
        assert repo.delete("x", sandbox_id=SBX) is True
        assert repo.load("x", sandbox_id=SBX) is None
        # Second delete returns False
        assert repo.delete("x", sandbox_id=SBX) is False

    def test_sandbox_isolation(self, db_setup):
        repo = db_setup["vice"]
        repo.insert_vice_state(ViceState(
            personality_id="shared",
            sandbox_id="sbx-A",
            started_at=ANCHOR, ends_at=ANCHOR + timedelta(hours=1),
            amount=100, duration_bucket='short', narration='x',
        ))
        # Same personality_id in a different sandbox is invisible
        assert repo.load("shared", sandbox_id="sbx-B") is None
        assert repo.is_on_vice("shared", sandbox_id="sbx-B", now=ANCHOR) is False


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
            db_setup["bankroll"].save_ai_bankroll(AIBankrollState(
                personality_id=pid, chips=50_000, last_regen_tick=ANCHOR,
            ), sandbox_id=SBX, chip_ledger_repo=db_setup["ledger"])

        result = resolve_ai_vice_spending(
            candidates={f"flush_{i}" for i in range(5)},
            vice_repo=db_setup["vice"],
            bankroll_repo=db_setup["bankroll"],
            chip_ledger_repo=db_setup["ledger"],
            sandbox_id=SBX,
            rng=_AlwaysLowRng(),
            now=ANCHOR,
            max_starts=2,
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
        vice_destroyed = (
            after_destructions.get('vice_spending', 0)
            - before_destructions.get('vice_spending', 0)
        )
        assert vice_destroyed == result[0].amount

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
        db_setup["vice"].insert_vice_state(ViceState(
            personality_id="flush_ai",
            sandbox_id=SBX,
            started_at=ANCHOR - timedelta(hours=2),
            ends_at=ANCHOR - timedelta(minutes=5),
            amount=1500,
            duration_bucket='medium',
            narration='cosmic foundation gift',
        ))
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
        db_setup["vice"].insert_vice_state(ViceState(
            personality_id="flush_ai",
            sandbox_id=SBX,
            started_at=ANCHOR,
            ends_at=ANCHOR + timedelta(hours=1),
            amount=500, duration_bucket='medium', narration='x',
        ))
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
            "flush_ai", json.dumps(tilted_psych), sandbox_id=SBX,
        )
        # Insert a vice that just ended; amount large enough to push
        # recovery factor past BASE_RECOVERY.
        db_setup["vice"].insert_vice_state(ViceState(
            personality_id="flush_ai",
            sandbox_id=SBX,
            started_at=ANCHOR - timedelta(hours=2),
            ends_at=ANCHOR - timedelta(minutes=5),
            amount=5000,
            duration_bucket='medium',
            narration='x',
        ))
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
            "flush_ai", sandbox_id=SBX,
        )
        data = json.loads(blob)
        new_composure = data['axes']['composure']
        assert new_composure > 0.2  # pulled up from the tilted starting state

    def test_recovery_skipped_when_no_psych_state(self, db_setup):
        # Insert vice for an AI with no emotional_state_json row
        db_setup["bankroll"].save_ai_bankroll(AIBankrollState(
            personality_id="no_psych", chips=10_000, last_regen_tick=ANCHOR,
        ), sandbox_id=SBX, chip_ledger_repo=db_setup["ledger"])
        db_setup["vice"].insert_vice_state(ViceState(
            personality_id="no_psych",
            sandbox_id=SBX,
            started_at=ANCHOR - timedelta(hours=2),
            ends_at=ANCHOR - timedelta(minutes=5),
            amount=500, duration_bucket='medium', narration='x',
        ))
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


if __name__ == "__main__":
    unittest.main()
