"""Tests for poker/strategy/overbet_context.py (POSTFLOP_NEXT_LEVER)."""

import pytest

from poker.strategy.intervention_trace import (
    InterventionOperation,
    validate_trace,
)
from poker.strategy.overbet_context import (
    DEFAULT_CLASSES,
    DEFAULT_FRACTION,
    DEFAULT_SIZE,
    DEFAULT_STREETS,
    _shift_bet_mass,
    apply_overbet_context,
)
from poker.strategy.strategy_profile import StrategyProfile


def _unopened_value():
    """Typical aggressor turn/river spot: mixed bet sizing on a value class."""
    return StrategyProfile(action_probabilities={'check': 0.4, 'bet_67': 0.4, 'bet_100': 0.2})


def _facing_bet():
    """Facing a bet: fold/call/raise distribution (no `bet_*`)."""
    return StrategyProfile(action_probabilities={'fold': 0.3, 'call': 0.5, 'raise_67': 0.2})


def _no_bet_mass():
    """Pure check/call/fold node — overbet is a no-op (nothing to relabel)."""
    return StrategyProfile(action_probabilities={'check': 0.7, 'fold': 0.3})


# ── _shift_bet_mass (the size-relabel surgery) ────────────────────────


class TestShiftBetMass:
    def test_fraction_1_relabels_all_bet_mass(self):
        sp = _unopened_value()  # bet_67: 0.4 + bet_100: 0.2 = 0.6 total bet mass
        out = _shift_bet_mass(sp, overbet_key='bet_150', fraction=1.0)
        assert out.action_probabilities['bet_150'] == pytest.approx(0.6)
        assert 'bet_67' not in out.action_probabilities
        assert 'bet_100' not in out.action_probabilities
        assert out.action_probabilities['check'] == pytest.approx(0.4)

    def test_fraction_partial_keeps_proportional_remainder(self):
        sp = _unopened_value()
        out = _shift_bet_mass(sp, overbet_key='bet_150', fraction=0.5)
        # 50% of the 0.6 bet mass moves → overbet, the other 50% stays
        # proportionally split between bet_67 (0.4) and bet_100 (0.2).
        assert out.action_probabilities['bet_150'] == pytest.approx(0.3)
        assert out.action_probabilities['bet_67'] == pytest.approx(0.2)
        assert out.action_probabilities['bet_100'] == pytest.approx(0.1)
        assert out.action_probabilities['check'] == pytest.approx(0.4)
        # Mass is conserved.
        assert sum(out.action_probabilities.values()) == pytest.approx(1.0)

    def test_no_bet_mass_returns_unchanged(self):
        sp = _no_bet_mass()
        out = _shift_bet_mass(sp, overbet_key='bet_150', fraction=1.0)
        assert out is sp  # identity short-circuit

    def test_fraction_zero_is_no_op(self):
        sp = _unopened_value()
        out = _shift_bet_mass(sp, overbet_key='bet_150', fraction=0.0)
        assert out is sp


# ── apply_overbet_context — gates ─────────────────────────────────────


class TestGates:
    def test_fires_on_turn_unopened_value(self):
        out, tr = apply_overbet_context(
            _unopened_value(),
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
        )
        assert tr.fired and tr.rule_id == 'overbet'
        assert out.action_probabilities['bet_150'] == pytest.approx(0.6)
        validate_trace(tr)
        assert tr.operation == InterventionOperation.OVERRIDE.value

    def test_fires_on_river_unopened_value(self):
        out, tr = apply_overbet_context(
            _unopened_value(),
            hand_class='strong_made',
            action_context='unopened',
            street='river',
            active_count=2,
        )
        assert tr.fired
        assert out.action_probabilities['bet_150'] == pytest.approx(0.6)

    def test_skips_on_flop(self):
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='flop',
            active_count=2,
        )
        assert not tr.fired
        assert tr.reason_code == 'gates_not_met'
        assert out is sp

    def test_skips_on_facing_bet(self):
        # The bot isn't *betting* in facing_bet spots — it's calling/raising.
        # Overbet sizing only applies when the bot is the bettor (unopened).
        sp = _facing_bet()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='facing_bet',
            street='turn',
            active_count=2,
        )
        assert not tr.fired
        assert out is sp

    def test_skips_off_class_medium(self):
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='medium_made',
            action_context='unopened',
            street='turn',
            active_count=2,
        )
        assert not tr.fired
        assert out is sp

    def test_skips_off_class_air(self):
        # Bluff-overbet was measured ~neutral (bot rarely bets air late) →
        # not in the default set. Layer must skip air classes.
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='air_no_draw',
            action_context='unopened',
            street='turn',
            active_count=2,
        )
        assert not tr.fired

    def test_no_op_when_no_bet_action(self):
        sp = _no_bet_mass()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
        )
        # Gates pass but there's no bet mass to shift → no-op trace.
        assert not tr.fired
        assert tr.reason_code == 'no_bet_action'
        assert out is sp


