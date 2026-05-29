"""Tests for the spot/line-specific personality tendency layer (item 3)."""

import dataclasses
from types import SimpleNamespace

from poker.strategy.deviation_profiles import DEVIATION_PROFILES, parse_spot_tendencies
from poker.strategy.intervention_trace import validate_trace
from poker.strategy.spot_tendencies import LAYER, apply_spot_tendencies
from poker.strategy.strategy_profile import StrategyProfile
from poker.tiered_bot_controller import TieredBotController

# A flop spot with mass split between checking and betting.
BASE = StrategyProfile(
    action_probabilities={'check': 0.30, 'bet_67': 0.50, 'bet_100': 0.20}
)
SLOWPLAY = (('slowplay', 0.6),)
GIVEUP = (('give_up_turn', 0.6),)
FITFOLD = (('fit_or_fold', 0.6),)
AUTOCBET = (('auto_cbet', 0.6),)
STICKY = (('sticky', 0.6),)
OVERBLUFF = (('over_bluff', 0.6),)
# A flop spot facing a bet (fold + call + a little raise) — fit-or-fold input.
FACING = StrategyProfile(
    action_probabilities={'fold': 0.40, 'call': 0.45, 'raise_67': 0.15}
)
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


# ── give-up turn / one-and-done ──────────────────────────────────────────────
# Dual of the multistreet H1 barrel: dampens turn bet mass for the thin/bluff
# classes when hero has initiative and is checked to. Turn-only; disjoint from
# slow-play (which targets nuts/strong_made).

def test_give_up_turn_fires_on_thin_hand_with_initiative():
    out, traces = _apply(tendencies=GIVEUP, hand_class='medium_made', street='turn')
    assert _agg(out) < _agg(BASE)  # barrel abandoned → aggression dampened
    assert out.action_probabilities['check'] > BASE.action_probabilities['check']
    assert len(traces) == 1 and traces[0].fired
    assert traces[0].layer == LAYER and traces[0].rule_id == 'give_up_turn'
    assert abs(sum(out.action_probabilities.values()) - 1.0) < 1e-9


def test_give_up_turn_fires_on_each_thin_class():
    for hc in ('medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw'):
        out, traces = _apply(tendencies=GIVEUP, hand_class=hc, street='turn')
        assert _agg(out) < _agg(BASE), hc
        assert traces[0].fired, hc


def test_give_up_turn_no_op_on_strong_value():
    # Strong hands keep betting (that's slow-play's domain, not give-up's).
    for hc in ('nuts', 'strong_made'):
        out, traces = _apply(tendencies=GIVEUP, hand_class=hc, street='turn')
        assert out is BASE, hc
        assert len(traces) == 1 and not traces[0].fired, hc


def test_give_up_turn_no_op_on_flop():
    # Turn-only: the flop c-bet is the first barrel, not a give-up.
    out, traces = _apply(tendencies=GIVEUP, hand_class='medium_made', street='flop')
    assert out is BASE and not traces[0].fired


def test_give_up_turn_no_op_on_river():
    out, traces = _apply(tendencies=GIVEUP, hand_class='medium_made', street='river')
    assert out is BASE and not traces[0].fired


def test_give_up_turn_no_op_without_initiative():
    out, traces = _apply(
        tendencies=GIVEUP, hand_class='medium_made', street='turn', has_initiative=False
    )
    assert out is BASE and not traces[0].fired


def test_give_up_turn_no_op_facing_a_bet():
    out, traces = _apply(
        tendencies=GIVEUP, hand_class='medium_made', street='turn', action_context='facing_bet'
    )
    assert out is BASE and not traces[0].fired


def test_give_up_turn_is_ablatable():
    out, traces = _apply(
        tendencies=GIVEUP, hand_class='medium_made', street='turn',
        disable_rules=frozenset({(LAYER, 'give_up_turn')}),
    )
    assert out is BASE
    assert len(traces) == 1 and not traces[0].fired
    assert traces[0].reason_code == 'disabled_by_ablation'


def test_give_up_turn_respects_per_action_cap():
    cap = 0.10
    out, traces = _apply(tendencies=GIVEUP, hand_class='medium_made', street='turn', max_shift=cap)
    for action, base_p in BASE.action_probabilities.items():
        shift = abs(out.action_probabilities[action] - base_p)
        assert shift <= cap + 1e-6, f"{action} moved {shift:.4f} > cap {cap}"
    assert traces[0].fired


