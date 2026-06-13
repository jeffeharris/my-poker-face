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

    def test_h1_skips_river_when_streets_restricted(self):
        # h1_streets={FLOP,TURN} drops the measured-toxic river barrel
        # (resolved draw → bluffing busted equity into a caller).
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=False)
        sp = self._unopened()
        out, tr = apply_multistreet_context(
            sp,
            signals=sig,
            hand_class='nuts',
            action_context='unopened',
            active_count=2,
            h1_streets=frozenset({'FLOP', 'TURN'}),
            street='river',
        )
        assert not tr.fired
        assert out is sp  # unchanged

    def test_h1_fires_flop_when_streets_restricted(self):
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=False)
        out, tr = apply_multistreet_context(
            self._unopened(),
            signals=sig,
            hand_class='strong_made',
            action_context='unopened',
            active_count=2,
            h1_streets=frozenset({'FLOP', 'TURN'}),
            street='flop',
        )
        assert tr.fired and tr.rule_id == 'barrel'
        assert out.action_probabilities['bet_67'] == pytest.approx(H1_BARREL_TARGET['strong_made'])

    def test_h1_all_streets_when_unrestricted(self):
        # Default (h1_streets=None) preserves the original all-streets behavior.
        sig = MultiStreetSignals(was_prev_street_aggressor=True, facing_double_barrel=False)
        _, tr = apply_multistreet_context(
            self._unopened(),
            signals=sig,
            hand_class='nuts',
            action_context='unopened',
            active_count=2,
            h1_streets=None,
            street='river',
        )
        assert tr.fired and tr.rule_id == 'barrel'


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


class TestH3StealGiveUp:
    """H3 float-and-steal: hero floated (not prev aggressor), opp c-bet the flop
    and checked the turn (give-up), foldable villain → bet air to steal."""

    def _sp(self):
        return StrategyProfile(action_probabilities={'check': 0.9, 'bet_67': 0.1})

    def _steal(self, sig, hand_class='air_no_draw', ftbb=0.6, target=0.55, **kw):
        return apply_multistreet_context(
            self._sp(),
            signals=sig,
            hand_class=hand_class,
            action_context=kw.pop('action_context', 'unopened'),
            active_count=kw.pop('active_count', 2),
            street=kw.pop('street', 'turn'),
            steal_target=target,
            steal_fold_to_big_bet=ftbb,
            **kw,
        )

    def _giveup_sig(self):
        # hero floated (not aggressor), opp c-bet the flop (→ then checked turn)
        return MultiStreetSignals(
            was_prev_street_aggressor=False, facing_double_barrel=False, opp_cbet_flop=True
        )

    def test_fires_on_giveup_line_with_air(self):
        out, tr = self._steal(self._giveup_sig())
        assert tr.fired and tr.rule_id == 'steal'
        assert out.action_probabilities['bet_67'] == pytest.approx(0.55)
        validate_trace(tr)

    def test_fires_on_air_strong_draw(self):
        _, tr = self._steal(self._giveup_sig(), hand_class='air_strong_draw')
        assert tr.fired and tr.rule_id == 'steal'

    def test_skips_when_target_zero(self):
        sp = self._sp()
        out, tr = apply_multistreet_context(
            sp, signals=self._giveup_sig(), hand_class='air_no_draw',
            action_context='unopened', active_count=2, street='turn',
            steal_target=0.0, steal_fold_to_big_bet=0.6,
        )
        assert not tr.fired and out is sp

    def test_skips_without_foldable_read(self):
        # No fold_to_big_bet read (None) → never bluff into an unknown/station.
        _, tr = self._steal(self._giveup_sig(), ftbb=None)
        assert not tr.fired

    def test_skips_when_villain_too_sticky(self):
        # fold_to_big_bet below the min → no fold equity → no steal.
        _, tr = self._steal(self._giveup_sig(), ftbb=0.30)
        assert not tr.fired

    def test_skips_when_hero_was_aggressor(self):
        # Hero c-bet (aggressor) → that's H1 territory, not a steal.
        sig = MultiStreetSignals(
            was_prev_street_aggressor=True, facing_double_barrel=False, opp_cbet_flop=False
        )
        _, tr = self._steal(sig)
        assert tr.rule_id != 'steal'

    def test_skips_when_opp_did_not_cbet_flop(self):
        # Checked-through flop (no give-up line) → not a steal.
        sig = MultiStreetSignals(
            was_prev_street_aggressor=False, facing_double_barrel=False, opp_cbet_flop=False
        )
        _, tr = self._steal(sig)
        assert not tr.fired

    def test_skips_multiway(self):
        _, tr = self._steal(self._giveup_sig(), active_count=H1_MAX_ACTIVE_PLAYERS + 1)
        assert not tr.fired

    def test_skips_facing_bet(self):
        # Villain bet the turn (didn't give up) → not a steal spot.
        _, tr = self._steal(self._giveup_sig(), action_context='facing_bet')
        assert not tr.fired

    def test_skips_off_turn(self):
        _, tr = self._steal(self._giveup_sig(), street='river')
        assert not tr.fired

    def test_skips_made_hand(self):
        # Only air classes steal; a made hand isn't in H3_STEAL_CLASSES.
        _, tr = self._steal(self._giveup_sig(), hand_class='weak_made')
        assert not tr.fired

    def test_disabled_via_ablation(self):
        _, tr = self._steal(self._giveup_sig(), disable_rules=frozenset({('multistreet_context', 'steal')}))
        assert not tr.fired

    def test_derive_signals_sets_opp_cbet_flop(self):
        c = _sim_controller(opp_bet={'FLOP': True})
        assert derive_signals(c, 'turn').opp_cbet_flop is True
        c2 = _sim_controller(opp_bet={'TURN': True})
        assert derive_signals(c2, 'turn').opp_cbet_flop is False


