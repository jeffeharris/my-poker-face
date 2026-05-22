"""Price-sensitive defense floor (TIEREDBOT_DECISION_QUALITY.md §2).

Prevents pure-folding of legitimate made hands at favorable prices.
Reads `(hand_class, nut_status, danger_flags)` from §1 and
`required_equity` from §4 — all available on DecisionContext and
the controller's pipeline snapshot.

## Matrix (top-down, first match wins)

| Condition                                               | Effect                  |
|---------------------------------------------------------|-------------------------|
| `hand_class == air`                                     | no floor (explicit)     |
| `nut_status == bluff_catcher`                           | no floor — §7.5 handles |
| `req ≤ 45%` AND `nut_status ∈ {near_nuts, actual_nuts}` | strongly prefer call    |
| `req ≤ 35%` AND (strong+ class OR `non_nut_strong`)     | keep call alive         |
| `req ≤ 20%` AND `hand_class ∈ {medium_made, …, nuts}`   | keep call alive         |

A "jam-price value-call" row for `non_nut_strong + strong_made/nuts`
at req 35-50% was tested via 1000×5 sim and *rejected* — see the
ROW_JAM_VALUE_CALL_MAX_REQ comment below for why the empirical
data argued against it.

## Pipeline placement

Sits between `_apply_bluff_catch_override` (Phase 7.5) and
`apply_short_stack_heuristics`. Skips when an upstream override
(value_override / bluff_catch_override) already replaced the
strategy — both are stronger interventions and the floor is a
fallback for the cases neither covers.

## Board-danger dampener

Board-level danger flags (`paired_board`, `four_straight_board`,
`four_flush_board`) reduce the floor's magnitude rather than
zeroing it. Each flag dampens by 15%, floor at 40% of full
magnitude. Hand-specific flags (`higher_straight_possible` etc.)
are *not* applied here — they're already encoded in `nut_status`
(a higher_straight_possible hand has `nut_status=non_nut_strong`,
which routes to the matrix's row 4 rather than row 3). Counting
them again would double-dampen.
"""

from typing import FrozenSet, Optional, Tuple

from .hand_classification import (
    FOUR_FLUSH_BOARD,
    FOUR_STRAIGHT_BOARD,
    NUT_ACTUAL,
    NUT_BLUFF_CATCHER,
    NUT_NEAR,
    NUT_NON_NUT_STRONG,
    PAIRED_BOARD,
)
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


# ── Matrix tunables ────────────────────────────────────────────────

# Row 3: "strongly prefer continue" — near/actual nuts at ≤45% req
FLOOR_TARGET_STRONG = 0.95

# Rows 4 and 5: "keep call alive"
FLOOR_TARGET_KEEP_ALIVE = 0.80

# Price ceilings per matrix row
ROW_STRONG_MAX_REQ = 0.45
ROW_KEEP_ALIVE_STRONG_MAX_REQ = 0.35
ROW_KEEP_ALIVE_MEDIUM_MAX_REQ = 0.20

# Hand classes that qualify for row 4 ("strong+ OR non_nut_strong")
_ROW_STRONG_CLASSES = frozenset({'strong_made', 'nuts'})

# Hand classes that qualify for row 5 ("medium+")
_ROW_MEDIUM_CLASSES = frozenset({'medium_made', 'strong_made', 'nuts'})

# Sim-validated decision (post-§6 1000×5 sim): a candidate "jam-price
# value-call" row for `non_nut_strong + strong_made/nuts` hands at
# req 35-50% was added, tested, and *reverted* when the sim showed
# the extra calls were net-negative (~-3.5 bb/100 across 5000 hands).
# The original assumption was that CaseBot's jam range was wide
# enough for `non_nut_strong` to call profitably; the data disagreed.
# Those folds are correct against CaseBot's actual (tight) jam
# range. If a future opponent profile has a demonstrably wider jam
# range, this row can be reintroduced with archetype gating.

# Board-only danger flags that count for the dampener. Hand-specific
# flags are already absorbed into nut_status; including them here
# would double-count.
_BOARD_DANGER_FLAGS = frozenset({
    PAIRED_BOARD, FOUR_STRAIGHT_BOARD, FOUR_FLUSH_BOARD,
})

# Multiplicative dampener: each board danger flag reduces magnitude
# by 15%, never below 40% of the un-dampened floor.
DANGER_DAMPENER_PER_FLAG = 0.15
DANGER_DAMPENER_FLOOR = 0.40