def test_give_up_turn_and_slowplay_are_disjoint():
    # Both configured: a turn medium_made fires give-up but not slow-play; a turn
    # nuts fires slow-play but not give-up. Exactly one reshape per spot.
    out, traces = _apply(
        tendencies=SLOWPLAY + GIVEUP, hand_class='medium_made', street='turn'
    )
    fired = [t for t in traces if t.fired]
    assert len(fired) == 1 and fired[0].rule_id == 'give_up_turn'

    out, traces = _apply(tendencies=SLOWPLAY + GIVEUP, hand_class='nuts', street='turn')
    fired = [t for t in traces if t.fired]
    assert len(fired) == 1 and fired[0].rule_id == 'slowplay'


def test_give_up_turn_emitted_traces_validate():
    _, fired = _apply(tendencies=GIVEUP, hand_class='medium_made', street='turn')
    _, disabled = _apply(
        tendencies=GIVEUP, hand_class='medium_made', street='turn',
        disable_rules=frozenset({(LAYER, 'give_up_turn')}),
    )
    _, noop = _apply(tendencies=GIVEUP, hand_class='nuts', street='turn')
    for traces in (fired, disabled, noop):
        for t in traces:
            validate_trace(t)


# ── fit-or-fold / over-fold to c-bet ─────────────────────────────────────────
# Pumps fold for weak/air facing a flop bet. Input distribution must contain a
# fold action (facing a bet), so these use FACING, not BASE.

def test_fit_or_fold_pumps_fold_on_weak_air():
    for hc in ('weak_made', 'air_no_draw'):
        out, traces = _apply(
            FACING, tendencies=FITFOLD, hand_class=hc,
            action_context='facing_bet', street='flop',
        )
        assert out.action_probabilities['fold'] > FACING.action_probabilities['fold'], hc
        assert traces[0].fired and traces[0].rule_id == 'fit_or_fold', hc
        assert abs(sum(out.action_probabilities.values()) - 1.0) < 1e-9, hc


def test_fit_or_fold_no_op_on_made_hands():
    # A connected hand isn't a fit-or-fold candidate.
    for hc in ('nuts', 'strong_made', 'medium_made', 'air_strong_draw'):
        out, traces = _apply(
            FACING, tendencies=FITFOLD, hand_class=hc,
            action_context='facing_bet', street='flop',
        )
        assert out is FACING, hc
        assert not traces[0].fired, hc


def test_fit_or_fold_no_op_when_unopened():
    # No bet to fold to.
    out, traces = _apply(
        FACING, tendencies=FITFOLD, hand_class='air_no_draw',
        action_context='unopened', street='flop',
    )
    assert out is FACING and not traces[0].fired


def test_fit_or_fold_no_op_off_flop():
    out, traces = _apply(
        FACING, tendencies=FITFOLD, hand_class='air_no_draw',
        action_context='facing_bet', street='turn',
    )
    assert out is FACING and not traces[0].fired


def test_fit_or_fold_respects_cap_and_ablation():
    cap = 0.10
    out, _ = _apply(
        FACING, tendencies=FITFOLD, hand_class='weak_made',
        action_context='facing_bet', street='flop', max_shift=cap,
    )
    for action, base_p in FACING.action_probabilities.items():
        assert abs(out.action_probabilities[action] - base_p) <= cap + 1e-6, action
    out, traces = _apply(
        FACING, tendencies=FITFOLD, hand_class='weak_made',
        action_context='facing_bet', street='flop',
        disable_rules=frozenset({(LAYER, 'fit_or_fold')}),
    )
    assert out is FACING and traces[0].reason_code == 'disabled_by_ablation'


# ── auto-c-bet / c-bets-100% ─────────────────────────────────────────────────
# Pumps bet for the checking part of the range with initiative on the flop.

def test_auto_cbet_pumps_bet_on_thin_hands():
    for hc in ('medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw'):
        out, traces = _apply(tendencies=AUTOCBET, hand_class=hc, street='flop')
        assert _agg(out) > _agg(BASE), hc  # bet frequency up
        assert out.action_probabilities['check'] < BASE.action_probabilities['check'], hc
        assert traces[0].fired and traces[0].rule_id == 'auto_cbet', hc
        assert abs(sum(out.action_probabilities.values()) - 1.0) < 1e-9, hc


def test_auto_cbet_no_op_on_strong_value():
    for hc in ('nuts', 'strong_made'):
        out, traces = _apply(tendencies=AUTOCBET, hand_class=hc, street='flop')
        assert out is BASE and not traces[0].fired, hc


def test_auto_cbet_no_op_without_initiative_or_facing_bet():
    out, traces = _apply(
        tendencies=AUTOCBET, hand_class='medium_made', street='flop', has_initiative=False
    )
    assert out is BASE and not traces[0].fired
    out, traces = _apply(
        tendencies=AUTOCBET, hand_class='medium_made', street='flop', action_context='facing_bet'
    )
    assert out is BASE and not traces[0].fired