class TestControllerStealIntegration:
    """Deterministic integration: the controller's _layer_multistreet_context
    reaches the H3 steal branch on a give-up-line air turn. Covers the wiring the
    unit tests don't — derive_signals reading the sim line + the ftbb read flowing
    through — without a stochastic sim (the spot is ~0.5% of hands)."""

    def _controller(self, steal_target=0.55):
        from unittest.mock import patch

        from poker.tiered_bot_controller import TieredBotController

        with patch(
            'poker.tiered_bot_controller.AIPlayerController.__init__', return_value=None
        ):
            c = TieredBotController.__new__(TieredBotController)
        c.player_name = 'Hero'
        c.memory_manager = None
        c.enable_multistreet_context = True
        # Give-up line: hero did NOT bet the flop, opp c-bet the flop.
        c._sim_hero_bet_by_street = {}
        c._sim_opp_bet_by_street = {'FLOP': True}
        c._sim_last_preflop_aggressor = 'Villain'
        c.steal_turn_target = steal_target
        c.air_barrel_target = 0.0
        c.river_bluff_ftbb_override = 0.6  # force foldable read
        c.disable_rules = frozenset()
        c._last_intervention_trace = []
        return c

    def _noop_traces(self):
        from poker.strategy.intervention_trace import make_no_op_trace

        t = make_no_op_trace('induce_override', 'default', 0, reason_code='x')
        return (
            make_no_op_trace('induce_override', 'default', 0, reason_code='x'),
            make_no_op_trace('strong_hand_override', 'default', 0, reason_code='x'),
            make_no_op_trace('bluff_catch_override', 'default', 0, reason_code='x'),
        )

    def _call(self, c, hand_strength='air_no_draw'):
        node = SimpleNamespace(street='turn', facing_action='unopened')
        sp = StrategyProfile(action_probabilities={'check': 0.9, 'bet_67': 0.1})
        iot, vot, bct = self._noop_traces()
        return c._layer_multistreet_context(
            sp,
            node=node,
            hand_strength=hand_strength,
            active_count=2,
            game_state=None,
            induce_override_trace=iot,
            value_override_trace=vot,
            bluff_catch_trace=bct,
        )

    def test_steal_fires_through_controller(self):
        out, tr = self._call(self._controller())
        assert tr.fired and tr.rule_id == 'steal'
        assert out.action_probabilities['bet_67'] == pytest.approx(0.55)

    def test_off_when_target_zero(self):
        out, tr = self._call(self._controller(steal_target=0.0))
        assert not tr.fired
        assert out.action_probabilities['bet_67'] == pytest.approx(0.1)
