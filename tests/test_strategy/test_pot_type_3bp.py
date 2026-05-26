"""Tests for 3-bet-pot (3BP) detection + the pot_type fallback.

- preflop_raise_count: incremented by preflop raises only, survives street
  resets, resets per hand, serializes.
- _determine_pot_type maps the count to SRP/3BP.
- A 3BP lookup degrades toward SRP via the fallback ladder (the authored 3BP
  precision chart was cut — see docs/plans/SNG_RUNNER_HARDENING.md — so 3BP
  spots always ride this fallback). The *classification* stays; only the chart
  data was removed.
"""

import pytest

from poker.poker_game import initialize_game_state, setup_hand, player_raise
from poker.repositories.serialization import restore_state_from_dict
from poker.strategy.postflop_classifier import _determine_pot_type
from poker.strategy.strategy_table import StrategyTable
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.nodes import PostflopNode


def _preflop_state():
    gs = initialize_game_state(['Alice', 'Bob', 'Charlie'])
    return setup_hand(gs)  # posts blinds, deals — preflop, no community cards


# ── preflop_raise_count increment ────────────────────────────────────────

class TestPreflopRaiseCount:
    def test_starts_at_zero(self):
        gs = _preflop_state()
        assert gs.preflop_raise_count == 0
        assert len(gs.community_cards) == 0

    def test_preflop_raise_increments(self):
        gs = _preflop_state()
        gs = player_raise(gs, gs.highest_bet * 3)
        assert gs.preflop_raise_count == 1

    def test_successive_preflop_raises_accumulate(self):
        gs = _preflop_state()
        gs = player_raise(gs, gs.highest_bet * 3)   # open → 1
        gs = player_raise(gs, gs.highest_bet * 3)   # 3-bet → 2
        assert gs.preflop_raise_count == 2

    def test_postflop_raise_does_not_increment(self):
        gs = _preflop_state()
        # Simulate being on the flop: community cards present, count carried over.
        gs = gs.update(community_cards=({'rank': '2', 'suit': 'Hearts'},),
                       preflop_raise_count=1)
        gs = player_raise(gs, gs.highest_bet * 3)
        assert gs.preflop_raise_count == 1  # unchanged postflop


# ── pot_type classification ──────────────────────────────────────────────

class TestDeterminePotType:
    @pytest.mark.parametrize('raises,expected', [
        (0, 'SRP'), (1, 'SRP'), (2, '3BP'), (3, '3BP'), (5, '3BP'),
    ])
    def test_mapping(self, raises, expected):
        from types import SimpleNamespace
        assert _determine_pot_type(SimpleNamespace(preflop_raise_count=raises)) == expected

    def test_missing_attr_defaults_srp(self):
        from types import SimpleNamespace
        assert _determine_pot_type(SimpleNamespace()) == 'SRP'


# ── serialization ────────────────────────────────────────────────────────

class TestSerialization:
    def test_round_trip_preserves_count(self):
        gs = _preflop_state().update(preflop_raise_count=2)
        gs2 = restore_state_from_dict(gs.to_dict())
        assert gs2.preflop_raise_count == 2

    def test_old_save_without_field_defaults_zero(self):
        d = _preflop_state().update(preflop_raise_count=2).to_dict()
        del d['preflop_raise_count']  # an old save predating the field
        assert restore_state_from_dict(d).preflop_raise_count == 0


# ── 3BP → SRP fallback (the authored 3BP chart was cut) ──────────────────

class TestThreeBetFallback:
    def test_3bp_missing_degrades_to_srp(self):
        # A table with only an SRP entry: a 3BP lookup degrades to it, not the
        # passive default. This is the always-on path now that the authored 3BP
        # precision chart is cut — 3BP spots ride the SRP fallback.
        srp = PostflopNode(
            street='river', position='OOP', pot_type='SRP', board_texture='monotone',
            made_tier='nuts', draw_modifier='no_draw', facing_action='facing_bet',
            spr_bucket='high')
        tbp = PostflopNode(
            street='river', position='OOP', pot_type='3BP', board_texture='monotone',
            made_tier='nuts', draw_modifier='no_draw', facing_action='facing_bet',
            spr_bucket='high')
        t = StrategyTable(preflop_data={}, postflop_data={
            srp.key: StrategyProfile({'jam': 0.8, 'call': 0.2})})
        out = t.lookup_postflop_with_fallback(tbp, ['fold', 'call', 'all_in'])
        assert out.action_probabilities.get('jam', 0) == pytest.approx(0.8, abs=0.01)
