"""Tests for cash_mode.staking_tier (Phase 2 Commit 1).

Covers tier resolution at the four boundary loads (premium / standard /
restricted / house_only), the carry-cap math against the STAKES_LADDER,
and defensive behavior on unknown stake labels.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_STATUS_CARRY,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from cash_mode.staking_tier import (
    CARRY_CAP_MULTIPLIER,
    TIER_HOUSE_ONLY,
    TIER_PREMIUM,
    TIER_RESTRICTED,
    TIER_STANDARD,
    compute_carry_load,
    max_carry_for_tier,
    resolve_tier,
)
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield StakeRepository(db_path)


def _seed_carry(
    repo: StakeRepository,
    *,
    stake_id: str,
    borrower_id: str = "alice",
    staker_id: str = "napoleon",
    carry_amount: int,
    stake_tier: str = "$10",
) -> None:
    repo.create_stake(
        Stake(
            stake_id=stake_id,
            session_id=f"sess-{stake_id}",
            staker_id=staker_id,
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id=borrower_id,
            borrower_kind=BORROWER_KIND_HUMAN,
            format="pure",
            principal=carry_amount,  # nominal; only carry_amount drives tier math
            match_amount=0,
            origination_fee=0,
            cut=0.20,
            status=STAKE_STATUS_CARRY,
            carry_amount=carry_amount,
            stake_tier=stake_tier,
            created_at=ANCHOR,
            settled_at=ANCHOR,
        )
    )


class TestMaxCarryForTier:
    def test_two_dollar_stake(self):
        # $2 BB × 40 BB min × 10 multiplier = 800
        assert max_carry_for_tier("$2") == 800

    def test_ten_dollar_stake(self):
        # $10 BB × 40 BB min × 10 multiplier = 4000
        assert max_carry_for_tier("$10") == 4000

    def test_unknown_stake_returns_zero(self):
        # Unknown label → defensive 0 (resolver will fall through to house).
        assert max_carry_for_tier("$bogus") == 0


class TestComputeCarryLoad:
    def test_zero_carries(self, repo):
        assert (
            compute_carry_load(
                borrower_id="alice",
                borrower_kind=BORROWER_KIND_HUMAN,
                stake_repo=repo,
            )
            == 0
        )

    def test_sums_all_carries(self, repo):
        _seed_carry(repo, stake_id="a", carry_amount=300)
        _seed_carry(repo, stake_id="b", carry_amount=200, staker_id="bezos")
        assert (
            compute_carry_load(
                borrower_id="alice",
                borrower_kind=BORROWER_KIND_HUMAN,
                stake_repo=repo,
            )
            == 500
        )

    def test_borrower_kind_isolation(self, repo):
        _seed_carry(repo, stake_id="a", borrower_id="zeus", carry_amount=300)
        # Different kind, same id — should NOT match.
        assert (
            compute_carry_load(
                borrower_id="zeus",
                borrower_kind="personality",
                stake_repo=repo,
            )
            == 0
        )


class TestResolveTierBoundaries:
    """Hit the four boundary loads at the $10 stake (max_carry = 4000)."""

    def test_zero_load_is_premium(self, repo):
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$10",
                stake_repo=repo,
            )
            == TIER_PREMIUM
        )

    def test_below_premium_threshold_is_premium(self, repo):
        # 0.19 × 4000 = 760 chips (just under 20% threshold).
        _seed_carry(repo, stake_id="a", carry_amount=760)
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$10",
                stake_repo=repo,
            )
            == TIER_PREMIUM
        )

    def test_at_premium_threshold_is_standard(self, repo):
        # 0.20 × 4000 = 800 (exactly at threshold → standard side).
        _seed_carry(repo, stake_id="a", carry_amount=800)
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$10",
                stake_repo=repo,
            )
            == TIER_STANDARD
        )

    def test_at_standard_threshold_is_restricted(self, repo):
        # 0.60 × 4000 = 2400.
        _seed_carry(repo, stake_id="a", carry_amount=2400)
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$10",
                stake_repo=repo,
            )
            == TIER_RESTRICTED
        )

    def test_at_restricted_threshold_is_house_only(self, repo):
        # 1.00 × 4000 = 4000.
        _seed_carry(repo, stake_id="a", carry_amount=4000)
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$10",
                stake_repo=repo,
            )
            == TIER_HOUSE_ONLY
        )

    def test_over_cap_is_house_only(self, repo):
        _seed_carry(repo, stake_id="a", carry_amount=10_000)
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$10",
                stake_repo=repo,
            )
            == TIER_HOUSE_ONLY
        )


class TestResolveTierDefensive:
    def test_unknown_stake_returns_house_only(self, repo):
        # Even with zero carries, an unknown stake forces house.
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$bogus",
                stake_repo=repo,
            )
            == TIER_HOUSE_ONLY
        )

    def test_tier_cap_drops_with_lower_playing_stake(self, repo):
        # $200 carry of 1000 chips:
        #   at $200 stake: cap = 8000 × 10 = 80000 → ratio 0.0125 → premium
        #   at $2 stake:   cap = 80 × 10 = 800   → ratio 1.25  → house_only
        _seed_carry(repo, stake_id="a", carry_amount=1000)
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$200",
                stake_repo=repo,
            )
            == TIER_PREMIUM
        )
        assert (
            resolve_tier(
                borrower_id="alice",
                current_stake_label="$2",
                stake_repo=repo,
            )
            == TIER_HOUSE_ONLY
        )


class TestCarryCapMultiplierIsTen:
    """Locked decision #8 — pin the constant so it can't drift silently."""

    def test_multiplier_is_ten(self):
        assert CARRY_CAP_MULTIPLIER == 10
