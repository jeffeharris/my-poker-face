"""Tests for the tilt_conditioning layer (PERCEPTIBILITY_CONDITIONING.md).

Phase 2 built the layer as INFRASTRUCTURE, inert by default. Phase 3 (#9) opts
the MANIAC in (cap 0.35 + the 6 aggressive Tendler rules) and lowers its
baseline; every OTHER archetype stays inert. These tests lock:

  - each Tendler tilt type selects the correct rule,
  - the cap clamp holds (offset never exceeds tilt_conditioning_cap),
  - composed state / flag-off / inert profile = no-op,
  - a synthetic opted-in profile + matching rule actually shifts the
    distribution,
  - preflop vs postflop scenario gating,
  - the double-count guard (this layer is disjoint from compute_trait_offsets'
    poise-gated emotional term),
  - the byte-identical invariant across every STILL-INERT archetype (all but the
    maniac), and
  - the Phase-3 maniac opt-in: it spikes in re-raise spots when tilted, is
    byte-identical when composed, and doesn't fire at an RFI node.
"""

from types import SimpleNamespace

import pytest

from poker.strategy.deviation_profiles import DEVIATION_PROFILES, DeviationProfile
from poker.strategy.nodes import PostflopNode, PreflopNode
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.tilt_conditioning import (
    TILT_TYPE_RULES,
    TiltScenarioRule,
    _resolve_position,
    _resolve_scenario,
    _resolve_tilt_type,
    apply_tilt_conditioning,
)

# ── Fixtures / builders ──────────────────────────────────────────────────────


def _strat():
    return StrategyProfile(action_probabilities={'fold': 0.4, 'call': 0.4, 'raise_3x': 0.2})


def _reraise_node():
    return PreflopNode(hand='AKo', position='CO', scenario='vs_open', opener_position='UTG')


def _rfi_node():
    return PreflopNode(hand='AKo', position='CO', scenario='rfi', opener_position='')


def _postflop_node():
    return PostflopNode(
        street='flop',
        position='IP',
        pot_type='SRP',
        board_texture='dry_high',
        made_tier='air',
        draw_modifier='no_draw',
        facing_action='unopened',
        spr_bucket='high',
    )


def _tilted(source='bad_beat', state='tilted', intensity=0.7):
    return (
        SimpleNamespace(state=state, intensity=intensity),
        SimpleNamespace(pressure_source=source),
    )


def _opted_in_profile(*, cap=0.5, rules=None, scenario_gate='preflop_reraise'):
    if rules is None:
        rules = (TiltScenarioRule('bad_beat', scenario_gate, 'all', 'aggressive', 1, 0.6),)
    return DeviationProfile(
        max_kl=1.0,
        max_per_action_shift=0.3,
        aggression_scale=1.0,
        looseness_scale=1.0,
        risk_scale=1.0,
        ego_fold_penalty=0.1,
        tilt_conditioning_cap=cap,
        tilt_scenario_rules=rules,
    )


def _max_moved(before: StrategyProfile, after: StrategyProfile) -> float:
    keys = set(before.action_probabilities) | set(after.action_probabilities)
    return max(
        abs(before.action_probabilities.get(k, 0.0) - after.action_probabilities.get(k, 0.0))
        for k in keys
    )


# ── Tendler-type rule selection ──────────────────────────────────────────────