# ── apply_overbet_context — knob overrides ────────────────────────────


class TestKnobs:
    def test_respects_overbet_max_active(self):
        # max_active=2 (HU/heads-up only) — fires at HU, not multiway.
        sp = _unopened_value()
        _, tr_hu = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            overbet_max_active=2,
        )
        assert tr_hu.fired
        out_mw, tr_mw = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=3,
            overbet_max_active=2,
        )
        assert not tr_mw.fired
        assert out_mw is sp

    def test_respects_overbet_classes_subset(self):
        # Restrict to nuts only → strong_made does NOT fire.
        sp = _unopened_value()
        _, tr_nuts = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            overbet_classes=frozenset({'nuts'}),
        )
        assert tr_nuts.fired
        _, tr_strong = apply_overbet_context(
            sp,
            hand_class='strong_made',
            action_context='unopened',
            street='turn',
            active_count=2,
            overbet_classes=frozenset({'nuts'}),
        )
        assert not tr_strong.fired

    def test_respects_overbet_streets_subset(self):
        # Restrict to RIVER only → turn does NOT fire.
        sp = _unopened_value()
        _, tr_turn = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            overbet_streets=frozenset({'RIVER'}),
        )
        assert not tr_turn.fired
        _, tr_river = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='river',
            active_count=2,
            overbet_streets=frozenset({'RIVER'}),
        )
        assert tr_river.fired

    def test_custom_size_relabels_to_that_size(self):
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            overbet_size=200,
        )
        assert tr.fired
        assert out.action_probabilities['bet_200'] == pytest.approx(0.6)
        assert 'bet_150' not in out.action_probabilities

    def test_fraction_below_one_partial_shift(self):
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            overbet_fraction=0.5,
        )
        assert tr.fired
        # half the bet mass (0.3) lands on bet_150; rest stays at bet_67/bet_100
        assert out.action_probabilities['bet_150'] == pytest.approx(0.3)
        assert out.action_probabilities['bet_67'] == pytest.approx(0.2)
        assert out.action_probabilities['bet_100'] == pytest.approx(0.1)


# ── apply_overbet_context — pipeline composition ──────────────────────


class TestPipelineComposition:
    def test_defers_when_prior_layer_fired(self):
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            prior_layer_fired=True,
        )
        assert not tr.fired
        assert tr.reason_code == 'prior_override_active'
        assert out is sp

    def test_disable_rule_returns_disabled_trace(self):
        sp = _unopened_value()
        out, tr = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
            disable_rules=frozenset({('overbet_context', 'overbet')}),
        )
        assert not tr.fired
        assert out is sp

    def test_case_insensitive_street(self):
        # Production passes `node.street` which is lowercase ('turn'); the
        # default streets frozenset is uppercase. Layer normalizes.
        sp = _unopened_value()
        _, tr_lower = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='turn',
            active_count=2,
        )
        _, tr_upper = apply_overbet_context(
            sp,
            hand_class='nuts',
            action_context='unopened',
            street='TURN',
            active_count=2,
        )
        assert tr_lower.fired and tr_upper.fired


# ── Defaults exposed for production tuning ────────────────────────────


class TestDefaults:
    def test_default_constants_match_measured_config(self):
        # If these defaults drift, the measured +EV results no longer apply —
        # re-run the matrix before relying on the bb/100 numbers in the docs.
        assert DEFAULT_SIZE == 150
        assert DEFAULT_FRACTION == 1.0
        assert DEFAULT_CLASSES == frozenset({'nuts', 'strong_made'})
        assert DEFAULT_STREETS == frozenset({'TURN', 'RIVER'})
