"""Unit tests for blind squeeze-defense (VS_SQUEEZE_DEFENSE_HANDOFF).

Two layers, tested in isolation:
  - `_apply_vs_squeeze_defense`: the value-floor / tiered-widen continue logic + the
    flag / knob / scenario / position / chart-miss / fold-base gates (with
    `_squeezer_width_read` patched to a fixed VPIP).
  - `_squeezer_width_read`: the last-raiser detection + VPIP read (stubbed game state
    + opponent model).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from poker.strategy.nodes import PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.tiered_bot_controller import TieredBotController


def _controller(knob=0.85):
    with patch('poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None):
        c = TieredBotController.__new__(TieredBotController)
    c.player_name = 'Hero'
    c.vs_squeeze_defense = knob
    c._last_pipeline_snapshot = {}
    c.opponent_model_manager = None
    return c


def _squeeze_node(hand='AA', position='BB'):
    # opener_position for vs_squeeze is the composite {opener}_vs_{squeezer}.
    return PreflopNode(
        hand=hand, position=position, scenario='vs_squeeze', opener_position='UTG_vs_BTN'
    )


_FOLD = StrategyProfile(action_probabilities={'fold': 1.0})


# ── _apply_vs_squeeze_defense: continue logic + gates (read patched) ──────────


class TestApplyVsSqueezeDefense:
    def _apply(self, controller, node, strat=_FOLD, vpip=0.70, flag=True, source='miss'):
        with (
            patch('poker.tiered_bot_controller._vs_squeeze_defense_enabled', return_value=flag),
            patch.object(TieredBotController, '_squeezer_width_read', return_value=vpip),
        ):
            return controller._apply_vs_squeeze_defense(
                strat, node, game_state=None, player_idx=0, chart_lookup_source=source
            )

    def test_value_floor_continues_even_vs_no_read(self):
        # AA is tier-0 (the floor) — continues regardless of the read (vpip=None).
        out = self._apply(_controller(), _squeeze_node('AA'), vpip=None)
        assert out.action_probabilities == {'call': 1.0}

    def test_value_floor_continues_vs_tight_squeeze(self):
        out = self._apply(_controller(), _squeeze_node('KK'), vpip=0.18)
        assert out.action_probabilities == {'call': 1.0}

    def test_marginal_hand_folds_vs_tight_squeeze(self):
        # 88 is tier-3 (maniac only) — vs a tight squeezer it stays folded.
        out = self._apply(_controller(), _squeeze_node('88'), vpip=0.18)
        assert out.action_probabilities == {'fold': 1.0}

    def test_widens_to_tier3_vs_maniac(self):
        # 88 continues only vs a wide/maniac squeezer (vpip ≥ 0.60) with a high knob.
        out = self._apply(_controller(knob=0.85), _squeeze_node('88'), vpip=0.70)
        assert out.action_probabilities == {'call': 1.0}

    def test_low_knob_keeps_widen_shallow(self):
        # weak_reg knob 0.30 vs a maniac → max_tier=round(3*0.3)=1, so 88 (tier-3)
        # still folds even though the squeezer is wide.
        out = self._apply(_controller(knob=0.30), _squeeze_node('88'), vpip=0.70)
        assert out.action_probabilities == {'fold': 1.0}

    def test_low_knob_still_continues_floor(self):
        # The floor (tier-0) survives any knob>0.
        out = self._apply(_controller(knob=0.30), _squeeze_node('QQ'), vpip=0.70)
        assert out.action_probabilities == {'call': 1.0}

    def test_no_op_when_flag_off(self):
        out = self._apply(_controller(), _squeeze_node('AA'), flag=False)
        assert out.action_probabilities == {'fold': 1.0}

    def test_no_op_when_knob_zero(self):
        out = self._apply(_controller(knob=0.0), _squeeze_node('AA'))
        assert out.action_probabilities == {'fold': 1.0}

    def test_no_op_off_the_blinds(self):
        # A BTN squeeze spot has a chart node — not the over-fold case.
        out = self._apply(_controller(), _squeeze_node('AA', position='BTN'))
        assert out.action_probabilities == {'fold': 1.0}

    def test_no_op_when_chart_hit(self):
        # Don't stomp a real squeeze node (source='hit'/'squeeze_degrade').
        out = self._apply(_controller(), _squeeze_node('AA'), source='hit')
        assert out.action_probabilities == {'fold': 1.0}
        out2 = self._apply(_controller(), _squeeze_node('AA'), source='squeeze_degrade')
        assert out2.action_probabilities == {'fold': 1.0}

    def test_no_op_outside_vs_squeeze(self):
        node = PreflopNode(hand='AA', position='BB', scenario='vs_3bet', opener_position='BTN')
        out = self._apply(_controller(), node)
        assert out.action_probabilities == {'fold': 1.0}

    def test_no_op_when_base_not_pure_fold(self):
        # Defensive: if the base isn't a conservative fold, leave it alone.
        mixed = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        out = self._apply(_controller(), _squeeze_node('AA'), strat=mixed)
        assert out.action_probabilities == {'fold': 0.5, 'call': 0.5}


# ── _squeezer_width_read: last-raiser detection + VPIP read ───────────────────


def _player(name, bet=0, stack=1000, folded=False):
    return SimpleNamespace(name=name, bet=bet, stack=stack, is_folded=folded)


def _manager_for(name, *, hands=40, vpip=0.55):
    tend = SimpleNamespace(hands_observed=hands, vpip_per_voluntary_opportunity=vpip)
    model = SimpleNamespace(tendencies=tend)
    captured = {}

    def get_model(hero, villain):
        captured['villain'] = villain
        return model

    return SimpleNamespace(get_model=get_model, _captured=captured)


def _squeeze_state():
    """Hero=BB@idx0 facing an UTG open (3bb) + BTN squeeze (9bb); SB folded."""
    players = [
        _player('Hero', bet=100),  # BB
        _player('Opener', bet=300),  # UTG open 3bb
        _player('Squeezer', bet=900),  # BTN squeeze 9bb (largest live bet)
        _player('Folder', bet=0, folded=True),
    ]
    return SimpleNamespace(players=players)


class TestSqueezerWidthRead:
    def test_reads_largest_live_bet(self):
        c = _controller()
        mgr = _manager_for('Squeezer', vpip=0.62)
        c.opponent_model_manager = mgr
        gs = _squeeze_state()
        assert c._squeezer_width_read(gs, 0) == pytest.approx(0.62)
        assert mgr._captured['villain'] == 'Squeezer'  # the 3-bettor, not the opener

    def test_insufficient_sample_returns_none(self):
        c = _controller()
        c.opponent_model_manager = _manager_for('Squeezer', hands=5)
        assert c._squeezer_width_read(_squeeze_state(), 0) is None

    def test_no_manager_returns_none(self):
        c = _controller()  # opponent_model_manager = None
        assert c._squeezer_width_read(_squeeze_state(), 0) is None

    def test_vpip_clamped(self):
        c = _controller()
        c.opponent_model_manager = _manager_for('Squeezer', vpip=1.4)
        assert c._squeezer_width_read(_squeeze_state(), 0) == 1.0
