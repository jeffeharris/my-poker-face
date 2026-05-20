"""Tests for the Phase 2 additions to compute_personality_offers.

Covers tier-driven filtering + per-tier rate bump, per-staker
garnishment, the rejections side-output, and the house_only short
circuit. Existing legacy tests live in test_personality_offers.py
and don't pass stake_repo/stake_label (so tier logic is bypassed).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pytest

from cash_mode.lender_profile import LenderProfile
from cash_mode.sponsor_offers import (
    GARNISHMENT_RATE_CAP,
    LenderRejection,
    TIER_RATE_BUMP,
    compute_personality_offers,
)
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_STATUS_CARRY,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from cash_mode.staking_tier import (
    TIER_PREMIUM,
    TIER_RESTRICTED,
    TIER_STANDARD,
)
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository


PLAYER = "alice"
MIN_BUY_IN = 400
MAX_BUY_IN = 1000
ANCHOR = datetime(2026, 5, 20, 12, 0, 0)


# --- Fakes (mirrors test_personality_offers.py for consistency) ---

@dataclass
class _RelState:
    respect: float = 0.5
    heat: float = 0.0
    likability: float = 0.5


class _FakeBankrollRepo:
    def __init__(self, profiles=None, bankrolls=None):
        self.profiles = profiles or {}
        self.bankrolls = bankrolls or {}

    def load_lender_profile(self, personality_id):
        return self.profiles.get(personality_id) or _default_profile()

    def load_ai_bankroll_current(self, personality_id, *, now=None):
        return self.bankrolls.get(personality_id)


class _FakeRelationshipRepo:
    def __init__(self, states=None):
        self.states = states or {}

    def load_relationship_state(self, *, observer_id, opponent_id, now=None):
        return self.states.get((observer_id, opponent_id))


def _default_profile() -> LenderProfile:
    return LenderProfile(
        willing=True,
        max_loan_pct_of_bankroll=0.20,
        floor_anchor=1.20,
        rate_anchor=0.20,
        respect_floor=0.30,
        heat_ceiling=0.70,
    )


# --- Test repo fixture (real StakeRepository on tempdb) ---

@pytest.fixture
def stake_repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield StakeRepository(db_path)


def _seed_carry(
    repo: StakeRepository,
    *,
    stake_id: str,
    borrower_id: str = PLAYER,
    staker_id: str,
    carry_amount: int,
) -> None:
    repo.create_stake(Stake(
        stake_id=stake_id,
        session_id=f"sess-{stake_id}",
        staker_id=staker_id,
        staker_kind=STAKER_KIND_PERSONALITY,
        borrower_id=borrower_id,
        borrower_kind=BORROWER_KIND_HUMAN,
        format="pure",
        principal=carry_amount,
        match_amount=0,
        origination_fee=0,
        cut=0.20,
        status=STAKE_STATUS_CARRY,
        carry_amount=carry_amount,
        stake_tier="$10",
        created_at=ANCHOR,
        settled_at=ANCHOR,
    ))


def _make_candidate(pid: str) -> dict:
    return {"personality_id": pid, "name": pid.title()}


# --- Tests ---


class TestTierBumpsRate:
    """Tier degrades the offer by adding a per-tier rate bump on top of
    the existing relationship-axis trims."""

    def test_premium_tier_no_rate_bump(self, stake_repo):
        # Zero carries → premium → bump = 0.
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        # Anchor rate 0.20, no relationship trim (neutral), no tier bump.
        assert len(offers) == 1
        assert offers[0].rate == pytest.approx(0.20)

    def test_standard_tier_bumps_rate(self, stake_repo):
        # Carry load 800 = 20% of $10's max_carry (4000) → tier=standard.
        _seed_carry(stake_repo, stake_id="old", staker_id="bezos", carry_amount=800)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        # Anchor rate 0.20 + standard bump 0.075 = 0.275.
        assert len(offers) == 1
        assert offers[0].rate == pytest.approx(0.20 + TIER_RATE_BUMP[TIER_STANDARD])

    def test_restricted_tier_bumps_rate_and_filters_low_relationship(self, stake_repo):
        # Carry load 2400 = 60% → restricted. Floors require likability ≥ 0.6
        # AND respect ≥ 0.6 — Napoleon's defaults (0.5 / 0.5) fail.
        _seed_carry(stake_repo, stake_id="old", staker_id="bezos", carry_amount=2400)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),  # default neutral
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        assert offers == []  # filtered out by tier floor

    def test_restricted_tier_allows_high_relationship_lender(self, stake_repo):
        _seed_carry(stake_repo, stake_id="old", staker_id="bezos", carry_amount=2400)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        # Napoleon trusts + likes Alice (above the restricted floor).
        rel_repo = _FakeRelationshipRepo(states={
            ("napoleon", PLAYER): _RelState(respect=0.7, likability=0.7),
        })
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=rel_repo,
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        # Surfaces but with a +0.20 rate bump on top of any relationship trims.
        assert len(offers) == 1
        # High likability + high respect → both trims fire (each -0.05 / -0.03
        # to floor and rate). Anchor 0.20 - 0.05 - 0.03 + 0.20 (restricted bump).
        assert offers[0].rate == pytest.approx(0.20 - 0.05 - 0.03 + 0.20)


class TestHouseOnlyShortCircuit:
    def test_house_only_tier_returns_empty(self, stake_repo):
        # Carry load >= 4000 = 100% → house_only.
        _seed_carry(stake_repo, stake_id="old", staker_id="bezos", carry_amount=4000)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        assert offers == []


class TestPerStakerGarnishment:
    """If the borrower has a carry owed to THIS specific lender, the
    lender's rate bumps proportionally on the new offer."""

    def test_existing_carry_with_same_lender_bumps_rate(self, stake_repo):
        # 400 chip carry to Napoleon. Capacity 1000. Bump = 400/1000 = 0.40,
        # capped at GARNISHMENT_RATE_CAP (0.20). Same-lender carry doesn't
        # cross threshold to tier=standard yet (400/4000 = 10% → premium).
        _seed_carry(stake_repo, stake_id="g1", staker_id="napoleon", carry_amount=400)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        # Anchor 0.20 + garnishment cap 0.20 = 0.40.
        assert len(offers) == 1
        assert offers[0].rate == pytest.approx(0.20 + GARNISHMENT_RATE_CAP)

    def test_carry_to_different_lender_doesnt_garnish(self, stake_repo):
        # Carry to Bezos doesn't affect Napoleon's offer rate.
        _seed_carry(stake_repo, stake_id="g1", staker_id="bezos", carry_amount=400)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        # Anchor rate, no garnishment for Napoleon.
        assert len(offers) == 1
        assert offers[0].rate == pytest.approx(0.20)

    def test_proportional_garnishment_under_cap(self, stake_repo):
        # Smaller carry: 100 chips owed to Napoleon, capacity 1000 → 0.10
        # bump, well under the 0.20 cap.
        _seed_carry(stake_repo, stake_id="g1", staker_id="napoleon", carry_amount=100)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            now=ANCHOR,
        )
        assert len(offers) == 1
        assert offers[0].rate == pytest.approx(0.20 + 0.10)


