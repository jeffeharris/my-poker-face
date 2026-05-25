"""Staker incentive scoring + per-staker history aggregation.

Two surfaces under test:

1. Pure scoring math in `cash_mode.staker_history`
   (`_excess_pressure`, `_belief_score`, `_relationship_warmth`,
   `candidate_weight`). No I/O — all inputs are values or simple
   dataclasses.

2. `StakeRepository.aggregate_history_for_staker` — single SQL
   aggregate over the `stakes` table; tempdb-backed.

Spec: `docs/plans/CASH_MODE_AI_STAKER_INCENTIVES.md`.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.staker_history import (
    BASE_WEIGHT,
    BELIEF_SCALE,
    CARRY_WEIGHT,
    DEFAULTED_WEIGHT,
    EXCESS_INCENTIVE_WEIGHT,
    HEAT_PENALTY_WEIGHT,
    MAX_BELIEF_BONUS,
    MAX_EXCESS_BONUS,
    MAX_WARMTH_BONUS,
    MIN_WEIGHT,
    RELATIONSHIP_WARMTH_BASELINE,
    SETTLED_WEIGHT,
    StakerHistoryStats,
    _belief_score,
    _excess_pressure,
    _relationship_warmth,
    candidate_weight,
)
from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

ANCHOR = datetime(2026, 5, 21, 12, 0, 0)


# --- Pure math --------------------------------------------------------------


class TestExcessPressure(unittest.TestCase):
    def test_zero_at_starting_bankroll(self):
        self.assertEqual(_excess_pressure(10_000, 10_000), 0.0)

    def test_zero_below_starting_bankroll(self):
        # Floor is the starting value; modestly broke AIs get 0, not negative.
        self.assertEqual(_excess_pressure(8_000, 10_000), 0.0)

    def test_modest_excess(self):
        # 1.2× starting → excess_ratio = 0.2 → 0.2 * 0.4 = 0.08.
        self.assertAlmostEqual(
            _excess_pressure(12_000, 10_000),
            0.2 * EXCESS_INCENTIVE_WEIGHT,
        )

    def test_clamped_at_max(self):
        # 100× starting would blow past the cap.
        self.assertEqual(
            _excess_pressure(1_000_000, 10_000),
            MAX_EXCESS_BONUS,
        )

    def test_zero_starting_bankroll_returns_zero(self):
        # Defensive — avoid div-by-zero on a misconfigured personality.
        self.assertEqual(_excess_pressure(5_000, 0), 0.0)


class TestBeliefScore(unittest.TestCase):
    def test_no_history_is_neutral(self):
        self.assertEqual(_belief_score(None), 0.0)

    def test_empty_counts_is_neutral(self):
        self.assertEqual(
            _belief_score(StakerHistoryStats(0, 0, 0)),
            0.0,
        )

    def test_pure_settled_is_positive(self):
        # 3 settled × 1.0 × 0.3 = 0.9.
        score = _belief_score(StakerHistoryStats(settled_count=3, carry_count=0, defaulted_count=0))
        self.assertAlmostEqual(
            score,
            3 * SETTLED_WEIGHT * BELIEF_SCALE,
        )
        self.assertGreater(score, 0)

    def test_pure_default_is_negative(self):
        # 1 default × -1.5 × 0.3 = -0.45.
        score = _belief_score(StakerHistoryStats(settled_count=0, carry_count=0, defaulted_count=1))
        self.assertAlmostEqual(
            score,
            DEFAULTED_WEIGHT * BELIEF_SCALE,
        )
        self.assertLess(score, 0)

    def test_default_outweighs_one_settle(self):
        # 1 settled + 1 default = (1 - 1.5) × 0.3 = -0.15.
        score = _belief_score(StakerHistoryStats(settled_count=1, carry_count=0, defaulted_count=1))
        self.assertLess(score, 0)
        self.assertAlmostEqual(
            score,
            (SETTLED_WEIGHT + DEFAULTED_WEIGHT) * BELIEF_SCALE,
        )

    def test_carry_is_mildly_negative(self):
        # 2 settled + 1 carry = (2 - 0.5) × 0.3 = 0.45.
        score = _belief_score(StakerHistoryStats(settled_count=2, carry_count=1, defaulted_count=0))
        self.assertAlmostEqual(
            score,
            (2 * SETTLED_WEIGHT + CARRY_WEIGHT) * BELIEF_SCALE,
        )

    def test_clamped_at_positive_bound(self):
        # 100 settled would overflow without the cap.
        score = _belief_score(
            StakerHistoryStats(settled_count=100, carry_count=0, defaulted_count=0)
        )
        self.assertEqual(score, MAX_BELIEF_BONUS)

    def test_clamped_at_negative_bound(self):
        score = _belief_score(
            StakerHistoryStats(settled_count=0, carry_count=0, defaulted_count=100)
        )
        self.assertEqual(score, -MAX_BELIEF_BONUS)


class TestRelationshipWarmth(unittest.TestCase):
    def test_none_returns_baseline(self):
        self.assertEqual(_relationship_warmth(None), RELATIONSHIP_WARMTH_BASELINE)

    def test_neutral_axes(self):
        # (0.5, 0.5, 0.0) → (0.5+0.5)/2 - 0 = 0.5; * 1.0 = 0.5.
        self.assertAlmostEqual(_relationship_warmth((0.5, 0.5, 0.0)), 0.5)

    def test_friendly_pair(self):
        # (0.8, 0.7, 0.0) → (0.8+0.7)/2 = 0.75.
        self.assertAlmostEqual(_relationship_warmth((0.8, 0.7, 0.0)), 0.75)

    def test_high_heat_drops_toward_zero(self):
        # (0.3, 0.3, 0.9) → 0.3 - 0.9 * 0.4 = 0.3 - 0.36 = -0.06 → clamped to 0.
        self.assertEqual(_relationship_warmth((0.3, 0.3, 0.9)), 0.0)

    def test_clamped_at_max(self):
        # (1.0, 1.0, 0.0) → 1.0; clamped at MAX_WARMTH_BONUS (1.0).
        self.assertEqual(_relationship_warmth((1.0, 1.0, 0.0)), MAX_WARMTH_BONUS)

    def test_heat_penalty_formula(self):
        # (0.6, 0.6, 0.5) → 0.6 - 0.5 * 0.4 = 0.4.
        self.assertAlmostEqual(
            _relationship_warmth((0.6, 0.6, 0.5)),
            (0.6 + 0.6) / 2 - 0.5 * HEAT_PENALTY_WEIGHT,
        )


class TestCandidateWeight(unittest.TestCase):
    def test_baseline_no_history_no_excess_neutral_axes(self):
        # Cold start with neutral axes: BASE + 0 (excess) + 0 (belief) + 0.3 (baseline).
        weight = candidate_weight(
            bankroll=10_000,
            starting_bankroll=10_000,
            history_stats=None,
            relationship_axes=None,
        )
        self.assertAlmostEqual(weight, BASE_WEIGHT + RELATIONSHIP_WARMTH_BASELINE)

    def test_wealthy_with_good_history_dominates(self):
        # 5× starting + 3 settled + warm relationship → large weight.
        weight = candidate_weight(
            bankroll=50_000,
            starting_bankroll=10_000,
            history_stats=StakerHistoryStats(settled_count=3, carry_count=0, defaulted_count=0),
            relationship_axes=(0.8, 0.7, 0.0),
        )
        # excess: 4.0 * 0.4 = 1.6
        # belief: 3 * 0.3 = 0.9
        # warmth: (0.8+0.7)/2 = 0.75
        # total: 1.0 + 1.6 + 0.9 + 0.75 = 4.25
        self.assertAlmostEqual(weight, BASE_WEIGHT + 1.6 + 0.9 + 0.75)

    def test_defaulted_history_can_go_below_base(self):
        # 1 default with no excess, neutral relationship → 1.0 - 0.45 + 0.3 = 0.85.
        weight = candidate_weight(
            bankroll=10_000,
            starting_bankroll=10_000,
            history_stats=StakerHistoryStats(settled_count=0, carry_count=0, defaulted_count=1),
            relationship_axes=None,
        )
        self.assertLess(weight, BASE_WEIGHT + RELATIONSHIP_WARMTH_BASELINE)

    def test_min_weight_floor_protects_against_extreme_negative(self):
        # Massive defaulted history clamps belief at -1.5; warmth at 0 (full heat).
        # Total would be 1.0 - 1.5 + 0 = -0.5 → floored at MIN_WEIGHT.
        weight = candidate_weight(
            bankroll=10_000,
            starting_bankroll=10_000,
            history_stats=StakerHistoryStats(settled_count=0, carry_count=0, defaulted_count=100),
            relationship_axes=(0.0, 0.0, 1.0),
        )
        self.assertEqual(weight, MIN_WEIGHT)

    def test_missing_bankroll_excludes_excess_part(self):
        # No bankroll wired → excess contribution skipped, not error.
        weight = candidate_weight(
            bankroll=None,
            starting_bankroll=10_000,
            history_stats=StakerHistoryStats(settled_count=2, carry_count=0, defaulted_count=0),
            relationship_axes=None,
        )
        # BASE + belief(2*0.3=0.6) + warmth(baseline 0.3) = 1.9
        self.assertAlmostEqual(weight, BASE_WEIGHT + 0.6 + RELATIONSHIP_WARMTH_BASELINE)

    def test_missing_starting_bankroll_excludes_excess_part(self):
        weight = candidate_weight(
            bankroll=50_000,
            starting_bankroll=None,
            history_stats=None,
            relationship_axes=None,
        )
        self.assertAlmostEqual(weight, BASE_WEIGHT + RELATIONSHIP_WARMTH_BASELINE)


# --- DB aggregation ---------------------------------------------------------


@pytest.fixture
def stake_repo(tmp_path):
    """Schema + StakeRepository on a fresh tempdb."""
    db = str(tmp_path / "staker_history.db")
    SchemaManager(db).ensure_schema()
    return StakeRepository(db)


def _stake(
    *,
    stake_id: str,
    staker_id: str,
    borrower_id: str,
    status: str,
    created_at: datetime = ANCHOR,
) -> Stake:
    return Stake(
        stake_id=stake_id,
        session_id=f"session_{stake_id}",
        staker_id=staker_id,
        staker_kind=STAKER_KIND_PERSONALITY,
        borrower_id=borrower_id,
        borrower_kind=BORROWER_KIND_PERSONALITY,
        format=STAKE_FORMAT_PURE,
        principal=400,
        match_amount=0,
        origination_fee=0,
        cut=0.30,
        status=status,
        carry_amount=0,
        stake_tier="$10",
        created_at=created_at,
    )


class TestAggregateHistoryForStaker:
    def test_empty_staker_returns_empty_dict(self, stake_repo):
        result = stake_repo.aggregate_history_for_staker("bezos")
        assert result == {}

    def test_active_stakes_are_excluded(self, stake_repo):
        # Active stakes don't have an outcome yet — should not be counted.
        stake_repo.create_stake(
            _stake(
                stake_id="s1",
                staker_id="bezos",
                borrower_id="hemingway",
                status=STAKE_STATUS_ACTIVE,
            )
        )
        result = stake_repo.aggregate_history_for_staker("bezos")
        assert result == {}

    def test_groups_by_borrower_and_status(self, stake_repo):
        stake_repo.create_stake(
            _stake(
                stake_id="s1",
                staker_id="bezos",
                borrower_id="hemingway",
                status=STAKE_STATUS_SETTLED,
            )
        )
        stake_repo.create_stake(
            _stake(
                stake_id="s2",
                staker_id="bezos",
                borrower_id="hemingway",
                status=STAKE_STATUS_SETTLED,
            )
        )
        stake_repo.create_stake(
            _stake(
                stake_id="s3",
                staker_id="bezos",
                borrower_id="hemingway",
                status=STAKE_STATUS_CARRY,
            )
        )
        stake_repo.create_stake(
            _stake(
                stake_id="s4",
                staker_id="bezos",
                borrower_id="napoleon",
                status=STAKE_STATUS_DEFAULTED,
            )
        )
        result = stake_repo.aggregate_history_for_staker("bezos")
        assert "hemingway" in result
        assert "napoleon" in result
        assert result["hemingway"] == StakerHistoryStats(
            settled_count=2,
            carry_count=1,
            defaulted_count=0,
        )
        assert result["napoleon"] == StakerHistoryStats(
            settled_count=0,
            carry_count=0,
            defaulted_count=1,
        )

    def test_other_stakers_history_excluded(self, stake_repo):
        # Napoleon staked Hemingway, but we're asking about bezos.
        stake_repo.create_stake(
            _stake(
                stake_id="s1",
                staker_id="napoleon",
                borrower_id="hemingway",
                status=STAKE_STATUS_SETTLED,
            )
        )
        result = stake_repo.aggregate_history_for_staker("bezos")
        assert result == {}

    def test_borrower_with_no_history_absent_from_dict(self, stake_repo):
        # Aggregation only returns borrowers we have history with —
        # callers handle absent keys as "no history" via .get(None).
        stake_repo.create_stake(
            _stake(
                stake_id="s1",
                staker_id="bezos",
                borrower_id="hemingway",
                status=STAKE_STATUS_SETTLED,
            )
        )
        result = stake_repo.aggregate_history_for_staker("bezos")
        assert "napoleon" not in result
        assert result.get("napoleon") is None


if __name__ == '__main__':
    unittest.main()
