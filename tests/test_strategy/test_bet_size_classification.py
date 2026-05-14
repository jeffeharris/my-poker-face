"""Tests for poker/strategy/bet_size_classification.py (plan §4)."""

import pytest

from poker.strategy.bet_size_classification import (
    BUCKET_JAM,
    BUCKET_LARGE,
    BUCKET_MEDIUM,
    BUCKET_SMALL,
    BetSizeClassification,
    classify_bet_size,
    classify_bet_size_bucket,
    required_equity,
)


class TestRequiredEquity:
    """The pot-odds formula: call / (pot + 2*call)."""

    def test_one_third_pot_bet_is_20_percent(self):
        # Pot 100, villain bets 33 → hero calls 33. final pot=100+66=166.
        # required = 33/166 ≈ 0.199
        assert required_equity(33, 100) == pytest.approx(33 / 166, rel=1e-3)
        assert required_equity(33, 100) < 0.20

    def test_half_pot_bet_is_25_percent(self):
        # Pot 100, villain bets 50 → required = 50/200 = 0.25
        assert required_equity(50, 100) == pytest.approx(0.25, rel=1e-6)

    def test_pot_size_bet_is_33_percent(self):
        # Pot 100, villain bets 100 → required = 100/300 = 0.333
        assert required_equity(100, 100) == pytest.approx(1 / 3, rel=1e-6)

    def test_2x_pot_bet_is_40_percent(self):
        # Pot 100, villain bets 200 → required = 200/500 = 0.40
        assert required_equity(200, 100) == pytest.approx(0.40, rel=1e-6)

    def test_huge_bet_asymptotes_to_50_percent(self):
        # Pot 100, villain bets 1_000_000 → ~0.49995
        req = required_equity(1_000_000, 100)
        assert req < 0.50
        assert req > 0.49

    def test_zero_call_returns_zero(self):
        assert required_equity(0, 100) == 0.0

    def test_negative_pot_defensive_returns_zero(self):
        # Defensive — should not crash, return 0.0
        assert required_equity(50, -100) == 0.0


class TestBucketBoundaries:
    """Per plan §4: small ≤20%, medium 20-35%, large 35-50%, jam >50% or all-in."""

    def test_one_third_pot_is_small(self):
        # required ≈ 19.9% → small
        assert classify_bet_size_bucket(33, 100) == BUCKET_SMALL

    def test_half_pot_is_medium(self):
        # required = 25% → medium
        assert classify_bet_size_bucket(50, 100) == BUCKET_MEDIUM

    def test_pot_sized_bet_is_medium(self):
        # required = 33.3% → medium (just under 35%)
        assert classify_bet_size_bucket(100, 100) == BUCKET_MEDIUM

    def test_1_5x_pot_bet_is_large(self):
        # pot 100, bet 150 → required = 150/400 = 37.5% → large
        assert classify_bet_size_bucket(150, 100) == BUCKET_LARGE

    def test_2x_pot_bet_is_large(self):
        # required = 40% → large
        assert classify_bet_size_bucket(200, 100) == BUCKET_LARGE

    def test_5x_pot_bet_is_large(self):
        # required = 500/1100 ≈ 45.5% → large (just under 50%)
        assert classify_bet_size_bucket(500, 100) == BUCKET_LARGE

    def test_50x_pot_bet_is_jam(self):
        # required = 5000/10100 ≈ 49.5% → still large; at very large bets
        # the bucket approaches but doesn't cross 50%. Use a true jam
        # via facing_all_in flag instead.
        req = required_equity(5000, 100)
        if req > 0.50:
            assert classify_bet_size_bucket(5000, 100) == BUCKET_JAM
        else:
            assert classify_bet_size_bucket(5000, 100) == BUCKET_LARGE

    def test_facing_all_in_is_jam_regardless_of_price(self):
        # Even a cheap all-in (rare — short villain into big pot) is 'jam'
        # because the *kind* of decision changes.
        assert classify_bet_size_bucket(10, 1000, facing_all_in=True) == BUCKET_JAM
        assert classify_bet_size_bucket(500, 100, facing_all_in=True) == BUCKET_JAM

    def test_no_bet_to_face_returns_none(self):
        assert classify_bet_size_bucket(0, 100) is None

    def test_exact_20_percent_is_small(self):
        # required = 20% → small (boundary is inclusive on small)
        # call/(pot+2*call) = 0.20 → call = 0.20*pot + 0.40*call → 0.60*call = 0.20*pot
        # → pot = 3*call. So call=20, pot=60 → required = 20/100 = 0.20
        assert required_equity(20, 60) == pytest.approx(0.20)
        assert classify_bet_size_bucket(20, 60) == BUCKET_SMALL

    def test_exact_35_percent_is_medium(self):
        # required = 35% → medium (boundary inclusive on medium side)
        # 0.35 = call/(pot+2*call) → pot = (1/0.35 - 2)*call ≈ 0.857*call
        # call=350, pot=300 → required = 350/(300+700) = 350/1000 = 0.35
        assert required_equity(350, 300) == pytest.approx(0.35)
        assert classify_bet_size_bucket(350, 300) == BUCKET_MEDIUM


class TestClassifyBetSizeDataclass:
    """The full BetSizeClassification dataclass."""

    def test_all_fields_populated_for_bet(self):
        result = classify_bet_size(100, 100)
        assert isinstance(result, BetSizeClassification)
        assert result.bucket == BUCKET_MEDIUM
        assert result.required_equity == pytest.approx(1 / 3, rel=1e-6)
        assert result.bet_size_pot_ratio == pytest.approx(1.0)
        assert result.facing_all_in is False

    def test_all_in_jam_classification(self):
        result = classify_bet_size(50, 200, facing_all_in=True)
        assert result.bucket == BUCKET_JAM
        assert result.facing_all_in is True

    def test_no_bet_to_face(self):
        result = classify_bet_size(0, 100)
        assert result.bucket is None
        assert result.required_equity == 0.0
        assert result.bet_size_pot_ratio == 0.0

    def test_dataclass_is_frozen(self):
        result = classify_bet_size(100, 100)
        with pytest.raises(Exception):  # FrozenInstanceError
            result.bucket = BUCKET_SMALL


class TestBucketConsistencyAcrossPriceGrid:
    """Sanity-check that the bucket assignments form a monotonic sequence:
    larger bet_size_pot_ratio → larger (or equal) bucket label."""

    BUCKET_ORDER = [BUCKET_SMALL, BUCKET_MEDIUM, BUCKET_LARGE, BUCKET_JAM]

    def test_buckets_are_monotonic_in_bet_size(self):
        pot = 100
        prev_idx = -1
        for call in [10, 25, 33, 50, 75, 100, 150, 200, 500, 5000]:
            bucket = classify_bet_size_bucket(call, pot)
            assert bucket is not None
            idx = self.BUCKET_ORDER.index(bucket)
            assert idx >= prev_idx, (
                f'Non-monotonic at call={call}: bucket={bucket} '
                f'after prev_idx={prev_idx}'
            )
            prev_idx = idx
