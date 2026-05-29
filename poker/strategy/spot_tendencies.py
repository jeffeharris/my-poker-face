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


def _pump_fold(
    strategy: StrategyProfile,
    strength: float,
    max_shift: float,
) -> StrategyProfile:
    """Move a `strength` fraction of all non-fold mass onto fold.

    The over-fold reshape (fit-or-fold, fold-to-barrel): scale every non-fold
    action down and pile the freed mass onto fold, then bound by the per-action
    cap. Returns `strategy` unchanged when fold isn't legal or there's nothing
    to move. The downstream math/defense floors keep final say on
    pot-odds-mandated calls, so this can't fold a hand the odds force.
    """
    probs = dict(strategy.action_probabilities)
    if 'fold' not in probs:
        return strategy
    sources = [a for a in probs if a != 'fold']
    current = sum(probs[a] for a in sources)
    if not sources or current <= 0.0 or strength <= 0.0:
        return strategy

    moved = current * min(1.0, strength)
    src_scale = (current - moved) / current
    new = {a: (probs['fold'] + moved if a == 'fold' else p * src_scale) for a, p in probs.items()}
    return _bound_to_cap(strategy, StrategyProfile(action_probabilities=new), max_shift)


def _pump_aggression(
    strategy: StrategyProfile,
    strength: float,
    max_shift: float,
) -> StrategyProfile:
    """Move a `strength` fraction of passive (check/call) mass onto bet/raise.

    The over-bet reshape (auto-c-bet): the inverse of `_dampen_aggression`.
    Redistribute the freed mass across the existing bet/raise actions
    (proportional to chart weight, or evenly when the chart zeroed them), then
    bound by the per-action cap. Returns `strategy` unchanged when there are no
    bet actions or no passive mass to convert.
    """
    probs = dict(strategy.action_probabilities)
    bets = _aggressive_keys(probs)
    sources = [a for a in probs if a in ('check', 'call')]
    current = sum(probs[a] for a in sources)
    if not bets or not sources or current <= 0.0 or strength <= 0.0:
        return strategy

    moved = current * min(1.0, strength)
    src_scale = (current - moved) / current
    bet_total = sum(probs[a] for a in bets)
    new = {}
    for a, p in probs.items():
        if a in sources:
            new[a] = p * src_scale
        elif a in bets:
            new[a] = p + (moved * (p / bet_total) if bet_total > 0 else moved / len(bets))
        else:
            new[a] = p
    return _bound_to_cap(strategy, StrategyProfile(action_probabilities=new), max_shift)


def _dampen_fold(
    strategy: StrategyProfile,
    strength: float,
    max_shift: float,
) -> StrategyProfile:
    """Move a `strength` fraction of fold mass onto call (the can't-fold reshape).

    The sticky/pays-off station: instead of folding a beat hand to a bet, call.
    Returns `strategy` unchanged when fold or call isn't legal, or there's no
    fold mass to move. The downstream floors only ever *add* calls (pot-odds), so
    this pumped-call survives — it's the leak the value overbet punishes.
    """
    probs = dict(strategy.action_probabilities)
    if 'fold' not in probs or 'call' not in probs:
        return strategy
    current = probs['fold']
    if current <= 0.0 or strength <= 0.0:
        return strategy
    moved = current * min(1.0, strength)
    new = dict(probs)
    new['fold'] = current - moved
    new['call'] = probs['call'] + moved
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
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
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


# ── give-up turn / one-and-done ──────────────────────────────────────────────
# The "no second barrel" leak: a player c-bets the flop (takes initiative) with
# a wide range, then on the turn — when checked to — gives up everything that
# isn't strong value, checking back instead of firing the second barrel. It is
# the **dual of the multistreet H1 barrel** (which *pumps* turn bet frequency
# for these same thin/semi-bluff classes); float-flop-steal-turn is its
# exploiter. Slow-play dampens the strong end (trap); give-up-turn dampens the
# *thin/bluff* end (no follow-through) — the two are disjoint by hand class.
#
# Turn only: the flop c-bet is the first barrel (initiative is taken there, so a
# flop give-up isn't "one-and-done"), and a river give-up is a distinct read.
# Strong value (nuts/strong_made) is intentionally excluded — a give-up player
# still bets those for value; abandoning them would be a (different) slow-play.
_GIVEUP_CLASSES = frozenset({'medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw'})
_GIVEUP_STREETS = frozenset({'turn'})


