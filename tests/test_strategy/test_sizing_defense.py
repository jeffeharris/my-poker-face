"""Tests for Phase B sizing defense (SIZING_AWARE_OPPONENT_MODELING.md §B).

Covers the pure call→fold transform (`compute_sizing_defense_strategy`): the
damping, envelope clamp, no-op when there's no call mass, raise/check mass
preservation, and ablation. The controller-level gating (face-up read maturity,
big-bet threshold, default-off, defer-on-prior) is exercised separately via the
resolver/apply path; here we lock the math + vocabulary that produces the
distribution.
"""

from __future__ import annotations

import pytest

from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import (
    DEFAULT_SIZING_DEFENSE_CALL_MULTIPLIER,
    compute_sizing_defense_strategy,
    proportional_call_multiplier,
)


class TestProportionalDampener:
    """The retention multiplier scales with how face-up the read is."""

    def _m(self, polar, floor=0.55):
        return proportional_call_multiplier(
            polar, min_polar=0.15, full_polar=0.40, floor=floor
        )

    def test_no_effect_at_threshold(self):
        # At the gate, a barely-face-up read keeps the full call range (mult 1.0).
        assert self._m(0.15) == pytest.approx(1.0)

    def test_floor_at_full_face_up(self):
        # At/above full_polar, saturates to the configured floor.
        assert self._m(0.40) == pytest.approx(0.55)
        assert self._m(0.80) == pytest.approx(0.55)  # clamped, never below floor

    def test_monotonic_ramp(self):
        # Strictly decreasing between threshold and full.
        a, b, c = self._m(0.20), self._m(0.28), self._m(0.36)
        assert 1.0 > a > b > c > 0.55

    def test_midpoint(self):
        # polar 0.275 = halfway → mult halfway between 1.0 and 0.55 = 0.775.
        assert self._m(0.275) == pytest.approx(0.775, abs=1e-6)

    def test_degenerate_span_returns_floor(self):
        # full_polar <= min_polar → flat floor (no ramp).
        assert proportional_call_multiplier(
            0.5, min_polar=0.4, full_polar=0.4, floor=0.55
        ) == pytest.approx(0.55)


def _call(strategy, **kw):
    # Default polar_score = full_polar (0.40) → proportional multiplier saturates
    # to the floor, so these "transform" tests exercise the max-effect behavior;
    # the ramp itself is covered in TestProportionalDampener.
    kw.setdefault('call_multiplier_floor', DEFAULT_SIZING_DEFENSE_CALL_MULTIPLIER)
    kw.setdefault('polar_score', 0.40)
    kw.setdefault('min_polar', 0.15)
    kw.setdefault('full_polar', 0.40)
    kw.setdefault('bet_ratio', 1.2)
    kw.setdefault('hand_strength', 'medium_made')
    kw.setdefault('max_total_shift', 0.4)
    kw.setdefault('legal_actions', ['call', 'fold'])
    return compute_sizing_defense_strategy(strategy, **kw)


class TestTransform:
    def test_damps_call_toward_fold(self):
        # call 0.8 × 0.55 wants call→0.44 (drop 0.36), but the L1 shift that
        # implies (0.36 call + 0.36 fold = 0.72) exceeds the 0.4 DEFAULT
        # envelope, so it's clamped to a 0.2 call→fold move. The damp direction
        # holds; the per-decision magnitude is envelope-capped by design.
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        out, trace = _call(s)
        probs = out.action_probabilities
        assert trace.fired
        assert probs['call'] == pytest.approx(0.6, abs=1e-6)
        assert probs['fold'] == pytest.approx(0.4, abs=1e-6)
        assert probs['call'] + probs['fold'] == pytest.approx(1.0)

    def test_small_call_mass_damps_fully_within_envelope(self):
        # call 0.3 × 0.55 = 0.165 (drop 0.135); L1 = 0.27 < 0.4 → not clamped.
        s = StrategyProfile(action_probabilities={'call': 0.3, 'raise': 0.4, 'fold': 0.3})
        out, _ = _call(s)
        assert out.action_probabilities['call'] == pytest.approx(0.165, abs=1e-6)

    def test_clamp_caps_the_shift(self):
        # A full damp (×0.0) wants to move 1.0 of mass; the DEFAULT envelope
        # (max_total_shift=0.4) caps the L1 shift, so call only drops by 0.2.
        s = StrategyProfile(action_probabilities={'call': 1.0})
        out, _ = _call(s, call_multiplier_floor=0.0)
        probs = out.action_probabilities
        # L1 distance = |Δcall| + |Δfold| = 0.2 + 0.2 = 0.4 = the cap.
        assert probs['call'] == pytest.approx(0.8, abs=1e-6)
        assert probs['fold'] == pytest.approx(0.2, abs=1e-6)

    def test_preserves_raise_and_check_mass(self):
        # Only call→fold is touched; a raise/check mix is left intact.
        s = StrategyProfile(
            action_probabilities={'call': 0.5, 'raise': 0.3, 'fold': 0.2}
        )
        out, _ = _call(s)
        probs = out.action_probabilities
        assert probs['raise'] == pytest.approx(0.3, abs=1e-6)
        # call dropped, fold absorbed the freed mass.
        assert probs['call'] < 0.5
        assert probs['fold'] > 0.2
        assert sum(probs.values()) == pytest.approx(1.0)

    def test_no_call_mass_is_noop(self):
        s = StrategyProfile(action_probabilities={'fold': 0.7, 'raise': 0.3})
        out, trace = _call(s)
        assert not trace.fired
        assert trace.reason_code == 'no_call_mass'
        assert out.action_probabilities == s.action_probabilities

    def test_all_in_call_token_when_call_illegal(self):
        # Short-stack call-off: 'call' isn't legal but 'all_in' is → the
        # call-equivalent abstract token is 'jam', and that mass is what gets
        # damped (the profile keys the continue-mass under the abstract token).
        s = StrategyProfile(action_probabilities={'jam': 0.8, 'fold': 0.2})
        out, trace = _call(s, legal_actions=['all_in', 'fold'])
        assert trace.fired
        assert trace.extra['call_action'] == 'jam'
        assert out.action_probabilities['jam'] < 0.8
        assert out.action_probabilities['fold'] > 0.2

    def test_ablation_returns_unchanged_with_disabled_trace(self):
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        out, trace = _call(s, disable_rules=frozenset({('sizing_defense', 'default')}))
        assert not trace.fired
        assert trace.reason_code == 'disabled_by_ablation'
        assert out.action_probabilities == s.action_probabilities


