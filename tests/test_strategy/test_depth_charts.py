"""Tests for depth-aware 6-max preflop chart selection.

The tiered bot was measured playing a byte-identical preflop game at
100/50/25bb (the diagnosed short-stack leak). These charts/selection make
the 6-max preflop table depth-dependent: pick the chart nearest the
effective stack (100/50/25bb buckets). HU routing is unchanged and takes
precedence; depth selection is 6-max-only for now.

See poker/strategy/data/depth_charts_README.md.
"""

import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.strategy_table import (
    StrategyTable,
    load_depth_strategy_tables,
    nearest_depth_bucket,
    DEPTH_CHART_BUCKETS,
)
from poker.tiered_bot_controller import TieredBotController


# ── nearest_depth_bucket ─────────────────────────────────────────────────

class TestNearestDepthBucket:
    def test_exact_buckets(self):
        assert nearest_depth_bucket(100) == 100
        assert nearest_depth_bucket(50) == 50
        assert nearest_depth_bucket(25) == 25

    def test_clamp_above_top(self):
        assert nearest_depth_bucket(200) == 100
        assert nearest_depth_bucket(100.1) == 100

    def test_clamp_below_bottom(self):
        # Below the shallowest bucket, clamp to it (push_fold/short_stack
        # take over the sub-15bb regime separately).
        assert nearest_depth_bucket(12) == 25
        assert nearest_depth_bucket(5) == 25

    def test_nearest_in_between(self):
        assert nearest_depth_bucket(30) == 25   # |30-25|=5 < |30-50|=20
        assert nearest_depth_bucket(45) == 50    # |45-50|=5 < |45-25|=20
        assert nearest_depth_bucket(60) == 50    # |60-50|=10 < |60-100|=40

    def test_tie_prefers_deeper(self):
        # Equidistant 37.5 between 25 and 50 → deeper (50): safer to flat
        # than to over-jam when exactly between depths.
        assert nearest_depth_bucket(37.5) == 50
        # 75 is equidistant between 50 and 100 → deeper (100).
        assert nearest_depth_bucket(75) == 100


# ── load_depth_strategy_tables ───────────────────────────────────────────

class TestLoadDepthTables:
    def test_loads_shallow_charts(self):
        tables = load_depth_strategy_tables()
        # 50 and 25 ship as generated files; 100 is the base table (absent here).
        assert set(tables) == {50, 25}
        assert 100 not in tables

    def test_tables_populated(self):
        tables = load_depth_strategy_tables()
        for depth, table in tables.items():
            assert table.size > 0, f"{depth}bb table is empty"

    def test_buckets_constant(self):
        assert DEPTH_CHART_BUCKETS == (100, 50, 25)


# ── _select_preflop_table (direct unit) ──────────────────────────────────

