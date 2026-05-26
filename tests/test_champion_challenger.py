"""Tests for the champion-vs-challenger eval harness (EVAL_HARNESS_PLAN §P0).

Pure-function tests for seat assignment + the change registry run fast; the
end-to-end matchup is a small (short) sim run whose headline assertion is chip
conservation — if the per-hand deltas don't sum to zero, the harness (or the
underlying sim) is leaking/creating chips and every bb/100 it reports is wrong.
"""

import pytest

from experiments.champion_challenger import (
    CHANGES,
    _challenger_seat_indices,
    run_cc_matchup,
)
from poker.strategy.strategy_table import load_strategy_table


class TestChallengerSeatIndices:
    def test_3v3_interleaves(self):
        assert _challenger_seat_indices(6, 3) == [0, 2, 4]

    def test_2v4_spreads(self):
        assert _challenger_seat_indices(6, 2) == [0, 3]

    def test_heads_up(self):
        assert _challenger_seat_indices(2, 1) == [0]

    @pytest.mark.parametrize("n_seats,n_chal", [(6, 0), (6, 6), (6, 7), (2, 0)])
    def test_rejects_degenerate_splits(self, n_seats, n_chal):
        with pytest.raises(ValueError):
            _challenger_seat_indices(n_seats, n_chal)


class TestChangeRegistry:
    def test_known_changes_present(self):
        # The two flavors the plan names must exist.
        assert 'multistreet' in CHANGES  # flag flavor
        assert 'low_spr' in CHANGES  # chart flavor
        assert 'three_bp' in CHANGES  # chart flavor (3BP, post lookup-tables merge)

    def test_every_change_is_well_formed(self):
        for name, spec in CHANGES.items():
            assert spec.description, name
            # Table builders are callables that produce a usable table.
            assert callable(spec.champion_table)
            assert callable(spec.challenger_table)

    @pytest.mark.parametrize("change", ['low_spr', 'three_bp'])
    def test_chart_flavor_tables_actually_differ(self, change):
        # A chart flavor must load genuinely different postflop tables (the
        # challenger has the extra authored slice), else the A/B is a silent
        # no-op.
        spec = CHANGES[change]
        champ = spec.champion_table()
        chal = spec.challenger_table()
        assert len(chal._postflop) > len(champ._postflop)

    def test_flag_flavor_shares_one_table_builder(self):
        # multistreet differs by flags, not charts.
        spec = CHANGES['multistreet']
        assert spec.champion_flags.get('enable_multistreet_context') is False
        assert spec.challenger_flags.get('enable_multistreet_context') is True


class TestMatchupConservation:
    """The harness must conserve chips: no rake, so every hand's deltas sum to
    zero. This is the load-bearing correctness check for the bb/100 it reports.
    """

    @pytest.mark.parametrize("change", ['multistreet', 'low_spr', 'three_bp'])
    def test_per_hand_deltas_sum_to_zero(self, change):
        spec = CHANGES[change]
        result = run_cc_matchup(
            change_name=change,
            archetype='Baseline',
            n_seats=6,
            n_challenger=3,
            n_hands=25,
            champion_table=spec.champion_table(),
            challenger_table=spec.challenger_table(),
            base_seed=42,
        )
        # Partition is correct: 3 challenger + 3 champion, disjoint.
        assert len(result.challenger_names) == 3
        assert len(result.champion_names) == 3
        assert not set(result.challenger_names) & set(result.champion_names)

        n_hands = len(next(iter(result.seat_deltas.values())))
        for hand_idx in range(n_hands):
            total = sum(result.seat_deltas[name][hand_idx] for name in result.seat_deltas)
            assert total == 0, f"chips not conserved on hand {hand_idx}: net {total}"

    def test_heads_up_runs(self):
        # The HU mode (1 challenger vs 1 champion) must also conserve.
        result = run_cc_matchup(
            change_name='multistreet',
            archetype='Baseline',
            n_seats=2,
            n_challenger=1,
            n_hands=20,
            champion_table=load_strategy_table(),
            challenger_table=load_strategy_table(),
            base_seed=99,
        )
        assert len(result.challenger_names) == 1
        assert len(result.champion_names) == 1
        n_hands = len(result.seat_deltas[result.challenger_names[0]])
        for hand_idx in range(n_hands):
            total = sum(result.seat_deltas[name][hand_idx] for name in result.seat_deltas)
            assert total == 0
