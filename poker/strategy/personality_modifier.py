"""
Personality modifier: logit-space distortion of solver baselines.

Takes a solver StrategyProfile and warps it according to personality anchors,
emotional state, and a deviation profile that caps how far the result can
stray from the baseline.
"""

from typing import List, Tuple

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
from .strategy_profile import StrategyProfile

# ── Action categorization ────────────────────────────────────────────────

# Abstract actions that map to engine 'raise' or 'all_in'
_RAISE_ACTIONS = frozenset(
    {
        # Preflop BB-relative and multiplier raises
        'raise_2.5bb',
        'raise_3bb',
        'raise_3x',
        'raise_4x',
        'raise_2.2x',
        # Postflop pot-relative bets and raises
        'bet_33',
        'bet_67',
        'bet_100',
        'raise_67',
        'raise_150',
    }
)


def _is_action_legal(action: str, legal_actions: List[str]) -> bool:
    """Check whether an abstract strategy action is legal given engine actions.

    Handles the mapping between abstract actions (raise_2.5bb, raise_3x, jam,
    bet_33, bet_67, raise_67, raise_150) and engine actions (raise, all_in,
    fold, call, check).
    """
    if action in legal_actions:
        return True
    if action in _RAISE_ACTIONS:
        return 'raise' in legal_actions or 'all_in' in legal_actions
    if action == 'jam':
        return 'all_in' in legal_actions
    # Fallback prefix match for any future bet_/raise_ actions
    if action.startswith(('bet_', 'raise_')):
        return 'raise' in legal_actions or 'all_in' in legal_actions
    return False


def categorize_action(action: str) -> str:
    """Categorize any action label into aggressive/passive/fold."""
    if action == 'fold':
        return 'fold'
    if action in ('check', 'call'):
        return 'passive'
    if action == 'jam' or action.startswith(('bet_', 'raise_')):
        return 'aggressive'
    return 'passive'  # unknown defaults to passive


# ── Math helpers ─────────────────────────────────────────────────────────


def _probs_to_logits(probs: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    return np.log(probs + eps)


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits)
    exp = np.exp(shifted)
    return exp / np.sum(exp)


def _kl_divergence(p: np.ndarray, q: np.ndarray, eps: float = 1e-12) -> float:
    """KL(p || q) for discrete distributions."""
    mask = (p > eps) & (q > eps)
    if not np.any(mask):
        return 0.0
    return float(np.sum(p[mask] * np.log(p[mask] / q[mask])))


# ── Emotional direction mapping ──────────────────────────────────────────

_EMOTIONAL_DIRECTION = {
    'tilted': 'aggressive',
    'overconfident': 'aggressive',
    'shaken': 'passive',
    'dissociated': 'passive',
    'composed': None,
}


def _tilt_signature_enabled() -> bool:
    """Live read of TILT_SIGNATURE_ENABLED; False if the registry is unavailable
    (sim/test isolation) so the off-path keeps the state-driven direction map."""
    try:
        from core.feature_flags import is_enabled

        return is_enabled('TILT_SIGNATURE_ENABLED')
    except Exception:
        return False


# ── Trait offset computation ─────────────────────────────────────────────


