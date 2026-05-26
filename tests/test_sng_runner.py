"""Tests for the WTA-SNG eval runner (EVAL_HARNESS_PLAN §P1).

Pure-function tests (seat specs, work split, Wilson CI) are fast. The
end-to-end SNG tests use a small field + aggressive blind ramp so a tournament
finishes in a handful of hands; the headline assertion is **whole-tournament
chip conservation** — under winner-take-all with no rake, the lone survivor must
hold every chip dealt (N × starting_stack). If that fails, the runner (or the
engine's stack carry-over / elimination) is leaking chips.
"""

import pytest

from experiments.sng_runner import (
    _cc_seat_specs,
    _field_seat_specs,
    _split,
    _wilson,
    play_sng,
)
from poker.strategy.strategy_table import load_strategy_table

# Aggressive ramp + shallow stacks → SNGs end in ~10-25 hands (fast tests).
_FAST_BLINDS = {'growth': 2.0, 'hands_per_level': 4, 'max_blind': 0}
_START_STACK = 2000  # 20bb at bb=100
_BIG_BLIND = 100


class TestWorkSplit:
    def test_split_covers_all_sngs(self):
        chunks = _split(400, base_seed=42)
        assert sum(c for _, c in chunks) == 400

    def test_split_seeds_contiguous_no_overlap(self):
        chunks = _split(10, base_seed=100)
        seeds = []
        for start, count in chunks:
            seeds.extend(range(start, start + count))
        assert seeds == list(range(100, 110))  # contiguous, no gaps/overlap

    def test_split_handles_fewer_sngs_than_workers(self):
        chunks = _split(1, base_seed=5)
        assert sum(c for _, c in chunks) == 1


class TestSeatSpecs:
    def test_field_specs_unique_names_even_with_dupes(self):
        table = load_strategy_table()
        specs = _field_seat_specs(['Baseline', 'Baseline', 'TAG'], table, rotation=0)
        names = [s[0] for s in specs]
        assert len(names) == len(set(names))  # unique despite repeated archetype

    def test_field_rotation_changes_seat_order(self):
        table = load_strategy_table()
        a = [s[0] for s in _field_seat_specs(['Baseline', 'TAG', 'LAG'], table, 0)]
        b = [s[0] for s in _field_seat_specs(['Baseline', 'TAG', 'LAG'], table, 1)]
        assert a != b and set(a) == set(b)

    def test_cc_specs_split_is_correct(self):
        full = load_strategy_table()
        specs, challenger_names = _cc_seat_specs(
            'multistreet', n_seats=6, n_challenger=3, champion_table=full,
            challenger_table=full, archetype='Baseline',
        )
        assert len(specs) == 6
        assert len(challenger_names) == 3
        champion_names = {s[0] for s in specs} - challenger_names
        assert len(champion_names) == 3


class TestWilson:
    def test_brackets_point_estimate(self):
        p, lo, hi = _wilson(50, 100)
        assert p == 0.5 and lo < 0.5 < hi

    def test_empty(self):
        assert _wilson(0, 0) == (0.0, 0.0, 0.0)


class TestPlaySng:
    def _small_field_specs(self):
        table = load_strategy_table()
        return _field_seat_specs(['Baseline', 'TAG', 'GTO-Lite'], table, rotation=0)

    def test_ends_with_winner_holding_every_chip(self):
        specs = self._small_field_specs()
        total = len(specs) * _START_STACK
        winner, hands, final_stacks = play_sng(
            specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7, max_hands=500
        )
        assert winner is not None
        # WTA, no rake: chips are conserved across the whole tournament, so the
        # survivors hold exactly what was dealt — and at a clean finish that's
        # one player with all of it.
        assert sum(final_stacks.values()) == total
        assert final_stacks.get(winner) == total
        assert len(final_stacks) == 1

    def test_terminates_well_under_max_hands(self):
        specs = self._small_field_specs()
        _, hands, _ = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=7)
        assert 0 < hands < 200  # escalating blinds force a finish

    def test_deterministic_for_a_seed(self):
        specs = self._small_field_specs()
        r1 = play_sng(specs, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=99)
        specs2 = self._small_field_specs()
        r2 = play_sng(specs2, _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=99)
        assert r1 == r2

    def test_different_seeds_can_differ(self):
        # Not strictly guaranteed, but across several seeds the winner should
        # vary at least once — confirms the seed actually drives the SNG.
        specs_fn = self._small_field_specs
        winners = {
            play_sng(specs_fn(), _FAST_BLINDS, _START_STACK, _BIG_BLIND, sng_seed=s)[0]
            for s in range(20, 32)
        }
        assert len(winners) >= 2