def _give_up_turn(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Give-up-turn handler. Returns (new_strategy, reason_code).

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    Reuses `_dampen_aggression` (the slow-play reshape) — same mechanism (move
    bet mass to check), different gate (thin/bluff classes on the turn).
    """
    applies = (
        hand_class in _GIVEUP_CLASSES
        and has_initiative
        and action_context == 'unopened'
        and (street or '').lower() in _GIVEUP_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _dampen_aggression(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_bet_mass_or_sink'
    return new, f'give_up_turn_{hand_class}'


# ── fit-or-fold / over-fold to c-bet ─────────────────────────────────────────
# The classic "no-pair-no-play" leak: facing a flop c-bet, continue ONLY with a
# strong made hand and fold everything else. Its exploiter is "barrel
# relentlessly" (any two cards print vs a player who only continues with the
# nuts-ish). Crucially this includes folding hands WITH equity — 2nd pair and
# flush/straight draws — which is what makes it a genuine −EV leak the barreler
# punishes (the narrow weak/air-only version priced free, because folding pure
# air is ~correct; the multiway gate confirmed it's the gate width, not the
# regime — docs/plans/MULTIWAY_PRICING_GATE.md). So the fold range is everything
# except {nuts, strong_made}.
_FITFOLD_CLASSES = frozenset({'medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw'})
_FITFOLD_STREETS = frozenset({'flop'})


def _fit_or_fold(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Fit-or-fold handler. Over-fold the weak/air range to a flop c-bet.

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    Initiative is intentionally not gated: the spot is facing a bet (the bettor
    holds initiative), and over-folding to it is the leak whether or not hero
    raised preflop.
    """
    applies = (
        hand_class in _FITFOLD_CLASSES
        and action_context == 'facing_bet'
        and (street or '').lower() in _FITFOLD_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _pump_fold(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_fold_action_or_mass'
    return new, f'fit_or_fold_{hand_class}'


# ── auto-c-bet / c-bets-100% ─────────────────────────────────────────────────
# The over-aggressive flop leak: c-bet with initiative regardless of holding,
# firing the hands a balanced range would check back. Pumps bet frequency for
# the checking part of the range (the thin/bluff classes; strong value already
# bets). Strong value (nuts/strong_made) is excluded — pumping it is a no-op
# (already bets) and conceptually it isn't the leak. Its exploiter is
# float/raise their c-bets. Note the duality with give-up-turn: an auto-c-bet +
# give-up-turn personality is the textbook "one-and-done" — fires every flop,
# folds the turn.
_AUTOCBET_CLASSES = frozenset({'medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw'})
_AUTOCBET_STREETS = frozenset({'flop'})


def _auto_cbet(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Auto-c-bet handler. Pump flop bet frequency with initiative.

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    """
    applies = (
        hand_class in _AUTOCBET_CLASSES
        and has_initiative
        and action_context == 'unopened'
        and (street or '').lower() in _AUTOCBET_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _pump_aggression(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_bet_action_or_passive_mass'
    return new, f'auto_cbet_{hand_class}'


# ── sticky / pays-off ────────────────────────────────────────────────────────
# The calling-station leak, spot-localized: facing a river bet/raise with a weak
# made hand, call instead of folding — "can't fold a pair." Unlike the global
# calling_station deviation profile (which loosens everywhere), this fires only
# on the river bluff-catch spot, the one the value overbet (built, +42 bb/100 vs
# payers) is designed to punish. A genuine −EV leak: paying off value bets with
# beat hands bleeds chips. Its exploiter is "value-bet thin + overbet, never
# bluff."
_STICKY_CLASSES = frozenset({'weak_made', 'medium_made'})
_STICKY_STREETS = frozenset({'river'})


def _sticky(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Sticky/pays-off handler. Over-call weak made hands to a river bet.

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    """
    applies = (
        hand_class in _STICKY_CLASSES
        and action_context in ('facing_bet', 'facing_raise')
        and (street or '').lower() in _STICKY_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _dampen_fold(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_fold_mass_or_call'
    return new, f'sticky_{hand_class}'


# ── over-bluff river ─────────────────────────────────────────────────────────
# Fire too many river bluffs: as the bettor on the river with air, bet at a
# higher frequency than the balanced chart (which the river bluff guardrail caps
# to an unexploitable rate). The *dual of the guardrail* — the guardrail is the
# defense that keeps a balanced bot from over-bluffing; this tendency is the leak
# of a player who bluffs anyway. NOTE the pipeline runs the guardrail (step 6)
# *before* this layer (step 6.b), so the pumped bluff is not re-capped — the two
# are mutually exclusive by intent. Its exploiter is "over-call bluff-catchers."
_OVERBLUFF_CLASSES = frozenset({'air_no_draw', 'air_strong_draw'})
_OVERBLUFF_STREETS = frozenset({'river'})


def _over_bluff(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Over-bluff handler. Pump river bet frequency with air, as the bettor.

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    Captures both the triple-barrel bluff and the river stab (no initiative
    required — `unopened` means hero can bet).
    """
    applies = (
        hand_class in _OVERBLUFF_CLASSES
        and action_context == 'unopened'
        and (street or '').lower() in _OVERBLUFF_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _pump_aggression(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_bet_action_or_passive_mass'
    return new, f'over_bluff_{hand_class}'


# ── under-bluff river ────────────────────────────────────────────────────────
# The inverse of over-bluff: as the river bettor with air, *never* pull the
# trigger — check back the busted hands a balanced range would bluff. The "honest"
# / face-up bettor: when they bet the river it's always value. Recognizable, and
# the human-learnable counter is "over-fold to their river bets, call their turn
# bets." Reuses the slow-play dampen (bet mass → check), gated on river air.
_UNDERBLUFF_CLASSES = frozenset({'air_no_draw', 'air_strong_draw'})
_UNDERBLUFF_STREETS = frozenset({'river'})


def _under_bluff(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Under-bluff handler. Dampen river bet frequency with air, as the bettor.

    `new_strategy is strategy` (identity) signals "gate not met / no-op".
    """
    applies = (
        hand_class in _UNDERBLUFF_CLASSES
        and action_context == 'unopened'
        and (street or '').lower() in _UNDERBLUFF_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _dampen_aggression(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_bet_mass_or_sink'
    return new, f'under_bluff_{hand_class}'


# ── over-fold to 2nd barrel ──────────────────────────────────────────────────
# Don't-pay-the-turn: facing a sustained value line (opp bet flop AND turn) with
# a marginal made hand, over-fold instead of calling. Unlike fit-or-fold (flop,
# cheap because marginal hands face later barrels), THIS is the turn commit where
# folding a hand that's actually ahead is a real mistake — it's the leak the
# double-barrel exploiter (multistreet H2) is built to punish. Gated on the
# `facing_double_barrel` signal (opp bet flop + the prior street), marginal made
# classes only (strong continues, air already folds).
_FOLD2B_CLASSES = frozenset({'medium_made', 'weak_made'})
_FOLD2B_STREETS = frozenset({'turn', 'river'})


def _over_fold_2nd_barrel(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Over-fold-to-2nd-barrel handler. Over-fold marginal made vs a sustained
    value line. `new_strategy is strategy` (identity) signals no-op."""
    applies = (
        hand_class in _FOLD2B_CLASSES
        and facing_double_barrel
        and action_context in ('facing_bet', 'facing_raise')
        and (street or '').lower() in _FOLD2B_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _pump_fold(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_fold_action_or_mass'
    return new, f'over_fold_2nd_barrel_{hand_class}'


# ── donk-when-weak / tiny donk ───────────────────────────────────────────────
# Lead into the aggressor OOP with weak hands instead of checking: the OOP player
# who, having NOT taken the prior aggression, bets out (donks) the part of the
# range that should check-and-fold or check-call. A readable, exploitable spot —
# the donk is face-up weak, and the counter is to raise it. Gated on OOP +
# unopened (first to act OOP) + NOT the prior aggressor + weak/medium classes.
_DONK_CLASSES = frozenset({'medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw'})
_DONK_STREETS = frozenset({'flop', 'turn'})


def _donk_when_weak(
    strategy: StrategyProfile,
    strength: float,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    has_initiative: bool,
    max_shift: float,
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
    **_,
) -> Tuple[StrategyProfile, str]:
    """Donk-when-weak handler. Pump bet (donk) OOP with weak hands into the
    aggressor. `new_strategy is strategy` (identity) signals no-op."""
    applies = (
        hand_class in _DONK_CLASSES
        and position == 'OOP'
        and not has_initiative
        and action_context == 'unopened'
        and (street or '').lower() in _DONK_STREETS
    )
    if not applies:
        return strategy, 'gate_not_met'
    new = _pump_aggression(strategy, strength, max_shift)
    if new is strategy:
        return strategy, 'no_bet_action_or_passive_mass'
    return new, f'donk_when_weak_{hand_class}'


# name -> handler. Add backlog tendencies (open-limp, position-blindness, ...) here.
_TENDENCIES = {
    'slowplay': _slowplay,
    'give_up_turn': _give_up_turn,
    'fit_or_fold': _fit_or_fold,
    'auto_cbet': _auto_cbet,
    'sticky': _sticky,
    'over_bluff': _over_bluff,
    'under_bluff': _under_bluff,
    'over_fold_2nd_barrel': _over_fold_2nd_barrel,
    'donk_when_weak': _donk_when_weak,
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
    facing_double_barrel: bool = False,
    position: Optional[str] = None,
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
        facing_double_barrel: opp bet flop AND the prior street (multistreet's
            facing_double_barrel) — drives over-fold-to-2nd-barrel.
        position: 'IP'/'OOP' (node.position) — drives donk-when-weak.
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
            facing_double_barrel=facing_double_barrel,
            position=position,
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
