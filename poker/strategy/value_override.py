"""
Value override: rule-based strategy replacement for strong-hand-vs-aggressor spots.

Phase 6.5 of the tiered-bot architecture. See
docs/plans/PHASE_6_OPPONENT_EXPLOITATION.md (Phase 6) and the Phase 6.5
plan at ~/.claude/plans/yes-ship-the-strong-hand-zesty-manatee.md.

## Architectural placement

Sits between exploitation offsets (`apply_exploitation_offsets`) and math
floor (`apply_pot_odds_floor`). When triggered, replaces the strategy
distribution entirely rather than nudging it — because offsets can't
cross decision boundaries that the table baseline locked in.

## Three-regime rationale

| Hand strength | Aggressive opp? | Behavior |
|---|---|---|
| Strong (top-tier preflop / strong_made+ postflop) | Yes | **value override (this module)** |
| Marginal | Yes | exploitation offsets |
| Weak | Yes | table (correct folds) |
| Any | No | table + personality (unchanged) |

## Why replacement, not offsets

A pro vs ManiacBot with AA doesn't think in "shift call probability by
+0.5 logit." They think: "Get the money in. Period."  When offsets max
out at ~30% probability shift but the right play is 100% commit, the
offset framework can't express it. This module does.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from .exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    GATING_FLOOR,
    MIN_HANDS_DEFAULT,
    classify_detected_patterns,
)
from .strategy_profile import StrategyProfile


class HandStrengthClass(str, Enum):
    """Strength tier for value-override eligibility.

    Strings inherited to keep callers simple (`hand_strength == 'nuts'`
    still works) while giving us type checking at call sites.
    """
    NUTS = 'nuts'
    STRONG_MADE = 'strong_made'
    # Preflop archetype-relative "strong" (top N% of starting hands,
    # threshold scaled by hero's baseline_looseness in the caller).
    STRONG = 'strong'
    # Anything else — override does NOT fire.
    NOT_STRONG = 'not_strong'


_OVERRIDE_TRIGGER_CLASSES = frozenset({
    HandStrengthClass.NUTS.value,
    HandStrengthClass.STRONG_MADE.value,
    HandStrengthClass.STRONG.value,
})


def _has_raise_or_jam(available_actions: List[str]) -> bool:
    """True if any raise-like / jam action label is present."""
    for action in available_actions:
        if action == 'jam':
            return True
        if action.startswith(('bet_', 'raise_')):
            return True
    return False


def _raise_actions(available_actions: List[str]) -> List[str]:
    return [
        a for a in available_actions
        if a == 'jam' or a.startswith(('bet_', 'raise_'))
    ]


# ── Public API ──────────────────────────────────────────────────────────

def should_apply_value_override(
    stats: AggregatedOpponentStats,
    hand_strength: str,
    decision_context: DecisionContext,
    adaptation_bias: float,
    tilt_factor: float = 1.0,
    min_hands: int = MIN_HANDS_DEFAULT,
) -> bool:
    """Return True if value override should fire for this decision.

    Conditions (all required):
      - Hero's hand_strength is in OVERRIDE_TRIGGER_CLASSES
        (nuts / strong_made / archetype-strong)
      - Opponent stats trigger hyper_aggressive pattern
      - Past cold-start (hands_observed >= min_hands)
      - (adaptation_bias × tilt_factor) > GATING_FLOOR

    Same gating as exploitation: psychology-aware (tilt suppresses)
    and confidence-aware (cold start gates).
    """
    if hand_strength not in _OVERRIDE_TRIGGER_CLASSES:
        return False
    if stats.hands_observed < min_hands:
        return False
    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False
    if 'hyper_aggressive' not in classify_detected_patterns(stats):
        return False
    return True


def compute_value_override_strategy(
    strategy: StrategyProfile,
    decision_context: DecisionContext,
    hand_strength: str,
) -> StrategyProfile:
    """Build a 'get money in' distribution over the strategy's existing keys.

    Does NOT invent new action labels — only redistributes probability mass
    across the keys already present in the input strategy. Three spots:

      - Facing all-in:  100% call (or 100% jam if no call option)
      - Facing any other bet:  50% call, 50% raise-like
      - Open spot (no bet to face):  scaled by hand class —
          nuts:         95% raise, 5% check/call
          strong_made:  80% raise, 20% check/call
          'strong' preflop:  90% raise, 10% check/call

    "Facing a bet" is detected by the presence of 'fold' in available
    actions — fold is only legal when there's something to call. This
    avoids needing call_amount in decision_context.
    """
    available = list(strategy.action_probabilities.keys())
    has_fold = 'fold' in available
    has_check = 'check' in available
    has_call = 'call' in available
    raises = _raise_actions(available)
    has_raise = bool(raises)

    # ── Facing all-in ──
    # Detected via decision_context flag set by the controller.
    if decision_context.facing_all_in:
        if has_call:
            return StrategyProfile(action_probabilities={'call': 1.0})
        if 'jam' in available:
            return StrategyProfile(action_probabilities={'jam': 1.0})
        # Pathological: no call or jam available. Fall back to strategy.
        return strategy

    # ── Facing any other bet (big or small) ──
    if has_fold:
        # 50% call, 50% raise-like (split evenly across available raises)
        if has_call and has_raise:
            n = len(raises)
            dist: Dict[str, float] = {'call': 0.5}
            for action in raises:
                dist[action] = 0.5 / n
            return StrategyProfile(action_probabilities=dist)
        if has_call:
            return StrategyProfile(action_probabilities={'call': 1.0})
        if has_raise:
            n = len(raises)
            return StrategyProfile(action_probabilities={
                a: 1.0 / n for a in raises
            })
        # Pathological — leave strategy alone
        return strategy

    # ── Open spot (no bet to face) ──
    # Raise probability scales with hand strength: nuts > strong_pre > strong_made.
    raise_prob_map = {
        HandStrengthClass.NUTS.value: 0.95,
        HandStrengthClass.STRONG.value: 0.90,
        HandStrengthClass.STRONG_MADE.value: 0.80,
    }
    raise_prob = raise_prob_map.get(hand_strength, 0.80)
    passive_prob = 1.0 - raise_prob

    if has_raise:
        n = len(raises)
        dist = {a: raise_prob / n for a in raises}
        if has_check:
            dist['check'] = passive_prob
        elif has_call:
            dist['call'] = passive_prob
        else:
            # Only raises available — give them all the mass.
            dist = {a: 1.0 / n for a in raises}
        return StrategyProfile(action_probabilities=dist)

    # No raise option (pathological for an open spot) — leave alone.
    return strategy