def test_auto_cbet_no_op_off_flop():
    out, traces = _apply(tendencies=AUTOCBET, hand_class='medium_made', street='turn')
    assert out is BASE and not traces[0].fired


def test_auto_cbet_is_the_dual_of_give_up_turn():
    # Same thin classes, opposite direction: auto-c-bet pumps the flop, give-up
    # dampens the turn — both fire, neither no-ops, on their own street.
    flop_out, _ = _apply(tendencies=AUTOCBET, hand_class='medium_made', street='flop')
    turn_out, _ = _apply(tendencies=GIVEUP, hand_class='medium_made', street='turn')
    assert _agg(flop_out) > _agg(BASE)
    assert _agg(turn_out) < _agg(BASE)


def test_new_leaks_traces_validate():
    runs = [
        _apply(FACING, tendencies=FITFOLD, hand_class='weak_made',
               action_context='facing_bet', street='flop'),
        _apply(tendencies=AUTOCBET, hand_class='air_no_draw', street='flop'),
        _apply(tendencies=AUTOCBET, hand_class='nuts', street='flop'),  # no-op
    ]
    for _, traces in runs:
        for t in traces:
            validate_trace(t)


# ── sticky / pays-off ────────────────────────────────────────────────────────
# Moves fold mass onto call for weak made facing a river bet. Uses FACING (has a
# fold action).

def test_sticky_pumps_call_on_weak_made_river():
    for hc in ('weak_made', 'medium_made'):
        out, traces = _apply(
            FACING, tendencies=STICKY, hand_class=hc,
            action_context='facing_bet', street='river',
        )
        assert out.action_probabilities['fold'] < FACING.action_probabilities['fold'], hc
        assert out.action_probabilities['call'] > FACING.action_probabilities['call'], hc
        assert traces[0].fired and traces[0].rule_id == 'sticky', hc
        assert abs(sum(out.action_probabilities.values()) - 1.0) < 1e-9, hc


def test_sticky_fires_facing_raise_too():
    out, traces = _apply(
        FACING, tendencies=STICKY, hand_class='weak_made',
        action_context='facing_raise', street='river',
    )
    assert traces[0].fired and out.action_probabilities['fold'] < FACING.action_probabilities['fold']


def test_sticky_no_op_off_river():
    out, traces = _apply(
        FACING, tendencies=STICKY, hand_class='weak_made',
        action_context='facing_bet', street='turn',
    )
    assert out is FACING and not traces[0].fired


def test_sticky_no_op_when_unopened():
    # Not facing a bet → nothing to call.
    out, traces = _apply(
        FACING, tendencies=STICKY, hand_class='weak_made',
        action_context='unopened', street='river',
    )
    assert out is FACING and not traces[0].fired


def test_sticky_no_op_on_strong_hands():
    # Strong hands aren't a pay-off spot (they'd raise, not crying-call).
    for hc in ('nuts', 'strong_made'):
        out, traces = _apply(
            FACING, tendencies=STICKY, hand_class=hc,
            action_context='facing_bet', street='river',
        )
        assert out is FACING and not traces[0].fired, hc


def test_sticky_respects_cap_and_ablation_and_validates():
    cap = 0.10
    out, _ = _apply(
        FACING, tendencies=STICKY, hand_class='weak_made',
        action_context='facing_bet', street='river', max_shift=cap,
    )
    for action, base_p in FACING.action_probabilities.items():
        assert abs(out.action_probabilities[action] - base_p) <= cap + 1e-6, action
    _, fired = _apply(
        FACING, tendencies=STICKY, hand_class='weak_made',
        action_context='facing_bet', street='river',
    )
    _, disabled = _apply(
        FACING, tendencies=STICKY, hand_class='weak_made',
        action_context='facing_bet', street='river',
        disable_rules=frozenset({(LAYER, 'sticky')}),
    )
    assert disabled[0].reason_code == 'disabled_by_ablation'
    for traces in (fired, disabled):
        for t in traces:
            validate_trace(t)


# ── over-bluff river ─────────────────────────────────────────────────────────
# Pumps river bet frequency for air as the bettor. Uses BASE (check + bet mass).

def test_over_bluff_pumps_river_bet_with_air():
    for hc in ('air_no_draw', 'air_strong_draw'):
        out, traces = _apply(tendencies=OVERBLUFF, hand_class=hc, street='river')
        assert _agg(out) > _agg(BASE), hc
        assert out.action_probabilities['check'] < BASE.action_probabilities['check'], hc
        assert traces[0].fired and traces[0].rule_id == 'over_bluff', hc
        assert abs(sum(out.action_probabilities.values()) - 1.0) < 1e-9, hc


