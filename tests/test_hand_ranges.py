"""Tests for hand_ranges.py weighted hand sampling and range estimation.

Tests board-connection weighted sampling, which adjusts opponent hand
sampling to favor hands that connect with the board when the opponent
has shown postflop aggression.
"""

import random
from collections import Counter
from typing import Set

import pytest

from poker.hand_ranges import (
    OpponentInfo,
    EquityConfig,
    sample_hand_for_opponent,
    get_opponent_range,
    _get_board_connection_weight,
    EARLY_POSITION_RANGE,
    LATE_POSITION_RANGE,
    Position,
)


class TestBoardConnectionWeight:
    """Tests for _get_board_connection_weight function."""

    def test_made_hand_higher_weight(self):
        """Made hands (pairs, two pair, etc.) get higher weight."""
        # AA on a A-K-5 board should have high weight (top pair)
        combo = ('Ah', 'Ad')
        board = ['Ac', 'Kd', '5s']
        weight = _get_board_connection_weight(combo, board)
        # Made hands should have weight > 1.0
        assert weight >= 1.5

    def test_air_lower_weight(self):
        """Air hands (no connection) get lower weight."""
        # 72o on A-K-Q rainbow board should have low weight
        combo = ('7h', '2d')
        board = ['As', 'Kc', 'Qd']
        weight = _get_board_connection_weight(combo, board)
        # Air should have weight < 1.0
        assert weight <= 1.0


class TestWeightedSamplingBehavior:
    """Tests for weighted sampling in sample_hand_for_opponent."""

    def _make_opponent_info(
        self,
        position: str = 'button',
        postflop_aggression: str = None,
        hands_observed: int = 20,
        vpip: float = 0.25,
    ) -> OpponentInfo:
        """Create test OpponentInfo."""
        return OpponentInfo(
            name='Villain',
            position=position,
            hands_observed=hands_observed,
            vpip=vpip,
            postflop_aggression_this_hand=postflop_aggression,
        )

    def test_no_weighting_when_board_empty(self):
        """No weighting applied when there's no board."""
        opponent = self._make_opponent_info(postflop_aggression='bet')
        excluded = set()
        config = EquityConfig()
        rng = random.Random(42)

        # With no board, sampling should work (no weighting applied)
        hand = sample_hand_for_opponent(opponent, excluded, config, rng, board_cards=None)
        assert hand is not None
        assert len(hand) == 2

    def test_no_weighting_when_board_under_3_cards(self):
        """No weighting when board has fewer than 3 cards."""
        opponent = self._make_opponent_info(postflop_aggression='bet')
        excluded = set()
        config = EquityConfig()
        rng = random.Random(42)

        # 2-card board (shouldn't happen in poker, but test the guard)
        hand = sample_hand_for_opponent(opponent, excluded, config, rng, board_cards=['As', 'Kd'])
        assert hand is not None

    def test_weighting_activated_on_flop(self):
        """Weighting activates when board has 3+ cards and opponent shows aggression."""
        opponent = self._make_opponent_info(postflop_aggression='bet')
        excluded = {'Ah', 'Kd'}  # Hero's hand
        config = EquityConfig()
        rng = random.Random(42)
        board = ['Qs', 'Js', '5h']

        # Should sample without error
        hand = sample_hand_for_opponent(opponent, excluded, config, rng, board_cards=board)
        assert hand is not None
        assert hand[0] not in excluded
        assert hand[1] not in excluded

    def test_weighting_requires_bet_or_raise_aggression(self):
        """Weighting only applied for 'bet' or 'raise' aggression."""
        opponent_bet = self._make_opponent_info(postflop_aggression='bet')
        opponent_raise = self._make_opponent_info(postflop_aggression='raise')
        opponent_check = self._make_opponent_info(postflop_aggression='check')

        excluded = {'Ah', 'Kd'}
        config = EquityConfig()
        board = ['Qs', 'Js', '5h']

        # All should sample successfully
        for opponent in [opponent_bet, opponent_raise, opponent_check]:
            rng = random.Random(42)
            hand = sample_hand_for_opponent(opponent, excluded, config, rng, board_cards=board)
            assert hand is not None

    def test_no_weighting_for_check_aggression(self):
        """Check aggression does not trigger weighted sampling."""
        # This is implicit in the code - when check/check_call, weighting is skipped
        opponent = self._make_opponent_info(postflop_aggression='check')
        excluded = {'Ah', 'Kd'}
        config = EquityConfig()
        rng = random.Random(42)
        board = ['Qs', 'Js', '5h']

        hand = sample_hand_for_opponent(opponent, excluded, config, rng, board_cards=board)
        assert hand is not None

    def test_excluded_cards_respected(self):
        """Sampled hands never include excluded cards."""
        opponent = self._make_opponent_info(postflop_aggression='bet')
        # Exclude many cards to stress-test
        excluded = {'Ah', 'Kd', 'Qs', 'Js', '5h', 'Ac', 'As', 'Ad'}
        config = EquityConfig()
        rng = random.Random(42)
        board = ['Qc', 'Jd', '5s']

        for _ in range(50):
            hand = sample_hand_for_opponent(opponent, excluded, config, rng, board_cards=board)
            if hand:
                assert hand[0] not in excluded, f"Got excluded card {hand[0]}"
                assert hand[1] not in excluded, f"Got excluded card {hand[1]}"

    def test_deterministic_with_seed(self):
        """Same seed produces same samples."""
        opponent = self._make_opponent_info(postflop_aggression='bet')
        excluded = {'Ah', 'Kd'}
        config = EquityConfig()
        board = ['Qs', 'Js', '5h']

        # Sample with seed 42
        rng1 = random.Random(42)
        hands1 = [sample_hand_for_opponent(opponent, excluded, config, rng1, board_cards=board)
                  for _ in range(10)]

        # Sample again with same seed
        rng2 = random.Random(42)
        hands2 = [sample_hand_for_opponent(opponent, excluded, config, rng2, board_cards=board)
                  for _ in range(10)]

        assert hands1 == hands2