class TestRejectionsSideOutput:
    """rejections_out captures who didn't surface and why."""

    def test_low_respect_captured(self, stake_repo):
        # Default profile's respect_floor is 0.30. Napoleon respects Alice at 0.1.
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        rel_repo = _FakeRelationshipRepo(states={
            ("napoleon", PLAYER): _RelState(respect=0.1, likability=0.5),
        })
        rejections: List[LenderRejection] = []
        compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=rel_repo,
            stake_repo=stake_repo,
            stake_label="$10",
            rejections_out=rejections,
            now=ANCHOR,
        )
        assert len(rejections) == 1
        assert rejections[0].lender_id == "napoleon"
        assert rejections[0].reason == "respect_too_low"

    def test_tier_floor_failure_captured(self, stake_repo):
        # Restricted tier requires likability ≥ 0.6. Push carry to restricted.
        _seed_carry(stake_repo, stake_id="old", staker_id="bezos", carry_amount=2400)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        # Napoleon's relationship is acceptable for the legacy gates
        # but doesn't clear the restricted-tier floor.
        rel_repo = _FakeRelationshipRepo(states={
            ("napoleon", PLAYER): _RelState(respect=0.55, likability=0.4),
        })
        rejections: List[LenderRejection] = []
        compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=rel_repo,
            stake_repo=stake_repo,
            stake_label="$10",
            rejections_out=rejections,
            now=ANCHOR,
        )
        assert len(rejections) == 1
        assert rejections[0].reason == "tier_floor"

    def test_no_rejections_for_qualifying_lender(self, stake_repo):
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        rejections: List[LenderRejection] = []
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            stake_repo=stake_repo,
            stake_label="$10",
            rejections_out=rejections,
            now=ANCHOR,
        )
        assert len(offers) == 1
        assert rejections == []


class TestBackwardCompat:
    """Old call sites that don't pass stake_repo / stake_label still get
    the pre-Phase-2 behavior — no tier filtering, no garnishment."""

    def test_no_stake_repo_skips_tier_logic(self, stake_repo):
        # Even with a huge carry on disk, omitting stake_repo means tier
        # logic isn't applied. The offer surfaces at anchor rate.
        _seed_carry(stake_repo, stake_id="old", staker_id="napoleon", carry_amount=4000)
        bankroll_repo = _FakeBankrollRepo(bankrolls={"napoleon": 5_000})
        offers = compute_personality_offers(
            player_owner_id=PLAYER,
            min_buy_in=MIN_BUY_IN, max_buy_in=MAX_BUY_IN,
            candidate_personalities=[_make_candidate("napoleon")],
            bankroll_repo=bankroll_repo,
            relationship_repo=_FakeRelationshipRepo(),
            # No stake_repo / stake_label.
            now=ANCHOR,
        )
        assert len(offers) == 1
        assert offers[0].rate == pytest.approx(0.20)  # anchor only
