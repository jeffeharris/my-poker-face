"""Tests for `compute_personality_offers` — Path B AI sponsor generator.

Pure-function tests with fake repos. Targets the four eligibility
gates (willing, capacity, respect_floor, heat_ceiling), the term
adjustments by relationship axes (likability/heat/respect trims),
and the capacity-descending sort.

The route layer (commit 4) wires this into `/api/cash/sponsor-offers`;
this commit covers only the pure logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pytest

from cash_mode.sponsor_offers import (
    PersonalitySponsorOffer,
    _adjusted_terms,
    _capacity_for_lender,
    _relationship_hint,
    compute_personality_offers,
)
from cash_mode.staker_profile import STAKER_PROFILE_DEFAULTS, StakerProfile


@dataclass
class _RelState:
    """Minimal RelationshipState double for the fake repo. Mirrors the
    fields `compute_personality_offers` reads."""

    respect: float = 0.5
    heat: float = 0.0
    likability: float = 0.5


class _FakeBankrollRepo:
    """Fake BankrollRepository for pure-function tests.

    `profiles` maps personality_id → StakerProfile (or omitted to
    default). `bankrolls` maps personality_id → projected chips int
    (or omitted to simulate no bankroll row, returning None).
    """

    def __init__(self, profiles=None, bankrolls=None):
        self.profiles = profiles or {}
        self.bankrolls = bankrolls or {}

    def load_staker_profile(self, personality_id):
        return self.profiles.get(personality_id, STAKER_PROFILE_DEFAULTS)

    def load_ai_bankroll_current(self, personality_id, *, sandbox_id, now=None):
        return self.bankrolls.get(personality_id)  # None when missing


class _FakeRelationshipRepo:
    """Fake RelationshipRepository for pure-function tests.

    `states` maps (observer_id, opponent_id) → _RelState. Missing
    pair returns None (the no-row state — treated as default neutral
    by compute_personality_offers)."""

    def __init__(self, states=None):
        self.states = states or {}

    def load_relationship_state(self, *, observer_id, opponent_id, now=None):
        return self.states.get((observer_id, opponent_id))


# Common table window for tests — $10 stake (min=400, max=1000).
MIN_BUY_IN = 400
MAX_BUY_IN = 1000


# --- Capacity math ---


class TestCapacityForLender:
    def test_pct_under_min_returns_raw(self):
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.05,
            floor_anchor=1.0,
            rate_anchor=0.2,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )
        # 5% of 5000 = 250 < min_buy_in 400 → returns raw 250 (caller
        # filters with `capacity < min_buy_in`).
        cap = _capacity_for_lender(profile, 5_000, min_buy_in=400, max_buy_in=1_000)
        assert cap == 250

    def test_pct_above_max_clamps_down(self):
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.50,
            floor_anchor=1.0,
            rate_anchor=0.2,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )
        # 50% of 10_000 = 5_000 > max 1_000 → clamp to 1_000.
        cap = _capacity_for_lender(profile, 10_000, min_buy_in=400, max_buy_in=1_000)
        assert cap == 1_000

    def test_pct_in_window_returns_pct(self):
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.10,
            floor_anchor=1.0,
            rate_anchor=0.2,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )
        # 10% of 8000 = 800 in [400, 1000] → 800.
        cap = _capacity_for_lender(profile, 8_000, min_buy_in=400, max_buy_in=1_000)
        assert cap == 800


# --- Term adjustment by relationship axes ---


class TestAdjustedTerms:
    def _profile(self):
        return StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.10,
            floor_anchor=1.20,
            rate_anchor=0.30,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )

    def test_neutral_relationship_returns_anchors(self):
        floor, rate = _adjusted_terms(
            self._profile(),
            likability=0.5,
            heat=0.0,
            respect=0.5,
        )
        assert floor == 1.20
        assert rate == 0.30

    def test_high_likability_softens_terms(self):
        floor, rate = _adjusted_terms(
            self._profile(),
            likability=0.8,
            heat=0.0,
            respect=0.5,
        )
        # 1.20 - 0.05 = 1.15; 0.30 - 0.05 = 0.25
        assert floor == pytest.approx(1.15)
        assert rate == pytest.approx(0.25)

    def test_high_heat_raises_terms(self):
        floor, rate = _adjusted_terms(
            self._profile(),
            likability=0.5,
            heat=0.6,
            respect=0.5,
        )
        # 1.20 + 0.10 = 1.30; 0.30 + 0.10 = 0.40
        assert floor == pytest.approx(1.30)
        assert rate == pytest.approx(0.40)

    def test_high_respect_softens_terms(self):
        floor, rate = _adjusted_terms(
            self._profile(),
            likability=0.5,
            heat=0.0,
            respect=0.7,
        )
        # 1.20 - 0.03 = 1.17; 0.30 - 0.03 = 0.27
        assert floor == pytest.approx(1.17)
        assert rate == pytest.approx(0.27)

    def test_floor_clamped_to_min(self):
        # Anchors near 1.00 plus likability+respect trims could go below 1.00.
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.10,
            floor_anchor=1.02,
            rate_anchor=0.10,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )
        floor, rate = _adjusted_terms(
            profile,
            likability=0.8,
            heat=0.0,
            respect=0.8,
        )
        # 1.02 - 0.05 - 0.03 = 0.94 → clamp to 1.00.
        assert floor == 1.00

    def test_rate_clamped_to_zero(self):
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.10,
            floor_anchor=1.20,
            rate_anchor=0.02,
            respect_floor=-0.5,
            heat_ceiling=0.7,
        )
        floor, rate = _adjusted_terms(
            profile,
            likability=0.8,
            heat=0.0,
            respect=0.8,
        )
        # 0.02 - 0.05 - 0.03 = -0.06 → clamp to 0.00.
        assert rate == 0.00

    def test_floor_clamped_to_max(self):
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.10,
            floor_anchor=1.45,
            rate_anchor=0.30,
            respect_floor=-0.5,
            heat_ceiling=1.0,
        )
        floor, rate = _adjusted_terms(
            profile,
            likability=0.5,
            heat=0.8,
            respect=0.5,
        )
        # 1.45 + 0.10 = 1.55 → clamp to 1.50.
        assert floor == 1.50

    def test_rate_clamped_to_max(self):
        profile = StakerProfile(
            willing=True,
            max_loan_pct_of_bankroll=0.10,
            floor_anchor=1.20,
            rate_anchor=0.50,
            respect_floor=-0.5,
            heat_ceiling=1.0,
        )
        floor, rate = _adjusted_terms(
            profile,
            likability=0.5,
            heat=0.8,
            respect=0.5,
        )
        # 0.50 + 0.10 = 0.60 → clamp to 0.55.
        assert rate == 0.55


# --- Relationship hint ---


class TestRelationshipHint:
    def test_high_heat_dominates(self):
        hint = _relationship_hint(likability=0.9, heat=0.6, respect=0.9)
        assert hint == "wants their money back"

    def test_moderate_heat_watching(self):
        hint = _relationship_hint(likability=0.5, heat=0.3, respect=0.5)
        assert hint == "watching you"

    def test_high_respect_and_likability(self):
        hint = _relationship_hint(likability=0.7, heat=0.0, respect=0.8)
        assert hint == "trusts you"

    def test_high_respect_only(self):
        hint = _relationship_hint(likability=0.4, heat=0.0, respect=0.7)
        assert hint == "respects your game"

    def test_high_likability_only(self):
        hint = _relationship_hint(likability=0.7, heat=0.0, respect=0.5)
        assert hint == "friendly"

    def test_neutral_returns_empty(self):
        hint = _relationship_hint(likability=0.5, heat=0.0, respect=0.5)
        assert hint == ""


# --- Eligibility gates ---


class TestEligibility:
    def test_unwilling_lender_excluded(self):
        profiles = {
            "mime": StakerProfile(
                willing=False,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={"mime": 50_000},
        )
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "mime", "name": "Mime"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert offers == []

    def test_no_bankroll_row_excluded(self):
        # AI never sat down → no bankroll row → load_ai_bankroll_current
        # returns None → skipped.
        profiles = {
            "newbie": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={})
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "newbie", "name": "Newbie"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert offers == []

    def test_capacity_below_min_excluded(self):
        # 5% of 3000 = 150 < min 400 → excluded.
        profiles = {
            "poor": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.05,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={"poor": 3_000})
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "poor", "name": "Poor"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert offers == []

    def test_respect_below_floor_excluded(self):
        profiles = {
            "strict": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=0.3,
                heat_ceiling=0.9,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={"strict": 10_000})
        rel = _FakeRelationshipRepo(
            states={
                ("strict", "player"): _RelState(respect=0.2, heat=0.0, likability=0.5),
            }
        )
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "strict", "name": "Strict"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert offers == []

    def test_heat_above_ceiling_excluded(self):
        profiles = {
            "chilly": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.4,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={"chilly": 10_000})
        rel = _FakeRelationshipRepo(
            states={
                ("chilly", "player"): _RelState(respect=0.5, heat=0.6, likability=0.5),
            }
        )
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "chilly", "name": "Chilly"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert offers == []

    def test_no_relationship_row_treated_as_neutral(self):
        # No relationship state → neutral defaults → all gates pass for
        # this lender → qualifies.
        profiles = {
            "fresh": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={"fresh": 10_000})
        rel = _FakeRelationshipRepo(states={})
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "fresh", "name": "Fresh"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert len(offers) == 1
        # Anchor terms unmodified (neutral relationship).
        assert offers[0].floor == 1.20
        assert offers[0].rate == 0.30


# --- Sort order, count, mixed case ---


class TestSortAndCount:
    def test_offers_sorted_by_capacity_desc(self):
        profiles = {
            "small": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
            "big": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
            "mid": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={
                "small": 5_000,  # 10% = 500
                "big": 10_000,  # 10% = 1000 (clamps at max)
                "mid": 7_000,  # 10% = 700
            },
        )
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {"personality_id": "small", "name": "Small"},
                {"personality_id": "big", "name": "Big"},
                {"personality_id": "mid", "name": "Mid"},
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert [o.lender_id for o in offers] == ["big", "mid", "small"]
        assert [o.capacity for o in offers] == [1_000, 700, 500]

    def test_count_truncates_results(self):
        profiles = {
            f"l{i}": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            )
            for i in range(5)
        }
        bankrolls = {f"l{i}": 10_000 - i * 1_000 for i in range(5)}
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls=bankrolls)
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {"personality_id": f"l{i}", "name": f"L{i}"} for i in range(5)
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            count=2,
            sandbox_id="test-sandbox-1",
        )
        assert len(offers) == 2  # truncated

    def test_returns_empty_when_no_candidates(self):
        bank = _FakeBankrollRepo()
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert offers == []

    def test_mixed_eligible_and_ineligible(self):
        profiles = {
            "willing": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
            "unwilling": StakerProfile(
                willing=False,
                max_loan_pct_of_bankroll=0.10,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
            "too_poor": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.03,
                floor_anchor=1.2,
                rate_anchor=0.3,
                respect_floor=-0.5,
                heat_ceiling=0.7,
            ),
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={
                "willing": 10_000,
                "unwilling": 10_000,
                "too_poor": 5_000,  # 3% = 150 < 400 min
            },
        )
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {"personality_id": "willing", "name": "Willing"},
                {"personality_id": "unwilling", "name": "Unwilling"},
                {"personality_id": "too_poor", "name": "Too Poor"},
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert len(offers) == 1
        assert offers[0].lender_id == "willing"


# --- Output shape sanity ---


class TestOutputShape:
    def test_offer_carries_lender_id_name_and_relationship_hint(self):
        profiles = {
            "napoleon": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.08,
                floor_anchor=1.4,
                rate_anchor=0.45,
                respect_floor=-0.9,
                heat_ceiling=0.95,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={"napoleon": 20_000})
        rel = _FakeRelationshipRepo(
            states={
                ("napoleon", "player"): _RelState(respect=0.5, heat=0.5, likability=0.5),
            }
        )
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "napoleon", "name": "Napoleon"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert len(offers) == 1
        offer = offers[0]
        assert offer.lender_id == "napoleon"
        assert offer.lender_name == "Napoleon"
        assert offer.relationship_hint == "wants their money back"
        # Heat-bumped terms: floor 1.4 + 0.10 = 1.5 (at clamp); rate 0.45 + 0.10 = 0.55 (at clamp).
        assert offer.floor == 1.50
        assert offer.rate == 0.55
        # 8% of 20_000 = 1_600 → clamped to max_buy_in 1_000.
        assert offer.amount == 1_000
        assert offer.capacity == 1_000

    def test_offer_flavor_includes_name(self):
        profiles = {
            "buddha": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.15,
                floor_anchor=1.0,
                rate_anchor=0.15,
                respect_floor=-0.7,
                heat_ceiling=0.85,
            )
        }
        bank = _FakeBankrollRepo(profiles=profiles, bankrolls={"buddha": 10_000})
        rel = _FakeRelationshipRepo()
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[{"personality_id": "buddha", "name": "Buddha"}],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
        )
        assert len(offers) == 1
        assert "Buddha" in offers[0].flavor


# --- Phase 2: 7-day default cooldown (locked decision #1) -------------------


class TestDefaultCooldown:
    """Lender refuses to back the player for 7 wall-clock days after
    the player defaulted on a stake from them. Uses a real
    StakeRepository against a tempdb because the cooldown query goes
    through SQL — fake repos can't simulate it."""

    @pytest.fixture
    def stake_repo(self, tmp_path):
        from poker.repositories.schema_manager import SchemaManager
        from poker.repositories.stake_repository import StakeRepository

        db = str(tmp_path / "sponsor_cooldown.db")
        SchemaManager(db).ensure_schema()
        return StakeRepository(db)

    def _seed_defaulted_stake(
        self,
        stake_repo,
        *,
        staker_id: str,
        borrower_id: str,
        settled_at: datetime,
        stake_id: str = "prior_default",
    ) -> None:
        from cash_mode.stakes import (
            BORROWER_KIND_HUMAN,
            STAKE_FORMAT_PURE,
            STAKE_STATUS_DEFAULTED,
            STAKER_KIND_PERSONALITY,
            Stake,
        )

        stake_repo.create_stake(
            Stake(
                stake_id=stake_id,
                session_id=f"sess_{stake_id}",
                staker_id=staker_id,
                staker_kind=STAKER_KIND_PERSONALITY,
                borrower_id=borrower_id,
                borrower_kind=BORROWER_KIND_HUMAN,
                format=STAKE_FORMAT_PURE,
                principal=2_000,
                match_amount=0,
                origination_fee=0,
                cut=0.30,
                status=STAKE_STATUS_DEFAULTED,
                carry_amount=0,
                stake_tier="$50",
                created_at=settled_at,
                settled_at=settled_at,
            )
        )

    def test_recent_default_filters_lender(self, stake_repo):
        from cash_mode.sponsor_offers import LenderRejection

        # Player defaulted on Napoleon 3 days ago — well inside the
        # 7-day window. Napoleon won't surface as a sponsor offer.
        now = datetime(2026, 5, 21, 12, 0)
        self._seed_defaulted_stake(
            stake_repo,
            staker_id="napoleon",
            borrower_id="player",
            settled_at=now - timedelta(days=3),
        )

        profiles = {
            "napoleon": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.15,
                floor_anchor=1.0,
                rate_anchor=0.15,
                respect_floor=-0.7,
                heat_ceiling=0.85,
            )
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={"napoleon": 20_000},
        )
        rel = _FakeRelationshipRepo(
            {
                ("napoleon", "player"): _RelState(
                    respect=0.6,
                    heat=0.0,
                    likability=0.6,
                ),
            }
        )
        rejections: list = []
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {
                    "personality_id": "napoleon",
                    "name": "Napoleon",
                }
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
            stake_repo=stake_repo,
            stake_label="$50",
            rejections_out=rejections,
            now=now,
        )
        assert offers == []
        assert len(rejections) == 1
        assert rejections[0].lender_id == "napoleon"
        assert rejections[0].reason == "recent_default"
        assert "defaulted" in rejections[0].detail.lower()

    def test_default_outside_window_does_not_filter(self, stake_repo):
        # Default settled 10 days ago — outside the 7-day window.
        # Napoleon surfaces as a normal sponsor offer.
        now = datetime(2026, 5, 21, 12, 0)
        self._seed_defaulted_stake(
            stake_repo,
            staker_id="napoleon",
            borrower_id="player",
            settled_at=now - timedelta(days=10),
        )

        profiles = {
            "napoleon": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.15,
                floor_anchor=1.0,
                rate_anchor=0.15,
                respect_floor=-0.7,
                heat_ceiling=0.85,
            )
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={"napoleon": 20_000},
        )
        rel = _FakeRelationshipRepo(
            {
                ("napoleon", "player"): _RelState(
                    respect=0.6,
                    heat=0.0,
                    likability=0.6,
                ),
            }
        )
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {
                    "personality_id": "napoleon",
                    "name": "Napoleon",
                }
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
            stake_repo=stake_repo,
            stake_label="$50",
            now=now,
        )
        assert len(offers) == 1
        assert offers[0].lender_id == "napoleon"

    def test_other_lenders_unaffected(self, stake_repo):
        # Defaulted on Napoleon only — Buddha still offers normally.
        now = datetime(2026, 5, 21, 12, 0)
        self._seed_defaulted_stake(
            stake_repo,
            staker_id="napoleon",
            borrower_id="player",
            settled_at=now - timedelta(days=3),
        )

        profiles = {
            "napoleon": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.15,
                floor_anchor=1.0,
                rate_anchor=0.15,
                respect_floor=-0.7,
                heat_ceiling=0.85,
            ),
            "buddha": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.15,
                floor_anchor=1.0,
                rate_anchor=0.15,
                respect_floor=-0.7,
                heat_ceiling=0.85,
            ),
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={"napoleon": 20_000, "buddha": 20_000},
        )
        rel = _FakeRelationshipRepo(
            {
                ("napoleon", "player"): _RelState(
                    respect=0.6,
                    heat=0.0,
                    likability=0.6,
                ),
                ("buddha", "player"): _RelState(
                    respect=0.6,
                    heat=0.0,
                    likability=0.6,
                ),
            }
        )
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {"personality_id": "napoleon", "name": "Napoleon"},
                {"personality_id": "buddha", "name": "Buddha"},
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
            stake_repo=stake_repo,
            stake_label="$50",
            now=now,
        )
        assert len(offers) == 1
        assert offers[0].lender_id == "buddha"

    def test_no_stake_repo_disables_cooldown(self, stake_repo):
        # Backward-compat: callers that don't pass stake_repo skip the
        # cooldown entirely. Defaulted-on Napoleon STILL surfaces in
        # this mode (the cooldown is opt-in via stake_repo).
        now = datetime(2026, 5, 21, 12, 0)
        self._seed_defaulted_stake(
            stake_repo,
            staker_id="napoleon",
            borrower_id="player",
            settled_at=now - timedelta(days=3),
        )

        profiles = {
            "napoleon": StakerProfile(
                willing=True,
                max_loan_pct_of_bankroll=0.15,
                floor_anchor=1.0,
                rate_anchor=0.15,
                respect_floor=-0.7,
                heat_ceiling=0.85,
            )
        }
        bank = _FakeBankrollRepo(
            profiles=profiles,
            bankrolls={"napoleon": 20_000},
        )
        rel = _FakeRelationshipRepo(
            {
                ("napoleon", "player"): _RelState(
                    respect=0.6,
                    heat=0.0,
                    likability=0.6,
                ),
            }
        )
        # NOTE: no stake_repo / stake_label passed → cooldown disabled.
        offers = compute_personality_offers(
            player_owner_id="player",
            min_buy_in=MIN_BUY_IN,
            max_buy_in=MAX_BUY_IN,
            candidate_personalities=[
                {
                    "personality_id": "napoleon",
                    "name": "Napoleon",
                }
            ],
            bankroll_repo=bank,
            relationship_repo=rel,
            sandbox_id="test-sandbox-1",
            now=now,
        )
        assert len(offers) == 1
        assert offers[0].lender_id == "napoleon"