def _bare_controller(*, base, hu=None, depth=None):
    """Minimal controller carrying just the table attributes the selector reads."""
    with patch('poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None):
        c = TieredBotController.__new__(TieredBotController)
    c.strategy_table = base
    c.hu_strategy_table = hu
    c.depth_strategy_tables = depth or {}
    return c


class TestSelectPreflopTable:
    def setup_method(self):
        self.base = StrategyTable({})
        self.t50 = StrategyTable({})
        self.t25 = StrategyTable({})
        self.hu = StrategyTable({})

    def test_deep_uses_base(self):
        c = _bare_controller(base=self.base, depth={50: self.t50, 25: self.t25})
        table, label = c._select_preflop_table(6, 100.0)
        assert table is self.base and label == '6max@100bb'

    def test_50bb_uses_50_table(self):
        c = _bare_controller(base=self.base, depth={50: self.t50, 25: self.t25})
        table, label = c._select_preflop_table(6, 50.0)
        assert table is self.t50 and label == '6max@50bb'

    def test_25bb_uses_25_table(self):
        c = _bare_controller(base=self.base, depth={50: self.t50, 25: self.t25})
        table, label = c._select_preflop_table(6, 25.0)
        assert table is self.t25 and label == '6max@25bb'

    def test_hu_ignores_depth(self):
        """2-handed always routes to the HU chart, even at a shallow stack."""
        c = _bare_controller(base=self.base, hu=self.hu, depth={50: self.t50, 25: self.t25})
        table, label = c._select_preflop_table(2, 25.0)
        assert table is self.hu and label == 'HU'

    def test_no_depth_tables_uses_base(self):
        """Back-compat: with no depth tables, base table at every depth."""
        c = _bare_controller(base=self.base, depth={})
        table, label = c._select_preflop_table(6, 25.0)
        assert table is self.base and label == '6max'

    def test_missing_bucket_falls_back_to_base(self):
        """Selected bucket with no table loaded → base (defensive)."""
        c = _bare_controller(base=self.base, depth={50: self.t50})  # no 25
        table, _ = c._select_preflop_table(6, 25.0)
        assert table is self.base


# ── End-to-end: shallow decision uses the shallow chart ──────────────────

# Distinct sentinel open per table so the decision reveals which chart fired.
_BASE_OPEN = 'raise_2.5bb'
_T50_OPEN = 'raise_3x'
_T25_OPEN = 'raise_2.2x'


def _rfi_table(action):
    """Table where SB opens AA with a sentinel action."""
    key = PreflopNode(hand='AA', position='SB', scenario='rfi', opener_position='').key
    return StrategyTable({key: StrategyProfile(action_probabilities={action: 1.0})})


def _make_game_state(stack):
    from core.card import Card
    players = []
    for i in range(6):
        players.append(SimpleNamespace(
            name='Hero' if i == 0 else f'Opp{i}',
            stack=stack, bet=0,
            hand=(Card('A', 'h'), Card('A', 's')),
            is_human=False, is_folded=False, is_all_in=False,
            has_acted=False, last_action=None,
        ))
    positions = {
        'button': 'Hero', 'small_blind_player': 'Hero',
        'big_blind_player': 'Opp1', 'under_the_gun': 'Opp2',
        'middle_position_1': 'Opp3', 'cutoff': 'Opp4',
    }
    # Hero is SB; mark blinds.
    players[0].bet = 50
    players[1].bet = 100
    return SimpleNamespace(
        players=players, current_player_idx=0, current_player=players[0],
        current_ante=100, highest_bet=100, last_raise_amount=100,
        min_raise_amount=100, raises_this_round=0, community_cards=(),
        pot={'total': 150}, table_positions=positions,
        current_player_options=['fold', 'call', 'raise', 'all_in'],
    )


def _e2e_controller(game_state, base, depth):
    with patch('poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None):
        c = TieredBotController.__new__(TieredBotController)
    phase = SimpleNamespace(name='PRE_FLOP')
    c.player_name = 'Hero'
    c.state_machine = SimpleNamespace(game_state=game_state, current_phase=phase, phase=phase)
    c.strategy_table = base
    c.hu_strategy_table = None
    c.depth_strategy_tables = depth
    c.debug_logging = False
    c.rng = random.Random(42)
    c._deviation_profile = None
    c.psychology = None
    c.skip_personality_distortion = True
    c.opponent_model_manager = None
    c.expression_generator = None
    c.prompt_config = SimpleNamespace(strategic_reflection=False)
    c._current_hand_plans = []
    c._hand_max_bluff_likelihood = 0
    return c


class TestDepthRoutingE2E:
    """Drive a real preflop decision; the open action reveals which depth
    chart was selected (effective stack derived from stack / 100bb)."""

    def _decide(self, stack):
        depth = {50: _rfi_table(_T50_OPEN), 25: _rfi_table(_T25_OPEN)}
        c = _e2e_controller(_make_game_state(stack), _rfi_table(_BASE_OPEN), depth)
        return c._get_ai_decision(
            message='', valid_actions=['fold', 'call', 'raise', 'all_in'],
            call_amount=100,
        )['hand_strategy']

    def test_deep_stack_uses_base_chart(self):
        assert _BASE_OPEN in self._decide(stack=10000)   # 100bb

    def test_50bb_uses_50_chart(self):
        assert _T50_OPEN in self._decide(stack=5000)      # 50bb

    def test_25bb_uses_25_chart(self):
        assert _T25_OPEN in self._decide(stack=2500)      # 25bb
