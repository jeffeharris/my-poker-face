"""Tests for poker/strategy/multistreet_context.py (STRUCTURAL_PASSIVITY_PLAN)."""

from types import SimpleNamespace

import pytest

from poker.strategy.intervention_trace import (
    InterventionOperation,
    validate_trace,
)
from poker.strategy.multistreet_context import (
    H1_BARREL_TARGET,
    H1_MAX_ACTIVE_PLAYERS,
    H2_FOLD_TARGET,
    MultiStreetSignals,
    _pump_bet,
    _pump_fold,
    apply_multistreet_context,
    derive_signals,
)
from poker.strategy.strategy_profile import StrategyProfile


def _sim_controller(player_name='Hero', hero_bet=None, opp_bet=None, pf_agg=None):
    """A bypassed-__init__-style controller exposing the sim shadow fields."""
    return SimpleNamespace(
        player_name=player_name,
        memory_manager=None,
        _sim_hero_bet_by_street=hero_bet or {},
        _sim_opp_bet_by_street=opp_bet or {},
        _sim_last_preflop_aggressor=pf_agg,
    )


# ── Distribution surgery ─────────────────────────────────────────────


class TestPumpBet:
    def test_pumps_existing_bet_mass_from_check(self):
        sp = StrategyProfile(action_probabilities={'check': 0.9, 'bet_67': 0.1})
        out = _pump_bet(sp, 0.7)
        assert out.action_probabilities['bet_67'] == pytest.approx(0.7)
        assert out.action_probabilities['check'] == pytest.approx(0.3)

    def test_splits_across_multiple_bet_sizes_proportionally(self):
        sp = StrategyProfile(
            action_probabilities={
                'check': 0.8,
                'bet_33': 0.15,
                'bet_100': 0.05,
            }
        )
        out = _pump_bet(sp, 0.6)
        # 0.4 added split 3:1 between bet_33 and bet_100
        assert out.action_probabilities['bet_33'] == pytest.approx(0.15 + 0.4 * 0.75)
        assert out.action_probabilities['bet_100'] == pytest.approx(0.05 + 0.4 * 0.25)
        assert sum(out.action_probabilities.values()) == pytest.approx(1.0)

    def test_no_bet_key_returns_unchanged(self):
        sp = StrategyProfile(action_probabilities={'check': 1.0})
        assert _pump_bet(sp, 0.7) is sp

    def test_already_above_target_unchanged(self):
        sp = StrategyProfile(action_probabilities={'check': 0.2, 'bet_67': 0.8})
        assert _pump_bet(sp, 0.7) is sp

    def test_zero_check_mass_unchanged(self):
        sp = StrategyProfile(action_probabilities={'bet_67': 1.0})
        assert _pump_bet(sp, 0.5) is sp


class TestPumpFold:
    def test_pumps_fold_from_call(self):
        sp = StrategyProfile(action_probabilities={'fold': 0.3, 'call': 0.7})
        out = _pump_fold(sp, 0.8)
        assert out.action_probabilities['fold'] == pytest.approx(0.8)
        assert out.action_probabilities['call'] == pytest.approx(0.2)

    def test_no_fold_key_unchanged(self):
        sp = StrategyProfile(action_probabilities={'check': 1.0})
        assert _pump_fold(sp, 0.8) is sp

    def test_already_above_unchanged(self):
        sp = StrategyProfile(action_probabilities={'fold': 0.9, 'call': 0.1})
        assert _pump_fold(sp, 0.8) is sp


# ── Signal derivation (sim path) ─────────────────────────────────────