@pytest.mark.parametrize(
    'source',
    ['bad_beat', 'got_sucked_out', 'big_loss', 'losing_streak', 'nemesis_loss', 'crippled'],
)
def test_aggressive_tilt_type_selects_its_rule_and_shifts(source):
    """Each aggressive Tendler type selects its rule and pushes aggression up."""
    rule = TiltScenarioRule(source, 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted(source=source)
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert trace.fired
    assert trace.reason_code == f'tilt_{source}'
    # Aggression mass increased, passive decreased.
    assert new.action_probabilities['raise_3x'] > strat.action_probabilities['raise_3x']
    assert new.action_probabilities['call'] < strat.action_probabilities['call']


def test_bluff_called_is_conservative_noop_v1():
    """mistake-tilt (bluff_called) is registered but magnitude 0.0 in V1 → no-op."""
    rule = TILT_TYPE_RULES['bluff_called']
    assert rule.max_magnitude == 0.0
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted(source='bluff_called')
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert new is strat
    assert not trace.fired


def test_resolve_tilt_type_requires_state_and_source():
    """The selector needs BOTH an aggressive tilt state AND a known source."""
    # Aggressive state + known source → the source.
    emo, comp = _tilted(source='bad_beat')
    assert _resolve_tilt_type(emo, comp) == 'bad_beat'
    # Composed state → None even with a source.
    assert _resolve_tilt_type(SimpleNamespace(state='composed'), comp) is None
    # Passive tilt state (shaken) → None (left to compute_trait_offsets).
    assert _resolve_tilt_type(SimpleNamespace(state='shaken', intensity=0.9), comp) is None
    # Aggressive state, no source → None.
    assert _resolve_tilt_type(emo, SimpleNamespace(pressure_source='')) is None
    # Aggressive state, unknown source → None.
    assert _resolve_tilt_type(emo, SimpleNamespace(pressure_source='won_big')) is None
    # Missing composure_state (sim/__new__) → None.
    assert _resolve_tilt_type(emo, None) is None


# ── Cap clamp invariant ──────────────────────────────────────────────────────


@pytest.mark.parametrize('cap', [0.02, 0.05, 0.1, 0.2])
def test_cap_clamp_holds(cap):
    """No action ever moves more than the profile's tilt_conditioning_cap."""
    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(cap=cap, rules=(rule,))
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert _max_moved(strat, new) <= cap + 1e-9


def test_magnitude_is_min_of_rule_and_cap():
    """The applied magnitude never exceeds either the rule reach or the cap."""
    # Rule reach 0.6, cap 0.05 → cap binds; movement <= 0.05.
    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(cap=0.05, rules=(rule,))
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert trace.inputs['magnitude'] == pytest.approx(0.05)


# ── No-op paths ──────────────────────────────────────────────────────────────


def test_inert_profile_is_noop():
    """cap == 0.0 (the Phase-2 default) returns the input unchanged."""
    profile = _opted_in_profile(cap=0.0, rules=())
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (), profile
    )
    assert new is strat
    assert not trace.fired
    assert trace.reason_code == 'inert_profile'


def test_cap_set_but_no_rules_is_noop():
    """A cap with no opted-in rules is still inert."""
    profile = _opted_in_profile(cap=0.5, rules=())
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (), profile
    )
    assert new is strat
    assert not trace.fired


def test_composed_state_is_noop():
    profile = _opted_in_profile()
    strat = _strat()
    emo = SimpleNamespace(state='composed', intensity=0.0)
    comp = SimpleNamespace(pressure_source='bad_beat')
    new, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        emo,
        comp,
        _reraise_node(),
        profile.tilt_scenario_rules,
        profile,
    )
    assert new is strat
    assert not trace.fired
    assert trace.reason_code == 'not_tilted'


def test_no_matching_rule_is_noop():
    """An opted-in profile with no rule for the active tilt type no-ops."""
    rule = TiltScenarioRule('big_loss', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted(source='bad_beat')  # active type has no rule
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert new is strat
    assert trace.reason_code == 'no_matching_rule'


def test_ablation_disables_rule():
    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        emo,
        comp,
        _reraise_node(),
        (rule,),
        profile,
        disable_rules=frozenset({('tilt_conditioning', 'tilt_bad_beat')}),
    )
    assert new is strat
    assert not trace.fired


# ── Scenario / position gating ───────────────────────────────────────────────


def test_preflop_reraise_gate_blocks_rfi():
    """A preflop_reraise rule fires at vs_open but not at rfi."""
    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted()
    strat = _strat()
    fired, _ = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert fired is not strat
    noop, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, _rfi_node(), (rule,), profile
    )
    assert noop is strat
    assert trace.reason_code == 'no_matching_rule'


