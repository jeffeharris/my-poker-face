"""Overbet sizing layer for polarized aggressor spots (docs/plans/POSTFLOP_NEXT_LEVER.md).

Per-node attribution gate, 2026-05-27, paired-CRN HU + 6-max:

  - The chart bet menu caps at ``bet_100`` (100% pot) — the bot is structurally
    incapable of overbetting. ``action_mapper.resolve_postflop_sizing`` already
    turns ``bet_150``/``bet_200``/etc. into correct chip amounts.
  - Adding value overbets (nuts / strong_made on dry turns, ~150% pot) measured
    +EV or neutral vs *every* opponent type — never negative: station +159,
    jeff +42 HU / +73 6-max, **punisher (reg) +13 [+8.5, +17.5]**, nit +11.5,
    lag +12.2. The reg floor (+13, CI-clear) is the headline: this is a
    universal value lever, not a fish exploit.
  - Bluff overbets add ~nothing (bot rarely bets pure air on later streets as
    the aggressor) → value-only captures the effect.

Honest caveats baked into defaults:

  - The probe was a "relabel all bet mass" max-overbet (``overbet_fraction=1.0``).
    Vs the realistic field (none of which reads sizing tells), face-up value
    overbetting is +EV and won't be punished — but the parameter is exposed for
    future tuning if a sizing-aware adapter is built.
  - Clone calling logic is pot-odds × stickiness; it cannot model overbet
    psychology (real-human size-fear). The +13 vs the reg is a conservative
    floor, not a humans number — the size-monotonic clone results
    (150/200/300 = +42/+48/+71 vs jeff) are likely inflated. Default size 150
    is the smallest validated overbet — defensible vs both clones and humans.
  - 6-max overbets fire multiway (the +73 includes multiway). ``max_active`` is
    None by default to match what was measured; a future multiway-vs-reg
    measurement can refine.

Pipeline placement
==================

Runs in the postflop pipeline AFTER ``multistreet_context`` (multistreet sets
the bet *frequency*, this layer sets the *size*) and BEFORE ``defense_floor``.
Mirrors ``multistreet_context``'s ``prior_layer_fired`` pattern and integrates
with ``intervention_trace``. Behind ``enable_overbet_context``; OFF arm is
byte-identical to pre-layer behavior.

Effect
======

When the gates fire, shift ``overbet_fraction`` of the existing ``bet_*`` mass
to ``bet_<overbet_size>``, scaling the remaining sized bets proportionally.
Check / call / raise / jam / fold are untouched — this is purely about *which
bet size* the bot uses when it bets value, not whether to bet.
"""

from typing import Dict, FrozenSet, Optional, Tuple

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
from .strategy_profile import StrategyProfile

LAYER = 'overbet_context'

DEFAULT_CLASSES: FrozenSet[str] = frozenset({'nuts', 'strong_made'})
# Bluff side (OVERBET_BALANCING.md T1): the air the bot already bets at runtime —
# semibluff draws first (equity + fold equity), then pure air. weak_made is
# excluded (it has showdown value; let it check and win rather than bluff it off).
# Routing a fraction of THIS bet-mass to the overbet size polarizes the overbet
# (value + bluff) so a sizing-reader can no longer profitably fold to it. Default
# OFF (overbet_bluff_fraction=0.0) → byte-identical to value-only.
DEFAULT_BLUFF_CLASSES: FrozenSet[str] = frozenset({'air_strong_draw', 'air_no_draw'})
DEFAULT_STREETS: FrozenSet[str] = frozenset({'TURN', 'RIVER'})
DEFAULT_SIZE = 150  # % of pot; bet_150 = 1.5x pot (smallest validated overbet)
DEFAULT_FRACTION = 1.0  # 1.0 = relabel all bet mass (matches the measured probe;
# leaves the parameter exposed for tuning when a sizing-aware opponent exists)


def _promote_check_to_bet(
    strategy: StrategyProfile, *, bet_key: str, fraction: float
) -> StrategyProfile:
    """Move ``fraction`` of the ``check`` mass to ``bet_key`` — *creating* bet
    mass where there was none.

    This is the river-bluff (T2) mechanism and is structurally different from
    ``_shift_bet_mass`` (T1), which only redistributes *existing* ``bet_*`` mass.
    The tell map (measure_passivity --tell-map) shows the bot gives up its river
    air into ``check`` (river big bets are ~95-100% value → a sizing-reader folds
    to them for free). T1 can't fix that — there is no air bet-mass to relabel —
    so the only way to de-face-up the river is to convert give-up-air checks into
    bets at the value size, balancing the betting range. Returns the input
    unchanged when there is no check mass to promote.
    """
    probs = dict(strategy.action_probabilities)
    check_mass = probs.get('check', 0.0)
    if check_mass <= 0.0 or fraction <= 0.0:
        return strategy
    move = check_mass * fraction
    new = dict(probs)
    remaining = check_mass - move
    if remaining > 1e-9:
        new['check'] = remaining
    else:
        new.pop('check', None)
    new[bet_key] = new.get(bet_key, 0.0) + move
    return StrategyProfile(action_probabilities=new)