def _floor_target_call_prob(
    hand_class: str,
    nut_status: str,
    required_equity: float,
) -> float:
    """Apply the §2 matrix. Returns 0.0 when no row matches."""
    # Row 1: air — no floor. simplify_hand_class() emits 'air_no_draw'
    # and 'air_strong_draw'; check both plus the legacy 'air' label so
    # the row isn't dead code that the bluff_catcher check has to cover.
    if hand_class in {'air', 'air_no_draw', 'air_strong_draw'}:
        return 0.0

    # Row 2: bluff_catcher — defer to §7.5 bluff_catch_override
    if nut_status == NUT_BLUFF_CATCHER:
        return 0.0

    # Row 3: strongly prefer continue
    if (
        required_equity <= ROW_STRONG_MAX_REQ
        and nut_status in (NUT_NEAR, NUT_ACTUAL)
    ):
        return FLOOR_TARGET_STRONG

    # Row 4: keep call alive — strong+ class OR non_nut_strong
    if required_equity <= ROW_KEEP_ALIVE_STRONG_MAX_REQ:
        if (
            hand_class in _ROW_STRONG_CLASSES
            or nut_status == NUT_NON_NUT_STRONG
        ):
            return FLOOR_TARGET_KEEP_ALIVE

    # Row 5: keep call alive — medium+ at cheap prices
    if required_equity <= ROW_KEEP_ALIVE_MEDIUM_MAX_REQ:
        if hand_class in _ROW_MEDIUM_CLASSES:
            return FLOOR_TARGET_KEEP_ALIVE

    return 0.0


def _apply_danger_dampener(
    target: float,
    current: float,
    danger_flags: FrozenSet[str],
    disable_rules=None,
) -> float:
    """Scale the gap between current and target by a danger-aware factor.

    With 0 board-danger flags the target is unchanged. Each board-danger
    flag reduces the move toward target by 15%, never below 40% of the
    full move (so a multi-flag board still pulls call probability up
    somewhat — the plan calls for "dampener, not auto-fold").

    Ablation: pass `('defense_floor', 'dampener')` in `disable_rules` to
    bypass the scaling and return the un-dampened target. Lets sims
    isolate the dampener's contribution from the matrix's contribution.
    """
    if is_rule_disabled(disable_rules, 'defense_floor', 'dampener'):
        return target
    danger_count = sum(1 for f in danger_flags if f in _BOARD_DANGER_FLAGS)
    if danger_count == 0:
        return target
    scale = max(
        DANGER_DAMPENER_FLOOR,
        1.0 - DANGER_DAMPENER_PER_FLAG * danger_count,
    )
    return current + (target - current) * scale


def _redistribute_to_call_target(
    strategy: StrategyProfile,
    target: float,
) -> StrategyProfile:
    """Return a new strategy with `call` bumped to `target`, with the
    delta drawn proportionally from non-call actions.

    If the strategy lacks a 'call' key, returns it unchanged.
    If non-call mass is already 0 (i.e. strategy is 100% call),
    returns it unchanged.
    """
    probs = dict(strategy.action_probabilities)
    if 'call' not in probs:
        return strategy

    current_call = probs.get('call', 0.0)
    if current_call >= target:
        return strategy

    non_call_total = sum(p for action, p in probs.items() if action != 'call')
    if non_call_total <= 0.0:
        return strategy

    new_non_call_total = max(0.0, non_call_total - (target - current_call))
    scale = new_non_call_total / non_call_total

    new_probs = {}
    for action, p in probs.items():
        if action == 'call':
            new_probs[action] = target
        else:
            new_probs[action] = p * scale
    return StrategyProfile(action_probabilities=new_probs)


def _build_fire_trace(
    strategy_before: StrategyProfile,
    strategy_after: StrategyProfile,
    *,
    hand_class: str,
    nut_status: str,
    required_equity: float,
    target: float,
    dampened_target: float,
    danger_flags: FrozenSet[str],
    matrix_row: str,
) -> InterventionTrace:
    """Construct a fire trace summarizing the floor's effect."""
    summary_before = summarize_strategy(strategy_before.action_probabilities)
    summary_after = summarize_strategy(strategy_after.action_probabilities)
    return InterventionTrace(
        layer='defense_floor',
        rule_id='default',
        layer_order=layer_order_for('defense_floor'),
        fired=True,
        operation=InterventionOperation.OVERRIDE.value,
        effect='pump_call',
        effect_size=l1_distance(
            strategy_before.action_probabilities,
            strategy_after.action_probabilities,
        ),
        action_changed=(
            primary_action(strategy_before.action_probabilities)
            != primary_action(strategy_after.action_probabilities)
        ),
        primary_action_before=primary_action(
            strategy_before.action_probabilities
        ),
        primary_action_after=primary_action(
            strategy_after.action_probabilities
        ),
        reason_code=f'matrix_row_{matrix_row}',
        rationale=(
            f'price-sensitive defense floor: row={matrix_row} '
            f'hand_class={hand_class} nut_status={nut_status} '
            f'req_equity={required_equity:.3f} target={target:.3f} '
            f'dampened_target={dampened_target:.3f}'
        ),
        inputs={
            'hand_class': hand_class,
            'nut_status': nut_status,
            'required_equity': round(required_equity, 4),
            'danger_flags': sorted(danger_flags),
            'matrix_row': matrix_row,
            'undampened_target': round(target, 4),
            'dampened_target': round(dampened_target, 4),
        },
        input_strategy_summary=summary_before,
        output_strategy_summary=summary_after,
    )


