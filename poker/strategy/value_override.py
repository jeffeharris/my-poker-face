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

    Strong-hand override (Phase 6.5) consumes NUTS / STRONG_MADE /
    STRONG. Phase 7.5 Item 1 bluff-catch override consumes MEDIUM_MADE /
    WEAK_MADE. The two are mutually exclusive by hand class.
    """
    NUTS = 'nuts'
    STRONG_MADE = 'strong_made'
    # Preflop archetype-relative "strong" (top N% of starting hands,
    # threshold scaled by hero's baseline_looseness in the caller).
    STRONG = 'strong'
    # Phase 7.5 Item 1: bluff-catch tier. Mirrors postflop_classifier's
    # made_tier strings — see poker/strategy/hand_classification.py.
    MEDIUM_MADE = 'medium_made'
    WEAK_MADE = 'weak_made'
    # Anything else — neither override fires.
    NOT_STRONG = 'not_strong'


_OVERRIDE_TRIGGER_CLASSES = frozenset({
    HandStrengthClass.NUTS.value,
    HandStrengthClass.STRONG_MADE.value,
    HandStrengthClass.STRONG.value,
})

# Phase 7.5 Item 1: bluff-catch trigger classes. Disjoint from
# _OVERRIDE_TRIGGER_CLASSES — guarantees the two overrides can't both
# fire on the same decision.
BLUFF_CATCH_TRIGGER_CLASSES = frozenset({
    HandStrengthClass.MEDIUM_MADE.value,
    HandStrengthClass.WEAK_MADE.value,
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


# ── Phase 7.5 Item 1: bluff-catch override building blocks ──────────────

# Texture bucket names that justify dampening the bluff-catch call rate.
# Plan referenced 'four_flush' / 'four_straight' / 'paired_high' but the
# actual classify_texture_bucket() produces 6 buckets, none of which use
# those names. Reconciled here using the actual outputs from
# poker/board_analyzer.classify_texture_bucket. Paired boards collapse
# into 'dry_low_static' in the bucket; the paired-board signal is passed
# separately as `is_paired_board` to the dampener.
_DANGEROUS_TEXTURES = frozenset({
    'monotone',           # 3+ same suit on board — flush threats real
    'wet_rainbow',        # connected rainbow — straight threats
    'two_tone_broadway',  # connected two-tone with broadway cards
    'two_tone_connected', # connected two-tone — backdoor draws got there
})


def _base_call_prob(hand_strength: str, bet_size_pot_ratio: float) -> float:
    """Pot-odds × hand-class base call probability.

    Bet/pot bands for medium_made and weak_made come from
    phase_7_5_config.bluff_catch.sizing. Each band represents a
    progressively tighter required-equity threshold per the standard
    pot-odds formula (bet / (pot + 2*bet)):
      - 1/3 pot bet → ~20% equity needed
      - pot-size bet → 33%
      - 2x pot bet → 40%
      - jam-ish (3x pot) → 43%

    The matrix encodes "stop overfolding versus confirmed
    over-aggression" — not literal equity computation. See plan §"Pot-
    odds-conditional splits" for the rationale.

    Returns 0.0 for hand classes outside BLUFF_CATCH_TRIGGER_CLASSES;
    the caller is expected to gate on hand class before calling this.
    """
    from .phase_7_5_config import CONFIG
    sizing = CONFIG.bluff_catch.sizing

    if hand_strength == HandStrengthClass.MEDIUM_MADE.value:
        if bet_size_pot_ratio <= 0.50:
            return sizing.medium_made_le_50_pct
        if bet_size_pot_ratio <= 1.00:
            return sizing.medium_made_le_100_pct
        if bet_size_pot_ratio <= 2.00:
            return sizing.medium_made_le_200_pct
        return sizing.medium_made_gt_200_pct
    if hand_strength == HandStrengthClass.WEAK_MADE.value:
        if bet_size_pot_ratio <= 0.33:
            return sizing.weak_made_le_33_pct
        if bet_size_pot_ratio <= 0.67:
            return sizing.weak_made_le_67_pct
        return sizing.weak_made_gt_67_pct
    return 0.0


def _board_danger_dampener(
    street: str,
    board_texture: str,
    hand_strength: str,
    is_paired_board: bool = False,
) -> float:
    """Return a multiplier in (0, 1] applied to the base call prob.

    Three independent dampeners compose multiplicatively:

    1. **Street** — river (no more cards; equity is realized at
       showdown) is harsher than turn, which is harsher than flop.
    2. **Texture** — boards where draws have credibly completed
       (monotone, connected two-tone, wet rainbow) cap our medium_made
       hand's equity vs the aggressor's range.
    3. **Weak-made on paired board** — weak_made (low pair or
       ace-high) on a paired board is structurally dominated by any
       hand with a card matching the pair.

    Returned multiplier values are CONSERVATIVE — better to fold a
    bluff-catch in a marginal spot than to bleed chips on the river.

    Args:
        street: 'flop' / 'turn' / 'river' (case-insensitive). Other
            values → flop-level dampener (no street penalty).
        board_texture: One of the bucket names from
            poker/board_analyzer.classify_texture_bucket. Names not
            in _DANGEROUS_TEXTURES → no texture dampener.
        hand_strength: HandStrengthClass value (only WEAK_MADE
            triggers the paired-board dampener).
        is_paired_board: True when the board has at least one pair.
            Passed separately because the texture-bucket name collapses
            paired-board into 'dry_low_static'.
    """
    from .phase_7_5_config import CONFIG
    d = CONFIG.bluff_catch.dampener

    dampener = 1.0

    # Street factor.
    s = (street or '').lower()
    if s == 'river':
        dampener *= d.street_river
    elif s == 'turn':
        dampener *= d.street_turn
    else:  # flop or unknown — flop-level (typically 1.0)
        dampener *= d.street_flop

    # Texture factor.
    if board_texture in _DANGEROUS_TEXTURES:
        dampener *= d.dangerous_texture_mult

    # Paired-board factor (weak_made only — dominated by any matching card).
    if is_paired_board and hand_strength == HandStrengthClass.WEAK_MADE.value:
        dampener *= d.weak_made_on_paired_mult

    return dampener


def _bluff_catch_call_probability(
    hand_strength: str,
    bet_size_pot_ratio: float,
    street: str,
    board_texture: str,
    is_paired_board: bool = False,
) -> float:
    """Compose base call probability × board-danger dampener.

    Final call_prob = _base_call_prob × _board_danger_dampener. Both
    factors are in [0, 1], so the result is also in [0, 1].

    See plan §"Pot-odds-conditional splits with board-danger dampener"
    for the behavioral envelope table this produces.
    """
    base = _base_call_prob(hand_strength, bet_size_pot_ratio)
    if base <= 0.0:
        return 0.0
    return base * _board_danger_dampener(
        street, board_texture, hand_strength, is_paired_board,
    )


def _clamp_to_envelope(
    proposed: StrategyProfile,
    baseline: StrategyProfile,
    max_total_shift: float,
) -> StrategyProfile:
    """Constrain `proposed` to within `max_total_shift` L1 distance of `baseline`.

    Phase 7.5 Item 1: ensures the bluff-catch override doesn't exceed
    the active clamp tier's permitted envelope. Computed as:

      L1_distance(proposed, baseline) = sum |proposed[a] - baseline[a]|

    If the L1 distance ≤ max_total_shift, returns proposed unchanged.
    Otherwise, linearly interpolates between baseline and proposed
    by `scale = max_total_shift / L1_distance` so the result is
    exactly at the boundary.

    Action key handling: the result uses the UNION of proposed and
    baseline keys; baseline-only keys contribute their full mass,
    proposed-only keys start from 0 in baseline.

    Use case: with a DEFAULT-tier clamp (= 0.4), a "100% call"
    bluff-catch on a hand whose baseline says "100% fold" gets pulled
    back to ~70% fold / 30% call (L1 distance = 2.0 × 0.4/2 = 0.4
    from each side — 30 percentage points moved). With EXTREME clamp
    (= 0.8), the same override fits without clamping.
    """
    # Build full key set from both distributions.
    all_actions = set(proposed.action_probabilities.keys()) | set(
        baseline.action_probabilities.keys()
    )

    p = {a: proposed.action_probabilities.get(a, 0.0) for a in all_actions}
    b = {a: baseline.action_probabilities.get(a, 0.0) for a in all_actions}

    l1 = sum(abs(p[a] - b[a]) for a in all_actions)

    if l1 <= max_total_shift or l1 == 0.0:
        # Return a fresh StrategyProfile filtered to nonzero entries
        # to match the caller's expectation.
        return StrategyProfile(
            action_probabilities={a: p[a] for a in all_actions if p[a] > 0.0}
        )

    # Linear interpolation from baseline toward proposed: scale = cap / l1.
    scale = max_total_shift / l1
    clamped = {a: b[a] + scale * (p[a] - b[a]) for a in all_actions}

    # Normalize for numerical safety (interpolation preserves sum=1.0
    # mathematically, but floating-point can drift).
    total = sum(clamped.values())
    if total > 0:
        clamped = {a: v / total for a, v in clamped.items()}

    return StrategyProfile(
        action_probabilities={a: v for a, v in clamped.items() if v > 0.0}
    )


# ── Phase 7.5 Item 1b: bluff-catch gate + strategy builder ──────────────

# Station heuristic constants — applied to a single opponent's
# AggregatedOpponentStats. The thresholds match what compute_exploitation_
# offsets uses for the legacy hyper_passive detection, but we re-encode
# them here as plain values to keep the bluff-catch path self-contained.
_STATION_VPIP_THRESHOLD = 0.55
_STATION_AF_CEILING = 1.5


def _is_station(stats) -> bool:
    """Detect call-station tendencies on a SINGLE opponent's stats.

    A station has high VPIP (sees lots of flops) and low aggression
    factor (rarely folds to bets). When such an opponent is in the
    pot behind hero, calling down with a medium- or weak-made hand
    is much riskier than HU: the station's range has showdown value
    that dominates our pair.

    Requires hands_observed ≥ MEDIUM_MIN_OPPORTUNITIES (sample gate)
    so noisy reads don't suppress the override unnecessarily.
    """
    from .phase_7_5_config import CONFIG
    min_sample = CONFIG.sample_thresholds.medium_min_opportunities
    return (
        stats.vpip > _STATION_VPIP_THRESHOLD
        and stats.aggression_factor < _STATION_AF_CEILING
        and stats.hands_observed >= min_sample
    )


def _continuing_opponents_block_bluff_catch(spots, aggressor_name) -> bool:
    """Phase 7.5 Item 1 multiway suppression.

    Returns True if ANY non-aggressor active opponent in `spots` would
    make bluff-catching reckless:
      - all-in (can't fold; our equity calc was vs aggressor only)
      - a station (high VPIP, low AF — likely has showdown value
        we can't beat)
      - low-sample (we don't have enough read to trust the rest of
        the field)

    Mirrors Phase 6.7's c-bet suppression rule. Heads-up case (zero
    continuing opponents) returns False (no suppression).
    """
    from .phase_7_5_config import CONFIG
    min_sample = CONFIG.sample_thresholds.medium_min_opportunities

    continuing = [
        s for s in spots
        if s.is_active and (aggressor_name is None or s.name != aggressor_name)
    ]
    if not continuing:
        return False  # HU — nothing to suppress

    for opp in continuing:
        if opp.is_all_in:
            return True
        if _is_station(opp.stats):
            return True
        # Sample axis: either facing-bet or postflop-open samples must
        # be at least MEDIUM threshold. A low-sample opponent in the
        # pot is too risky regardless of their other stats.
        opp_sample = max(
            opp.stats.facing_bet_opportunities,
            opp.stats.postflop_open_opportunities,
        )
        if opp_sample < min_sample:
            return True
    return False


def should_apply_bluff_catch_override(
    spots,
    hand_strength: str,
    decision_context,
    adaptation_bias: float,
    tilt_factor: float,
    clamp_tier,
    aggressor_spot,
) -> bool:
    """Phase 7.5 Item 1 trigger gate for the bluff-catch override.

    All conditions required:

    1. Hand class is `medium_made` or `weak_made` (BLUFF_CATCH_TRIGGER_CLASSES).
       Mutually exclusive with the strong-hand override (`nuts` /
       `strong_made` / `strong`).
    2. Hero is facing a bet — `fold` is in the action set
       (signalled by decision_context.facing_all_in or facing_big_bet
       or `bet_size_pot_ratio > 0`). For Phase 7.5 we use the
       `bet_size_pot_ratio > 0` signal as the canonical "facing a bet"
       check — it's populated only when there's a live bet to face.
    3. Active clamp tier is EXTREME — Item 2's tier classifier
       confirmed the aggressor is over-aggressive. Lower tiers don't
       justify the bluff-catch override (the legacy exploitation
       offsets still nudge marginally).
    4. Tilt suppression honored: `adaptation_bias × tilt_factor >
       GATING_FLOOR`. Calling down with marginal hands while tilted
       is bad; the standard gating formula handles this.
    5. Multiway suppression: no continuing opponent is all-in /
       station / low-sample. See _continuing_opponents_block_bluff_catch.

    Returns False if any condition fails.
    """
    from .exploitation import ClampTier  # avoid cycle at module import

    if hand_strength not in BLUFF_CATCH_TRIGGER_CLASSES:
        return False
    if clamp_tier != ClampTier.EXTREME:
        return False
    if adaptation_bias * tilt_factor <= GATING_FLOOR:
        return False

    # Facing-bet check via DecisionContext: bet_size_pot_ratio > 0 means
    # there's a live bet. Use getattr for back-compat with older
    # DecisionContext instances that lack the field (Item 1c adds it).
    bet_ratio = getattr(decision_context, 'bet_size_pot_ratio', 0.0) or 0.0
    if bet_ratio <= 0.0:
        return False

    aggressor_name = aggressor_spot.name if aggressor_spot is not None else None
    if _continuing_opponents_block_bluff_catch(spots, aggressor_name):
        return False

    return True


def compute_bluff_catch_strategy(
    strategy: StrategyProfile,
    decision_context,
    hand_strength: str,
    max_total_shift: float,
) -> StrategyProfile:
    """Build the bluff-catch override distribution and clamp to envelope.

    Reads bet_size_pot_ratio, street, board_texture, and is_paired_board
    from the DecisionContext (the controller populates them at the
    decision point). Composes _bluff_catch_call_probability and
    splits the strategy mass between `call` and `fold`. The output is
    then `_clamp_to_envelope`d against the original strategy so the
    total L1 shift doesn't exceed the active clamp tier's cap.

    Action vocabulary: bluff-catch produces a {call, fold} distribution
    only — raise variants and check are removed. This is the
    "supersede with a specific distribution" pattern from §"Envelope
    semantics."
    """
    bet_ratio = getattr(decision_context, 'bet_size_pot_ratio', 0.0) or 0.0
    street = (getattr(decision_context, 'street', '') or '').lower()
    texture = getattr(decision_context, 'board_texture', '') or ''
    is_paired = bool(getattr(decision_context, 'is_paired_board', False))

    call_prob = _bluff_catch_call_probability(
        hand_strength, bet_ratio, street, texture, is_paired_board=is_paired,
    )
    fold_prob = max(0.0, 1.0 - call_prob)

    proposed = StrategyProfile(action_probabilities={
        'call': call_prob,
        'fold': fold_prob,
    })
    return _clamp_to_envelope(proposed, strategy, max_total_shift)