def test_postflop_aggressor_gate():
    """A postflop_aggressor rule fires on a PostflopNode, not on a reraise node."""
    rule = TiltScenarioRule('bad_beat', 'postflop_aggressor', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted()
    strat = StrategyProfile(action_probabilities={'check': 0.5, 'bet_67': 0.5})
    fired, t = apply_tilt_conditioning(
        strat, ['check', 'bet'], emo, comp, _postflop_node(), (rule,), profile
    )
    assert fired is not strat and t.fired
    # The same rule must NOT fire at a preflop reraise node.
    pf = _strat()
    noop, ntrace = apply_tilt_conditioning(
        pf, ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    assert noop is pf and not ntrace.fired


def test_position_gate():
    """An IP-gated rule fires IP and no-ops OOP."""
    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'IP', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted()
    strat = _strat()
    # CO resolves to IP.
    ip_node = PreflopNode(hand='AKo', position='CO', scenario='vs_open', opener_position='UTG')
    fired, _ = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, ip_node, (rule,), profile
    )
    assert fired is not strat
    # UTG resolves to OOP → no match.
    oop_node = PreflopNode(hand='AKo', position='UTG', scenario='vs_open', opener_position='HJ')
    noop, trace = apply_tilt_conditioning(
        strat, ['fold', 'call', 'raise'], emo, comp, oop_node, (rule,), profile
    )
    assert noop is strat
    assert trace.reason_code == 'no_matching_rule'


def test_resolve_scenario_and_position():
    assert _resolve_scenario(_reraise_node()) == 'preflop_reraise'
    assert _resolve_scenario(_rfi_node()) == 'all'
    assert _resolve_scenario(_postflop_node()) == 'postflop_aggressor'
    assert _resolve_position(_postflop_node()) == 'IP'
    assert _resolve_position(PreflopNode('AKo', 'CO', 'vs_open', 'UTG')) == 'IP'
    assert _resolve_position(PreflopNode('AKo', 'UTG', 'vs_open', 'HJ')) == 'OOP'


# ── Double-count guard ───────────────────────────────────────────────────────


def test_double_count_guard_disjoint_from_emotional_shift_path():
    """The layer is disjoint from compute_trait_offsets' poise-gated term.

    compute_trait_offsets keys ONLY on emotional_state.state (the quadrant) and
    scales by intensity*(1-poise). This layer additionally REQUIRES a
    composure_state.pressure_source (the cause) and does NOT re-apply
    intensity*(1-poise) — its magnitude is the fixed profile cap. So:
      - same emotional state, NO pressure_source → this layer no-ops (the
        personality term still ran; no double-count), and
      - the applied magnitude is independent of `intensity` (it's the cap),
        proving it isn't re-applying the personality term's quantity.
    """
    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(cap=0.05, rules=(rule,))
    strat = _strat()
    # Same aggressive state, but NO cause → no tilt-conditioning (only the
    # personality term, applied upstream, would have fired).
    emo = SimpleNamespace(state='tilted', intensity=0.9)
    noop, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        emo,
        SimpleNamespace(pressure_source=''),
        _reraise_node(),
        (rule,),
        profile,
    )
    assert noop is strat
    assert trace.reason_code == 'not_tilted'

    # Magnitude is the cap, independent of intensity (not intensity*(1-poise)).
    comp = SimpleNamespace(pressure_source='bad_beat')
    _, t_low = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        SimpleNamespace(state='tilted', intensity=0.1),
        comp,
        _reraise_node(),
        (rule,),
        profile,
    )
    _, t_high = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        SimpleNamespace(state='tilted', intensity=0.99),
        comp,
        _reraise_node(),
        (rule,),
        profile,
    )
    assert t_low.inputs['magnitude'] == t_high.inputs['magnitude'] == pytest.approx(0.05)


# ── Byte-identical invariant across the still-inert real archetypes ───────────
#
# Phase 3 (#9 / PERCEPTIBILITY_CONDITIONING.md) opts the MANIAC in
# (tilt_conditioning_cap > 0 + the 6 aggressive Tendler rules), so the maniac is
# DELIBERATELY excluded from the inert invariant below — its opt-in is locked by
# the positive tests in the next block. EVERY OTHER archetype must stay inert
# (byte-identical flag-off behavior — only the maniac profile moved).
_OPTED_IN = {'maniac'}
_STILL_INERT = sorted(name for name in DEVIATION_PROFILES if name not in _OPTED_IN)