def _matrix_row_label(
    hand_class: str,
    nut_status: str,
    required_equity: float,
) -> Optional[str]:
    """Return the row label ('strong' / 'keep_alive_strong' / 'keep_alive_medium')
    that matched, or None when nothing matched.

    Mirrors `_floor_target_call_prob`'s branching order — keep in sync.
    """
    if hand_class in {'air', 'air_no_draw', 'air_strong_draw'} or nut_status == NUT_BLUFF_CATCHER:
        return None
    if (
        required_equity <= ROW_STRONG_MAX_REQ
        and nut_status in (NUT_NEAR, NUT_ACTUAL)
    ):
        return 'strong'
    if required_equity <= ROW_KEEP_ALIVE_STRONG_MAX_REQ:
        if (
            hand_class in _ROW_STRONG_CLASSES
            or nut_status == NUT_NON_NUT_STRONG
        ):
            return 'keep_alive_strong'
    if required_equity <= ROW_KEEP_ALIVE_MEDIUM_MAX_REQ:
        if hand_class in _ROW_MEDIUM_CLASSES:
            return 'keep_alive_medium'
    return None


def apply_defense_floor(
    strategy: StrategyProfile,
    *,
    hand_class: str,
    nut_status: str,
    danger_flags: FrozenSet[str],
    required_equity: float,
    facing_bet: bool,
    prior_layer_fired: bool = False,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Apply the price-sensitive defense floor.

    Args:
        strategy: current StrategyProfile coming out of the upstream
            pipeline (post-bluff_catch).
        hand_class: post-§1 `hand_class` ('nuts' / 'strong_made' /
            'medium_made' / 'weak_made' / 'air' /
            'air_strong_draw' / 'air_no_draw').
        nut_status: §1 `nut_status` ('actual_nuts' / 'near_nuts' /
            'non_nut_strong' / 'bluff_catcher').
        danger_flags: §1 board+hand danger flags.
        required_equity: §4 required pot-odds equity in [0, 0.5).
        facing_bet: True iff hero faces a non-zero bet.
        prior_layer_fired: True iff value_override or bluff_catch
            already replaced the distribution this decision. The
            floor defers to upstream overrides.
        disable_rules: ablation set; if `('defense_floor', 'default')`
            is in the set, emit a disabled trace and pass through.

    Returns:
        `(new_strategy, trace)`. When the floor doesn't fire,
        `new_strategy is strategy` and trace is a `no_op`.
    """
    if is_rule_disabled(disable_rules, 'defense_floor', 'default'):
        return strategy, make_disabled_trace(
            layer='defense_floor', rule_id='default',
            layer_order=layer_order_for('defense_floor'),
        )

    if not facing_bet:
        return strategy, make_no_op_trace(
            layer='defense_floor', rule_id='default',
            layer_order=layer_order_for('defense_floor'),
            reason_code='no_bet_to_face',
        )

    if prior_layer_fired:
        return strategy, make_no_op_trace(
            layer='defense_floor', rule_id='default',
            layer_order=layer_order_for('defense_floor'),
            reason_code='prior_override_active',
        )

    target = _floor_target_call_prob(hand_class, nut_status, required_equity)
    if target <= 0.0:
        return strategy, make_no_op_trace(
            layer='defense_floor', rule_id='default',
            layer_order=layer_order_for('defense_floor'),
            reason_code='no_eligible_row',
        )

    current_call = strategy.action_probabilities.get('call', 0.0)
    if 'call' not in strategy.action_probabilities:
        return strategy, make_no_op_trace(
            layer='defense_floor', rule_id='default',
            layer_order=layer_order_for('defense_floor'),
            reason_code='call_action_unavailable',
        )

    dampened_target = _apply_danger_dampener(
        target, current_call, danger_flags, disable_rules=disable_rules,
    )

    if current_call >= dampened_target:
        return strategy, make_no_op_trace(
            layer='defense_floor', rule_id='default',
            layer_order=layer_order_for('defense_floor'),
            reason_code='already_above_floor',
        )

    new_strategy = _redistribute_to_call_target(strategy, dampened_target)
    matrix_row = _matrix_row_label(hand_class, nut_status, required_equity) or 'unknown'

    return new_strategy, _build_fire_trace(
        strategy_before=strategy,
        strategy_after=new_strategy,
        hand_class=hand_class,
        nut_status=nut_status,
        required_equity=required_equity,
        target=target,
        dampened_target=dampened_target,
        danger_flags=danger_flags,
        matrix_row=matrix_row,
    )