def _shift_bet_mass(
    strategy: StrategyProfile, *, overbet_key: str, fraction: float
) -> StrategyProfile:
    """Move ``fraction`` of the total ``bet_*`` mass to ``overbet_key``,
    scaling the remaining sized bets proportionally. Returns the input
    unchanged when there is no bet mass to shift.

    `fraction == 1.0` collapses every existing bet size into the overbet
    (matches the load-time `_overbet_transform` probe in `ab_node_attribution`).
    """
    probs = dict(strategy.action_probabilities)
    bet_keys = [a for a in probs if a.startswith('bet_')]
    bet_mass = sum(probs[a] for a in bet_keys)
    if bet_mass <= 0.0 or fraction <= 0.0:
        return strategy

    keep = 1.0 - fraction
    new: Dict[str, float] = {}
    for a, p in probs.items():
        if a.startswith('bet_'):
            scaled = p * keep
            if scaled > 0.0:
                new[a] = scaled
        else:
            new[a] = p
    new[overbet_key] = new.get(overbet_key, 0.0) + bet_mass * fraction
    return StrategyProfile(action_probabilities=new)


def apply_overbet_context(
    strategy: StrategyProfile,
    *,
    hand_class: str,
    action_context: str,
    street: Optional[str],
    active_count: int,
    overbet_size: int = DEFAULT_SIZE,
    overbet_fraction: float = DEFAULT_FRACTION,
    overbet_classes: Optional[FrozenSet[str]] = None,
    overbet_streets: Optional[FrozenSet[str]] = None,
    overbet_max_active: Optional[int] = None,
    overbet_bluff_fraction: float = 0.0,
    overbet_bluff_classes: Optional[FrozenSet[str]] = None,
    river_bluff_fraction: float = 0.0,
    river_bluff_classes: Optional[FrozenSet[str]] = None,
    river_bluff_size: Optional[int] = None,
    river_bluff_fold_to_big_bet: Optional[float] = None,
    river_bluff_min_ftbb: float = 0.6,
    prior_layer_fired: bool = False,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Apply the overbet sizing override.

    Args:
        strategy: distribution coming out of ``multistreet_context``.
        hand_class: simplify_hand_class output ('nuts'/'strong_made'/
            'medium_made'/'weak_made'/'air_strong_draw'/'air_no_draw').
        action_context: node.facing_action ('unopened'/'facing_bet'/
            'facing_raise'). Overbet only fires on 'unopened' (the bot is
            *betting*, not raising).
        street: 'flop' / 'turn' / 'river' (case-insensitive).
        active_count: number of players still in the hand.
        overbet_size: pot-percentage size, e.g. 150 → bet_150 (150% pot).
        overbet_fraction: share of the existing bet mass to relabel to the
            overbet size. 1.0 = the measured probe (collapse all bet sizes).
        overbet_classes: hand_class set the layer fires on (default
            {'nuts', 'strong_made'} — the validated value-only set).
        overbet_streets: street set the layer fires on (default
            {'TURN', 'RIVER'} — where overbets earned in the matrix).
        overbet_max_active: if set, only fire when ``active_count <= max``.
            None (default) = no gate; the +73 6-max measurement included
            multiway overbets and was strongly positive.
        prior_layer_fired: True iff an upstream override already replaced the
            distribution this decision — defer to it (mirrors multistreet).
        disable_rules: ablation set; (LAYER, 'overbet').

    Returns ``(new_strategy, trace)``; ``new_strategy is strategy`` on no-op.
    """
    order = layer_order_for(LAYER)

    if prior_layer_fired:
        return strategy, make_no_op_trace(
            LAYER,
            'default',
            order,
            reason_code='prior_override_active',
        )

    value_classes = overbet_classes if overbet_classes is not None else DEFAULT_CLASSES
    bluff_classes = (
        overbet_bluff_classes if overbet_bluff_classes is not None else DEFAULT_BLUFF_CLASSES
    )
    streets = overbet_streets if overbet_streets is not None else DEFAULT_STREETS

    # ── River-bluff (T2): CREATE bluff supply from give-up-air checks ────────
    # The tell map shows the bot's river big bets are ~95-100% value (face-up).
    # T1 (_shift_bet_mass) can't fix it — there's no river air bet-mass to
    # relabel — so this path promotes give-up-air CHECK mass to a bet at the
    # value/overbet size, making the river betting range polar (value + bluff)
    # so a sizing-reader can't profitably fold to it. Takes precedence over the
    # T1 bluff path on the river (which would no-op for lack of bet mass).
    # Default OFF (river_bluff_fraction=0.0) → byte-identical.
    river_classes = (
        river_bluff_classes if river_bluff_classes is not None else DEFAULT_BLUFF_CLASSES
    )
    # Regime gate: fire ONLY vs a detected sizing-reader / over-folder
    # (fold_to_big_bet >= threshold). Cold-start / no read / a caller (read None
    # or below threshold) → value-only, so the river bluff never bleeds into the
    # fish that fill the game (measured cost −7.18 bb/100 vs a caller; gain only
    # +1.90 vs a reader). This is what makes balancing the river a vs-human win
    # at ~zero fish cost — its first consumer of the Phase A fold_to_big_bet read.
    reader_detected = (
        river_bluff_fold_to_big_bet is not None
        and river_bluff_fold_to_big_bet >= river_bluff_min_ftbb
    )
    is_river_bluff = (
        action_context == 'unopened'
        and (street or '').upper() == 'RIVER'
        and river_bluff_fraction > 0.0
        and reader_detected
        and hand_class in river_classes
        and (overbet_max_active is None or active_count <= overbet_max_active)
    )
    if is_river_bluff and not prior_layer_fired:
        if is_rule_disabled(disable_rules, LAYER, 'overbet'):
            return strategy, make_disabled_trace(LAYER, 'overbet', order)
        river_key = f'bet_{river_bluff_size if river_bluff_size is not None else overbet_size}'
        new = _promote_check_to_bet(strategy, bet_key=river_key, fraction=river_bluff_fraction)
        if new is strategy:
            return strategy, make_no_op_trace(
                LAYER,
                'overbet',
                order,
                reason_code='no_check_mass',
            )
        return new, InterventionTrace(
            layer=LAYER,
            rule_id='overbet',
            layer_order=order,
            fired=True,
            operation=InterventionOperation.OVERRIDE.value,
            effect='promote_check_to_river_bluff',
            effect_size=l1_distance(
                strategy.action_probabilities,
                new.action_probabilities,
            ),
            action_changed=(
                primary_action(strategy.action_probabilities)
                != primary_action(new.action_probabilities)
            ),
            primary_action_before=primary_action(strategy.action_probabilities),
            primary_action_after=primary_action(new.action_probabilities),
            replaced_prior_action=True,
            reason_code=f'river_bluff_{hand_class}',
            rationale=(
                f'river bluff (create): hand_class={hand_class} '
                f'size={river_key} fraction={river_bluff_fraction:.2f} '
                f'active={active_count}'
            ),
            inputs={
                'hand_class': hand_class,
                'action_context': action_context,
                'street': street,
                'active_count': active_count,
                'river_bluff_size': river_bluff_size
                if river_bluff_size is not None
                else overbet_size,
                'river_bluff_fraction': round(river_bluff_fraction, 4),
                'side': 'river_bluff',
            },
            input_strategy_summary=summarize_strategy(strategy.action_probabilities),
            output_strategy_summary=summarize_strategy(new.action_probabilities),
        )

    street_ok = (
        action_context == 'unopened'
        and (street or '').upper() in streets
        and (overbet_max_active is None or active_count <= overbet_max_active)
    )
    # Value side (always-on) vs bluff side (gated on overbet_bluff_fraction > 0).
    # Mutually exclusive per decision: a hand is either a value class or a bluff
    # class. The bluff side is what de-face-up's the overbet (OVERBET_BALANCING.md).
    is_value = street_ok and hand_class in value_classes
    is_bluff = street_ok and overbet_bluff_fraction > 0.0 and hand_class in bluff_classes
    if not (is_value or is_bluff):
        return strategy, make_no_op_trace(
            LAYER,
            'default',
            order,
            reason_code='gates_not_met',
        )

    if is_rule_disabled(disable_rules, LAYER, 'overbet'):
        return strategy, make_disabled_trace(LAYER, 'overbet', order)

    side = 'value' if is_value else 'bluff'
    fraction = overbet_fraction if is_value else overbet_bluff_fraction
    overbet_key = f'bet_{overbet_size}'
    new = _shift_bet_mass(strategy, overbet_key=overbet_key, fraction=fraction)
    if new is strategy:
        return strategy, make_no_op_trace(
            LAYER,
            'overbet',
            order,
            reason_code='no_bet_action',
        )

    return new, InterventionTrace(
        layer=LAYER,
        rule_id='overbet',
        layer_order=order,
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect=f'shift_bet_mass_to_overbet_{side}',
        effect_size=l1_distance(
            strategy.action_probabilities,
            new.action_probabilities,
        ),
        action_changed=(
            primary_action(strategy.action_probabilities)
            != primary_action(new.action_probabilities)
        ),
        primary_action_before=primary_action(strategy.action_probabilities),
        primary_action_after=primary_action(new.action_probabilities),
        replaced_prior_action=True,
        reason_code=f'overbet_{side}_{hand_class}',
        rationale=(
            f'overbet sizing ({side}): hand_class={hand_class} street={street} '
            f'size={overbet_size}% fraction={fraction:.2f} '
            f'active={active_count}'
        ),
        inputs={
            'hand_class': hand_class,
            'action_context': action_context,
            'street': street,
            'active_count': active_count,
            'overbet_size': overbet_size,
            'overbet_fraction': round(fraction, 4),
            'side': side,
        },
        input_strategy_summary=summarize_strategy(strategy.action_probabilities),
        output_strategy_summary=summarize_strategy(new.action_probabilities),
    )