class TestControllerGating:
    """The default-off + maturity gates on the controller's apply path."""

    def _bot(self):
        from poker.tiered_bot_controller import TieredBotController

        bot = TieredBotController.__new__(TieredBotController)
        bot.player_name = 'Hero'
        bot.opponent_model_manager = None
        bot.sizing_defense_enabled = True
        bot.sizing_defense_min_polar = 0.15
        bot.sizing_defense_full_polar = 0.40
        bot.sizing_defense_call_multiplier = 0.55
        bot.sizing_defense_min_bet_ratio = 0.75
        bot.sizing_defense_polar_override = None
        bot.disable_rules = frozenset()
        bot.debug_logging = False
        return bot

    def test_default_off_is_a_noop(self):
        bot = self._bot()
        bot.sizing_defense_enabled = False
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        out, trace = bot._apply_sizing_defense(
            s, game_state=None, player_idx=0, valid_actions=['call', 'fold'],
            anchors=object(), hand_strength='medium_made', prior_layer_fired=False,
        )
        assert not trace.fired
        assert trace.reason_code == 'disabled'
        assert out is s

    def test_defers_when_prior_layer_fired(self):
        bot = self._bot()
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        _, trace = bot._apply_sizing_defense(
            s, game_state=None, player_idx=0, valid_actions=['call', 'fold'],
            anchors=object(), hand_strength='medium_made', prior_layer_fired=True,
        )
        assert not trace.fired
        assert trace.reason_code == 'prior_layer_fired'

    def test_strong_hand_not_eligible(self):
        bot = self._bot()
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        _, trace = bot._apply_sizing_defense(
            s, game_state=None, player_idx=0, valid_actions=['call', 'fold'],
            anchors=object(), hand_strength='nuts', prior_layer_fired=False,
        )
        assert not trace.fired
        assert trace.reason_code == 'hand_class_not_eligible'

    def test_override_path_no_manager(self):
        # With the eval override set, the resolver returns it without a manager.
        bot = self._bot()
        bot.sizing_defense_polar_override = 0.3
        assert bot._resolve_sizing_defense_polar(game_state=None) == 0.3

    def test_below_polar_threshold_is_not_face_up(self):
        bot = self._bot()
        bot.sizing_defense_polar_override = 0.05  # below min_polar 0.15
        from types import SimpleNamespace

        # Stub the decision context so a big bet is "faced".
        bot._build_decision_context = lambda gs, idx: SimpleNamespace(
            bet_size_pot_ratio=1.2
        )
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        _, trace = bot._apply_sizing_defense(
            s, game_state=None, player_idx=0, valid_actions=['call', 'fold'],
            anchors=object(), hand_strength='medium_made', prior_layer_fired=False,
        )
        assert not trace.fired
        assert trace.reason_code == 'not_face_up'

    def test_fires_vs_face_up_big_bet(self):
        bot = self._bot()
        bot.sizing_defense_polar_override = 0.3
        from types import SimpleNamespace

        bot._build_decision_context = lambda gs, idx: SimpleNamespace(
            bet_size_pot_ratio=1.2
        )
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        out, trace = bot._apply_sizing_defense(
            s, game_state=None, player_idx=0, valid_actions=['call', 'fold'],
            anchors=object(), hand_strength='medium_made', prior_layer_fired=False,
        )
        assert trace.fired
        assert out.action_probabilities['call'] < 0.8

    def test_small_bet_does_not_fire(self):
        bot = self._bot()
        bot.sizing_defense_polar_override = 0.3
        from types import SimpleNamespace

        bot._build_decision_context = lambda gs, idx: SimpleNamespace(
            bet_size_pot_ratio=0.4  # below min_bet_ratio 0.75
        )
        s = StrategyProfile(action_probabilities={'call': 0.8, 'fold': 0.2})
        _, trace = bot._apply_sizing_defense(
            s, game_state=None, player_idx=0, valid_actions=['call', 'fold'],
            anchors=object(), hand_strength='medium_made', prior_layer_fired=False,
        )
        assert not trace.fired
        assert trace.reason_code == 'not_a_big_bet'
