"""Tests for the spot/line-specific personality tendency layer (item 3)."""

from poker.strategy.intervention_trace import validate_trace
from poker.strategy.spot_tendencies import LAYER, apply_spot_tendencies
from poker.strategy.strategy_profile import StrategyProfile

# A flop spot with mass split between checking and betting.
BASE = StrategyProfile(
    action_probabilities={'check': 0.30, 'bet_67': 0.50, 'bet_100': 0.20}
)
SLOWPLAY = (('slowplay', 0.6),)
# Loose cap so the reshape isn't clipped (isolates the slow-play effect).
LOOSE_CAP = 0.60


def _agg(strategy):
    p = strategy.action_probabilities
    return sum(v for a, v in p.items() if a in ('jam', 'all_in') or a.startswith(('bet_', 'raise_')))


def _apply(strategy=BASE, *, hand_class='nuts', action_context='unopened', street='flop',
           has_initiative=True, tendencies=SLOWPLAY, max_shift=LOOSE_CAP, disable_rules=None):
    return apply_spot_tendencies(
        strategy,
        spot_tendencies=tendencies,
        max_per_action_shift=max_shift,
        hand_class=hand_class,
        action_context=action_context,
        street=street,
        has_initiative=has_initiative,
        disable_rules=disable_rules,
    )


def test_slowplay_fires_on_strong_hand_with_initiative():
    out, traces = _apply()
    assert _agg(out) < _agg(BASE)  # aggression dampened
    assert out.action_probabilities['check'] > BASE.action_probabilities['check']
    assert len(traces) == 1 and traces[0].fired
    assert traces[0].layer == LAYER and traces[0].rule_id == 'slowplay'
    assert abs(sum(out.action_probabilities.values()) - 1.0) < 1e-9


def test_slowplay_strong_made_also_fires():
    out, traces = _apply(hand_class='strong_made')
    assert _agg(out) < _agg(BASE)
    assert traces[0].fired


def test_no_op_when_hand_class_not_strong():
    out, traces = _apply(hand_class='medium_made')
    assert out is BASE
    assert len(traces) == 1 and not traces[0].fired


def test_no_op_without_initiative():
    out, traces = _apply(has_initiative=False)
    assert out is BASE and not traces[0].fired


def test_no_op_facing_a_bet():
    out, traces = _apply(action_context='facing_bet')
    assert out is BASE and not traces[0].fired


def test_no_op_on_river():
    out, traces = _apply(street='river')
    assert out is BASE and not traces[0].fired


def test_disabled_rule_is_ablated():
    out, traces = _apply(disable_rules=frozenset({(LAYER, 'slowplay')}))
    assert out is BASE  # no reshape
    assert len(traces) == 1 and not traces[0].fired
    assert traces[0].reason_code == 'disabled_by_ablation'


def test_empty_config_is_identity_no_traces():
    out, traces = _apply(tendencies=())
    assert out is BASE and traces == []


def test_unknown_tendency_ignored():
    out, traces = _apply(tendencies=(('not_a_real_tendency', 0.5),))
    assert out is BASE and traces == []


def test_per_action_cap_is_respected():
    # Tight cap: no single action may move more than max_shift from base.
    cap = 0.10
    out, traces = _apply(max_shift=cap)
    for action, base_p in BASE.action_probabilities.items():
        shift = abs(out.action_probabilities[action] - base_p)
        assert shift <= cap + 1e-6, f"{action} moved {shift:.4f} > cap {cap}"
    assert traces[0].fired


def test_zero_strength_is_no_op():
    out, traces = _apply(tendencies=(('slowplay', 0.0),))
    assert out is BASE and not traces[0].fired


def test_emitted_traces_validate():
    _, fired = _apply()
    _, disabled = _apply(disable_rules=frozenset({(LAYER, 'slowplay')}))
    _, noop = _apply(hand_class='air_no_draw')
    for traces in (fired, disabled, noop):
        for t in traces:
            validate_trace(t)
