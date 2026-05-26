"""Phase 7.6 Step 6: stateless strategy-pipeline replay for Mode 1
(shadow-eval).

Given a snapshot of the per-decision inputs (built by the controller
at decision time and persisted via `strategy_pipeline_snapshot_json`),
reconstructs the strategy pipeline and produces the final strategy
distribution. Mode 1 calls this twice per decision — once with
`disable_rules=frozenset()` (live), once with `disable_rules={target}`
(shadow) — and reports the L1 distance.

Replay is intentionally NOT a perfect reproduction of the controller's
live decision flow: it doesn't sample an action (no RNG state needed),
doesn't run river bluff guardrail, and doesn't tally counters. The
goal is to compute the strategy distribution at the same frozen
state, which is what Mode 1 actually measures.

Snapshot schema (v82): a JSON-safe dict with these keys (all
optional except `base_strategy_probs` and `legal_actions`):

  - phase: 'PRE_FLOP' or 'POSTFLOP' (drives the pipeline path)
  - legal_actions: list[str]
  - base_strategy_probs: dict[str, float] (post-table-lookup, pre-personality)
  - anchors: dict (PersonalityAnchors.__dict__)
  - emotional_state: dict with at least {state, severity, intensity}
  - deviation_profile_name: str (one of DEVIATION_PROFILES keys)
  - decision_context: dict matching DecisionContext fields
  - aggregated_stats: dict matching AggregatedOpponentStats fields
  - multiway_cbet_intensity, value_vs_station_intensity_used,
    steal_pressure_intensity_used: floats
  - hand_strength: str (HandStrengthClass value, postflop only)
  - effective_stack_bb: float
  - clamp_value: float (L1 cap for exploitation)
  - clamp_tier_label: str (lowercase tier, for bluff_catch tier_label)
  - cost_to_call, pot_total, player_stack, player_bet, big_blind: ints
    (for math_floor)
  - adaptation_bias: float (anchors.adaptation_bias proxy)
  - tilt_factor: float (precomputed from emotional_state)
  - exploitation_strength: float
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, FrozenSet, Tuple

from .deviation_profiles import DEVIATION_PROFILES
from .exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    apply_exploitation_offsets,
    compute_exploitation_offsets,
)
from .math_floor import apply_pot_odds_floor
from .personality_modifier import modify_strategy
from .short_stack import apply_short_stack_heuristics
from .strategy_profile import StrategyProfile
from .value_override import (
    BLUFF_CATCH_TRIGGER_CLASSES,
    compute_bluff_catch_strategy,
    compute_value_override_strategy,
    should_apply_value_override,
)


def replay_strategy_pipeline(
    snapshot: Dict[str, Any],
    disable_rules: FrozenSet[Tuple[str, str]] = frozenset(),
) -> StrategyProfile:
    """Re-run the strategy pipeline from a persisted snapshot.

    Returns the final strategy distribution. Mode 1 (shadow-eval) calls
    this twice — `disable_rules=frozenset()` for the live recomputation
    and `disable_rules={target}` for the shadow — and reports the
    distribution-L1 distance.

    On unexpected snapshot shapes (missing required keys, type
    mismatches), returns the base strategy unchanged. Replay is a
    best-effort post-hoc reconstruction; it must not raise on malformed
    snapshots since the analysis script catches dozens of decisions
    per game.
    """
    try:
        base_probs = snapshot['base_strategy_probs']
        legal_actions = snapshot['legal_actions']
    except (KeyError, TypeError):
        return StrategyProfile(action_probabilities={})

    if not isinstance(base_probs, dict) or not isinstance(legal_actions, list):
        return StrategyProfile(action_probabilities={})

    if not base_probs or not legal_actions:
        return StrategyProfile(action_probabilities=base_probs or {})

    strategy = StrategyProfile(action_probabilities=dict(base_probs))

    # ── Personality distortion ───────────────────────────────────────
    anchors = _reconstruct_anchors(snapshot.get('anchors'))
    emotional = _reconstruct_emotional_state(snapshot.get('emotional_state'))
    profile = DEVIATION_PROFILES.get(
        snapshot.get('deviation_profile_name', ''),
    )
    if anchors is not None and profile is not None and emotional is not None:
        strategy, _trace = modify_strategy(
            base=strategy,
            legal_actions=legal_actions,
            anchors=anchors,
            emotional_state=emotional,
            deviation_profile=profile,
            disable_rules=disable_rules,
        )

    # ── Exploitation offsets ─────────────────────────────────────────
    stats = _reconstruct_stats(snapshot.get('aggregated_stats'))
    dctx = _reconstruct_decision_context(snapshot.get('decision_context'))
    adaptation_bias = float(snapshot.get('adaptation_bias', 0.0))
    tilt_factor = float(snapshot.get('tilt_factor', 1.0))
    exploitation_strength = float(snapshot.get('exploitation_strength', 1.0))
    multiway_cbet_intensity = float(snapshot.get('multiway_cbet_intensity', 0.0))
    vvs_intensity = float(snapshot.get('value_vs_station_intensity_used', 0.0))
    steal_intensity = float(snapshot.get('steal_pressure_intensity_used', 0.0))

    if stats is not None and dctx is not None:
        offsets = compute_exploitation_offsets(
            stats=stats,
            adaptation_bias=adaptation_bias,
            decision_context=dctx,
            available_actions=list(strategy.action_probabilities.keys()),
            tilt_factor=tilt_factor,
            exploitation_strength=exploitation_strength,
            multiway_cbet_intensity=multiway_cbet_intensity,
            value_vs_station_intensity=vvs_intensity,
            steal_pressure_intensity=steal_intensity,
            disable_rules=disable_rules,
        )
        if offsets:
            clamp_value = float(snapshot.get('clamp_value', 0.4))
            strategy = apply_exploitation_offsets(
                strategy=strategy,
                offsets=offsets,
                legal_actions=legal_actions,
                max_total_shift=clamp_value,
            )

    # ── Strong-hand override (postflop, only fires on STRONG hand classes) ─
    hand_strength = snapshot.get('hand_strength', '') or ''
    phase = snapshot.get('phase', 'POSTFLOP')

    if hand_strength and stats is not None and dctx is not None and anchors is not None:
        # Replay should_apply gates with the same inputs the controller saw.
        if should_apply_value_override(
            stats=stats,
            hand_strength=hand_strength,
            decision_context=dctx,
            adaptation_bias=adaptation_bias,
            tilt_factor=tilt_factor,
        ):
            strategy, _trace = compute_value_override_strategy(
                strategy=strategy,
                decision_context=dctx,
                hand_strength=hand_strength,
                disable_rules=disable_rules,
            )

    # ── Bluff-catch override (postflop, mutually exclusive with strong-hand) ─
    snapshot.get('opponent_spots') or []
    # The bluff_catch gate needs spots (multiway suppression). Without
    # them, we can't faithfully replay — fall back to: only replay when
    # hand_strength is in the trigger set AND the snapshot indicates
    # the gate was set in the live run.
    if (
        hand_strength in BLUFF_CATCH_TRIGGER_CLASSES
        and phase == 'POSTFLOP'
        and snapshot.get('bluff_catch_gate_passed') is True
        and dctx is not None
    ):
        clamp_value = float(snapshot.get('clamp_value', 0.4))
        clamp_tier_label = snapshot.get('clamp_tier_label', 'extreme')
        strategy, _trace = compute_bluff_catch_strategy(
            strategy=strategy,
            decision_context=dctx,
            hand_strength=hand_strength,
            max_total_shift=clamp_value,
            legal_actions=legal_actions,
            tier_label=clamp_tier_label,
            disable_rules=disable_rules,
        )

    # ── Short-stack heuristic ────────────────────────────────────────
    effective_stack_bb = snapshot.get('effective_stack_bb')
    if effective_stack_bb is not None:
        strategy, _trace = apply_short_stack_heuristics(
            strategy=strategy,
            effective_stack_bb=float(effective_stack_bb),
            legal_actions=legal_actions,
            disable_rules=disable_rules,
        )

    # ── Math floor ───────────────────────────────────────────────────
    big_blind = snapshot.get('big_blind')
    if big_blind is not None and big_blind > 0:
        strategy, _trace = apply_pot_odds_floor(
            strategy=strategy,
            cost_to_call=int(snapshot.get('cost_to_call', 0) or 0),
            pot_total=int(snapshot.get('pot_total', 0) or 0),
            player_stack=int(snapshot.get('player_stack', 0) or 0),
            player_bet=int(snapshot.get('player_bet', 0) or 0),
            big_blind=int(big_blind),
            legal_actions=legal_actions,
            disable_rules=disable_rules,
        )

    return strategy


# ── Reconstruction helpers ──────────────────────────────────────────────


def _reconstruct_anchors(payload):
    """Build a PersonalityAnchors instance (or a duck-typed namespace
    sufficient for `modify_strategy`'s reads).

    `modify_strategy` reads anchors via compute_trait_offsets — which
    accesses `baseline_aggression`, `baseline_looseness`, `ego`,
    `poise`, `expressiveness`, `risk_identity`, `adaptation_bias`. A
    SimpleNamespace with those attrs is enough.
    """
    if not payload or not isinstance(payload, dict):
        return None
    try:
        from poker.psychology_model import PersonalityAnchors

        # Use the real class so any future invariants are checked.
        return PersonalityAnchors(
            **{
                k: v
                for k, v in payload.items()
                if k
                in {
                    'baseline_aggression',
                    'baseline_looseness',
                    'ego',
                    'poise',
                    'expressiveness',
                    'risk_identity',
                    'adaptation_bias',
                    'baseline_energy',
                    'recovery_rate',
                }
            }
        )
    except Exception:
        # Fall back to a duck type so replay doesn't fail outright.
        return SimpleNamespace(**payload)


def _reconstruct_emotional_state(payload):
    """Build an EmotionalShift-shaped namespace.

    `modify_strategy` reads `emotional_state.state` and
    `emotional_state.severity` / `emotional_state.intensity` via
    `apply_emotional_window_shift` indirectly. A SimpleNamespace with
    these attrs works.
    """
    if not payload or not isinstance(payload, dict):
        return None
    return SimpleNamespace(
        state=payload.get('state', 'composed'),
        severity=payload.get('severity', 'none'),
        intensity=payload.get('intensity', 0.0),
    )


def _reconstruct_decision_context(payload):
    if not payload or not isinstance(payload, dict):
        return None
    try:
        return DecisionContext(
            **{k: v for k, v in payload.items() if k in DecisionContext.__dataclass_fields__}
        )
    except (TypeError, ValueError):
        return None


def _reconstruct_stats(payload):
    if not payload or not isinstance(payload, dict):
        return None
    try:
        return AggregatedOpponentStats(
            **{
                k: v
                for k, v in payload.items()
                if k in AggregatedOpponentStats.__dataclass_fields__
            }
        )
    except (TypeError, ValueError):
        return None