def compute_trait_offsets(
    actions: List[str],
    anchors,
    emotional_state,
    profile: DeviationProfile,
) -> np.ndarray:
    """Compute logit-space offsets from personality traits and emotional state.

    Offsets are computed per-action based on:
    - Aggression (centered at 0.5): boost aggressive, penalize passive
    - Looseness (centered at 0.5): penalize fold, boost non-fold
    - Risk identity (centered at 0.5): boost jam, penalize passive
    - Ego: penalize folding (not centered — ego=0 means no penalty)
    - Emotional modifiers: gated by (1 - poise)
    """
    n = len(actions)
    offsets = np.zeros(n)

    agg_dev = anchors.baseline_aggression - 0.5
    loose_dev = anchors.baseline_looseness - 0.5
    risk_dev = anchors.risk_identity - 0.5

    for i, action in enumerate(actions):
        cat = categorize_action(action)

        # Aggression: boost aggressive, penalize passive
        if cat == 'aggressive':
            offsets[i] += agg_dev * profile.aggression_scale
        elif cat == 'passive':
            offsets[i] -= agg_dev * profile.aggression_scale

        # Looseness: penalize fold (widen the range you ENTER), routing the
        # freed entry to CALLING — not raising. Looseness widens *which hands you
        # play*, not *how often you 3-bet*; boosting aggressive actions here made
        # loose archetypes (lag/maniac/station/fish) double-count looseness as
        # aggression, a primary driver of the preflop 3-bet inflation. Aggression
        # frequency is governed solely by agg_dev × aggression_scale above.
        # (Knob 1b — see docs/technical/ARCHETYPE_SHAPING_FINDINGS.md.)
        if cat == 'fold':
            offsets[i] -= loose_dev * profile.looseness_scale
        elif cat == 'passive':
            offsets[i] += loose_dev * profile.looseness_scale * 0.5

        # Risk identity: boost jam, penalize passive
        if action == 'jam':
            offsets[i] += risk_dev * profile.risk_scale
        elif cat == 'passive':
            offsets[i] -= risk_dev * profile.risk_scale * 0.5

        # Ego fold penalty (always applied, scaled by ego)
        if cat == 'fold':
            offsets[i] -= anchors.ego * profile.ego_fold_penalty

    # Emotional modifiers gated by poise
    if emotional_state is not None and emotional_state.state != 'composed':
        poise_gate = 1.0 - anchors.poise
        scale = emotional_state.intensity * poise_gate
        # §4 behavioral signature (flag TILT_SIGNATURE_ENABLED): under a TILT
        # state the distortion direction is CHARACTER-driven by risk_identity —
        # risk-seekers spew (aggressive), risk-averse collapse (passive) — instead
        # of the state-driven default (tilted=aggressive for everyone). Mirrors the
        # standard bot's compute_modifiers shaken-gate split. Overconfident (a
        # confidence state, not tilt) is left on the state map. Off => legacy map.
        if _tilt_signature_enabled() and emotional_state.state in (
            'tilted',
            'shaken',
            'dissociated',
        ):
            direction = 'aggressive' if anchors.risk_identity >= 0.5 else 'passive'
        else:
            direction = _EMOTIONAL_DIRECTION.get(emotional_state.state)

        if direction and scale > 0:
            for i, action in enumerate(actions):
                cat = categorize_action(action)
                if direction == 'aggressive':
                    if cat == 'aggressive':
                        offsets[i] += scale
                    elif cat == 'passive':
                        offsets[i] -= scale
                else:  # passive direction
                    if cat == 'passive':
                        offsets[i] += scale
                    elif cat == 'aggressive':
                        offsets[i] -= scale

    return offsets


# ── Divergence clamping ──────────────────────────────────────────────────


def _clip_and_normalize(
    probs: np.ndarray,
    base_probs: np.ndarray,
    max_shift: float,
    eps: float = 1e-12,
    max_iters: int = 100,
) -> np.ndarray:
    """Iteratively clip per-action and renormalize until convergence.

    A single clip-renormalize pass can push values outside the cap when the
    total changes.  Iterating guarantees the final distribution satisfies
    both the per-action cap and sums to 1.

    Convergence is linear, so a hard-binding tight cap with a large initial
    distortion (e.g. the maniac profile's 0.35 cap) needs ~50 passes to
    settle under 1e-6; 10 left a ~2e-6 residual that broke the cap invariant.
    """
    result = probs.copy()
    for _ in range(max_iters):
        result = np.clip(result, base_probs - max_shift, base_probs + max_shift)
        result = np.maximum(result, 0.0)
        total = np.sum(result)
        if total < eps:
            return base_probs.copy()
        result = result / total
        if np.all(np.abs(result - base_probs) <= max_shift + 1e-10):
            break
    return result


def clamp_divergence(
    base_probs: np.ndarray,
    new_probs: np.ndarray,
    base_logits: np.ndarray,
    offsets: np.ndarray,
    profile: DeviationProfile,
    eps: float = 1e-12,
) -> np.ndarray:
    """Clamp modified probabilities to stay within divergence budget.

    Procedure:
    1. Per-action cap with max(0.0, ...) floor
    2. Renormalize (iterative clip-normalize to convergence)
    3. KL check -> binary search for alpha if exceeded
    4. Re-apply per-action cap after KL scaling
    5. Final renormalize
    """
    max_shift = profile.max_per_action_shift

    # Steps 1-2: Iterative clip and normalize
    clamped = _clip_and_normalize(new_probs, base_probs, max_shift, eps)

    # Step 3: KL check
    kl = _kl_divergence(clamped, base_probs, eps)

    if kl <= profile.max_kl:
        return clamped

    # Binary search for alpha in [0, 1] such that KL stays within budget
    lo, hi = 0.0, 1.0
    for _ in range(32):
        mid = (lo + hi) / 2.0
        trial_probs = _softmax(base_logits + mid * offsets)
        trial_probs = _clip_and_normalize(trial_probs, base_probs, max_shift, eps)
        trial_kl = _kl_divergence(trial_probs, base_probs, eps)
        if trial_kl > profile.max_kl:
            hi = mid
        else:
            lo = mid

    # Use conservative (lo) bound
    clamped = _softmax(base_logits + lo * offsets)

    # Steps 4-5: Final iterative clip and normalize
    return _clip_and_normalize(clamped, base_probs, max_shift, eps)


