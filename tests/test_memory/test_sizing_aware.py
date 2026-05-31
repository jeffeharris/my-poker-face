"""Tests for Sizing-aware modeling Phase A.

Covers the size-binned equity tracking (`update_equity_at_bet_size` →
`sizing_polarization_score`), the live `fold_to_big_bet` tracker, the
sample-gating that holds the neutral prior, and to_dict/from_dict round-trip.
See docs/plans/SIZING_AWARE_OPPONENT_MODELING.md (Phase A).
"""

from __future__ import annotations

import pytest

from poker.memory.opponent_model import (
    OpponentTendencies,
    SIZING_BIG_BET_POT_RATIO,
    SIZING_MIN_BIN_SAMPLE,
)


class TestSizeBinnedEquity:
    def test_big_and_small_bins_are_independent(self):
        t = OpponentTendencies()
        t.update_equity_at_bet_size(0.90, bet_fraction=1.2)  # big
        t.update_equity_at_bet_size(0.85, bet_fraction=0.80)  # big
        t.update_equity_at_bet_size(0.45, bet_fraction=0.40)  # small
        t.update_equity_at_bet_size(0.55, bet_fraction=0.33)  # small
        assert t._equity_betting_big_count == 2
        assert t._equity_betting_small_count == 2
        assert t.equity_when_betting_big == pytest.approx(0.875)
        assert t.equity_when_betting_small == pytest.approx(0.50)

    def test_threshold_boundary_is_big(self):
        t = OpponentTendencies()
        t.update_equity_at_bet_size(0.7, bet_fraction=SIZING_BIG_BET_POT_RATIO)
        assert t._equity_betting_big_count == 1
        assert t._equity_betting_small_count == 0

    def test_out_of_range_inputs_are_noops(self):
        t = OpponentTendencies()
        t.update_equity_at_bet_size(1.5, bet_fraction=1.0)  # bad equity
        t.update_equity_at_bet_size(0.5, bet_fraction=-0.1)  # bad fraction
        t.update_equity_at_bet_size(0.5, bet_fraction=None)  # missing fraction
        assert t._equity_betting_big_count == 0
        assert t._equity_betting_small_count == 0


class TestPolarizationScore:
    def test_score_holds_neutral_until_both_bins_sampled(self):
        t = OpponentTendencies()
        # Only big-bin samples — score must stay at the neutral 0.0 prior.
        for _ in range(SIZING_MIN_BIN_SAMPLE + 2):
            t.update_equity_at_bet_size(0.9, bet_fraction=1.0)
        t._recalculate_stats()
        assert t.sizing_polarization_score == 0.0

    def test_polar_player_scores_positive(self):
        t = OpponentTendencies(hands_observed=50)
        for _ in range(SIZING_MIN_BIN_SAMPLE):
            t.update_equity_at_bet_size(0.90, bet_fraction=1.1)  # big = strong
            t.update_equity_at_bet_size(0.40, bet_fraction=0.4)  # small = weak
        t._recalculate_stats()
        # bets big with strength, small with air → strongly face-up
        assert t.sizing_polarization_score == pytest.approx(0.50)
        assert "face-up sizing" in t.get_summary()

    def test_balanced_player_scores_near_zero(self):
        t = OpponentTendencies()
        for _ in range(SIZING_MIN_BIN_SAMPLE):
            t.update_equity_at_bet_size(0.62, bet_fraction=1.1)
            t.update_equity_at_bet_size(0.60, bet_fraction=0.4)
        t._recalculate_stats()
        assert abs(t.sizing_polarization_score) < 0.05


class TestFoldToBigBet:
    def test_live_fold_rate(self):
        t = OpponentTendencies()
        for _ in range(7):
            t.update_fold_to_big_bet(folded=True)
        for _ in range(3):
            t.update_fold_to_big_bet(folded=False)
        assert t._big_bet_faced_count == 10
        assert t.fold_to_big_bet == pytest.approx(0.7)

    def test_overfolder_surfaces_in_description(self):
        t = OpponentTendencies(hands_observed=50)
        for _ in range(8):
            t.update_fold_to_big_bet(folded=True)
        assert "over-folds to big bets" in t.get_summary()


class TestSerializationRoundTrip:
    def test_round_trip_preserves_sizing_state(self):
        t = OpponentTendencies()
        for _ in range(SIZING_MIN_BIN_SAMPLE):
            t.update_equity_at_bet_size(0.88, bet_fraction=1.2)
            t.update_equity_at_bet_size(0.42, bet_fraction=0.35)
        for _ in range(6):
            t.update_fold_to_big_bet(folded=True)
        t._recalculate_stats()

        restored = OpponentTendencies.from_dict(t.to_dict())
        assert restored._equity_betting_big_count == t._equity_betting_big_count
        assert restored._equity_betting_small_count == t._equity_betting_small_count
        assert restored.equity_when_betting_big == pytest.approx(t.equity_when_betting_big)
        assert restored.sizing_polarization_score == pytest.approx(t.sizing_polarization_score)
        assert restored.fold_to_big_bet == pytest.approx(t.fold_to_big_bet)
        assert restored._big_bet_faced_count == t._big_bet_faced_count

    def test_legacy_record_defaults_to_neutral_priors(self):
        # An old serialized record without any sizing fields.
        restored = OpponentTendencies.from_dict({'hands_observed': 50})
        assert restored.equity_when_betting_big == 0.5
        assert restored.equity_when_betting_small == 0.5
        assert restored.sizing_polarization_score == 0.0
        assert restored.fold_to_big_bet == 0.5
        assert restored._big_bet_faced_count == 0
