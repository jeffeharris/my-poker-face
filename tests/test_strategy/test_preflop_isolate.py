"""Tests for poker/strategy/preflop_isolate.py (STRUCTURAL_PASSIVITY_PLAN Track 1)."""

import pytest

from poker.strategy.preflop_isolate import (
    ISOLATE_POSITIONS,
    build_isolation_table,
    transform_vs_open_to_isolate,
)
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.strategy_table import StrategyTable


def _prof(**probs):
    return StrategyProfile(action_probabilities=dict(probs))


class TestTransform:
    def test_shifts_call_to_raise_for_oop_defender(self):
        data = {'vs_open|HJ|UTG|TT': _prof(call=0.30, fold=0.55, raise_3x=0.15)}
        out = transform_vs_open_to_isolate(data, shift_fraction=0.7)
        p = out['vs_open|HJ|UTG|TT'].action_probabilities
        assert p['call'] == pytest.approx(0.09)
        assert p['raise_3x'] == pytest.approx(0.36)
        assert p['fold'] == pytest.approx(0.55)  # fold untouched
        assert sum(p.values()) == pytest.approx(1.0)

    def test_ip_defender_untouched(self):
        data = {'vs_open|BTN|UTG|TT': _prof(call=0.30, fold=0.55, raise_3x=0.15)}
        out = transform_vs_open_to_isolate(data, shift_fraction=0.7)
        # BTN is IP — not in ISOLATE_POSITIONS, passes through identical
        assert out['vs_open|BTN|UTG|TT'] is data['vs_open|BTN|UTG|TT']

    def test_bb_untouched(self):
        data = {'vs_open|BB|UTG|TT': _prof(call=0.30, fold=0.55, raise_3x=0.15)}
        out = transform_vs_open_to_isolate(data)
        assert out['vs_open|BB|UTG|TT'] is data['vs_open|BB|UTG|TT']

    def test_low_call_rows_untouched(self):
        # call below min_call=0.10 → leave the bottom of the range alone
        data = {'vs_open|SB|UTG|72o': _prof(call=0.05, fold=0.90, raise_3x=0.05)}
        out = transform_vs_open_to_isolate(data, min_call=0.10)
        assert out['vs_open|SB|UTG|72o'] is data['vs_open|SB|UTG|72o']

    def test_rfi_and_vs3bet_untouched(self):
        data = {
            'rfi|CO||TT': StrategyProfile(
                action_probabilities={
                    'call': 0.2,
                    'fold': 0.3,
                    'raise_2.5': 0.5,
                }
            ),
            'vs_3bet|CO|BTN|TT': _prof(call=0.4, fold=0.4, raise_3x=0.2),
        }
        out = transform_vs_open_to_isolate(data)
        assert out['rfi|CO||TT'] is data['rfi|CO||TT']
        assert out['vs_3bet|CO|BTN|TT'] is data['vs_3bet|CO|BTN|TT']

    def test_all_oop_positions_in_scope(self):
        assert set(ISOLATE_POSITIONS) == {'SB', 'HJ', 'CO'}

    def test_no_raise_action_skips(self):
        # row with call mass but no raise_3x key — nothing to shift into
        data = {'vs_open|CO|UTG|TT': _prof(call=0.40, fold=0.60)}
        out = transform_vs_open_to_isolate(data)
        assert out['vs_open|CO|UTG|TT'] is data['vs_open|CO|UTG|TT']


class TestBuildTable:
    def test_builds_new_table_sharing_postflop(self):
        pre = {'vs_open|CO|UTG|AJs': _prof(call=0.30, fold=0.55, raise_3x=0.15)}
        post = {'flop|IP|SRP|dry_high|nuts|no_draw|unopened|high': _prof(check=0.3, bet_67=0.7)}
        table = StrategyTable(pre, post)
        iso = build_isolation_table(table, shift_fraction=0.7)
        # transformed preflop
        assert iso._preflop['vs_open|CO|UTG|AJs'].action_probabilities['raise_3x'] == pytest.approx(
            0.36
        )
        # original table untouched (non-destructive)
        assert table._preflop['vs_open|CO|UTG|AJs'].action_probabilities[
            'raise_3x'
        ] == pytest.approx(0.15)
        # postflop shared unchanged
        assert iso._postflop == post