# ── Main entry point ─────────────────────────────────────────────────────


def modify_strategy(
    base: StrategyProfile,
    legal_actions: List[str],
    anchors,
    emotional_state,
    deviation_profile: DeviationProfile,
    disable_rules=None,
) -> Tuple[StrategyProfile, InterventionTrace]:
    """Apply personality distortion to a solver baseline strategy.

    Pipeline:
    1. Mask illegal and zero-support actions
    2. Renormalize supported subset
    3. Convert to logits
    4. Compute trait offsets
    5. Apply offsets -> softmax
    6. Clamp divergence
    7. Reconstruct full distribution (zeros preserved)
    8. Return new StrategyProfile

    Phase 7.6 (Step 4): returns `(strategy, trace)`. Per the plan
    §"Migration plan" recommendation, the personality trace is
    intentionally simpler than detection-rule traces — it just records
    which deviation profile was applied and the resulting L1 shift.
    Distortion preserves prior intent (`operation='adjust'`,
    `preserved_prior_intent=True`), unlike the override layers
    downstream. fired=True when the strategy actually changed; fired=
    False for degenerate-support early-outs.

    Phase 7.6 (Step 5): when disabled, emits a `disabled_by_ablation`
    no-op trace and returns the strategy unchanged.
    """
    if is_rule_disabled(disable_rules, 'personality', 'default'):
        return base, make_disabled_trace(
            layer='personality',
            rule_id='default',
            layer_order=layer_order_for('personality'),
        )

    eps = 1e-12

    all_actions = list(base.action_probabilities.keys())
    base_probs_full = np.array([base.action_probabilities[a] for a in all_actions])

    # Step 1: Identify supported actions (legal AND nonzero base prob)
    supported_mask = np.array(
        [
            _is_action_legal(a, legal_actions) and (base.action_probabilities[a] > 0.0)
            for a in all_actions
        ]
    )

    supported_indices = np.where(supported_mask)[0]

    if len(supported_indices) <= 1:
        return base, make_no_op_trace(
            layer='personality',
            rule_id='default',
            layer_order=layer_order_for('personality'),
            reason_code='single_supported_action',
        )

    # Step 2: Extract and renormalize supported subset
    supported_actions = [all_actions[i] for i in supported_indices]
    supported_probs = base_probs_full[supported_indices]
    total = np.sum(supported_probs)
    if total < eps:
        return base, make_no_op_trace(
            layer='personality',
            rule_id='default',
            layer_order=layer_order_for('personality'),
            reason_code='zero_total_probability',
        )
    supported_probs = supported_probs / total

    # Step 3: Convert to logits
    base_logits = _probs_to_logits(supported_probs)

    # Step 4: Compute trait offsets
    offsets = compute_trait_offsets(supported_actions, anchors, emotional_state, deviation_profile)

    # Step 5: Apply offsets -> softmax
    new_logits = base_logits + offsets
    new_probs = _softmax(new_logits)

    # Step 6: Clamp divergence
    final_probs = clamp_divergence(
        supported_probs, new_probs, base_logits, offsets, deviation_profile
    )

    # Step 7: Reconstruct full distribution (zeros preserved)
    result = {}
    supported_idx = 0
    for i, action in enumerate(all_actions):
        if supported_mask[i]:
            result[action] = float(final_probs[supported_idx])
            supported_idx += 1
        else:
            result[action] = 0.0

    modified = StrategyProfile(action_probabilities=result)
    trace = _build_personality_trace(
        base=base,
        modified=modified,
        anchors=anchors,
        emotional_state=emotional_state,
        deviation_profile=deviation_profile,
    )
    return modified, trace


def _resolve_deviation_profile_name(deviation_profile) -> str:
    """Find the DEVIATION_PROFILES key matching `deviation_profile`.

    DeviationProfile is a frozen dataclass without an embedded `name`
    attribute, so we reverse-lookup. Returns 'unknown' on no match —
    a custom-constructed profile that isn't in the canonical dict
    still produces a valid trace.
    """
    name = getattr(deviation_profile, 'name', '') or ''
    if name:
        return name
    try:
        from .deviation_profiles import DEVIATION_PROFILES
    except ImportError:
        return 'unknown'
    for profile_name, candidate in DEVIATION_PROFILES.items():
        if candidate is deviation_profile or candidate == deviation_profile:
            return profile_name
    return 'unknown'


