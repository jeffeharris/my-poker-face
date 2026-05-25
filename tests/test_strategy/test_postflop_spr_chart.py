"""Tests for the low-SPR postflop chart generator + its merge at load.

generate_postflop_spr.transform_low_spr: at low SPR, commit-worthy hands
(made value or strong draw) route bet/raise → jam; air/weak give up the bluff
(bet → check unopened, raise → fold facing). load_strategy_table merges the
generated low-SPR slice so low-SPR lookups hit exact entries.
"""

import pytest

from poker.strategy.data.generate_postflop_spr import transform_low_spr
from poker.strategy.nodes import PostflopNode
from poker.strategy.strategy_table import load_strategy_table

# ── transform_low_spr ────────────────────────────────────────────────────


class TestTransformCommit:
    @pytest.mark.parametrize('made', ['nuts', 'strong_made', 'medium_made'])
    def test_made_value_bets_become_jam(self, made):
        out = transform_low_spr(
            {'bet_33': 0.5, 'bet_67': 0.3, 'check': 0.2}, made, 'no_draw', 'unopened'
        )
        assert out['jam'] == pytest.approx(0.8, abs=0.01)
        assert out['check'] == pytest.approx(0.2, abs=0.01)

    def test_strong_draw_commits_even_if_air(self):
        # made_tier 'air' but a strong draw → semi-bluff jam.
        out = transform_low_spr({'bet_67': 0.36, 'check': 0.64}, 'air', 'strong_draw', 'unopened')
        assert out['jam'] == pytest.approx(0.36, abs=0.01)

    def test_facing_bet_raises_become_jam(self):
        out = transform_low_spr(
            {'call': 0.4, 'raise_67': 0.3, 'raise_150': 0.2, 'jam': 0.1},
            'nuts',
            'no_draw',
            'facing_bet',
        )
        assert out['jam'] == pytest.approx(0.6, abs=0.01)
        assert out['call'] == pytest.approx(0.4, abs=0.01)
        assert 'raise_67' not in out and 'raise_150' not in out


class TestTransformGiveUp:
    def test_air_unopened_bets_become_check(self):
        out = transform_low_spr(
            {'bet_33': 0.184, 'bet_67': 0.061, 'check': 0.755}, 'air', 'no_draw', 'unopened'
        )
        assert out == {'check': 1.0}

    def test_air_facing_bet_bluffraise_becomes_fold(self):
        out = transform_low_spr(
            {'call': 0.15, 'fold': 0.8, 'raise_67': 0.05}, 'air', 'no_draw', 'facing_bet'
        )
        assert out['fold'] == pytest.approx(0.85, abs=0.01)
        assert out['call'] == pytest.approx(0.15, abs=0.01)
        assert 'raise_67' not in out

    @pytest.mark.parametrize('draw', ['no_draw', 'weak_draw', 'backdoor'])
    def test_weak_draws_do_not_commit(self, draw):
        # weak_made + non-strong draw is not commit-worthy → bluff folds.
        out = transform_low_spr(
            {'call': 0.6, 'fold': 0.3, 'raise_67': 0.1}, 'weak_made', draw, 'facing_bet'
        )
        assert out.get('jam', 0) == 0
        assert out['fold'] == pytest.approx(0.4, abs=0.01)


class TestTransformInvariants:
    def test_pure_passive_unchanged_shape(self):
        out = transform_low_spr({'check': 0.7, 'call': 0.3}, 'weak_made', 'no_draw', 'unopened')
        assert out == pytest.approx({'check': 0.7, 'call': 0.3}, abs=0.01)

    def test_normalized(self):
        out = transform_low_spr(
            {'bet_33': 0.461, 'bet_67': 0.134, 'check': 0.405}, 'medium_made', 'no_draw', 'unopened'
        )
        assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)


# ── merge at load ────────────────────────────────────────────────────────


class TestLowSPRMerge:
    @pytest.fixture(scope='class')
    def table(self):
        return load_strategy_table()

    def test_low_spr_entries_loaded(self, table):
        # 2160 authored high + 2160 generated low.
        assert table.postflop_size == 4320

    def test_low_spr_lookup_hits_exact(self, table):
        # Exact lookup (no fallback) returns the committed low-SPR entry.
        n = PostflopNode(
            street='flop',
            position='IP',
            pot_type='SRP',
            board_texture='dry_high',
            made_tier='nuts',
            draw_modifier='no_draw',
            facing_action='unopened',
            spr_bucket='low',
        )
        profile = table.lookup_postflop(n)
        assert profile is not None
        assert profile.action_probabilities.get('jam', 0) > 0.5

    def test_high_spr_entry_still_present(self, table):
        n = PostflopNode(
            street='flop',
            position='IP',
            pot_type='SRP',
            board_texture='dry_high',
            made_tier='nuts',
            draw_modifier='no_draw',
            facing_action='unopened',
            spr_bucket='high',
        )
        assert table.lookup_postflop(n) is not None
