"""Tests for the sponsor archetype pool + offer generation.

Covers:
  - Each archetype produces a valid amount within [min_buy_in, max_buy_in]
    at every stake tier.
  - `compute_offers_for_table` returns the requested count of distinct
    offers (no duplicates across the 3 sampled).
  - `compute_offers_for_table` is deterministic with a seeded rng.
  - `offer_for_archetype` round-trips id → SponsorOffer and rejects
    unknown ids.
  - `find_archetype` returns None for unknown ids.
"""

from __future__ import annotations

import random

import pytest

from cash_mode.sponsor_offers import (
    SPONSOR_ARCHETYPES,
    compute_offers_for_table,
    find_archetype,
    offer_for_archetype,
)

# Stake-ladder windows (40 BB min, 100 BB max), one entry per tier.
STAKES = [
    ("$2", 80, 200),
    ("$10", 400, 1_000),
    ("$50", 2_000, 5_000),
    ("$200", 8_000, 20_000),
    ("$1000", 40_000, 100_000),
]


class TestArchetypeAmounts:
    @pytest.mark.parametrize("label,mn,mx", STAKES)
    def test_every_archetype_within_window(self, label, mn, mx):
        for arch in SPONSOR_ARCHETYPES:
            offer = offer_for_archetype(arch.id, mn, mx)
            assert offer is not None
            assert mn <= offer.amount <= mx, (
                f"{arch.id} at {label}: amount {offer.amount} " f"outside window [{mn}, {mx}]"
            )

    def test_friendly_boost_is_min_buy_in(self):
        offer = offer_for_archetype("friendly_boost", 400, 1_000)
        assert offer.amount == 400

    def test_whale_backer_is_max_buy_in(self):
        offer = offer_for_archetype("whale_backer", 400, 1_000)
        assert offer.amount == 1_000

    def test_loan_shark_has_predatory_floor(self):
        # Defining trait of Loan Shark: floor > 1.0 (must repay more
        # than principal before any split). Locks the design intent.
        offer = offer_for_archetype("loan_shark", 400, 1_000)
        assert offer.floor > 1.0

    def test_the_premium_has_zero_cut(self):
        # Defining trait of The Premium: pay a high floor, keep all
        # the upside (rate == 0). Locks the design intent.
        offer = offer_for_archetype("the_premium", 400, 1_000)
        assert offer.rate == 0.0


class TestComputeOffersForTable:
    def test_returns_three_distinct_by_default(self):
        offers = compute_offers_for_table(400, 1_000)
        assert len(offers) == 3
        archetype_ids = [o.archetype_id for o in offers]
        assert len(set(archetype_ids)) == 3

    def test_deterministic_with_seeded_rng(self):
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        offers_a = compute_offers_for_table(400, 1_000, rng=rng_a)
        offers_b = compute_offers_for_table(400, 1_000, rng=rng_b)
        assert [o.archetype_id for o in offers_a] == [o.archetype_id for o in offers_b]

    def test_count_can_be_overridden(self):
        offers = compute_offers_for_table(400, 1_000, count=6)
        assert len(offers) == 6  # the full pool

    def test_count_exceeding_pool_raises(self):
        with pytest.raises(ValueError):
            compute_offers_for_table(400, 1_000, count=99)


class TestArchetypeLookup:
    def test_find_archetype_returns_known(self):
        arch = find_archetype("loan_shark")
        assert arch is not None
        assert arch.name == "Loan Shark"

    def test_find_archetype_returns_none_for_unknown(self):
        assert find_archetype("totally_fake") is None

    def test_offer_for_archetype_rejects_unknown(self):
        assert offer_for_archetype("totally_fake", 400, 1_000) is None