def _build_personality_trace(
    base: StrategyProfile,
    modified: StrategyProfile,
    anchors,
    emotional_state,
    deviation_profile: DeviationProfile,
) -> InterventionTrace:
    """Construct the InterventionTrace for a personality distortion pass.

    Simpler than detection-rule traces (plan §"Migration plan"): just
    the deviation profile applied and the L1 shift. Offsets aren't
    surfaced — they're per-action and tied to internal trait math
    (`compute_trait_offsets`) which downstream attribution can re-
    derive from the inputs.
    """
    base_probs = base.action_probabilities
    out_probs = modified.action_probabilities

    effect_size = l1_distance(base_probs, out_probs)
    fired = effect_size > 1e-9

    primary_before = primary_action(base_probs)
    primary_after = primary_action(out_probs)

    profile_name = _resolve_deviation_profile_name(deviation_profile)
    emotional_label = getattr(emotional_state, 'state', '') or ''

    if not fired:
        return make_no_op_trace(
            layer='personality',
            rule_id='default',
            layer_order=layer_order_for('personality'),
            reason_code='no_distortion',
        )

    return InterventionTrace(
        layer='personality',
        rule_id='default',
        layer_order=layer_order_for('personality'),
        fired=True,
        operation=InterventionOperation.ADJUST.value,
        effect='offsets_applied',
        effect_size=round(effect_size, 4),
        action_changed=(primary_before != primary_after),
        primary_action_before=primary_before,
        primary_action_after=primary_after,
        replaced_prior_action=False,
        preserved_prior_intent=True,
        reason_code=f'deviation_profile_{profile_name}',
        rationale=(
            f"Personality distortion via {profile_name or 'unknown'} profile "
            f"(emotional_state={emotional_label or 'unknown'})"
        ),
        confidence=1.0,
        inputs={
            'deviation_profile': profile_name,
            'emotional_state': emotional_label,
        },
        input_strategy_summary=summarize_strategy(base_probs),
        output_strategy_summary=summarize_strategy(out_probs),
    )


# ── River bluff guardrail ───────────────────────────────────────────────

# Archetype → max bluff-to-value ratio
_BLUFF_RATIOS = {
    'nit': 0.8,
    'rock': 0.8,
    'tag': 0.8,
    'calling_station': 1.0,
    'lag': 1.2,
    'maniac': 1.2,
}

# Hand classes considered bluffs when betting/raising on the river
_BLUFF_CLASSES = frozenset({'air_no_draw', 'air_strong_draw', 'weak_made'})


def apply_river_bluff_guardrail(
    strategy: StrategyProfile,
    hand_class: str,
    archetype: str,
) -> StrategyProfile:
    """Cap river bluff frequency to prevent over-bluffing after distortion.

    Only applies when hand_class is a bluff class (air/weak). Scales down
    bet/raise probabilities and redistributes mass to check/fold.

    Args:
        strategy: Post-distortion strategy profile
        hand_class: Simplified hand class (from simplify_hand_class)
        archetype: Personality archetype name (nit, rock, tag, etc.)

    Returns:
        Adjusted StrategyProfile with bluff frequency capped
    """
    if hand_class not in _BLUFF_CLASSES:
        return strategy

    max_ratio = _BLUFF_RATIOS.get(archetype, 1.0)
    # Max bluff frequency: ratio / (1 + ratio)
    # e.g., ratio=1.0 → max 50% betting, ratio=0.8 → max 44%, ratio=1.2 → max 55%
    max_bet_freq = max_ratio / (1.0 + max_ratio)

    probs = dict(strategy.action_probabilities)

    # Sum up all aggressive action probabilities
    bet_freq = sum(p for a, p in probs.items() if categorize_action(a) == 'aggressive')

    if bet_freq <= max_bet_freq or bet_freq < 1e-12:
        return strategy

    # Scale down aggressive actions proportionally
    scale = max_bet_freq / bet_freq
    excess = bet_freq - max_bet_freq

    # Find passive/fold actions to redistribute to
    passive_actions = [a for a in probs if categorize_action(a) in ('passive', 'fold')]
    passive_total = sum(probs[a] for a in passive_actions)

    result = {}
    for action, prob in probs.items():
        cat = categorize_action(action)
        if cat == 'aggressive':
            result[action] = prob * scale
        elif passive_total > 0 and cat in ('passive', 'fold'):
            # Redistribute excess proportionally to existing passive/fold weights
            result[action] = prob + excess * (prob / passive_total)
        else:
            result[action] = prob

    return StrategyProfile(action_probabilities=result)