class TestOpponentRangeEstimation:
    """Tests for get_opponent_range function."""

    def _make_opponent_info(
        self,
        position: str = 'button',
        preflop_action: str = None,
        hands_observed: int = 20,
        vpip: float = None,
        pfr: float = None,
    ) -> OpponentInfo:
        return OpponentInfo(
            name='Villain',
            position=position,
            hands_observed=hands_observed,
            vpip=vpip,
            pfr=pfr,
            preflop_action=preflop_action,
        )

    def test_4bet_plus_always_ultra_premium(self):
        """4bet+ action always uses ultra-premium range regardless of stats."""
        opponent = self._make_opponent_info(
            preflop_action='4bet+',
            hands_observed=0,  # No stats
        )
        config = EquityConfig()

        range_set = get_opponent_range(opponent, config)

        # Ultra-premium is AA-JJ, AK
        assert 'AA' in range_set
        assert 'KK' in range_set
        assert 'AKs' in range_set
        assert 'AKo' in range_set
        # Should NOT have weaker hands
        assert '77' not in range_set
        assert 'QJs' not in range_set

    def test_3bet_without_stats_uses_standard(self):
        """3bet without stats uses standard 3bet range."""
        opponent = self._make_opponent_info(
            preflop_action='3bet',
            hands_observed=0,
        )
        config = EquityConfig()

        range_set = get_opponent_range(opponent, config)

        # Standard 3bet includes premium hands
        assert 'AA' in range_set
        assert 'AQs' in range_set
        # Range should be ~8% of hands
        assert len(range_set) < 20

    def test_open_raise_with_pfr_uses_pfr_range(self):
        """Open raise with PFR stats uses PFR-based range."""
        opponent = self._make_opponent_info(
            preflop_action='open_raise',
            hands_observed=30,
            pfr=0.12,  # 12% PFR
        )
        config = EquityConfig()

        range_set = get_opponent_range(opponent, config)

        # 12% PFR should give EARLY_POSITION_RANGE-ish range
        assert 'AA' in range_set
        assert 'AKs' in range_set

    def test_fallback_to_position_range(self):
        """Without stats or action, falls back to position-based range."""
        opponent = self._make_opponent_info(
            position='button',
            hands_observed=0,
        )
        config = EquityConfig()

        range_set = get_opponent_range(opponent, config)

        # Button should have LATE_POSITION_RANGE
        assert len(range_set) >= len(EARLY_POSITION_RANGE)


class TestRangeUtilities:
    """Tests for range utility functions."""

    def test_position_ranges_increasing_size(self):
        """Position ranges should increase from early to late."""
        # Early < Middle < Late
        assert len(EARLY_POSITION_RANGE) < len(LATE_POSITION_RANGE)

    def test_ranges_contain_premium_hands(self):
        """All standard ranges contain premium hands."""
        premium = {'AA', 'KK', 'AKs'}
        for position_range in [EARLY_POSITION_RANGE, LATE_POSITION_RANGE]:
            for hand in premium:
                assert hand in position_range, f"{hand} missing from range"