@pytest.mark.parametrize('name', _STILL_INERT)
def test_every_other_archetype_profile_is_inert(name):
    """Every non-opted-in archetype keeps the layer inert (cap 0.0 / empty rules)."""
    profile = DEVIATION_PROFILES[name]
    assert profile.tilt_conditioning_cap == 0.0
    assert profile.tilt_scenario_rules == ()


@pytest.mark.parametrize('name', _STILL_INERT)
def test_other_archetype_is_byte_identical_under_tilt(name):
    """With a non-opted-in profile, the layer is a byte-identical no-op even on a
    fully-tilted state at a re-raise node (the invariant Phase 3 preserves for
    every archetype EXCEPT the maniac)."""
    profile = DEVIATION_PROFILES[name]
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        emo,
        comp,
        _reraise_node(),
        profile.tilt_scenario_rules,
        profile,
    )
    assert new is strat
    assert not trace.fired
    assert new.action_probabilities == strat.action_probabilities


# ── Phase-3 maniac opt-in (#9) ────────────────────────────────────────────────


def test_maniac_is_opted_into_tilt_conditioning():
    """The maniac carries a non-zero cap + the 6 aggressive Tendler rules."""
    profile = DEVIATION_PROFILES['maniac']
    assert profile.tilt_conditioning_cap == pytest.approx(0.35)
    rule_types = {r.tilt_type for r in profile.tilt_scenario_rules}
    assert rule_types == {
        'bad_beat',
        'got_sucked_out',
        'big_loss',
        'losing_streak',
        'nemesis_loss',
        'crippled',
    }
    # bluff_called (V1 conservative no-op) is intentionally NOT opted in.
    assert 'bluff_called' not in rule_types


@pytest.mark.parametrize(
    'source',
    ['bad_beat', 'got_sucked_out', 'big_loss', 'losing_streak', 'nemesis_loss', 'crippled'],
)
def test_maniac_tilt_spikes_aggression_in_reraise_spot(source):
    """A freshly-tilted maniac amplifies aggression at a preflop re-raise node,
    bounded by its cap (the conditioned tilt-STATE spike)."""
    profile = DEVIATION_PROFILES['maniac']
    emo, comp = _tilted(source=source)
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        emo,
        comp,
        _reraise_node(),
        profile.tilt_scenario_rules,
        profile,
    )
    assert trace.fired
    assert trace.reason_code == f'tilt_{source}'
    assert new.action_probabilities['raise_3x'] > strat.action_probabilities['raise_3x']
    # bounded by the maniac's cap.
    assert _max_moved(strat, new) <= profile.tilt_conditioning_cap + 1e-9


def test_maniac_is_byte_identical_when_composed():
    """A COMPOSED maniac (no tilt) is a byte-identical no-op — the flag-on,
    no-tilt path must equal the flag-off baseline (the layer only fires on a
    concrete tilt cause)."""
    profile = DEVIATION_PROFILES['maniac']
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        SimpleNamespace(state='composed', intensity=0.0),
        SimpleNamespace(pressure_source=''),
        _reraise_node(),
        profile.tilt_scenario_rules,
        profile,
    )
    assert new is strat
    assert not trace.fired
    assert new.action_probabilities == strat.action_probabilities


def test_maniac_tilt_does_not_fire_at_rfi_node():
    """The maniac's rules gate on preflop_reraise; an RFI (open) node — not a
    re-raise spot — is a no-op even when tilted."""
    profile = DEVIATION_PROFILES['maniac']
    emo, comp = _tilted()
    strat = _strat()
    new, trace = apply_tilt_conditioning(
        strat,
        ['fold', 'call', 'raise'],
        emo,
        comp,
        _rfi_node(),
        profile.tilt_scenario_rules,
        profile,
    )
    assert new is strat
    assert not trace.fired


def test_trace_validates_against_schema():
    """A fired tilt trace passes the intervention-trace schema invariants."""
    from poker.strategy.intervention_trace import validate_trace

    rule = TiltScenarioRule('bad_beat', 'preflop_reraise', 'all', 'aggressive', 1, 0.6)
    profile = _opted_in_profile(rules=(rule,))
    emo, comp = _tilted()
    _, trace = apply_tilt_conditioning(
        _strat(), ['fold', 'call', 'raise'], emo, comp, _reraise_node(), (rule,), profile
    )
    validate_trace(trace)  # raises on violation