class TestDeriveSignals:
    def test_flop_prev_aggressor_is_preflop_raiser(self):
        c = _sim_controller(pf_agg='Hero')
        assert derive_signals(c, 'flop').was_prev_street_aggressor is True
        c2 = _sim_controller(pf_agg='Villain')
        assert derive_signals(c2, 'flop').was_prev_street_aggressor is False

    def test_turn_prev_aggressor_is_flop_bettor(self):
        c = _sim_controller(hero_bet={'FLOP': True})
        assert derive_signals(c, 'turn').was_prev_street_aggressor is True
        c2 = _sim_controller(hero_bet={'TURN': True})  # bet turn, not flop
        assert derive_signals(c2, 'turn').was_prev_street_aggressor is False

    def test_river_prev_aggressor_is_turn_bettor(self):
        c = _sim_controller(hero_bet={'TURN': True})
        assert derive_signals(c, 'river').was_prev_street_aggressor is True

    def test_facing_double_barrel_turn(self):
        c = _sim_controller(opp_bet={'FLOP': True, 'TURN': True})
        assert derive_signals(c, 'turn').facing_double_barrel is True
        c2 = _sim_controller(opp_bet={'TURN': True})  # turn only, no flop bet
        assert derive_signals(c2, 'turn').facing_double_barrel is False

    def test_no_double_barrel_on_flop(self):
        c = _sim_controller(opp_bet={'FLOP': True})
        assert derive_signals(c, 'flop').facing_double_barrel is False

    def test_recorder_path_overrides_sim_fields(self):
        """Production: a populated hand_recorder takes precedence."""
        actions = [
            SimpleNamespace(phase='PRE_FLOP', player_name='Hero', action='raise'),
            SimpleNamespace(phase='FLOP', player_name='Hero', action='bet'),
        ]
        c = SimpleNamespace(
            player_name='Hero',
            memory_manager=SimpleNamespace(
                hand_recorder=SimpleNamespace(
                    current_hand=SimpleNamespace(actions=actions),
                ),
            ),
            # sim fields say otherwise — must be ignored when recorder present
            _sim_hero_bet_by_street={},
            _sim_opp_bet_by_street={},
            _sim_last_preflop_aggressor=None,
        )
        assert derive_signals(c, 'turn').was_prev_street_aggressor is True


# ── apply_multistreet_context ────────────────────────────────────────


class TestApplyH1:
    def _unopened(self):
        return StrategyProfile(action_probabilities={'check': 0.9, 'bet_67': 0.1})

    def test_h1_fires_hu_value_unopened_prev_aggressor(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=False)
        out, tr = apply_multistreet_context(
            self._unopened(),
            signals=sig,
            hand_class='strong_made',
            action_context='unopened',
            active_count=2,
        )
        assert tr.fired and tr.rule_id == 'barrel'
        assert out.action_probabilities['bet_67'] == pytest.approx(H1_BARREL_TARGET['strong_made'])
        validate_trace(tr)
        assert tr.operation == InterventionOperation.OVERRIDE.value

    def test_h1_skips_multiway(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=False)
        sp = self._unopened()
        out, tr = apply_multistreet_context(
            sp,
            signals=sig,
            hand_class='strong_made',
            action_context='unopened',
            active_count=H1_MAX_ACTIVE_PLAYERS + 1,
        )
        assert not tr.fired
        assert out is sp  # unchanged

    def test_h1_skips_when_not_prev_aggressor(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=False, facing_double_barrel=False)
        _, tr = apply_multistreet_context(
            self._unopened(),
            signals=sig,
            hand_class='nuts',
            action_context='unopened',
            active_count=2,
        )
        assert not tr.fired

    def test_h1_disabled_toggle(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=False)
        _, tr = apply_multistreet_context(
            self._unopened(),
            signals=sig,
            hand_class='nuts',
            action_context='unopened',
            active_count=2,
            h1_enabled=False,
        )
        assert not tr.fired


class TestApplyH2:
    def _facing(self):
        return StrategyProfile(action_probabilities={'fold': 0.3, 'call': 0.7})

    def test_h2_fires_double_barrel_marginal(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=False, facing_double_barrel=True)
        out, tr = apply_multistreet_context(
            self._facing(),
            signals=sig,
            hand_class='weak_made',
            action_context='facing_bet',
            active_count=2,
        )
        assert tr.fired and tr.rule_id == 'fold_barrel'
        assert out.action_probabilities['fold'] == pytest.approx(H2_FOLD_TARGET['weak_made'])
        validate_trace(tr)

    def test_h2_skips_strong_hand(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=False, facing_double_barrel=True)
        _, tr = apply_multistreet_context(
            self._facing(),
            signals=sig,
            hand_class='strong_made',
            action_context='facing_bet',
            active_count=2,
        )
        assert not tr.fired  # strong hands aren't in H2_FOLD_TARGET

    def test_h2_skips_when_no_double_barrel(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=False, facing_double_barrel=False)
        _, tr = apply_multistreet_context(
            self._facing(),
            signals=sig,
            hand_class='weak_made',
            action_context='facing_bet',
            active_count=2,
        )
        assert not tr.fired


class TestPriorLayerFired:
    def test_defers_to_upstream_override(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=True)
        sp = StrategyProfile(action_probabilities={'check': 0.9, 'bet_67': 0.1})
        out, tr = apply_multistreet_context(
            sp,
            signals=sig,
            hand_class='nuts',
            action_context='unopened',
            active_count=2,
            prior_layer_fired=True,
        )
        assert out is sp
        assert not tr.fired and tr.reason_code == 'prior_override_active'
