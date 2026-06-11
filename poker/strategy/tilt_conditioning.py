"""Tilt-conditioning layer (Option C — the full `tilt_conditioning` strategy layer).

Phase 2 of `docs/plans/PERCEPTIBILITY_CONDITIONING.md` (backlog #12). The
believability thesis (`docs/technical/ARCHETYPE_SHAPING_FINDINGS.md` §C): *a high
frequency is realistic; a constant high frequency is a caricature.* This layer
turns aggression into a **state** — when an archetype is freshly tilted by a
concrete cause (Tendler's 7 tilt types), it transiently spikes its re-raise /
aggression frequency, so a sustained-high reading becomes a *conditioned*
read the player can learn and exploit, not a flat per-archetype constant.

Shape & mechanics mirror the existing logit-space conditioners
(`personality_modifier`, `spot_tendencies`, `exploitation`): it emits a
logit-space offset, clips it per-action, renormalizes, and emits an
`InterventionTrace`. It is a **conditioner, not an override** — the math floor
still runs after it (it can never fold a hand the pot odds force).

Phase-2 invariant (test-locked): **inert by default.** Every shipped
`DeviationProfile` keeps `tilt_conditioning_cap == 0.0`, so with the flag off OR
no archetype opted in, this layer is a byte-identical no-op. Phase 3 (separate)
opts maniac in + lowers its baseline.

The two channels (the "both-channels" decision in the plan): when this layer
fires it emits a reason_code that `narration_facts` maps to an intuition-framed
observation ("still stinging from that last one"), so a tilt spike is
*telegraphed* through the avatar — readable, not silent.

────────────────────────────────────────────────────────────────────────────
Double-count guard (precise composition with `compute_trait_offsets`)
────────────────────────────────────────────────────────────────────────────
`personality_modifier.compute_trait_offsets` ALREADY applies an emotional
offset: for an aggressive emotional state (`tilted`/`overconfident`) it adds
`intensity * (1 - poise)` to every aggressive action and subtracts it from every
passive one (`personality_modifier.py:166-184`). That is the *generic, spot-blind
poise-gated* response and it is NOT re-handled here.

This layer is DISJOINT from that term in two ways, so the two compose without
stacking the same quantity:

  1. **Different selector.** This layer keys on
     `composure_state.pressure_source` — the *cause* of the tilt (bad_beat,
     got_sucked_out, …) — which `compute_trait_offsets` never reads. The
     personality term keys only on the coarse `emotional_state.state`
     quadrant. So a personality offset can exist with no pressure_source (no
     tilt-conditioning) and vice-versa.

  2. **Different magnitude source.** The personality term's size is
     `intensity * (1 - poise)`. This layer does NOT multiply by that term
     again — its offset is `rule.direction * profile.tilt_conditioning_cap`
     (a fixed per-profile reach), and `emotional_state.state` is used only as a
     binary SELECTOR (must be an aggressive tilt state) plus a light
     0/1 presence check, never re-applied as a magnitude. The cap is the binding
     lever (same role `max_per_action_shift` plays for the other layers).

  3. **Different scope.** It only fires in re-raise / postflop-aggressor spots
     (the spots the re-raise cap currently throttles), and only in the
     AGGRESSIVE direction. The PASSIVE direction (shaken/dissociated) is left
     entirely to `compute_trait_offsets`'s poise-gate — re-handling it here
     would double-count the protective shift.

Net: `compute_trait_offsets` says "an upset player plays a bit more aggressively
in general"; this layer says "*and* a player upset by a SPECIFIC bad beat, in a
re-raise spot, leans into it harder than its baseline." Two distinct reads, two
distinct logit contributions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .deviation_profiles import DeviationProfile
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
from .personality_modifier import _clip_and_normalize, categorize_action
from .strategy_profile import StrategyProfile

LAYER = 'tilt_conditioning'

# Emotional states this layer responds to. ONLY the aggressive tilt states —
# the passive ones (shaken/dissociated) are handled by compute_trait_offsets'
# poise-gate (see the double-count guard in the module docstring). 'composed'
# (or None) means no tilt → no-op.
_AGGRESSIVE_TILT_STATES = frozenset({'tilted', 'overconfident'})

# Scenario gate values.
_SCENARIO_PREFLOP_RERAISE = 'preflop_reraise'
_SCENARIO_POSTFLOP_AGGRESSOR = 'postflop_aggressor'
_SCENARIO_ALL = 'all'

# Position gate values.
_POSITION_ALL = 'all'
_POSITION_IP = 'IP'
_POSITION_OOP = 'OOP'

# Preflop positions that play in position (used to resolve IP/OOP at a
# PreflopNode, which carries a seat name rather than IP/OOP).
_PREFLOP_IP_POSITIONS = frozenset({'BTN', 'CO'})

# Preflop scenarios that count as a re-raise spot (the spots the re-raise cap
# throttles — where this layer specifically amplifies).
_RERAISE_SCENARIOS = frozenset({'vs_open', 'vs_3bet', 'vs_4bet'})


@dataclass(frozen=True)
class TiltScenarioRule:
    """One tilt-type → conditioning rule.

    Fields:
      tilt_type: the Tendler type key (matches composure_state.pressure_source).
      scenario_gate: 'preflop_reraise' / 'postflop_aggressor' / 'all'.
      position_gate: 'all' / 'IP' / 'OOP'.
      action_target: which action category the offset pushes ('aggressive' /
          'passive' / 'fold').
      direction: +1 amplifies the target category, -1 dampens it.
      max_magnitude: the rule's intrinsic logit reach BEFORE the profile cap.
          The applied magnitude is min(max_magnitude, profile.tilt_conditioning_cap).
    """

    tilt_type: str
    scenario_gate: str
    position_gate: str
    action_target: str
    direction: int
    max_magnitude: float


# ── Tendler's 7 tilt types → conditioning rules ──────────────────────────────
# Each maps a composure_state.pressure_source to an aggression amplification in
# re-raise spots. The CAUSE is the trigger; the emotion layer + relationship
# layer already carry the generic spot-blind response — this is the specific,
# re-raise-spot amplification the re-raise cap currently throttles.
#
#   bad_beat       → injustice tilt: "I should have won" → push back, re-raise wider.
#   got_sucked_out → injustice/variance tilt (the river cooler) → same.
#   big_loss       → desperation tilt: chasing the loss back → re-raise wider.
#   losing_streak  → running-bad tilt: frustration accumulation → re-raise wider.
#   nemesis_loss   → revenge/entitlement tilt vs the player who got you → lean in.
#   bluff_called   → mistake tilt: V1 keeps this CONSERVATIVE/neutral (a caught
#                    bluff makes a thinking player tighten, not spew). No-op for
#                    now (direction toward the value end is deferred); registered
#                    so the type is enumerated + telegraphable.
TILT_TYPE_RULES: dict[str, TiltScenarioRule] = {
    'bad_beat': TiltScenarioRule(
        tilt_type='bad_beat',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.6,
    ),
    'got_sucked_out': TiltScenarioRule(
        tilt_type='got_sucked_out',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.6,
    ),
    'big_loss': TiltScenarioRule(
        tilt_type='big_loss',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.5,
    ),
    'losing_streak': TiltScenarioRule(
        tilt_type='losing_streak',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.5,
    ),
    'nemesis_loss': TiltScenarioRule(
        tilt_type='nemesis_loss',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.6,
    ),
    # 'crippled' is also a negative composure event (psychology_model.py); treat
    # it as desperation tilt, same shape as big_loss.
    'crippled': TiltScenarioRule(
        tilt_type='crippled',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.5,
    ),
    # mistake tilt (caught bluffing): V1 conservative — registered + telegraphable
    # but magnitude 0.0 so it never shifts the distribution. Reason code still
    # fires for narration so the read is surfaced ("rattled — got caught").
    'bluff_called': TiltScenarioRule(
        tilt_type='bluff_called',
        scenario_gate=_SCENARIO_PREFLOP_RERAISE,
        position_gate=_POSITION_ALL,
        action_target='aggressive',
        direction=+1,
        max_magnitude=0.0,
    ),
}

# The set of reason_codes this layer can emit (the tilt-type codes). Registered
# in intervention_trace._RULE_IDS_BY_LAYER and narration_facts.
TILT_REASON_CODES: frozenset = frozenset(f'tilt_{t}' for t in TILT_TYPE_RULES)


def _resolve_tilt_type(emotional_state, composure_state) -> Optional[str]:
    """Resolve the active Tendler tilt type, or None when not tilted.

    Returns a key into TILT_TYPE_RULES. Requires BOTH:
      - an aggressive tilt emotional state (the binary selector; the passive
        states are left to compute_trait_offsets), AND
      - a known pressure_source on composure_state (the CAUSE).
    Returns None (no-op) when either is missing — including the composed state
    and the sim/__new__ path where composure_state is absent.
    """
    state = getattr(emotional_state, 'state', None) if emotional_state is not None else None
    if state not in _AGGRESSIVE_TILT_STATES:
        return None
    source = getattr(composure_state, 'pressure_source', '') if composure_state is not None else ''
    if not source or source not in TILT_TYPE_RULES:
        return None
    return source


def _resolve_scenario(node) -> str:
    """Resolve a node to a scenario_gate value.

    PreflopNode carries `scenario` (rfi/vs_open/vs_3bet/vs_4bet); a re-raise
    scenario → 'preflop_reraise'. A PostflopNode (has `street`, no preflop
    `scenario` semantics) → 'postflop_aggressor'. Anything else → 'all'
    (gates that require a specific scenario will simply not match).
    """
    scenario = getattr(node, 'scenario', None)
    if scenario in _RERAISE_SCENARIOS:
        return _SCENARIO_PREFLOP_RERAISE
    # PostflopNode has a street; PreflopNode does not.
    if getattr(node, 'street', None):
        return _SCENARIO_POSTFLOP_AGGRESSOR
    return _SCENARIO_ALL


def _resolve_position(node) -> str:
    """Resolve a node to an IP/OOP position bucket.

    PostflopNode.position is already 'IP'/'OOP'. PreflopNode.position is a seat
    name → map BTN/CO to IP, the rest to OOP. Unknown → 'OOP' (conservative).
    """
    pos = getattr(node, 'position', None)
    if pos in (_POSITION_IP, _POSITION_OOP):
        return pos
    if pos in _PREFLOP_IP_POSITIONS:
        return _POSITION_IP
    return _POSITION_OOP


def _scenario_matches(rule: TiltScenarioRule, resolved_scenario: str) -> bool:
    if rule.scenario_gate == _SCENARIO_ALL:
        return True
    return rule.scenario_gate == resolved_scenario


def _position_matches(rule: TiltScenarioRule, resolved_position: str) -> bool:
    if rule.position_gate == _POSITION_ALL:
        return True
    return rule.position_gate == resolved_position


def _apply_offset(
    strategy: StrategyProfile,
    *,
    action_target: str,
    direction: int,
    magnitude: float,
) -> StrategyProfile:
    """Apply a logit-space offset toward/away from a target action category.

    Mirrors the personality layer's mechanic (logit offset → softmax → clip →
    renormalize), but bounded directly by `magnitude` (the resolved cap) as the
    per-action clip. Pushes `direction * magnitude` onto every action in the
    target category and the opposite onto the complementary
    (aggressive↔passive) category, so probability mass actually moves rather
    than the whole distribution shifting uniformly.

    Returns `strategy` unchanged (identity) when magnitude<=0 or there is no
    mass to move.
    """
    if magnitude <= 0.0:
        return strategy

    keys = list(strategy.action_probabilities.keys())
    if len(keys) <= 1:
        return strategy

    base = np.array([strategy.action_probabilities[k] for k in keys], dtype=float)
    # Work in logit space so the offset composes the same way the other layers'
    # offsets do.
    logits = np.log(np.clip(base, 1e-12, None))
    offsets = np.zeros(len(keys))
    for i, action in enumerate(keys):
        cat = categorize_action(action)
        if cat == action_target:
            offsets[i] += direction * magnitude
        elif action_target == 'aggressive' and cat == 'passive':
            offsets[i] -= direction * magnitude
        elif action_target == 'passive' and cat == 'aggressive':
            offsets[i] -= direction * magnitude
        elif action_target == 'fold' and cat != 'fold':
            offsets[i] -= direction * magnitude

    shifted = logits + offsets
    shifted = shifted - np.max(shifted)
    exp = np.exp(shifted)
    new = exp / np.sum(exp)
    # Bound per-action movement by `magnitude` (the resolved cap) and renormalize
    # — identical to the personality / spot-tendency clamp.
    bounded = _clip_and_normalize(new, base, magnitude)
    return StrategyProfile(action_probabilities={k: float(bounded[i]) for i, k in enumerate(keys)})


def apply_tilt_conditioning(
    strategy: StrategyProfile,
    legal_actions: List[str],
    emotional_state,
    composure_state,
    node,
    archetype_rules: Tuple[TiltScenarioRule, ...],
    profile: DeviationProfile,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Apply the tilt-conditioning offset, gated by the profile's cap.

    Returns `(new_strategy, trace)`. Identity strategy + `fired=False` trace
    when:
      - the profile is inert (`tilt_conditioning_cap == 0.0`) — the Phase-2
        default for EVERY shipped archetype, so this is the byte-identical path,
      - the state is composed / not an aggressive tilt, or no pressure_source,
      - no archetype rule matches the resolved tilt type + scenario + position,
      - the resolved offset has no mass to move.

    Args:
        strategy: distribution coming out of the spot-tendencies layer.
        legal_actions: engine legal actions (reserved for parity with the other
            layers; the offset only moves mass between actions already present).
        emotional_state: EmotionalShift (`.state`/`.intensity`) — the SELECTOR.
        composure_state: ComposureState (`.pressure_source`) — the CAUSE.
        node: PreflopNode / PostflopNode — drives scenario + position gates.
        archetype_rules: the profile's `tilt_scenario_rules` (the rules this
            archetype has opted into). Empty → no-op.
        profile: the DeviationProfile (its `tilt_conditioning_cap` is the
            binding lever — 0.0 means inert).
        disable_rules: ablation set; (LAYER, 'tilt_<type>') suppresses a rule.
    """
    order = layer_order_for(LAYER)
    cap = float(getattr(profile, 'tilt_conditioning_cap', 0.0) or 0.0)

    # Fast inert path: the Phase-2 byte-identical guarantee.
    if cap <= 0.0 or not archetype_rules:
        return strategy, make_no_op_trace(LAYER, 'default', order, reason_code='inert_profile')

    tilt_type = _resolve_tilt_type(emotional_state, composure_state)
    if tilt_type is None:
        return strategy, make_no_op_trace(LAYER, 'default', order, reason_code='not_tilted')

    resolved_scenario = _resolve_scenario(node)
    resolved_position = _resolve_position(node)

    # Find the archetype's rule for this tilt type that also passes the
    # scenario + position gates.
    matched: Optional[TiltScenarioRule] = None
    for rule in archetype_rules:
        if rule.tilt_type != tilt_type:
            continue
        if not _scenario_matches(rule, resolved_scenario):
            continue
        if not _position_matches(rule, resolved_position):
            continue
        matched = rule
        break

    if matched is None:
        return strategy, make_no_op_trace(LAYER, 'default', order, reason_code='no_matching_rule')

    rule_id = f'tilt_{matched.tilt_type}'
    if is_rule_disabled(disable_rules, LAYER, rule_id):
        return strategy, make_disabled_trace(LAYER, rule_id, order)

    # The applied magnitude is bounded by the profile cap (the binding lever),
    # never exceeding it — this is the cap-clamp invariant the tests lock.
    magnitude = min(matched.max_magnitude, cap)

    new_strategy = _apply_offset(
        strategy,
        action_target=matched.action_target,
        direction=matched.direction,
        magnitude=magnitude,
    )

    if new_strategy is strategy or magnitude <= 0.0:
        return strategy, make_no_op_trace(LAYER, rule_id, order, reason_code='no_shift')

    effect = l1_distance(strategy.action_probabilities, new_strategy.action_probabilities)
    if effect <= 0.0:
        return strategy, make_no_op_trace(LAYER, rule_id, order, reason_code='no_shift')

    trace = InterventionTrace(
        layer=LAYER,
        rule_id=rule_id,
        layer_order=order,
        fired=True,
        operation=InterventionOperation.ADJUST.value,
        effect=f'{rule_id}_amplify',
        effect_size=effect,
        action_changed=(
            primary_action(strategy.action_probabilities)
            != primary_action(new_strategy.action_probabilities)
        ),
        primary_action_before=primary_action(strategy.action_probabilities),
        primary_action_after=primary_action(new_strategy.action_probabilities),
        preserved_prior_intent=True,
        reason_code=rule_id,
        rationale=(
            f'tilt_conditioning {matched.tilt_type}: scenario={resolved_scenario} '
            f'position={resolved_position} target={matched.action_target} '
            f'dir={matched.direction} mag={magnitude:.3f} (cap={cap:.3f})'
        ),
        confidence=1.0,
        inputs={
            'tilt_type': matched.tilt_type,
            'scenario': resolved_scenario,
            'position': resolved_position,
            'action_target': matched.action_target,
            'direction': matched.direction,
            'magnitude': round(magnitude, 4),
            'cap': round(cap, 4),
        },
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new_strategy.action_probabilities),
    )
    return new_strategy, trace
