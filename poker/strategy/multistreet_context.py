"""Multi-street context override (docs/plans/STRUCTURAL_PASSIVITY_PLAN.md).

The postflop strategy table is a **memoryless per-street policy** keyed on
`street|position|pot_type|texture|hand_class|draw|action_context|spr`. Nothing
carries the *cross-street line*: when deciding the turn the bot has no record
of what it did on the flop. The diagnosed consequence is pathological
passivity — it bets ~4% with initiative, raises 0% facing bets, and almost
never continues a barrel (the `continue_story` failure), because it re-derives
a fresh distribution each street with no memory of the story it was telling.

This layer gives the postflop decision two cross-street signals the base
decision currently lacks, and applies them as a thin, narrowly-gated override
(NOT chart-key surgery — per-street chart edits were proven inert):

  - **H1 (initiative / barrel continuation):** when hero was the aggressor on
    the previous betting round (`was_prev_street_aggressor`) and the action is
    checked to it (`unopened`), pump the bet frequency for value / strong-draw
    classes — gated to HU spots so it never reintroduces the multiway
    over-aggression that already failed in testing.
  - **H2 (don't pay off barrels):** when facing a sustained multi-street value
    line (`facing_double_barrel`: opp bet flop AND the prior street) with a
    marginal made hand, pump the fold frequency (shift call -> fold).

## Pipeline placement

Runs after `bluff_catch_override` and immediately before `defense_floor`,
participating in `prior_layer_fired`. It mirrors `defense_floor`'s
"skip when an upstream override already replaced the strategy" pattern, and
defers to the downstream floors / math_floor (which keep final say on
pot-odds-mandated calls). Behind the `enable_multistreet_context` flag; OFF is
byte-identical to current behavior.

## Signal derivation (dual-path)

Production reads the live action log (`memory_manager.hand_recorder`). The sim
bypasses MemoryManager, so it reads the `_sim_hero_bet_by_street` /
`_sim_opp_bet_by_street` / `_sim_last_preflop_aggressor` shadow fields the
harness drives (mirroring the existing `_sim_*` aggressor tracking).
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

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

LAYER = 'multistreet_context'

_AGGRESSIVE_PHASES = ('FLOP', 'TURN', 'RIVER')
_AGGRESSIVE_ACTIONS = frozenset({'bet', 'raise', 'all_in'})

# ── H1: barrel-continuation bet-frequency targets by simplified hand_class ──
# (simplify_hand_class outputs). Value classes barrel for value; a strong draw
# barrels as a +EV semi-bluff. Marginal/air classes are intentionally absent —
# H1 continues a credible value/semi-bluff line, it is not a blanket bet-more.
H1_BARREL_TARGET: Dict[str, float] = {
    'nuts': 0.80,
    'strong_made': 0.70,
    'medium_made': 0.55,
    'air_strong_draw': 0.50,
}

# H1 fires only in spots with at most this many *active players* (HU = 2).
# Multiway suppression (poker/strategy/multiway.py) already (correctly) damps
# aggression at 3+ players; barreling a credible story into a field was tested
# and made bb/100 worse. Keep H1 to true HU.
H1_MAX_ACTIVE_PLAYERS = 2

# ── H2: fold-frequency targets when facing a double barrel, by hand_class ──
# Marginal made hands only (the coach's `dont_pay_double_barrels` is gated on
# one-pair "marginal" hands). Strong+ continues; air already folds.
H2_FOLD_TARGET: Dict[str, float] = {
    'weak_made': 0.80,
    'medium_made': 0.60,
}


@dataclass(frozen=True)
class MultiStreetSignals:
    """Cross-street line context the memoryless table lacks."""

    was_prev_street_aggressor: bool  # hero had/took initiative the prior round
    facing_double_barrel: bool  # opp bet flop AND the immediately-prior street


# ── Signal derivation ──────────────────────────────────────────────────────


def _line_from_recorder(
    controller,
) -> Optional[Tuple[Dict[str, bool], Dict[str, bool], Optional[str]]]:
    """Production path: derive per-street aggression from the live action log.

    Returns (hero_bet_by_street, opp_bet_by_street, preflop_aggressor) or None
    when no MemoryManager / recorded hand is available (→ caller falls back to
    the sim shadow fields).
    """
    mm = getattr(controller, 'memory_manager', None)
    recorder = getattr(mm, 'hand_recorder', None) if mm is not None else None
    current = getattr(recorder, 'current_hand', None) if recorder is not None else None
    actions = getattr(current, 'actions', None) if current is not None else None
    if actions is None:
        return None

    hero = controller.player_name
    hero_bet: Dict[str, bool] = {}
    opp_bet: Dict[str, bool] = {}
    preflop_aggressor: Optional[str] = None
    for a in actions:
        if a.phase == 'PRE_FLOP' and a.action in ('raise', 'all_in'):
            preflop_aggressor = a.player_name
        elif a.phase in _AGGRESSIVE_PHASES and a.action in _AGGRESSIVE_ACTIONS:
            if a.player_name == hero:
                hero_bet[a.phase] = True
            else:
                opp_bet[a.phase] = True
    return hero_bet, opp_bet, preflop_aggressor


def derive_signals(controller, street: str) -> MultiStreetSignals:
    """Derive the multi-street signals for the current `street` decision.

    `street` is the lowercase node street ('flop' / 'turn' / 'river').
    Production reads the live recorder; sim reads the `_sim_*` shadow fields.
    """
    line = _line_from_recorder(controller)
    if line is None:
        hero_bet = getattr(controller, '_sim_hero_bet_by_street', None) or {}
        opp_bet = getattr(controller, '_sim_opp_bet_by_street', None) or {}
        preflop_aggressor = getattr(controller, '_sim_last_preflop_aggressor', None)
    else:
        hero_bet, opp_bet, preflop_aggressor = line

    phase = street.upper()
    hero = controller.player_name

    # "Was hero the aggressor on the previous betting round?" — preflop raiser
    # for a flop decision; the prior postflop street's bettor otherwise. This
    # captures both the c-bet (flop, when hero raised preflop) and the barrel
    # (turn/river, when hero bet the prior street).
    if phase == 'FLOP':
        was_prev = preflop_aggressor == hero
    elif phase == 'TURN':
        was_prev = bool(hero_bet.get('FLOP'))
    elif phase == 'RIVER':
        was_prev = bool(hero_bet.get('TURN'))
    else:
        was_prev = False

    # Double barrel = opponent bet the flop AND the immediately-prior street
    # (the sustained value line). On the turn the "prior street" *is* the turn
    # bet hero is now facing; on the river it's the turn.
    if phase == 'TURN':
        facing_db = bool(opp_bet.get('FLOP')) and bool(opp_bet.get('TURN'))
    elif phase == 'RIVER':
        facing_db = bool(opp_bet.get('FLOP')) and bool(opp_bet.get('TURN'))
    else:
        facing_db = False

    return MultiStreetSignals(
        was_prev_street_aggressor=was_prev,
        facing_double_barrel=facing_db,
    )


# ── Distribution surgery ────────────────────────────────────────────────────


def _aggressive_keys(probs: Dict[str, float]):
    """Sized bet/raise/jam action keys present in the distribution.

    Matches value_override's `_raise_actions`: the strategy distribution uses
    sized abstract actions ('bet_67', 'raise_150', 'jam', 'all_in'); these are
    the keys to pump toward for a barrel.
    """
    return [a for a in probs if a in ('jam', 'all_in') or a.startswith(('bet_', 'raise_'))]


def _pump_bet(strategy: StrategyProfile, target: float) -> StrategyProfile:
    """Return a strategy with total bet/raise mass raised to `target`, drawing
    the delta proportionally from non-bet actions (check). Unchanged when no
    aggressive action is available, mass is already there, or check mass is 0.
    """
    probs = dict(strategy.action_probabilities)
    bets = _aggressive_keys(probs)
    if not bets:
        return strategy
    current = sum(probs[a] for a in bets)
    if current >= target:
        return strategy
    non_bet = sum(p for a, p in probs.items() if a not in bets)
    if non_bet <= 0.0:
        return strategy

    add = target - current
    scale = max(0.0, (non_bet - add)) / non_bet
    new: Dict[str, float] = {}
    for a, p in probs.items():
        if a not in bets:
            new[a] = p * scale
    # Distribute the freed mass across bet actions: proportional to existing
    # weight, or evenly when the chart gave them zero mass.
    if current > 0.0:
        for a in bets:
            new[a] = probs[a] + add * (probs[a] / current)
    else:
        even = add / len(bets)
        for a in bets:
            new[a] = probs[a] + even
    return StrategyProfile(action_probabilities=new)


def _pump_fold(strategy: StrategyProfile, target: float) -> StrategyProfile:
    """Return a strategy with `fold` raised to `target`, drawing the delta
    proportionally from non-fold actions (call/raise). Mirrors defense_floor's
    `_redistribute_to_call_target`, inverted onto fold.
    """
    probs = dict(strategy.action_probabilities)
    if 'fold' not in probs:
        return strategy
    current = probs.get('fold', 0.0)
    if current >= target:
        return strategy
    non_fold = sum(p for a, p in probs.items() if a != 'fold')
    if non_fold <= 0.0:
        return strategy
    scale = max(0.0, (non_fold - (target - current))) / non_fold
    return StrategyProfile(
        action_probabilities={a: (target if a == 'fold' else p * scale) for a, p in probs.items()}
    )


def _fire_trace(
    before: StrategyProfile,
    after: StrategyProfile,
    *,
    rule_id: str,
    effect: str,
    reason_code: str,
    signals: MultiStreetSignals,
    hand_class: str,
    action_context: str,
    target: float,
) -> InterventionTrace:
    return InterventionTrace(
        layer=LAYER,
        rule_id=rule_id,
        layer_order=layer_order_for(LAYER),
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect=effect,
        effect_size=l1_distance(
            before.action_probabilities,
            after.action_probabilities,
        ),
        action_changed=(
            primary_action(before.action_probabilities)
            != primary_action(after.action_probabilities)
        ),
        primary_action_before=primary_action(before.action_probabilities),
        primary_action_after=primary_action(after.action_probabilities),
        replaced_prior_action=True,
        reason_code=reason_code,
        rationale=(
            f'multistreet {rule_id}: hand_class={hand_class} '
            f'ctx={action_context} target={target:.2f} '
            f'prev_aggr={signals.was_prev_street_aggressor} '
            f'double_barrel={signals.facing_double_barrel}'
        ),
        inputs={
            'hand_class': hand_class,
            'action_context': action_context,
            'was_prev_street_aggressor': signals.was_prev_street_aggressor,
            'facing_double_barrel': signals.facing_double_barrel,
            'target': round(target, 4),
        },
        input_strategy_summary=summarize_strategy(before.action_probabilities),
        output_strategy_summary=summarize_strategy(after.action_probabilities),
    )


def apply_multistreet_context(
    strategy: StrategyProfile,
    *,
    signals: MultiStreetSignals,
    hand_class: str,
    action_context: str,
    active_count: int,
    h1_enabled: bool = True,
    h2_enabled: bool = True,
    h1_classes: Optional[frozenset] = None,
    h1_streets: Optional[frozenset] = None,
    street: Optional[str] = None,
    air_barrel_target: float = 0.0,
    air_barrel_fold_to_big_bet: Optional[float] = None,
    air_barrel_min_ftbb: float = 0.6,
    air_barrel_streets: Optional[frozenset] = None,
    prior_layer_fired: bool = False,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Apply the multi-street context override.

    Args:
        strategy: distribution coming out of bluff_catch_override.
        signals: derived cross-street line context.
        hand_class: simplify_hand_class output ('nuts'/'strong_made'/
            'medium_made'/'weak_made'/'air_strong_draw'/'air_no_draw').
        action_context: node.facing_action ('unopened'/'facing_bet'/
            'facing_raise').
        active_count: number of players still in the hand (HU = 2).
        h1_enabled / h2_enabled: per-hypothesis A/B toggles.
        h1_classes: optional subset of H1_BARREL_TARGET keys to barrel (the
            rest are skipped). None = all four. Used to A/B "value-only"
            barreling (drop air_strong_draw) vs high-WtSD opponents.
        prior_layer_fired: True iff an upstream override already replaced the
            distribution this decision — defer to it (mirrors defense_floor).
        disable_rules: ablation set; (LAYER,'barrel') / (LAYER,'fold_barrel').

    Returns `(new_strategy, trace)`; `new_strategy is strategy` on no-op.
    """
    order = layer_order_for(LAYER)

    if prior_layer_fired:
        return strategy, make_no_op_trace(
            LAYER,
            'default',
            order,
            reason_code='prior_override_active',
        )

    # ── H1: barrel / initiative continuation ────────────────────────────────
    # `h1_streets` (None = all) restricts the streets H1 barrels on. The
    # per-node attribution gate showed river barrel-continuation bleeds vs both
    # a folder and a reg (the "strong draw" has resolved by the river → bluffing
    # busted equity into a caller), while flop/turn continuation captures fold
    # equity vs over-folders. Pass {'FLOP','TURN'} to drop the toxic river leg.
    # ── H1-air: gated turn air-barrel (river-air SUPPLY build) ───────────────
    # Pure air (air_no_draw) is deliberately ABSENT from H1_BARREL_TARGET — the
    # bot gives it up on the turn, which starves the river bluff (T2): even
    # promoting 100% of give-up-air river checks tops out at ~31% bluff share
    # (< the ~37% GTO target) because little air survives to the river. This
    # branch barrels a fraction of TURN air so more of it reaches the checked-to
    # river for T2 to convert. Gated on a detected over-folder (fold_to_big_bet
    # >= min) + HU + turn-only — vs a caller / cold-start it never fires (the
    # barrel would just bleed). OFF by default (air_barrel_target=0.0) →
    # byte-identical. Sits before standard H1 (air_no_draw isn't an H1 class).
    air_reader = (
        air_barrel_fold_to_big_bet is not None
        and air_barrel_fold_to_big_bet >= air_barrel_min_ftbb
    )
    air_barrel_applies = (
        h1_enabled
        and air_barrel_target > 0.0
        and air_reader
        and signals.was_prev_street_aggressor
        and action_context == 'unopened'
        and active_count <= H1_MAX_ACTIVE_PLAYERS
        and hand_class == 'air_no_draw'
        and (street or '').upper() in (air_barrel_streets or frozenset({'TURN'}))
    )
    if air_barrel_applies:
        if is_rule_disabled(disable_rules, LAYER, 'barrel'):
            return strategy, make_disabled_trace(LAYER, 'barrel', order)
        new = _pump_bet(strategy, air_barrel_target)
        if new is not strategy:
            return new, _fire_trace(
                strategy,
                new,
                rule_id='barrel',
                effect='pump_bet_air',
                reason_code='air_barrel_turn',
                signals=signals,
                hand_class=hand_class,
                action_context=action_context,
                target=air_barrel_target,
            )
        return strategy, make_no_op_trace(
            LAYER, 'barrel', order, reason_code='no_bet_action_or_above_target',
        )

    barrel_classes = h1_classes if h1_classes is not None else H1_BARREL_TARGET.keys()
    h1_applies = (
        h1_enabled
        and signals.was_prev_street_aggressor
        and action_context == 'unopened'
        and active_count <= H1_MAX_ACTIVE_PLAYERS
        and hand_class in H1_BARREL_TARGET
        and hand_class in barrel_classes
        and (h1_streets is None or (street or '').upper() in h1_streets)
    )
    if h1_applies:
        if is_rule_disabled(disable_rules, LAYER, 'barrel'):
            return strategy, make_disabled_trace(LAYER, 'barrel', order)
        target = H1_BARREL_TARGET[hand_class]
        new = _pump_bet(strategy, target)
        if new is not strategy:
            return new, _fire_trace(
                strategy,
                new,
                rule_id='barrel',
                effect='pump_bet',
                reason_code=f'barrel_continuation_{hand_class}',
                signals=signals,
                hand_class=hand_class,
                action_context=action_context,
                target=target,
            )
        return strategy, make_no_op_trace(
            LAYER,
            'barrel',
            order,
            reason_code='no_bet_action_or_above_target',
        )

    # ── H2: don't pay off double barrels ────────────────────────────────────
    h2_applies = (
        h2_enabled
        and signals.facing_double_barrel
        and action_context in ('facing_bet', 'facing_raise')
        and hand_class in H2_FOLD_TARGET
    )
    if h2_applies:
        if is_rule_disabled(disable_rules, LAYER, 'fold_barrel'):
            return strategy, make_disabled_trace(LAYER, 'fold_barrel', order)
        target = H2_FOLD_TARGET[hand_class]
        new = _pump_fold(strategy, target)
        if new is not strategy:
            return new, _fire_trace(
                strategy,
                new,
                rule_id='fold_barrel',
                effect='pump_fold',
                reason_code=f'fold_double_barrel_{hand_class}',
                signals=signals,
                hand_class=hand_class,
                action_context=action_context,
                target=target,
            )
        return strategy, make_no_op_trace(
            LAYER,
            'fold_barrel',
            order,
            reason_code='no_fold_action_or_above_target',
        )

    return strategy, make_no_op_trace(
        LAYER,
        'default',
        order,
        reason_code='gates_not_met',
    )
