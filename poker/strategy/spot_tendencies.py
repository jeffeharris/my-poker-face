"""Spot/line-specific personality tendencies (PERSONALITY_PRICING_AND_VARIETY.md, item 3).

The global-scalar personality distortion (`personality_modifier.modify_strategy`)
is **spot-blind**: it applies the same logit offsets at every node. This layer adds
*spot-specific* tendencies — reshapes that fire only in particular situations (e.g.
slow-play a strong hand when you have initiative on the flop/turn) — keyed on the
node + line context the memoryless distortion can't see.

It mirrors the two existing post-personality reshapes:
  - `apply_river_bluff_guardrail` (a spot-gated reshape that runs after personality),
  - `apply_multistreet_context` (gated, traced, ablatable). In fact **slow-play is the
    inverse of the multistreet H1 barrel**: H1 *pumps* bet frequency for strong classes
    with initiative; slow-play *dampens* it (trap instead of fast-play).

Each tendency is gated by per-profile config (`DeviationProfile.spot_tendencies`:
`((name, strength), ...)`), bounded by the profile's per-action cap
(`max_per_action_shift` — the lever that actually binds; KL is inert, see the plan
doc), emits an `InterventionTrace`, and is ablatable via `disable_rules`
(`(LAYER, name)`). Defaults ship with no tendencies on until each is priced + budgeted.
"""

from typing import List, Optional, Tuple

import numpy as np

from .intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    is_rule_disabled,
    l1_distance,
    layer_order_for,
    make_disabled_trace,
    make_no_op_trace,
    primary_action,
    summarize_strategy,
)
from .personality_modifier import _clip_and_normalize
from .strategy_profile import StrategyProfile

LAYER = 'spot_tendencies'

# ── slow-play / trap ────────────────────────────────────────────────────────
# Trap the strongest made hands by checking instead of betting when you hold
# initiative and the action is checked to you. Flop/turn only: river slow-play
# is a distinct read and the river bluff guardrail already shapes that street.
_SLOWPLAY_CLASSES = frozenset({'nuts', 'strong_made'})
_SLOWPLAY_STREETS = frozenset({'flop', 'turn'})


def _aggressive_keys(probs):
    """Sized bet/raise/jam action keys present in the distribution.

    Matches multistreet_context._aggressive_keys: the strategy distribution uses
    sized abstract actions ('bet_67', 'raise_150', 'jam', 'all_in').
    """
    return [a for a in probs if a in ('jam', 'all_in') or a.startswith(('bet_', 'raise_'))]


def _bound_to_cap(
    base: StrategyProfile,
    candidate: StrategyProfile,
    max_shift: float,
) -> StrategyProfile:
    """Clamp `candidate` so no action moved more than `max_shift` from `base`.

    Reuses the personality layer's iterative clip-renormalize so a spot
    tendency respects the same per-action EV budget as the global-scalar
    distortion. `candidate` shares `base`'s action keys (the reshapes only
    move mass between existing actions).
    """
    keys = list(base.action_probabilities.keys())
    base_arr = np.array([base.action_probabilities[k] for k in keys])
    cand_arr = np.array([candidate.action_probabilities[k] for k in keys])
    bounded = _clip_and_normalize(cand_arr, base_arr, max_shift)
    return StrategyProfile(
        action_probabilities={k: float(bounded[i]) for i, k in enumerate(keys)}
    )


def _dampen_aggression(
    strategy: StrategyProfile,
    strength: float,
    max_shift: float,
) -> StrategyProfile:
    """Move a `strength` fraction of bet/raise mass onto check (else call).

    The trap reshape: reduce aggressive mass, redistribute to the passive sink
    proportionally, then bound by the per-action cap. Returns `strategy`
    unchanged when there's no bet mass to move or no passive sink to absorb it.
    """
    probs = dict(strategy.action_probabilities)
    bets = _aggressive_keys(probs)
    # Prefer check as the trap sink; fall back to call if checking isn't legal.
    sinks = [a for a in probs if a == 'check'] or [a for a in probs if a == 'call']
    current = sum(probs[a] for a in bets)
    if not bets or not sinks or current <= 0.0 or strength <= 0.0:
        return strategy

    removed = current * min(1.0, strength)
    bet_scale = (current - removed) / current
    sink_total = sum(probs[a] for a in sinks)
    new = {}
    for a, p in probs.items():
        if a in bets:
            new[a] = p * bet_scale
        elif a in sinks:
            new[a] = p + (removed * (p / sink_total) if sink_total > 0 else removed / len(sinks))
        else:
            new[a] = p
    return _bound_to_cap(strategy, StrategyProfile(action_probabilities=new), max_shift)


