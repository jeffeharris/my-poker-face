#!/usr/bin/env python3
"""Unit tests for the preflop leak drill engine.

Uses the real solver charts (available in-container) — the drill must grade
against the same reference the leak finder uses.
"""

import random

from flask_app.services.coach_drill import (
    DRILL_PLAYERS,
    grade_drill_answer,
    pick_drill_leak,
    sample_drill_spots,
)


class TestSampleSpots:
    def test_samples_gradeable_spots(self):
        spots = sample_drill_spots('rfi', 'SB', n=10, rng=random.Random(1))
        assert len(spots) == 10
        # Every served spot must grade (no None) — that's the sampling contract.
        for s in spots:
            assert s['scenario'] == 'rfi' and s['position'] == 'SB'
            assert grade_drill_answer('rfi', 'SB', s['hand'], 'fold') is not None

    def test_distinct_hands(self):
        spots = sample_drill_spots('rfi', 'BTN', n=12, rng=random.Random(2))
        hands = [s['hand'] for s in spots]
        assert len(hands) == len(set(hands))


class TestGrading:
    def test_raising_an_open_is_good(self):
        # KQs from the SB: the chart opens it ~90% → raising is 'good'.
        g = grade_drill_answer('rfi', 'SB', 'KQs', 'raise')
        assert g['verdict'] == 'good'
        assert g['primary_action'] == 'raise'

    def test_limping_an_open_is_a_leak(self):
        # Calling (limping) where the chart raises-or-folds → 'leak'.
        g = grade_drill_answer('rfi', 'SB', 'KQs', 'call')
        assert g['verdict'] == 'leak'
        assert g['your_freq'] < 0.10

    def test_folding_trash_is_good(self):
        g = grade_drill_answer('rfi', 'UTG', '72o', 'fold')
        assert g['verdict'] == 'good'

    def test_opening_trash_from_utg_is_a_leak(self):
        g = grade_drill_answer('rfi', 'UTG', '72o', 'raise')
        assert g['verdict'] == 'leak'

    def test_invalid_action_returns_none(self):
        assert grade_drill_answer('rfi', 'SB', 'KQs', 'mystery') is None

    def test_chart_freq_shape(self):
        g = grade_drill_answer('rfi', 'SB', 'KQs', 'raise')
        assert set(g['chart_freq']) == {'fold', 'call', 'raise'}


class TestPickLeak:
    def test_prefers_hand_then_spot(self):
        leak_set = {
            'by_hand': {('rfi', 'CO', 'Q7o'): {'kind': 'too_loose'}},
            'by_spot': {('rfi', 'SB'): {'kind': 'limp'}},
        }
        pick = pick_drill_leak(leak_set)
        assert pick['scenario'] == 'rfi' and pick['position'] == 'CO'

    def test_falls_back_to_spot(self):
        leak_set = {'by_hand': {}, 'by_spot': {('rfi', 'SB'): {'kind': 'limp'}}}
        assert pick_drill_leak(leak_set)['kind'] == 'limp'

    def test_none_when_empty(self):
        assert pick_drill_leak({'by_hand': {}, 'by_spot': {}}) is None