def test_over_bluff_no_op_with_made_hands():
    # A made hand betting the river is value, not an over-bluff.
    for hc in ('nuts', 'strong_made', 'medium_made', 'weak_made'):
        out, traces = _apply(tendencies=OVERBLUFF, hand_class=hc, street='river')
        assert out is BASE and not traces[0].fired, hc


def test_over_bluff_no_op_off_river():
    out, traces = _apply(tendencies=OVERBLUFF, hand_class='air_no_draw', street='turn')
    assert out is BASE and not traces[0].fired


def test_over_bluff_no_op_facing_a_bet():
    # As the bettor only (unopened); facing a bet with air is a fold/call spot.
    out, traces = _apply(
        tendencies=OVERBLUFF, hand_class='air_no_draw', street='river', action_context='facing_bet'
    )
    assert out is BASE and not traces[0].fired


def test_over_bluff_respects_cap_and_ablation_and_validates():
    cap = 0.10
    out, _ = _apply(tendencies=OVERBLUFF, hand_class='air_no_draw', street='river', max_shift=cap)
    for action, base_p in BASE.action_probabilities.items():
        assert abs(out.action_probabilities[action] - base_p) <= cap + 1e-6, action
    _, fired = _apply(tendencies=OVERBLUFF, hand_class='air_no_draw', street='river')
    _, disabled = _apply(
        tendencies=OVERBLUFF, hand_class='air_no_draw', street='river',
        disable_rules=frozenset({(LAYER, 'over_bluff')}),
    )
    assert disabled[0].reason_code == 'disabled_by_ablation'
    for traces in (fired, disabled):
        for t in traces:
            validate_trace(t)


# ── per-personality override hook ────────────────────────────────────────────

def test_parse_spot_tendencies_normalizes():
    assert parse_spot_tendencies(None) == ()
    assert parse_spot_tendencies([]) == ()
    assert parse_spot_tendencies([['slowplay', 0.8]]) == (('slowplay', 0.8),)
    # float coercion + order preserved + accepts tuples
    assert parse_spot_tendencies((('slowplay', 1), ('donk', 0.5))) == (
        ('slowplay', 1.0), ('donk', 0.5),
    )


def _mk_controller(base=None, override=None, resolved=False, config=None):
    """Minimal controller (parent __init__ bypassed) exercising deviation_profile."""
    c = TieredBotController.__new__(TieredBotController)
    c._deviation_profile = base
    c._spot_tendencies_override = override
    c._spot_tendencies_resolved = resolved
    c.psychology = SimpleNamespace(personality_config=config) if config is not None else None
    return c


def test_no_override_returns_archetype_profile_unchanged():
    c = _mk_controller(base=DEVIATION_PROFILES['tag'])
    assert c.deviation_profile is DEVIATION_PROFILES['tag']
    assert c.deviation_profile.spot_tendencies == ()


def test_explicit_override_merges_onto_profile():
    c = _mk_controller(base=DEVIATION_PROFILES['tag'], override=(('slowplay', 0.8),))
    prof = c.deviation_profile
    assert prof.spot_tendencies == (('slowplay', 0.8),)
    # only spot_tendencies changed; the rest of TAG is intact
    assert prof.max_kl == DEVIATION_PROFILES['tag'].max_kl
    assert prof.aggression_scale == DEVIATION_PROFILES['tag'].aggression_scale


def test_override_resolved_from_personality_config():
    c = _mk_controller(
        base=DEVIATION_PROFILES['tag'],
        config={'spot_tendencies': [['slowplay', 0.6]]},
    )
    assert c.deviation_profile.spot_tendencies == (('slowplay', 0.6),)


def test_explicit_override_wins_over_config():
    c = _mk_controller(
        base=DEVIATION_PROFILES['tag'],
        override=(('slowplay', 0.9),),
        config={'spot_tendencies': [['slowplay', 0.1]]},
    )
    assert c.deviation_profile.spot_tendencies == (('slowplay', 0.9),)


def test_explicit_empty_override_turns_off_profile_tendencies():
    base = dataclasses.replace(
        DEVIATION_PROFILES['tag'], spot_tendencies=(('slowplay', 0.5),)
    )
    c = _mk_controller(base=base, override=())  # explicit () = opt out
    assert c.deviation_profile.spot_tendencies == ()


def test_absent_config_inherits_profile_tendencies():
    base = dataclasses.replace(
        DEVIATION_PROFILES['tag'], spot_tendencies=(('slowplay', 0.5),)
    )
    c = _mk_controller(base=base, config={})  # no 'spot_tendencies' key
    assert c.deviation_profile.spot_tendencies == (('slowplay', 0.5),)