def _slowplay(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
) -> Tuple[StrategyProfile, str]:
    """Slow-play handler. Returns (new_strategy, reason_code).

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    """
    applies = (
        hand_class in _SLOWPLAY_CLASSES
        and has_initiative
        and action_context == 'unopened'
        and (street or '').lower() in _SLOWPLAY_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _dampen_aggression(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_bet_mass_or_sink'
    return new, f'slowplay_{hand_class}'


# name -> handler. Add backlog tendencies (donk, open-limp, ...) here.
_TENDENCIES = {
    'slowplay': _slowplay,
}


def _fire_trace(
    before: StrategyProfile,
    after: StrategyProfile,
    *,
    rule_id: str,
    reason_code: str,
    strength: float,
    hand_class: str,
    action_context: str,
    has_initiative: bool,
) -> InterventionTrace:
    return InterventionTrace(
        layer=LAYER,
        rule_id=rule_id,
        layer_order=layer_order_for(LAYER),
        fired=True,
        operation=InterventionOperation.ADJUST.value,
        effect=f'{rule_id}_reshape',
        effect_size=l1_distance(before.action_probabilities, after.action_probabilities),
        action_changed=(
            primary_action(before.action_probabilities)
            != primary_action(after.action_probabilities)
        ),
        primary_action_before=primary_action(before.action_probabilities),
        primary_action_after=primary_action(after.action_probabilities),
        preserved_prior_intent=True,
        reason_code=reason_code,
        rationale=(
            f'spot_tendency {rule_id}: hand_class={hand_class} ctx={action_context} '
            f'initiative={has_initiative} strength={strength:.2f}'
        ),
        inputs={
            'rule_id': rule_id,
            'strength': round(strength, 4),
            'hand_class': hand_class,
            'action_context': action_context,
            'has_initiative': has_initiative,
        },
        input_strategy_summary=summarize_strategy(before.action_probabilities),
        output_strategy_summary=summarize_strategy(after.action_probabilities),
    )


def apply_spot_tendencies(
    strategy: StrategyProfile,
    *,
    spot_tendencies: Tuple[Tuple[str, float], ...],
    max_per_action_shift: float,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    disable_rules=None,
) -> Tuple[StrategyProfile, List[InterventionTrace]]:
    """Apply a profile's configured spot tendencies, in config order.

    Args:
        strategy: distribution coming out of the personality layer.
        spot_tendencies: the profile's ((name, strength), ...) config.
        max_per_action_shift: the profile's per-action cap (the bounding lever).
        hand_class: simplify_hand_class output ('nuts'/'strong_made'/...).
        action_context: node.facing_action ('unopened'/'facing_bet'/'facing_raise').
        street: lowercase node street.
        has_initiative: hero was the aggressor on the previous betting round
            (multistreet's was_prev_street_aggressor).
        disable_rules: ablation set; (LAYER, name) suppresses a tendency.

    Returns `(new_strategy, traces)`; `new_strategy is strategy` when nothing fired.
    Each configured tendency contributes exactly one trace (fire / no-op / disabled).
    """
    order = layer_order_for(LAYER)
    traces: List[InterventionTrace] = []
    current = strategy

    for name, strength in spot_tendencies:
        handler = _TENDENCIES.get(name)
        if handler is None:
            continue  # forward-compatible: unknown name is ignored
        if is_rule_disabled(disable_rules, LAYER, name):
            traces.append(make_disabled_trace(LAYER, name, order))
            continue
        new, reason_code = handler(
            current,
            strength,
            hand_class=hand_class,
            action_context=action_context,
            street=street,
            has_initiative=has_initiative,
            max_shift=max_per_action_shift,
        )
        if new is not current:
            traces.append(
                _fire_trace(
                    current,
                    new,
                    rule_id=name,
                    reason_code=reason_code,
                    strength=strength,
                    hand_class=hand_class,
                    action_context=action_context,
                    has_initiative=has_initiative,
                )
            )
            current = new
        else:
            traces.append(make_no_op_trace(LAYER, name, order, reason_code=reason_code))

    return current, traces
