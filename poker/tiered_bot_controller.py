"""
TieredBotController - Solver baselines + personality distortion, no LLM decisions.

A subclass of AIPlayerController that:
- Makes decisions via solver-derived strategy tables + personality distortion
- Has full psychology system (tilt, emotions, axes)
- Never uses LLM for decisions (LLM is expression layer only, Phase 4)

Phases:
- Preflop: full strategy table lookup + personality modifier
- Postflop: hand-crafted flop strategies + turn/river heuristics + personality
"""

import dataclasses
import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from .controllers import AIPlayerController, _get_canonical_hand
from .card_utils import card_to_string
from .bounded_options import get_emotional_shift
from .strategy.nodes import PreflopNode
from .strategy.strategy_table import StrategyTable
from .strategy.preflop_classifier import build_preflop_node, get_6max_position
from .strategy.postflop_classifier import build_postflop_node
from .strategy.personality_modifier import modify_strategy, apply_river_bluff_guardrail
from .strategy.deviation_profiles import select_deviation_profile, DeviationProfile
from .strategy.action_mapper import resolve_preflop_sizing, resolve_postflop_sizing
from .strategy.push_fold import lookup_push_fold_action, PUSH_FOLD_THRESHOLD_BB
from .strategy.strategy_profile import StrategyProfile
from .strategy.hand_classification import simplify_hand_class
from .strategy.multiway import apply_multiway_adjustment
from .strategy.math_floor import apply_pot_odds_floor
from .strategy.exploitation import (
    compute_exploitation_offsets,
    compute_exploitation_offsets_with_traces,
    apply_exploitation_offsets,
    classify_detected_patterns,
    classify_opponent_archetype,
    DecisionContext,
    AggregatedOpponentStats,
    ClampTier,
    GATING_FLOOR,
    OpponentSpot,
    _determine_clamp,
    aggregate_from_spots,
    compute_multiway_cbet_intensity,
    compute_steal_pressure_intensity,
    compute_value_vs_station_intensity,
    is_steal_pressure_enabled,
    is_value_vs_station_enabled,
    select_primary_aggressor,
)
from .strategy.value_override import (
    BLUFF_CATCH_TRIGGER_CLASSES,
    HandStrengthClass,
    compute_bluff_catch_strategy,
    compute_value_override_strategy,
    should_apply_bluff_catch_override,
    should_apply_value_override,
)
from .strategy.intervention_trace import (
    InterventionOperation,
    InterventionTrace,
    layer_order_for,
    make_no_op_trace,
)
from .strategy.short_stack import apply_short_stack_heuristics
from .stack_utils import big_blind_of, effective_stack_bb
from .hand_tiers import is_hand_in_range
from .strategy.expression_context import ExpressionContext
from .strategy.expression_generator import ExpressionGenerator
from .archetypes import classify_from_anchors

logger = logging.getLogger(__name__)


def _coarse_strength_tier(hand_name: str) -> str:
    """Map a hand_name label to one of Monster/Strong/Marginal/Weak/Drawing.

    Postflop labels from evaluate_hand_strength carry an explicit tier
    suffix (e.g. "Two Pair - Strong"); preflop labels from
    classify_preflop_hand carry a category prefix (e.g. "AKs - Suited
    broadway, Top 5%"). Returns '' when the label gives no usable signal.
    """
    if not hand_name:
        return ''
    s = hand_name.lower()

    # Postflop strength suffix wins when present.
    if 'monster' in s:
        return 'Monster'
    if 'very strong' in s or 'full house' in s or 'quads' in s or 'four of a kind' in s or 'straight flush' in s:
        return 'Monster'
    if 'strong' in s or 'flush' in s or 'straight' in s or 'trip' in s or 'three of a kind' in s or 'two pair' in s:
        return 'Strong'
    if 'marginal' in s or 'one pair' in s:
        return 'Marginal'
    if 'weak' in s or 'high card' in s:
        return 'Weak'

    # Preflop preview categories.
    if 'top 5%' in s or 'top 10%' in s or 'premium' in s or 'high pocket pair' in s:
        return 'Monster'
    if 'top 20%' in s or 'top 25%' in s or 'medium pocket pair' in s or 'suited broadway' in s:
        return 'Strong'
    if 'top 35%' in s or 'top 45%' in s or 'offsuit broadway' in s or 'suited ace' in s or 'low pocket pair' in s:
        return 'Marginal'
    if 'bottom' in s or 'offsuit ace' in s:
        return 'Weak'

    return ''


# Canonical exploitation rule order — mirrors compute_exploitation_offsets_
# with_traces. Kept in one place so the controller-level early-out (when
# manager / anchors unavailable) emits the same rule_id surface as a
# normal-path evaluation that gated each rule out individually.
# Re-export from strategy.exploitation so the early-out path emits the
# same trace surface as the hot path. T3-62 — was previously duplicated
# locally and the two definitions had already drifted.
from .strategy.exploitation import RULE_ORDER as _EXPLOITATION_RULE_ORDER  # noqa: E402


def _exploitation_no_op_traces(
    reason_code: str,
    disable_rules=None,
) -> List[InterventionTrace]:
    """One no-op trace per declared exploitation/Phase 8 rule.

    Used for the controller-level early-out paths (manager / anchors
    unavailable) where compute_exploitation_offsets_with_traces never
    runs. Keeps the per-decision trace surface consistent across
    decisions — `rule_id`-level firing-rate analyses see all 7 rules
    every decision, just with different reason codes.

    Phase 7.6 Step 5: when a rule is in `disable_rules`, its trace
    reports `disabled_by_ablation` instead of `reason_code`. The
    ablation signal wins over the natural early-out signal so Mode 4
    can attribute correctly even on the manager-unavailable path.
    """
    from .strategy.intervention_trace import (
        is_rule_disabled,
        layer_order_for,
        make_disabled_trace,
        make_no_op_trace,
    )
    out = []
    for (layer, rule_id) in _EXPLOITATION_RULE_ORDER:
        if is_rule_disabled(disable_rules, layer, rule_id):
            out.append(make_disabled_trace(
                layer=layer, rule_id=rule_id,
                layer_order=layer_order_for(layer),
            ))
        else:
            out.append(make_no_op_trace(
                layer=layer, rule_id=rule_id,
                layer_order=layer_order_for(layer),
                reason_code=reason_code,
            ))
    return out


def _fill_prior_action_source(
    current_trace: InterventionTrace,
    earlier_traces: List[InterventionTrace],
) -> InterventionTrace:
    """Set `current_trace.prior_action_source` from the last fired
    earlier trace in `earlier_traces`. Returns a new InterventionTrace
    (the dataclass is frozen).

    Phase 7.6 Step 2: bluff_catch is now downstream of value_override
    in the postflop pipeline, so when both fire we want bluff_catch's
    trace to record `prior_action_source='strong_hand_override.default'`
    (or whichever earlier layer last took the action). This makes the
    overwrite chain visible without an O(n²) walk at analysis time.

    If no earlier layer fired (or `current_trace` itself is fired=False),
    the field is left as-is. Layers that did not modify the strategy
    don't count as the "source" of the prior action.
    """
    if not current_trace.fired:
        return current_trace
    if current_trace.prior_action_source:
        # Already filled (e.g. layer set it directly) — don't clobber.
        return current_trace

    for prior in reversed(earlier_traces):
        if prior.fired:
            return dataclasses.replace(
                current_trace,
                prior_action_source=f'{prior.layer}.{prior.rule_id}',
            )
    return current_trace


class TieredBotController(AIPlayerController):
    """AI player using 3-layer tiered architecture.

    Layer 1: Solver-derived baselines (strategy table lookup)
    Layer 2: Personality distortion (logit-space modification)
    Layer 3: Expression (LLM narrates - not implemented in Phase 1)
    """

    def __init__(
        self,
        player_name: str,
        strategy_table: StrategyTable,
        state_machine=None,
        llm_config=None,
        debug_logging: bool = False,
        rng_seed=None,
        skip_personality_distortion: bool = False,
        expression_generator: Optional[ExpressionGenerator] = None,
        hu_strategy_table: Optional[StrategyTable] = None,
        **kwargs,
    ):
        super().__init__(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            **kwargs,
        )
        self.strategy_table = strategy_table
        self.hu_strategy_table = hu_strategy_table
        self.debug_logging = debug_logging
        self.rng = random.Random(rng_seed)
        # Competitive feel: bet sizing jitter band. When > 0, the action
        # mapper samples the raise-to amount uniformly from
        # [target * (1 - sizing_jitter), target * (1 + sizing_jitter)]
        # instead of always emitting the exact table-derived value.
        # Default 0.0 preserves deterministic sizing — controllers /
        # experiment configs that want the variance enable it explicitly.
        # Zero EV cost (band is symmetric around the table's intent),
        # but breaks sizing tells like "always bets 67% on the flop."
        self.sizing_jitter: float = 0.0
        # Relationship layer (Track B Phase 2): when True (default),
        # _apply_exploitation reads get_relationship_modifier() for the
        # selected target opponent and scales pattern-derived offsets
        # accordingly. Set to False to back the modifier seam out at
        # runtime without redeploying — the only feature flag justified
        # in Phase 1 per the consultancy review, given the seam touches
        # the load-bearing exploitation path and a regression there is
        # slow to debug under sim runtime pressure. Sim A/B runs can
        # compare flag-on vs flag-off to isolate any modifier-driven
        # regression to this one boolean.
        self.apply_relationship_modifier: bool = True
        # Stashed at the end of each _apply_exploitation call for
        # diagnostics / Mode 1 replay. None when the modifier seam
        # didn't fire (flag off, no observer_id, no target, identity
        # modifier).
        self._last_relationship_modifier = None
        self._last_relationship_target_id: Optional[str] = None
        self._deviation_profile: Optional[DeviationProfile] = None
        self.skip_personality_distortion = skip_personality_distortion
        self.expression_generator = expression_generator
        # Phase 7.6: per-decision intervention trace accumulator. Reset
        # at the start of each decision method; default empty so readers
        # never see a stale list from a prior controller instance.
        self._last_intervention_trace: List[InterventionTrace] = []
        # Phase 7.6 Step 6: per-decision pipeline snapshot for Mode 1
        # (shadow-eval) replay. Filled in incrementally during
        # _get_postflop_decision / _get_preflop_decision.
        self._last_pipeline_snapshot: Dict[str, Any] = {}
        # Phase 7.6 Step 5: ablation hook. Set this to a
        # FrozenSet[Tuple[str, str]] of (layer, rule_id) entries to
        # suppress those rules at decision time. Default is empty —
        # all rules fire normally. Mode 4 (ablation matrix) sweeps
        # set this per matchup; Mode 1 (shadow-eval) uses it for
        # counterfactual per-decision evaluation.
        self.disable_rules: frozenset = frozenset()

        # Multi-street context layer (docs/plans/STRUCTURAL_PASSIVITY_PLAN.md).
        # OFF by default — byte-identical to pre-layer behavior. When enabled,
        # the postflop pipeline reads hero's-own-line + sustained-aggression
        # context (which the memoryless table lacks) and applies a narrowly-
        # gated barrel-continuation (H1) / fold-to-double-barrel (H2) override.
        # The two sub-toggles let the A/B isolate which hypothesis carries any
        # effect (H1-only / H2-only / both).
        self.enable_multistreet_context: bool = False
        self.multistreet_h1_barrel: bool = True
        self.multistreet_h2_foldbarrel: bool = True

        # Sim-mode performance flag. When True, decision_analyzer
        # skips Monte Carlo equity computation (~200-500ms per
        # decision — dominant cost in long sim runs) but still
        # persists trace + snapshot. Production / UI paths leave
        # this False so coaching and decision-quality scoring keep
        # their equity field. Set by the experiment runner; default
        # off so non-sim callers see no behavior change.
        self.skip_equity_in_analysis: bool = False

    def _snapshot_personality_inputs(self, anchors, emotional_state) -> None:
        """Phase 7.6 Step 6: record the inputs `modify_strategy` consumed
        so the replay function can re-invoke the personality layer.

        Stores into `self._last_pipeline_snapshot`. Best-effort — if
        anchors / emotional_state aren't serializable, the snapshot key
        is omitted and replay falls back to skipping that layer.
        """
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if snap is None:
            return
        if anchors is not None:
            try:
                snap['anchors'] = {
                    'baseline_aggression': float(getattr(anchors, 'baseline_aggression', 0.5)),
                    'baseline_looseness': float(getattr(anchors, 'baseline_looseness', 0.5)),
                    'ego': float(getattr(anchors, 'ego', 0.5)),
                    'poise': float(getattr(anchors, 'poise', 0.5)),
                    'expressiveness': float(getattr(anchors, 'expressiveness', 0.5)),
                    'risk_identity': float(getattr(anchors, 'risk_identity', 0.5)),
                    'adaptation_bias': float(getattr(anchors, 'adaptation_bias', 0.5)),
                    'baseline_energy': float(getattr(anchors, 'baseline_energy', 0.5)),
                    'recovery_rate': float(getattr(anchors, 'recovery_rate', 0.15)),
                }
            except (TypeError, ValueError):
                pass
        if emotional_state is not None:
            snap['emotional_state'] = {
                'state': getattr(emotional_state, 'state', 'composed'),
                'severity': getattr(emotional_state, 'severity', 'none'),
                'intensity': float(getattr(emotional_state, 'intensity', 0.0) or 0.0),
            }
        # Deviation profile name (reverse-lookup against DEVIATION_PROFILES).
        try:
            from .strategy.deviation_profiles import DEVIATION_PROFILES
            for name, candidate in DEVIATION_PROFILES.items():
                if candidate is self.deviation_profile:
                    snap['deviation_profile_name'] = name
                    break
        except Exception:
            pass

    def _build_narration_facts(self, phase: str):
        """Phase 7.6 Step 5: build a NarrationFacts payload from the
        controller's per-decision intervention trace.

        Returns None when no trace is available or the adapter raises
        — the ExpressionContext.narration_facts field stays None and
        the LLM prompt falls back to the standard template.

        `phase` here is the controller's narrow string (e.g. 'flop',
        'pre_flop'); we normalize to the NarrationContext.street
        convention.
        """
        traces = getattr(self, '_last_intervention_trace', None)
        if not traces:
            return None
        try:
            from .strategy.narration_facts import (
                NarrationContext,
                traces_to_narration_facts,
            )
            street = (phase or '').replace('pre_flop', 'preflop').lower()
            ctx = NarrationContext(
                street=street,
                position_context='',  # not yet captured per-decision
                risk_posture='',      # ditto
            )
            return traces_to_narration_facts(traces, ctx)
        except Exception as e:  # noqa: BLE001 — narration is observability
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"narration_facts build failed: {e}"
            )
            return None

    def _snapshot_math_floor_inputs(self, game_state, player_idx: int) -> None:
        """Phase 7.6 Step 6: record math-floor inputs for replay."""
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if snap is None:
            return
        try:
            player = game_state.players[player_idx]
            big_blind = getattr(game_state, 'current_ante', 0) or 0
            pot_total = (
                game_state.pot.get('total', 0)
                if isinstance(getattr(game_state, 'pot', None), dict) else 0
            )
            cost_to_call = getattr(game_state, 'call_amount', 0) or 0
            snap['cost_to_call'] = int(cost_to_call)
            snap['pot_total'] = int(pot_total)
            snap['player_stack'] = int(getattr(player, 'stack', 0) or 0)
            snap['player_bet'] = int(getattr(player, 'bet', 0) or 0)
            snap['big_blind'] = int(big_blind)
        except (AttributeError, TypeError, IndexError):
            # Best-effort — leave snap incomplete on weird states.
            pass

    def _snapshot_exploitation_inputs(
        self, *, stats, decision_context, adaptation_bias: float,
        tilt_factor: float, exploitation_strength: float,
        multiway_cbet_intensity: float, vvs_intensity_used: float,
        steal_intensity_used: float, clamp_value: float = 0.4,
        clamp_tier_label: str = 'extreme',
    ) -> None:
        """Phase 7.6 Step 6: record exploitation pipeline inputs."""
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if snap is None:
            return
        if stats is not None:
            try:
                import dataclasses
                snap['aggregated_stats'] = dataclasses.asdict(stats)
            except (TypeError, ValueError):
                pass
        if decision_context is not None:
            try:
                import dataclasses
                snap['decision_context'] = dataclasses.asdict(decision_context)
            except (TypeError, ValueError):
                pass
        snap['adaptation_bias'] = float(adaptation_bias)
        snap['tilt_factor'] = float(tilt_factor)
        snap['exploitation_strength'] = float(exploitation_strength)
        snap['multiway_cbet_intensity'] = float(multiway_cbet_intensity)
        snap['value_vs_station_intensity_used'] = float(vvs_intensity_used)
        snap['steal_pressure_intensity_used'] = float(steal_intensity_used)
        snap['clamp_value'] = float(clamp_value)
        snap['clamp_tier_label'] = str(clamp_tier_label)

    @property
    def deviation_profile(self) -> DeviationProfile:
        """Lazy-init deviation profile from personality anchors."""
        if self._deviation_profile is None:
            if self.psychology and self.psychology.anchors:
                self._deviation_profile = select_deviation_profile(self.psychology.anchors)
            else:
                # Fallback to TAG if no psychology loaded yet
                from .strategy.deviation_profiles import DEVIATION_PROFILES
                self._deviation_profile = DEVIATION_PROFILES['tag']
        return self._deviation_profile

    @property
    def archetype_name(self) -> str:
        """Get personality archetype name from anchors."""
        if self.skip_personality_distortion:
            return 'baseline'
        anchors = self.psychology.anchors if self.psychology else None
        if not anchors:
            return 'tag'
        if anchors.baseline_looseness < 0.25 and anchors.baseline_aggression < 0.25:
            return 'nit'
        if anchors.baseline_looseness > 0.80 and anchors.baseline_aggression > 0.80:
            return 'maniac'
        base = classify_from_anchors(
            anchors.baseline_looseness, anchors.baseline_aggression
        )
        return {
            'tight_passive': 'rock', 'tight_aggressive': 'tag',
            'loose_passive': 'calling_station', 'loose_aggressive': 'lag',
            'default': 'tag',
        }.get(base, 'tag')

    def decide_action(self, game_messages=None) -> Dict:
        """Tiered decision: bypass the LLM-coupled parent pipeline.

        The parent AIPlayerController.decide_action runs LLM bookkeeping
        (conversation memory, chattiness checks, message summarization) that
        TieredBotController doesn't need — decisions come from strategy
        tables, not the LLM. We go straight to _get_ai_decision.

        The optional expression layer (Layer 3) is invoked inside
        _get_ai_decision via _attach_expression after the action commits.
        """
        # Stash recent table activity for the Layer 3 narration prompt.
        # The action is already locked by then; this is descriptive context
        # so the LLM can reference opponents by name and react in character.
        self._current_game_messages = game_messages
        game_state = self.state_machine.game_state
        try:
            valid_actions = game_state.current_player_options
        except Exception:
            valid_actions = ['fold', 'check', 'call', 'raise']
        return self._get_ai_decision(
            message='',
            valid_actions=valid_actions,
            call_amount=getattr(game_state, 'call_amount', 0) or 0,
        )

    def _get_ai_decision(self, message: str, **context) -> Dict:
        """Override: Use strategy tables + personality distortion instead of LLM.

        Routes to preflop or postflop decision logic based on game phase.
        """
        game_state = self.state_machine.game_state
        player = game_state.current_player
        player_idx = game_state.current_player_idx
        valid_actions = context.get('valid_actions', [])
        phase = self.state_machine.current_phase

        is_preflop = phase and phase.name == 'PRE_FLOP'
        if not is_preflop:
            return self._get_postflop_decision(
                game_state, player_idx, valid_actions, context
            )

        # ── Preflop decision ──
        # Phase 7.6 (Step 2): per-decision intervention trace accumulator.
        # Reset at the top so a fallback / early-return path doesn't leak
        # a stale trace from the prior decision. Symmetric with the
        # postflop method's init at line ~316.
        self._last_intervention_trace: List[InterventionTrace] = []
        # Phase 7.6 (Step 6): pipeline snapshot for Mode 1 (shadow-eval).
        self._last_pipeline_snapshot: Dict[str, Any] = {
            'phase': 'PRE_FLOP',
            'legal_actions': list(valid_actions),
        }

        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        canonical_hand = _get_canonical_hand(hole_cards) if hole_cards else ''

        if not canonical_hand:
            logger.warning(f"[TIERED_BOT] {self.player_name}: No canonical hand, using fallback")
            return self._postflop_fallback(valid_actions)

        node = build_preflop_node(game_state, player_idx, canonical_hand)

        # Phase 7: route to HU chart when the hand started 2-handed. Gate on
        # seated count (not non-folded count) so 6-max spots that collapse to
        # 2 players after folds still use the 6-max chart.
        num_seated = len(game_state.players)
        preflop_table = (
            self.hu_strategy_table
            if num_seated == 2 and self.hu_strategy_table is not None
            else self.strategy_table
        )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"hand={canonical_hand} node_key={node.key} "
                f"chart={'HU' if preflop_table is self.hu_strategy_table else '6max'}"
            )

        # Layer 1: Lookup base strategy. Short-stack HU spots bypass the
        # deep-stack table and use the dedicated push/fold chart instead;
        # the deep-stack ranges are mis-calibrated below ~15 BB because
        # standard raise sizes commit too much of the stack to be coherent
        # short of jamming.
        push_fold_action = self._try_push_fold_lookup(
            canonical_hand, game_state, player_idx, num_seated,
        )
        if push_fold_action is not None:
            base_strategy = StrategyProfile(
                action_probabilities={push_fold_action: 1.0}
            )
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"push_fold={push_fold_action} hand={canonical_hand}"
                )
            self._last_pipeline_snapshot['push_fold_routed'] = True
        else:
            base_strategy = preflop_table.lookup_with_fallback(node, valid_actions)
            self._last_pipeline_snapshot['push_fold_routed'] = False

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"base_strategy={base_strategy.action_probabilities}"
            )

        # Snapshot preflop base_strategy (already an input to personality).
        self._last_pipeline_snapshot['base_strategy_probs'] = dict(
            base_strategy.action_probabilities
        )

        # Layer 2: Personality distortion (skipped for BaselineSolverBot)
        emotional_state = get_emotional_shift(self.psychology)
        anchors = self.psychology.anchors if self.psychology else None

        # Snapshot personality inputs.
        self._snapshot_personality_inputs(anchors, emotional_state)

        if anchors and not self.skip_personality_distortion:
            modified_strategy, personality_trace = modify_strategy(
                base=base_strategy,
                legal_actions=valid_actions,
                anchors=anchors,
                emotional_state=emotional_state,
                deviation_profile=self.deviation_profile,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
        else:
            modified_strategy = base_strategy
            personality_trace = make_no_op_trace(
                layer='personality', rule_id='default',
                layer_order=layer_order_for('personality'),
                reason_code='distortion_skipped',
            )
        self._last_intervention_trace.append(personality_trace)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"modified_strategy={modified_strategy.action_probabilities}"
            )

        # Phase 6: opponent exploitation (between personality and math floor)
        # Preflop passes hand_strength=None — value_vs_station is
        # postflop-only and the preflop classifier returns a different
        # two-class enum (STRONG / NOT_STRONG) consumed only by the
        # value_override path below.
        modified_strategy, exploitation_traces = self._apply_exploitation(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=None,
        )
        self._last_intervention_trace.extend(exploitation_traces)

        # Phase 6.5: strong-hand value override.
        # Replaces strategy entirely when hero has a top-tier hand vs a
        # detected hyper-aggressive opponent — offsets can't shift
        # probability mass enough for these spots.
        modified_strategy, value_override_trace = self._apply_value_override(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=self._classify_preflop_hand_strength(canonical_hand, anchors),
        )
        self._last_intervention_trace.append(value_override_trace)

        # Playstyle-gated rule diagnostics. Preflop only sees the
        # steal_pressure counters fire (value_vs_station is
        # postflop-only). Same call site shape as postflop so the
        # method is symmetric.
        self._tally_playstyle_rule_event()

        # Phase 6 Step B: short-stack heuristic. Depth-aware suppression
        # of medium-raise probability mass below 20 BB effective stack.
        # Independent of opponent type — always fires when stack is short.
        effective_stack_bb = self._compute_effective_stack_bb(game_state, player_idx)
        # Snapshot for Mode 1 replay.
        self._last_pipeline_snapshot['effective_stack_bb'] = effective_stack_bb
        modified_strategy, short_stack_trace = apply_short_stack_heuristics(
            modified_strategy,
            effective_stack_bb=effective_stack_bb,
            legal_actions=valid_actions,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )
        self._last_intervention_trace.append(short_stack_trace)

        # Math floor: override when pot odds / pot-committed / short stack
        # make personality-driven folds clearly -EV.
        self._snapshot_math_floor_inputs(game_state, player_idx)
        modified_strategy, math_floor_trace = self._apply_math_floor(
            modified_strategy, game_state, player_idx, valid_actions
        )
        math_floor_trace = _fill_prior_action_source(
            math_floor_trace, self._last_intervention_trace,
        )
        self._last_intervention_trace.append(math_floor_trace)

        abstract_action = modified_strategy.sample_action(self.rng)
        self._last_pipeline_snapshot['sampled_abstract_action'] = abstract_action

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"sampled={abstract_action} emotional={emotional_state.state}"
            )

        game_action, raise_to = resolve_preflop_sizing(
            abstract_action, game_state, player_idx,
            rng=self.rng,
            sizing_jitter=getattr(self, 'sizing_jitter', 0.0),
        )

        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(
                game_action, raise_to, valid_actions
            )

        self._last_pipeline_snapshot['resolved_action'] = game_action
        self._last_pipeline_snapshot['resolved_raise_to'] = raise_to

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"final_action={game_action} raise_to={raise_to}"
            )

        decision = {
            'action': game_action,
            'raise_to': raise_to,
            'dramatic_sequence': [],
            'hand_strategy': (
                f"Tiered bot: {node.scenario} {node.position} "
                f"with {canonical_hand} -> {abstract_action}"
            ),
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }
        self._attach_expression(decision, game_state, player_idx, phase='pre_flop')
        return decision

    def _get_postflop_decision(
        self, game_state, player_idx: int,
        valid_actions: List[str], context: dict,
    ) -> Dict:
        """Postflop decision: strategy table + personality + multiway + guardrails."""
        player = game_state.players[player_idx]

        # Phase 7.6 (Step 1): per-decision intervention trace accumulator.
        # Reset at the top so a fallback / early-return path doesn't leak
        # a stale trace from the prior decision. Only bluff_catch is
        # migrated in Step 1; other layers append once they migrate.
        self._last_intervention_trace: List[InterventionTrace] = []

        # Phase 7.6 (Step 6): per-decision strategy pipeline snapshot
        # for Mode 1 (shadow-eval) replay. Filled in incrementally as
        # the pipeline runs; capture step serializes to JSON.
        self._last_pipeline_snapshot: Dict[str, Any] = {
            'phase': 'POSTFLOP',
            'legal_actions': list(valid_actions),
        }

        # 1. Convert cards to string format
        hole_cards = [card_to_string(c) for c in player.hand] if player.hand else []
        community_cards = [
            card_to_string(c) for c in game_state.community_cards
        ] if game_state.community_cards else []

        if not hole_cards or len(community_cards) < 3:
            return self._postflop_fallback(valid_actions)

        # 2. Build PostflopNode
        try:
            node = build_postflop_node(
                game_state, player_idx, hole_cards, community_cards
            )
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop_classifier error: {e}, using fallback"
            )
            return self._postflop_fallback(valid_actions)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop node_key={node.key}"
            )
        # Snapshot the node key (encodes street|position|pot_type|texture|
        # hand_class|draw|action_context|spr) so passivity instrumentation can
        # pair the resolved action with its full postflop context without
        # re-deriving the node. Cheap; the snapshot already exists for replay.
        self._last_pipeline_snapshot['node_key'] = node.key

        # 3. Lookup base strategy (with texture fallback)
        base_strategy = self.strategy_table.lookup_postflop_with_fallback(
            node, valid_actions
        )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop base_strategy={base_strategy.action_probabilities}"
            )

        # 4. Multiway adjustment (if > 2 active players)
        active_count = sum(
            1 for p in game_state.players
            if not p.is_folded
        )
        if active_count > 2:
            base_strategy = apply_multiway_adjustment(
                base_strategy, active_count, node.position
            )
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"multiway_adjusted ({active_count} players)="
                    f"{base_strategy.action_probabilities}"
                )
        # Snapshot: base_strategy AFTER multiway adjustment is the input
        # to the personality layer — that's what replay needs.
        self._last_pipeline_snapshot['base_strategy_probs'] = dict(
            base_strategy.action_probabilities
        )

        # 5. Personality distortion (skipped for BaselineSolverBot)
        emotional_state = get_emotional_shift(self.psychology)
        anchors = self.psychology.anchors if self.psychology else None

        # Snapshot personality inputs.
        self._snapshot_personality_inputs(anchors, emotional_state)

        if anchors and not self.skip_personality_distortion:
            modified_strategy, personality_trace = modify_strategy(
                base=base_strategy,
                legal_actions=valid_actions,
                anchors=anchors,
                emotional_state=emotional_state,
                deviation_profile=self.deviation_profile,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
        else:
            modified_strategy = base_strategy
            personality_trace = make_no_op_trace(
                layer='personality', rule_id='default',
                layer_order=layer_order_for('personality'),
                reason_code='distortion_skipped',
            )
        self._last_intervention_trace.append(personality_trace)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop modified={modified_strategy.action_probabilities}"
            )

        # 6. River bluff guardrail
        if node.street == 'river':
            simplified_class = simplify_hand_class(
                node.made_tier, node.draw_modifier
            )
            modified_strategy = apply_river_bluff_guardrail(
                modified_strategy, simplified_class, self.archetype_name
            )
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"river_guardrail class={simplified_class} "
                    f"arch={self.archetype_name} "
                    f"result={modified_strategy.action_probabilities}"
                )

        # Hand strength is consumed by exploitation (value_vs_station
        # gate) AND by value_override + bluff_catch below, so compute
        # it once up front. The classifier is pure on `node`, so the
        # ordering shift vs older revisions is safe.
        hand_strength = self._classify_postflop_hand_strength(node)
        # Snapshot hand_strength for Mode 1 replay.
        self._last_pipeline_snapshot['hand_strength'] = hand_strength
        # Plan §1: snapshot extended classification (nut_status, danger
        # flags) so diagnostic traces and §2 defense-floor consumers can
        # read the joint (hand_class, nut_status) gate without
        # re-classifying.
        self._last_pipeline_snapshot['nut_status'] = node.nut_status
        self._last_pipeline_snapshot['danger_flags'] = node.danger_flags
        # Plan §2 + §4: build DecisionContext once at the outer scope so
        # the §4 snapshot fields and the §2 defense_floor can read it
        # without each rebuilding via the inner `_apply_*` methods. Inner
        # methods continue to rebuild their own context (pre-existing
        # redundancy); the outer instance is used only by post-bluff_catch
        # consumers and the snapshot. primary_aggressor_spot=None falls
        # back to the aggregate path which is sufficient for the bet
        # bucket / required_equity / facing_bet fields.
        outer_decision_context = self._build_decision_context(
            game_state, player_idx,
        )
        # Plan §4: snapshot bet-size bucket + required_equity for
        # diagnostics. The DecisionContext already carries these for
        # strategy rules; snapshotting here mirrors the pattern used for
        # nut_status/danger_flags so post-hand analysis
        # (casebot_breakdown etc.) can read them off the controller's
        # last-decision state.
        self._last_pipeline_snapshot['bet_bucket'] = (
            outer_decision_context.bet_bucket
        )
        self._last_pipeline_snapshot['required_equity'] = (
            outer_decision_context.required_equity
        )
        # Plan §6: opponent_archetype is snapshotted inside
        # `_tally_exploitation_event` (where `stats` is already
        # selected) — see that method. Done as a side effect of the
        # tally call so we don't duplicate _select_exploitation_stats_from_spots.

        # 6a. Phase 6: opponent exploitation (between personality and math floor)
        modified_strategy, exploitation_traces = self._apply_exploitation(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=hand_strength,
        )
        self._last_intervention_trace.extend(exploitation_traces)

        # 6a.45 Phase A induce_override: smooth-call vs detected
        # multi-street barrelers with nuts IP on dry boards. Sits
        # IMMEDIATELY BEFORE value_override; when induce fires, value
        # override defers via its `prior_layer_fired` check. The two
        # rules' gates overlap on hyper_aggressive+nuts spots — induce
        # has the narrower gate (IP, dry board, ≥40 BB, sample floor)
        # and wins when both match.
        modified_strategy, induce_override_trace = self._apply_induce_override(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            node=node,
            hand_strength=hand_strength,
            active_opponent_count=active_count - 1,
        )
        self._last_intervention_trace.append(induce_override_trace)

        # 6a.5 Phase 6.5: strong-hand value override.
        # Replaces strategy when hero has a strong made hand vs a detected
        # hyper-aggressive opponent. Sits after exploitation so it takes
        # precedence on the few decisions where it fires.
        modified_strategy, value_override_trace = self._apply_value_override(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=hand_strength,
            prior_layer_fired=induce_override_trace.fired,
        )
        self._last_intervention_trace.append(value_override_trace)

        # Playstyle-gated rule diagnostics. Must run after value_override
        # so the fired-vs-superseded distinction for value_vs_station is
        # correct (override replaces the strategy, which discards
        # Phase-8 offsets — counter tracks that case separately).
        self._tally_playstyle_rule_event()

        # 6a.5b Phase 7.5 Item 1: bluff-catch override.
        # Mutually exclusive with the strong-hand override above (trigger
        # classes are disjoint by hand_strength). Replaces strategy with
        # a pot-odds-conditional {call, fold} distribution when hero has
        # a marginal made hand (medium/weak) vs a confirmed EXTREME-tier
        # aggressor, with multiway / dangerous-board suppression applied.
        modified_strategy, bluff_catch_trace = self._apply_bluff_catch_override(
            modified_strategy, game_state, player_idx, valid_actions,
            anchors, emotional_state,
            hand_strength=hand_strength,
        )
        # Fill in prior_action_source — if an earlier layer fired (made
        # `replaced_prior_action=True` true), record which one. Today
        # only value_override is the candidate; later steps add more
        # earlier layers and the same loop covers them.
        bluff_catch_trace = _fill_prior_action_source(
            bluff_catch_trace, self._last_intervention_trace,
        )
        self._last_intervention_trace.append(bluff_catch_trace)

        # 6a.5b.2 Multi-street context (STRUCTURAL_PASSIVITY_PLAN.md).
        # Behind enable_multistreet_context (default off). Reads hero's-own-
        # line (was_prev_street_aggressor) + sustained-aggression
        # (facing_double_barrel) — signals the memoryless table can't see —
        # and applies a narrowly-gated barrel-continuation (H1, HU only) /
        # fold-to-double-barrel (H2) override. Sits before defense_floor and
        # feeds prior_layer_fired so the floor defers when it replaces the
        # distribution; downstream math_floor keeps final say on pot-odds
        # mandates. OFF arm is byte-identical to current behavior.
        multistreet_trace = make_no_op_trace(
            layer='multistreet_context', rule_id='default',
            layer_order=layer_order_for('multistreet_context'),
            reason_code='flag_disabled',
        )
        if getattr(self, 'enable_multistreet_context', False):
            from .strategy.multistreet_context import (
                apply_multistreet_context, derive_signals,
            )
            signals = derive_signals(self, node.street)
            ms_prior_fired = (
                induce_override_trace.fired
                or value_override_trace.fired
                or bluff_catch_trace.fired
            )
            modified_strategy, multistreet_trace = apply_multistreet_context(
                modified_strategy,
                signals=signals,
                hand_class=hand_strength,
                action_context=node.facing_action,
                active_count=active_count,
                h1_enabled=getattr(self, 'multistreet_h1_barrel', True),
                h2_enabled=getattr(self, 'multistreet_h2_foldbarrel', True),
                h1_classes=getattr(self, 'multistreet_h1_classes', None),
                prior_layer_fired=ms_prior_fired,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            multistreet_trace = _fill_prior_action_source(
                multistreet_trace, self._last_intervention_trace,
            )
        self._last_intervention_trace.append(multistreet_trace)

        # 6a.5c Plan §2: price-sensitive defense floor. Pumps call
        # probability for legitimate made hands at favorable prices
        # that the upstream rules left fold-heavy. Sits *after* both
        # overrides so it defers when either has already replaced the
        # distribution (prior_layer_fired). Reads §1's hand_class +
        # nut_status + danger_flags from the postflop node and §4's
        # required_equity + facing_bet from DecisionContext.
        from .strategy.defense_floor import apply_defense_floor
        prior_layer_fired = (
            induce_override_trace.fired
            or value_override_trace.fired
            or bluff_catch_trace.fired
            or multistreet_trace.fired
        )
        defense_floor_facing_bet = (
            outer_decision_context.bet_bucket is not None
        )
        modified_strategy, defense_floor_trace = apply_defense_floor(
            modified_strategy,
            hand_class=hand_strength,
            nut_status=node.nut_status,
            danger_flags=node.danger_flags,
            required_equity=outer_decision_context.required_equity,
            facing_bet=defense_floor_facing_bet,
            prior_layer_fired=prior_layer_fired,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )
        defense_floor_trace = _fill_prior_action_source(
            defense_floor_trace, self._last_intervention_trace,
        )
        self._last_intervention_trace.append(defense_floor_trace)

        # 6a.6 Phase 6 Step B: short-stack heuristic. Suppress medium-raise
        # probability mass below 20 BB effective stack — non-jam raises
        # are structurally bad at short depth.
        effective_stack_bb = self._compute_effective_stack_bb(game_state, player_idx)
        self._last_pipeline_snapshot['effective_stack_bb'] = effective_stack_bb
        modified_strategy, short_stack_trace = apply_short_stack_heuristics(
            modified_strategy,
            effective_stack_bb=effective_stack_bb,
            legal_actions=valid_actions,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )
        self._last_intervention_trace.append(short_stack_trace)

        # 6b. Math floor — override when arithmetic mandates a call/jam.
        # Runs AFTER personality + river guardrail so it has final say.
        self._snapshot_math_floor_inputs(game_state, player_idx)
        modified_strategy, math_floor_trace = self._apply_math_floor(
            modified_strategy, game_state, player_idx, valid_actions
        )
        math_floor_trace = _fill_prior_action_source(
            math_floor_trace, self._last_intervention_trace,
        )
        self._last_intervention_trace.append(math_floor_trace)

        # 7. Sample action
        abstract_action = modified_strategy.sample_action(self.rng)
        self._last_pipeline_snapshot['sampled_abstract_action'] = abstract_action

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop sampled={abstract_action} "
                f"emotional={emotional_state.state}"
            )

        # 8. Resolve sizing
        game_action, raise_to = resolve_postflop_sizing(
            abstract_action, game_state, player_idx,
            rng=self.rng,
            sizing_jitter=getattr(self, 'sizing_jitter', 0.0),
        )

        # 9. Validate action is legal
        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(
                game_action, raise_to, valid_actions
            )

        self._last_pipeline_snapshot['resolved_action'] = game_action
        self._last_pipeline_snapshot['resolved_raise_to'] = raise_to

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop final={game_action} raise_to={raise_to}"
            )

        decision = {
            'action': game_action,
            'raise_to': raise_to,
            'dramatic_sequence': [],
            'hand_strategy': (
                f"Tiered bot: {node.street} {node.position} "
                f"{node.board_texture} {node.made_tier}/{node.draw_modifier} "
                f"-> {abstract_action}"
            ),
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }
        self._attach_expression(decision, game_state, player_idx, phase=node.street)
        return decision

    def _apply_exploitation(
        self, strategy, game_state, player_idx, valid_actions,
        anchors, emotional_state,
        hand_strength: Optional[str] = None,
    ) -> Tuple['StrategyProfile', List[InterventionTrace]]:
        """Phase 6 opponent exploitation step.

        Inserts between personality distortion and math floor. No-ops when:
        - opponent_model_manager is not attached (sim or test without manager)
        - anchors is None (BaselineSolverBot)
        - aggregated stats produce no offsets (cold start, low adaptation_bias,
          heavy tilt, or no opponent matches an exploitation rule)

        hand_strength is the postflop class string from
        `_classify_postflop_hand_strength` (see HandStrengthClass). The
        postflop caller computes it once and passes it in; the preflop
        caller passes None. Consumed only by the value_vs_station gate
        (STRONG_MADE / NUTS only).

        Phase 7.6 (Step 3): returns `(strategy, traces)` where `traces`
        is one InterventionTrace per declared rule (5 exploitation
        sub-rules + 2 Phase 8 layers). Even when the layer-level early-
        out fires (manager None, anchors None), all rules emit no_op
        traces so analysis sees a consistent rule_id surface.
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy, _exploitation_no_op_traces(
                'manager_unavailable', disable_rules=getattr(self, "disable_rules", frozenset()),
            )

        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        # Phase 6.7a: build spots once, then route stat selection through
        # the spot-aware path. The legacy aggregate fields are preserved
        # via aggregate_from_spots(), so unmigrated rules see identical
        # behavior in unambiguous cases.
        spots = self._build_opponent_spots(game_state, manager)
        stats, primary_spot, ambiguous = (
            self._select_exploitation_stats_from_spots(spots, game_state)
        )

        decision_context = self._build_decision_context(
            game_state, player_idx,
            primary_aggressor_spot=primary_spot,
        )

        # Phase 6.7b Part A: pre-compute multiway c-bet intensity from
        # spots when the decision context suggests we might fire (flop
        # as preflop aggressor + >1 active opponents). The helper
        # returns 0 unless all gates pass (all foldy, adequate samples,
        # no all-in opponents); the offset rule treats 0 as "don't fire."
        multiway_cbet_intensity = 0.0
        if (
            decision_context.is_flop_as_preflop_aggressor
            and decision_context.active_opponent_count > 1
        ):
            multiway_cbet_intensity = compute_multiway_cbet_intensity(spots)

        # Playstyle-gated rule families: spot context for stealing /
        # value extraction. The "raw" intensity is computed regardless
        # of the playstyle gate so the diagnostic counters can
        # distinguish `eligible` (intensity would be > 0) from
        # `enabled_eligible` (intensity actually flows through). The
        # "used" intensity (passed to compute_exploitation_offsets) is
        # zeroed when the archetype isn't in the rule's frozenset.
        archetype = self.archetype_name
        call_amount = getattr(game_state, 'call_amount', 0) or 0
        has_bet_legal = any(
            a == 'bet' or a.startswith('bet_')
            or a == 'raise' or a.startswith('raise_')
            or a == 'all_in'
            for a in valid_actions
        )

        vvs_intensity_raw = 0.0
        if (
            hand_strength in {
                HandStrengthClass.STRONG_MADE.value,
                HandStrengthClass.NUTS.value,
            }
            and call_amount == 0
            and has_bet_legal
        ):
            vvs_intensity_raw = compute_value_vs_station_intensity(spots)
        vvs_intensity_used = (
            vvs_intensity_raw if is_value_vs_station_enabled(archetype) else 0.0
        )

        steal_intensity_raw = 0.0
        if (
            decision_context.is_preflop
            and call_amount == 0
            and has_bet_legal
        ):
            steal_intensity_raw = compute_steal_pressure_intensity(spots)
        steal_intensity_used = (
            steal_intensity_raw if is_steal_pressure_enabled(archetype) else 0.0
        )

        # Plan §5: bluff reduction vs stations. Mirrors value_vs_station
        # but with the inverse hand-strength gate — fires on air-class
        # hands when a station is in the field. Shares the same station
        # detection (compute_value_vs_station_intensity returns >0 iff a
        # qualifying station is present), so reusing it keeps the
        # "what's a station" definition consistent. Hand-strength gate
        # below disjoint from vvs's strong+ gate; the two rules cannot
        # fire on the same decision.
        bluff_reduction_intensity_raw = 0.0
        if (
            hand_strength in {'air_no_draw', 'air_strong_draw'}
            and has_bet_legal
        ):
            bluff_reduction_intensity_raw = (
                compute_value_vs_station_intensity(spots)
            )
        # Re-use the value_vs_station playstyle gate — same archetypes
        # benefit (nit/rock/tag postflop archetypes that face stations).
        bluff_reduction_intensity_used = (
            bluff_reduction_intensity_raw
            if is_value_vs_station_enabled(archetype) else 0.0
        )

        exploitation_strength = getattr(self, 'exploitation_strength', 1.0)
        # Phase 8.1c: pass through whether at least one continuing
        # non-all-in opponent is station-like. Gates the base
        # hyper_passive rule against misfiring when the stake-weighted
        # aggregate looks station-y purely because an all-in station
        # dominated the weight. Reuses compute_value_vs_station_intensity
        # — it returns >0 iff a continuing non-all-in opponent passes
        # _is_hyper_passive with adequate sample.
        non_all_in_station_continuing = (
            compute_value_vs_station_intensity(spots) > 0.0
        )
        offsets, exploitation_traces = compute_exploitation_offsets_with_traces(
            stats=stats,
            adaptation_bias=anchors.adaptation_bias,
            decision_context=decision_context,
            available_actions=list(strategy.action_probabilities.keys()),
            tilt_factor=tilt_factor,
            exploitation_strength=exploitation_strength,
            multiway_cbet_intensity=multiway_cbet_intensity,
            value_vs_station_intensity=vvs_intensity_used,
            steal_pressure_intensity=steal_intensity_used,
            bluff_reduction_intensity=bluff_reduction_intensity_used,
            non_all_in_station_continuing=non_all_in_station_continuing,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )

        # Stash for the Phase-8 tally helper (called AFTER value_override
        # below so we know whether the override absorbed the offsets).
        # `phase_8_will_emit` mirrors the gate inside
        # compute_exploitation_offsets — when False, the function bails
        # before the Phase 8 branches regardless of intensity, so
        # `fired` shouldn't increment even though intensity was
        # "enabled_eligible." (The aggregate cold-start gate no longer
        # blocks Phase 8 — see exploitation.py docstring.)
        effective_bias = anchors.adaptation_bias * tilt_factor
        phase_8_will_emit = effective_bias > GATING_FLOOR

        self._last_value_vs_station_intensity_raw = vvs_intensity_raw
        self._last_value_vs_station_intensity_used = vvs_intensity_used
        self._last_steal_pressure_intensity_raw = steal_intensity_raw
        self._last_steal_pressure_intensity_used = steal_intensity_used
        self._last_phase_8_will_emit = phase_8_will_emit
        self._last_exploitation_archetype = archetype

        # Diagnostic counters: track detection vs firing per rule. Useful
        # for sim runs to see if exploitation is actually engaging.
        self._tally_exploitation_event(
            stats, offsets, decision_context, spots=spots,
            ambiguous_aggressor=ambiguous,
            multiway_cbet_intensity=multiway_cbet_intensity,
        )

        # Track B Phase 2: relationship-modifier scaling. Composes with
        # the pattern-derived offsets above; runs before clamp/gating
        # so the existing safety rails still bound the final shift.
        # Behind self.apply_relationship_modifier so the seam can be
        # backed out at runtime if a regression surfaces — see the
        # constructor docstring on that flag.
        self._last_relationship_modifier = None
        self._last_relationship_target_id = None
        if offsets and self.apply_relationship_modifier:
            offsets = self._apply_relationship_modifier_to_offsets(
                offsets=offsets,
                manager=manager,
                spots=spots,
                primary_spot=primary_spot,
            )

        if not offsets:
            return strategy, exploitation_traces

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"exploitation offsets={offsets}"
            )

        # Phase 7.5 Item 2c: route the L1 clamp through _determine_clamp,
        # replacing the legacy two-tier _pick_max_total_shift. Tier is
        # determined by opponent's postflop signal axes (AF_postflop OR
        # all_in_per_facing_bet OR postflop_jam_open_rate) with the
        # sliding-window ratchet-down applied when recent stats diverge.
        clamp_value, clamp_tier, winning_axis = self._compute_clamp(
            stats, manager, primary_spot,
        )

        # Stash tier diagnostic for downstream callers / capture.
        self._last_clamp_tier = clamp_tier
        self._last_clamp_axis = winning_axis

        # Phase 7.6 Step 6: snapshot exploitation inputs for replay.
        clamp_tier_label = (
            clamp_tier.value.lower() if hasattr(clamp_tier, 'value')
            else str(clamp_tier).lower()
        )
        self._snapshot_exploitation_inputs(
            stats=stats, decision_context=decision_context,
            adaptation_bias=anchors.adaptation_bias, tilt_factor=tilt_factor,
            exploitation_strength=exploitation_strength,
            multiway_cbet_intensity=multiway_cbet_intensity,
            vvs_intensity_used=vvs_intensity_used,
            steal_intensity_used=steal_intensity_used,
            clamp_value=clamp_value,
            clamp_tier_label=clamp_tier_label,
        )

        updated_strategy = apply_exploitation_offsets(
            strategy=strategy,
            offsets=offsets,
            legal_actions=valid_actions,
            max_total_shift=clamp_value,
        )
        return updated_strategy, exploitation_traces

    def _apply_relationship_modifier_to_offsets(
        self,
        offsets: Dict[str, float],
        manager,
        spots,
        primary_spot,
    ) -> Dict[str, float]:
        """Scale pattern-derived exploitation offsets by the relationship
        modifier for the selected target opponent.

        Composition (per the design doc's Phase 2 spec):
          1. Pattern detection produced the `offsets` dict above
             (unchanged).
          2. Resolve hero observer_id and the target opponent_id —
             aggressor when there is one, heat-max fallback otherwise.
          3. Read get_relationship_modifier(observer, target, now).
          4. Scale: bluff_freq_mult multiplies positive offsets on
             aggressive actions (bet_*, raise_*, all_in);
             fold_to_pressure_mult scales negative `fold` offsets.
          5. (call_threshold_offset is stashed on the controller for
             diagnostics — wiring it into the value-vs-station
             threshold path is a follow-up refinement.)
          6. Return the scaled offsets so existing clamp/gating runs
             unchanged.

        Returns the offsets dict (possibly mutated). Stashes the
        applied modifier + target id on the controller for replay
        diagnostics. Early-outs gracefully when:
          - No relationship_repo is attached to the manager
          - Hero has no resolved personality_id (display name not
            registered)
          - No suitable target can be picked
          - The computed modifier is the identity (no behavior change)

        In all early-out paths, returns the offsets dict verbatim.
        """
        from datetime import datetime
        from poker.memory.relationship_modifier import get_relationship_modifier

        # Manager must carry a relationship_repo for this to do anything.
        if getattr(manager, '_relationship_repo', None) is None:
            return offsets

        # Hero's stable personality_id. The opponent_model_manager
        # tracks display_name → personality_id via register_player_id;
        # if the hero hasn't been registered (e.g. sim runs without
        # full personality wiring), the modifier seam no-ops.
        name_to_id = getattr(manager, '_name_to_id', {})
        observer_id = name_to_id.get(self.player_name)
        if observer_id is None:
            return offsets

        # Target selection. Prefer the primary aggressor when one
        # exists (reuses _select_exploitation_stats_from_spots' work).
        # Fall back to heat-max for open / checked-around spots.
        target_id = self._select_relationship_target_id(
            manager=manager,
            spots=spots,
            primary_spot=primary_spot,
            observer_id=observer_id,
        )
        if target_id is None:
            return offsets

        modifier = get_relationship_modifier(
            manager=manager,
            observer_id=observer_id,
            target_opponent_id=target_id,
            now=datetime.utcnow(),
        )
        if modifier.is_identity:
            # Stash for diagnostics even though it doesn't change offsets —
            # makes "we considered the modifier and it was a no-op" visible
            # in replay traces.
            self._last_relationship_modifier = modifier
            self._last_relationship_target_id = target_id
            return offsets

        # Apply the multipliers. Composition is per-action:
        #   bluff_freq_mult     scales aggressive-action positive offsets
        #   fold_to_pressure_mult scales `fold`'s negative offset magnitude
        scaled = dict(offsets)
        for action, delta in offsets.items():
            if delta > 0 and self._is_aggressive_action_label(action):
                scaled[action] = delta * modifier.bluff_freq_mult
            elif action == 'fold' and delta < 0:
                # Scale the magnitude. modifier.fold_to_pressure_mult < 1
                # means "don't fold as much vs respected opponents" — i.e.
                # the original `fold -=` reduction gets dampened. So we
                # multiply the negative delta by the modifier.
                scaled[action] = delta * modifier.fold_to_pressure_mult

        self._last_relationship_modifier = modifier
        self._last_relationship_target_id = target_id

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: relationship modifier "
                f"target={target_id} mod={modifier} offsets={scaled}"
            )

        return scaled

    @staticmethod
    def _is_aggressive_action_label(label: str) -> bool:
        """True for action labels that represent aggressive moves
        (bet, raise, all_in). The exploitation rules emit these with
        named suffixes (bet_67, raise_3x, etc.) so we match by prefix."""
        return (
            label == 'bet'
            or label.startswith('bet_')
            or label == 'raise'
            or label.startswith('raise_')
            or label == 'jam'
            or label == 'all_in'
        )

    def _select_relationship_target_id(
        self,
        manager,
        spots,
        primary_spot,
        observer_id: str,
    ) -> Optional[str]:
        """Pick the (observer, target) pair for the relationship read.

        Rules (from design doc):
          - Eligible opponents = active, not all-in, in the hand.
          - If primary_spot is set (clear aggressor on this street),
            use it. Reuses _select_exploitation_stats_from_spots'
            existing aggressor selection — no parallel implementation.
          - Else, heat-max fallback: among eligible spots, pick the
            one with the highest projected heat from observer's POV.
            Ties: max respect, then alphabetical opponent_id.
          - If no eligible opponents have any relationship state, or
            no spot's name resolves to a personality_id, returns None
            and the modifier seam no-ops.

        All-in opponents are excluded because the bluff-frequency
        and fold-to-pressure multipliers have no meaningful effect
        against opponents who can't call further bets or apply more
        pressure. (Same rationale as compute_value_vs_station_intensity.)
        """
        name_to_id = getattr(manager, '_name_to_id', {})

        # Primary aggressor path
        if primary_spot is not None:
            target_id = name_to_id.get(primary_spot.name)
            return target_id  # may be None if name wasn't registered

        # Heat-max fallback. Only fires when there's no clear aggressor.
        eligible = [
            s for s in spots
            if s.is_active and not s.is_all_in
        ]
        if not eligible:
            return None

        repo = getattr(manager, '_relationship_repo', None)
        if repo is None:
            return None

        from datetime import datetime
        now = datetime.utcnow()
        best: Optional[Tuple[float, float, str]] = None  # (heat, respect, opp_id)
        for spot in eligible:
            opp_id = name_to_id.get(spot.name)
            if opp_id is None:
                continue
            state = repo.load_relationship_state(observer_id, opp_id, now=now)
            if state is None:
                continue
            key = (state.heat, state.respect, opp_id)
            if best is None or key > best:
                # Sort key: heat desc → respect desc → opp_id asc
                # (we negate by using tuple comparison; since we want
                # max-heat, max-respect, and alphabetical opp_id tie-
                # break, we compare on (heat, respect, -ord_of_opp_id)
                # equivalent via reverse-sort or via picking the max).
                # Simpler: just pick the lex-greatest tuple where
                # heat/respect are positively valued and opp_id is
                # tiebreaker — but we want SMALLEST opp_id for ties.
                # Use a normalized key.
                best = key
        if best is None:
            return None

        # Adjust tiebreaker: among all eligible with state, find
        # max (heat, respect); among those tied, the smallest opp_id.
        max_heat_respect = (best[0], best[1])
        # Collect all eligible matching max (heat, respect)
        candidates = []
        for spot in eligible:
            opp_id = name_to_id.get(spot.name)
            if opp_id is None:
                continue
            state = repo.load_relationship_state(observer_id, opp_id, now=now)
            if state is None:
                continue
            if (state.heat, state.respect) == max_heat_respect:
                candidates.append(opp_id)
        return min(candidates) if candidates else None

    def _compute_clamp(
        self, stats, manager, primary_spot,
    ):
        """Phase 7.5 Item 2c: build the (recent_stats, archetype) inputs
        for _determine_clamp from the controller's context.

        - recent_stats: pulled from the primary aggressor's
          OpponentTendencies.recent_postflop_stats() when a primary spot
          exists. None for the aggregate-fallback path (the sliding
          window only makes sense per-opponent).
        - archetype: the primary spot's name, used for the benchmark
          prior shortcut (off by default; enabled only in validation
          experiments).

        Returns (clamp_value, tier, winning_axis).
        """
        recent_stats = None
        archetype = None
        if primary_spot is not None and manager is not None:
            archetype = primary_spot.name
            try:
                model = manager.get_model(self.player_name, primary_spot.name)
                if model is not None:
                    t = getattr(model, 'tendencies', None)
                    if t is not None and hasattr(t, 'recent_postflop_stats'):
                        recent_stats = t.recent_postflop_stats()
            except Exception:
                recent_stats = None

        return _determine_clamp(
            stats=stats,
            recent_stats=recent_stats,
            bettor_archetype=archetype,
        )

    def _apply_induce_override(
        self, strategy, game_state, player_idx, valid_actions,
        anchors, emotional_state, *,
        node, hand_strength, active_opponent_count: int,
    ) -> Tuple['StrategyProfile', InterventionTrace]:
        """Phase A: induce override (smooth-call vs barrelers).

        Sits immediately before `_apply_value_override` in the postflop
        pipeline. When this rule fires, value_override defers via its
        `prior_layer_fired` check. See poker/strategy/induce_override.py
        for the full design + docs/plans/INDUCE_OVERRIDE_PHASE_A.md.

        Mirrors `_apply_value_override`'s shape: ablation check first,
        then manager + anchors gate, then spot-based stat selection,
        then delegate to the rule module's apply function. The rule
        module owns the actual gate logic; this method handles
        controller-side plumbing.
        """
        from .strategy.induce_override import apply_induce_override
        from .strategy.intervention_trace import (
            is_rule_disabled, make_disabled_trace,
        )

        if is_rule_disabled(
            getattr(self, "disable_rules", frozenset()),
            'induce_override', 'default',
        ):
            return strategy, make_disabled_trace(
                layer='induce_override', rule_id='default',
                layer_order=layer_order_for('induce_override'),
            )

        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy, make_no_op_trace(
                layer='induce_override', rule_id='default',
                layer_order=layer_order_for('induce_override'),
                reason_code='manager_unavailable',
            )

        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        # Reuse value_override's stat selection so both layers see the
        # same aggressor when both gates evaluate the same decision.
        spots = self._build_opponent_spots(game_state, manager)
        stats, primary_spot, _ambiguous = (
            self._select_exploitation_stats_from_spots(spots, game_state)
        )

        decision_context = self._build_decision_context(
            game_state, player_idx,
            primary_aggressor_spot=primary_spot,
        )

        effective_stack_bb = self._compute_effective_stack_bb(
            game_state, player_idx,
        )

        return apply_induce_override(
            strategy,
            stats=stats,
            hand_strength=hand_strength,
            nut_status=node.nut_status,
            street=node.street,
            position=node.position,
            danger_flag_count=len(node.danger_flags),
            effective_stack_bb=effective_stack_bb,
            active_opponent_count=active_opponent_count,
            decision_context=decision_context,
            adaptation_bias=anchors.adaptation_bias,
            tilt_factor=tilt_factor,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )

    def _apply_value_override(
        self, strategy, game_state, player_idx, valid_actions,
        anchors, emotional_state, hand_strength,
        prior_layer_fired: bool = False,
    ) -> Tuple['StrategyProfile', InterventionTrace]:
        """Phase 6.5: strong-hand value override.

        Replaces the strategy distribution (not nudges it) when hero has
        a top-tier hand against a detected hyper-aggressive opponent.
        Bypasses offset-based shaping which can't shift probability mass
        far enough for these high-conviction spots.

        Same gating as exploitation: no-ops when manager not attached,
        anchors None, opponent not aggressive, hand not strong enough,
        or psychology gates suppress.

        Phase 7.6 (Step 2): returns `(strategy, trace)`. Each early-out
        path emits a `fired=False` trace with a distinct `reason_code`
        so attribution analysis can distinguish "manager not attached"
        (cold start) from "gate rejected" (opponent not aggressive).

        Phase 7.6 (Step 5): when the rule is ablation-disabled, this
        method short-circuits BEFORE the manager check so the trace
        reports `disabled_by_ablation` (not `manager_unavailable`).
        """
        # Default for the Phase-8 tally — set unconditionally so the
        # postflop caller never reads a stale flag from a prior decision.
        self._last_value_override_fired = False

        # Phase A induce_override: defer when induce already replaced
        # the strategy this decision. Without this, value_override
        # would overwrite induce's 100%-call distribution back to
        # 50/50 call/raise and the trap mechanic is lost.
        if prior_layer_fired:
            return strategy, make_no_op_trace(
                layer='strong_hand_override', rule_id='default',
                layer_order=layer_order_for('strong_hand_override'),
                reason_code='deferred_to_induce_override',
            )

        # Phase 7.6 Step 5: ablation short-circuit.
        from .strategy.intervention_trace import is_rule_disabled, make_disabled_trace
        if is_rule_disabled(getattr(self, "disable_rules", frozenset()), 'strong_hand_override', 'default'):
            return strategy, make_disabled_trace(
                layer='strong_hand_override', rule_id='default',
                layer_order=layer_order_for('strong_hand_override'),
            )

        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy, make_no_op_trace(
                layer='strong_hand_override',
                rule_id='default',
                layer_order=layer_order_for('strong_hand_override'),
                reason_code='manager_unavailable',
            )

        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        # Phase 6.7a: route through spots so value override sees the same
        # aggressor selection as exploitation. Behavior is identical to
        # the legacy path in unambiguous cases.
        spots = self._build_opponent_spots(game_state, manager)
        stats, primary_spot, _ambiguous = (
            self._select_exploitation_stats_from_spots(spots, game_state)
        )

        decision_context = self._build_decision_context(
            game_state, player_idx,
            primary_aggressor_spot=primary_spot,
        )

        should_fire = should_apply_value_override(
            stats=stats,
            hand_strength=hand_strength,
            decision_context=decision_context,
            adaptation_bias=anchors.adaptation_bias,
            tilt_factor=tilt_factor,
        )

        self._tally_value_override_event(stats, hand_strength, should_fire)

        # Stash for the Phase-8 tally — distinguishes
        # value_vs_station_fired (offsets contributed AND survived) from
        # value_vs_station_superseded_by_override (offsets emitted but
        # replaced by this override).
        self._last_value_override_fired = bool(should_fire)

        if not should_fire:
            return strategy, make_no_op_trace(
                layer='strong_hand_override',
                rule_id='default',
                layer_order=layer_order_for('strong_hand_override'),
                reason_code='gate_rejected',
            )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"value_override fired hand={hand_strength}"
            )

        return compute_value_override_strategy(
            strategy=strategy,
            decision_context=decision_context,
            hand_strength=hand_strength,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )

    def _classify_preflop_hand_strength(self, canonical_hand, anchors=None):
        """'strong' if hand in archetype-scaled override range, else 'not_strong'.

        Phase 6.5 v3: looseness-scaled with TIGHTER cap for very-loose
        heroes (Maniac). The full validation history:
          - v1 (cap=25% for Maniac): LAG +56 bb/100, Maniac -179 bb/100
          - v2 (fixed 15% for all):  LAG -73 bb/100, Maniac -129 bb/100
          - v3 (cap=15% for Maniac): keeps LAG benefit; tightens Maniac
            to avoid 22/A8o/K9o coinflips that hurt its raise-or-fold style.

        The intuition: LAGs benefit from a wider override because hands
        like 88 / AJo are profitable calls vs maniac shoves AND already
        in LAG's natural value range. Maniacs DON'T benefit on those same
        hands because their aggressive style produces +EV via raise-or-
        fold rather than coinflip-calls — override changes that.
        """
        if not canonical_hand:
            return HandStrengthClass.NOT_STRONG.value
        looseness = getattr(anchors, 'baseline_looseness', 0.4) if anchors else 0.4
        # Boundaries use <= on the upper bound so archetypes configured
        # exactly at the threshold (LAG looseness=0.70) land in the
        # intended band rather than slipping into the next one.
        if looseness < 0.30:
            threshold = 0.10   # Nit / Rock
        elif looseness < 0.50:
            threshold = 0.15   # TAG (Calling Station also lands here)
        elif looseness <= 0.70:
            threshold = 0.20   # LAG (0.70) — top 20% includes 88/AJo
        else:
            threshold = 0.15   # Maniac (0.85+) — tightened to avoid coinflips
        if is_hand_in_range(canonical_hand, threshold):
            return HandStrengthClass.STRONG.value
        return HandStrengthClass.NOT_STRONG.value

    def _compute_effective_stack_bb(self, game_state, player_idx):
        """Effective stack in big blinds — delegates to `stack_utils`."""
        return effective_stack_bb(game_state, game_state.players[player_idx])

    def _classify_postflop_hand_strength(self, node):
        """Map PostflopNode → simplified hand class string ('nuts',
        'strong_made', 'medium_made', etc.). Reuses the same classifier
        used by the river bluff guardrail.
        """
        return simplify_hand_class(node.made_tier, node.draw_modifier)

    def _apply_bluff_catch_override(
        self, strategy, game_state, player_idx, valid_actions,
        anchors, emotional_state, hand_strength,
    ) -> Tuple['StrategyProfile', InterventionTrace]:
        """Phase 7.5 Item 1: bluff-catch override for marginal hands
        vs confirmed extreme aggressors.

        Mutually exclusive with the strong-hand value override (the two
        trigger classes are disjoint — see BLUFF_CATCH_TRIGGER_CLASSES vs
        _OVERRIDE_TRIGGER_CLASSES). When this fires, it replaces the
        strategy with a pot-odds-conditional {call, fold} distribution
        (dampened by board texture / street / paired-board flag) and
        clamps the L1 shift to the active EXTREME tier envelope.

        Phase 7.6 (Step 1): returns `(strategy, trace)`. Every code path
        emits a trace — no-op early-outs each get a `fired=False` trace
        with a distinct `reason_code` so attribution analysis can see
        "this rule wasn't on the path" vs "this rule was evaluated but
        gated out."

        Early-out paths:
          - manager not attached or anchors None
          - hand_strength outside bluff-catch trigger classes (skip
            without expensive spot/stats build)
          - rule is ablation-disabled (Step 5)
        """
        # Phase 7.6 Step 5: ablation short-circuit before any other
        # gating, so the trace reports `disabled_by_ablation`.
        from .strategy.intervention_trace import is_rule_disabled, make_disabled_trace
        if is_rule_disabled(getattr(self, "disable_rules", frozenset()), 'bluff_catch_override', 'default'):
            return strategy, make_disabled_trace(
                layer='bluff_catch_override', rule_id='default',
                layer_order=layer_order_for('bluff_catch_override'),
            )

        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy, make_no_op_trace(
                layer='bluff_catch_override',
                rule_id='default',
                layer_order=layer_order_for('bluff_catch_override'),
                reason_code='manager_unavailable',
            )

        # Cheap gate: skip the spot/stats build entirely when the hand
        # class doesn't trigger bluff-catch. Avoids work on the bulk of
        # postflop decisions (strong / not_strong / weak_draw / etc.).
        if hand_strength not in BLUFF_CATCH_TRIGGER_CLASSES:
            return strategy, make_no_op_trace(
                layer='bluff_catch_override',
                rule_id='default',
                layer_order=layer_order_for('bluff_catch_override'),
                reason_code='hand_class_not_eligible',
            )

        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        spots = self._build_opponent_spots(game_state, manager)
        stats, primary_spot, _ambiguous = (
            self._select_exploitation_stats_from_spots(spots, game_state)
        )

        decision_context = self._build_decision_context(
            game_state, player_idx,
            primary_aggressor_spot=primary_spot,
        )

        # Re-compute clamp (with recent_stats from the primary aggressor's
        # sliding window) to determine if EXTREME tier is active.
        # _compute_clamp was added in Item 2c.
        clamp_value, clamp_tier, _winning_axis = self._compute_clamp(
            stats, manager, primary_spot,
        )

        should_fire = should_apply_bluff_catch_override(
            spots=spots,
            hand_strength=hand_strength,
            decision_context=decision_context,
            adaptation_bias=anchors.adaptation_bias,
            tilt_factor=tilt_factor,
            clamp_tier=clamp_tier,
            aggressor_spot=primary_spot,
        )

        self._tally_bluff_catch_event(hand_strength, should_fire)

        if not should_fire:
            return strategy, make_no_op_trace(
                layer='bluff_catch_override',
                rule_id='default',
                layer_order=layer_order_for('bluff_catch_override'),
                reason_code='gate_rejected',
            )

        override, trace = compute_bluff_catch_strategy(
            strategy=strategy,
            decision_context=decision_context,
            hand_strength=hand_strength,
            max_total_shift=clamp_value,
            legal_actions=valid_actions,
            tier_label=clamp_tier.value.lower() if hasattr(clamp_tier, 'value')
                else str(clamp_tier).lower(),
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"BLUFF-CATCH override {hand_strength} vs "
                f"{primary_spot.name if primary_spot else 'aggregate'} "
                f"@ bet_ratio={decision_context.bet_size_pot_ratio:.2f} "
                f"texture={decision_context.board_texture} "
                f"street={decision_context.street} → "
                f"{dict(override.action_probabilities)}"
            )

        return override, trace

    def _tally_playstyle_rule_event(self):
        """Diagnostic counters for the playstyle-gated rule families
        (value_vs_station, steal_pressure).

        Reads stashed state set by `_apply_exploitation` and the
        `_last_value_override_fired` flag set by `_apply_value_override`.
        Must be called AFTER `_apply_value_override` returns so the
        fired-vs-superseded distinction is correct.

        Counters land under `manager._exploitation_counters` alongside
        the existing diagnostic counters. Per-archetype keys so a 6-max
        sim with mixed archetypes can answer "did the rule fire for
        TAG specifically" without summing across the whole table.

        Identities that hold by construction:
          eligible = enabled_eligible + diagnostic_only
          For value_vs_station:
              enabled_eligible = fired
                               + superseded_by_override
                               + blocked_by_bias_floor
          For steal_pressure (no override interaction):
              enabled_eligible = fired + blocked_by_bias_floor

        `blocked_by_bias_floor` captures the case where the rule was
        enabled for the archetype AND would have driven non-zero
        intensity, but `compute_exploitation_offsets` bailed before
        the Phase 8 branches because `effective_bias = adaptation_bias
        × tilt_factor <= GATING_FLOOR` (heavy tilt or very-low
        adaptation_bias). Tracked so `fired` cleanly counts decisions
        where Phase 8 actually contributed offsets.
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return

        archetype = getattr(self, '_last_exploitation_archetype', None)
        if archetype is None:
            # _apply_exploitation never ran (early-out path), nothing
            # to tally — also means no override fired this decision
            # so the post-override reset is a no-op.
            return

        if not hasattr(manager, '_exploitation_counters'):
            from collections import Counter
            manager._exploitation_counters = Counter()
        c = manager._exploitation_counters

        vvs_raw = getattr(self, '_last_value_vs_station_intensity_raw', 0.0)
        vvs_used = getattr(self, '_last_value_vs_station_intensity_used', 0.0)
        steal_raw = getattr(self, '_last_steal_pressure_intensity_raw', 0.0)
        steal_used = getattr(self, '_last_steal_pressure_intensity_used', 0.0)
        override_fired = getattr(self, '_last_value_override_fired', False)
        will_emit = getattr(self, '_last_phase_8_will_emit', False)

        # value_vs_station
        if vvs_raw > 0.0:
            c[f'value_vs_station_eligible_{archetype}'] += 1
            if vvs_used > 0.0:
                c[f'value_vs_station_enabled_eligible_{archetype}'] += 1
                if not will_emit:
                    c[f'value_vs_station_blocked_by_bias_floor_{archetype}'] += 1
                elif override_fired:
                    c[f'value_vs_station_superseded_by_override_{archetype}'] += 1
                else:
                    c[f'value_vs_station_fired_{archetype}'] += 1
            else:
                c[f'value_vs_station_diagnostic_only_{archetype}'] += 1

        # steal_pressure (no override interaction — preflop open spot
        # and the override path requires facing aggression)
        if steal_raw > 0.0:
            c[f'steal_pressure_eligible_{archetype}'] += 1
            if steal_used > 0.0:
                c[f'steal_pressure_enabled_eligible_{archetype}'] += 1
                if will_emit:
                    c[f'steal_pressure_fired_{archetype}'] += 1
                else:
                    c[f'steal_pressure_blocked_by_bias_floor_{archetype}'] += 1
            else:
                c[f'steal_pressure_diagnostic_only_{archetype}'] += 1

        # Reset per-decision stash so the next decision starts clean.
        # Without this, an early-out _apply_exploitation could leave
        # stale intensities visible to the next tally call.
        self._last_value_vs_station_intensity_raw = 0.0
        self._last_value_vs_station_intensity_used = 0.0
        self._last_steal_pressure_intensity_raw = 0.0
        self._last_steal_pressure_intensity_used = 0.0
        self._last_phase_8_will_emit = False
        self._last_exploitation_archetype = None
        self._last_value_override_fired = False

    def _tally_bluff_catch_event(self, hand_strength, fired):
        """Per-decision diagnostic counters for bluff-catch."""
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return
        if not hasattr(manager, '_exploitation_counters'):
            from collections import Counter
            manager._exploitation_counters = Counter()
        c = manager._exploitation_counters
        if hand_strength in BLUFF_CATCH_TRIGGER_CLASSES:
            c['bluff_catch_eligible'] += 1
        if fired:
            c['bluff_catch_fired'] += 1

    def _tally_value_override_event(self, stats, hand_strength, fired):
        """Diagnostic counters for value override (parallel to exploitation tally).

        Tracked keys (under manager._exploitation_counters for unified output):
          value_override_eligible_strong   — strong hand observed
          value_override_eligible_aggro    — aggressor detected
          value_override_fired             — override actually replaced strategy
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return
        if not hasattr(manager, '_exploitation_counters'):
            from collections import Counter
            manager._exploitation_counters = Counter()
        c = manager._exploitation_counters
        is_strong = hand_strength in {
            HandStrengthClass.NUTS.value,
            HandStrengthClass.STRONG_MADE.value,
            HandStrengthClass.STRONG.value,
        }
        if is_strong:
            c['value_override_eligible_strong'] += 1
        if classify_opponent_archetype(stats) == 'hyper_aggressive':
            c['value_override_eligible_aggro'] += 1
        if fired:
            c['value_override_fired'] += 1

    def _tally_exploitation_event(
        self, stats, offsets, decision_context,
        spots=None, ambiguous_aggressor=False,
        multiway_cbet_intensity: float = 0.0,
    ):
        """Increment diagnostic counters for this decision.

        Counters live on opponent_model_manager (persists across hands)
        rather than the controller (rebuilt per hand in sims).

        Tracked keys:
          decisions             — total decisions that reached this step
          cold_start            — gated off (hands_observed below min)
          detected_<pattern>    — pattern was detected (regardless of firing)
          fired                 — offsets came back non-empty
          detected_but_no_fire  — patterns detected but rule didn't fire
                                  (e.g. tight_nit detected outside open spot,
                                   or gated by adaptation_bias × tilt floor)
          no_pattern_matched    — past cold-start, no pattern matched stats

        Phase 6.6 adds c-bet-specific counters:
          flop_as_preflop_aggressor_spots — hero reached a potential
                                            c-bet spot (regardless of
                                            opponent stats)
          heads_up_cbet_spots             — the potential c-bet spot was
                                            heads-up
          fired_high_fold_to_cbet         — the c-bet rule contributed
                                            non-zero offsets

        Phase 6.7a adds spot-aware counters:
          spot_built_decisions            — any decision where spots
                                            were constructed
          selected_aggressor_decisions    — select_primary_aggressor
                                            returned a non-None spot
          ambiguous_aggressor_decisions   — facing a bet, multiple tied
                                            spots, no aggressor flag,
                                            no recent_aggressor_name —
                                            fell back to
                                            aggregate_from_spots
          multiway_cbet_opportunity_logged
                                          — multiway flop spot where
                                            hero is preflop aggressor
                                            AND opponent stats would
                                            trigger high_fold_to_cbet
                                            in 6.7b. Diagnostic only;
                                            6.7a does not act on it.
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return
        if not hasattr(manager, '_exploitation_counters'):
            from collections import Counter
            manager._exploitation_counters = Counter()
        c = manager._exploitation_counters
        c['decisions'] += 1

        # Phase 6.7a spot-aware counters. spots is provided when the
        # caller goes through the spot-aware path; legacy callers omit it.
        if spots is not None:
            c['spot_built_decisions'] += 1
            if decision_context.facing_aggressor_name is not None:
                c['selected_aggressor_decisions'] += 1
            if ambiguous_aggressor:
                c['ambiguous_aggressor_decisions'] += 1

            # Phase 6.7a/6.7b: multiway flop c-bet diagnostic. The
            # opportunity_logged counter MUST mirror the same gates the
            # actual rule uses (compute_multiway_cbet_intensity) so the
            # logged count is the would-have-fired count, not a looser
            # superset. That means: all active opponents have fold_to_cbet
            # > 0.60, cbet_faced_count >= 5, none is all-in.
            if (
                decision_context.is_flop_as_preflop_aggressor
                and decision_context.active_opponent_count > 1
            ):
                active = [s for s in spots if s.is_active]
                if (
                    active
                    and not any(s.is_all_in for s in active)
                    and all(
                        s.stats.fold_to_cbet > 0.60
                        and s.stats.cbet_faced_count >= 5
                        for s in active
                    )
                ):
                    c['multiway_cbet_opportunity_logged'] += 1
                    # Phase 6.7b Part A: separate counter for when the
                    # rule actually contributed offsets, so we can
                    # distinguish "stats qualify" from "rule fired".
                    # multiway_cbet_intensity == 0 here only when the
                    # cold-start / adaptation_bias gate blocked it.
                    if multiway_cbet_intensity > 0.0 and offsets:
                        cbet_fired = any(
                            a.startswith('bet_') or a == 'check'
                            for a in offsets
                        )
                        if cbet_fired:
                            c['fired_multiway_cbet'] += 1

        # Phase 6.6 c-bet spot counters track DECISION CONTEXT availability,
        # not just whether stats triggered a fire. Useful to confirm the
        # gating math (is_flop_as_preflop_aggressor + HU constraint) is
        # actually producing spots before debugging firing rate.
        if decision_context.is_flop_as_preflop_aggressor:
            c['flop_as_preflop_aggressor_spots'] += 1
            if decision_context.active_opponent_count == 1:
                c['heads_up_cbet_spots'] += 1

        # Cold-start gating is internal to compute_exploitation_offsets;
        # we mirror its checks here for diagnostic visibility.
        if stats.hands_observed < 15:
            c['cold_start'] += 1
            # Plan §6: surface cold_start as a distinct archetype value
            # on the snapshot — analytics need to distinguish
            # "insufficient sample" from "past sample, no detector fired".
            # Defensive: tests may construct controllers without going
            # through __init__ (mocks); snapshot dict may not exist.
            snap = getattr(self, '_last_pipeline_snapshot', None)
            if snap is not None:
                snap['opponent_archetype'] = 'cold_start'
            return

        patterns_this_decision = classify_detected_patterns(stats)
        for pattern in patterns_this_decision:
            c[f'detected_{pattern}'] += 1

        # §1.5a: per-archetype counter, in addition to the per-pattern
        # `detected_<pattern>` counters above. Operators can read the
        # archetype distribution ("hero saw X% pure_station / Y%
        # sticky_jammer / ...") in one place. `None` is bucketed as
        # `unmatched` so cold-start vs. genuinely-balanced opponents
        # show up rather than being silently dropped.
        #
        # Plan §6 side effect: also snapshot the archetype on the
        # pipeline so post-decision analytics (e.g. casebot_breakdown's
        # enriched fold capture) can correlate the archetype with hand
        # class / nut_status / bet bucket. The aggregate-cold-start
        # early return above means cold-start decisions get
        # 'cold_start' rather than an archetype label — distinct from
        # 'unmatched' (past min hands but no detector fired).
        archetype = classify_opponent_archetype(stats) or 'unmatched'
        c[f'archetype_classified_{archetype}'] += 1
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if snap is not None:
            snap['opponent_archetype'] = archetype

        # Phase 6.6: c-bet fire detection. The c-bet rule is the only
        # source of bet_*/check offsets when ALL of these hold:
        # is_flop_as_preflop_aggressor + active_opponent_count == 1 +
        # high_fold_to_cbet pattern detected. Other rules (hyper_passive,
        # tight_nit) can emit bet_* offsets too, so we must replicate the
        # full c-bet rule gate to avoid overcounting in multiway spots
        # where a different pattern produced the bet_* offset.
        if (
            'high_fold_to_cbet' in patterns_this_decision
            and offsets
            and decision_context.is_flop_as_preflop_aggressor
            and decision_context.active_opponent_count == 1
        ):
            cbet_fired = any(
                a.startswith('bet_') or a == 'check' for a in offsets
            )
            if cbet_fired:
                c['fired_high_fold_to_cbet'] += 1

        if offsets:
            c['fired'] += 1
        elif patterns_this_decision:
            # Detected but didn't fire — likely tight_nit-only in a non-open spot,
            # or gated by the (bias × tilt) floor.
            c['detected_but_no_fire'] += 1
        else:
            c['no_pattern_matched'] += 1

    def _build_opponent_spots(self, game_state, manager) -> List[OpponentSpot]:
        """Build one OpponentSpot per non-hero player at decision time.

        Phase 6.7a infrastructure. Folded players are excluded from the
        active set via is_active=False (kept in the list for diagnostic
        completeness but filtered by aggregate_from_spots and
        select_primary_aggressor). All other fields come from the
        game state plus the hero's existing opponent model entries.

        is_aggressor reflects MemoryManager-tracked accepted-action
        aggression for the current street (recent_aggressor_name) or the
        preflop aggressor when current street is PRE_FLOP. Never inferred
        from equal bet amounts.
        """
        hero_name = self.player_name
        phase = self.state_machine.current_phase
        phase_name = phase.name if phase is not None else None

        if phase_name == 'PRE_FLOP':
            live_aggressor = self._last_preflop_aggressor()
        else:
            mm = getattr(self, 'memory_manager', None)
            if mm is not None:
                live_aggressor = getattr(mm, 'recent_aggressor_name', None)
            else:
                live_aggressor = getattr(self, '_sim_recent_aggressor', None)

        # Hero position relative to action — used for has_position_on_hero.
        hero_idx = None
        for i, p in enumerate(game_state.players):
            if p.name == hero_name:
                hero_idx = i
                break

        # Blind seats — preserved across streets so postflop callers
        # can still see who started the hand as SB / BB.
        sb_idx = getattr(game_state, 'small_blind_idx', None)
        bb_idx = getattr(game_state, 'big_blind_idx', None)

        spots: List[OpponentSpot] = []
        for i, p in enumerate(game_state.players):
            if p.name == hero_name:
                continue

            is_folded = bool(getattr(p, 'is_folded', False))
            is_active = not is_folded
            stack = int(getattr(p, 'stack', 0) or 0)
            bet = int(getattr(p, 'bet', 0) or 0)
            total = getattr(p, 'total_bet', None)
            committed_hand = int(total if total is not None else bet)
            is_all_in = is_active and stack <= 0

            # can_act_behind: opponent is still alive AND has not yet
            # acted on the current betting round. Player.has_acted is
            # reset by the state machine whenever an accepted raise
            # reopens the action, so this naturally captures BB option
            # and re-opens after a 3-bet without seat-order traversal.
            has_acted = bool(getattr(p, 'has_acted', False))
            can_act_behind = is_active and not is_all_in and not has_acted

            is_blind = (
                (sb_idx is not None and i == sb_idx)
                or (bb_idx is not None and i == bb_idx)
            )

            # Pull stats from existing opponent model if present. Use
            # the non-creating accessor — spot construction runs at every
            # decision for every non-hero player, and using get_model
            # would silently lazy-create empty models, polluting the
            # manager dict across a long run. Tests stub get_model_if_
            # exists; production reads the real dict.
            stats = AggregatedOpponentStats()
            if manager is not None:
                model = None
                try:
                    accessor = getattr(manager, 'get_model_if_exists', None)
                    if accessor is not None:
                        model = accessor(hero_name, p.name)
                except Exception:
                    model = None
                if model is not None:
                    t = getattr(model, 'tendencies', None)
                    try:
                        has_obs = t is not None and t.hands_observed > 0
                    except (TypeError, AttributeError):
                        has_obs = False
                    if has_obs:
                        stats = AggregatedOpponentStats(
                            hands_observed=t.hands_observed,
                            vpip=t.vpip,
                            pfr=t.pfr,
                            aggression_factor=t.aggression_factor,
                            all_in_frequency=t.all_in_frequency,
                            fold_to_cbet=t.fold_to_cbet,
                            cbet_faced_count=t._cbet_faced_count,
                            # Phase 8.1a c-bet attempt fields. getattr-with-
                            # default keeps SimpleNamespace mocks happy.
                            cbet_attempt_rate=getattr(
                                t, 'cbet_attempt_rate', 0.5,
                            ),
                            postflop_seen_as_pfr_count=getattr(
                                t, '_postflop_seen_as_pfr_count', 0,
                            ),
                            # Phase B Item 1 barrel fields.
                            barrel_frequency=getattr(
                                t, 'barrel_frequency', 0.5,
                            ),
                            barrel_opportunities=getattr(
                                t, '_barrel_opportunity_count', 0,
                            ),
                            third_barrel_frequency=getattr(
                                t, 'third_barrel_frequency', 0.5,
                            ),
                            third_barrel_opportunities=getattr(
                                t, '_third_barrel_opportunity_count', 0,
                            ),
                            # Phase 7.5 Step 0 fields — populated for
                            # diagnostic visibility. Item 2 consumes them
                            # for tier classification.
                            aggression_factor_postflop=t.aggression_factor_postflop,
                            all_in_per_facing_bet=t.all_in_per_facing_bet,
                            facing_bet_opportunities=t._facing_bet_opportunities,
                            postflop_jam_open_rate=t.postflop_jam_open_rate,
                            postflop_open_opportunities=t._postflop_open_opportunities,
                            # Opportunity-normalized preflop fields.
                            # getattr-with-default so SimpleNamespace test
                            # mocks built before this field landed still
                            # work (they fall back to neutral prior / 0).
                            pfr_per_open_opportunity=getattr(
                                t, 'pfr_per_open_opportunity', 0.5,
                            ),
                            vpip_per_voluntary_opportunity=getattr(
                                t, 'vpip_per_voluntary_opportunity', 0.5,
                            ),
                            preflop_open_opportunities=getattr(
                                t, '_preflop_open_opportunities', 0,
                            ),
                            preflop_voluntary_opportunities=getattr(
                                t, '_preflop_voluntary_opportunities', 0,
                            ),
                            # Polarization Phase A equity-at-action fields.
                            # getattr-with-default for SimpleNamespace
                            # tests predating the field.
                            equity_when_betting_postflop=getattr(
                                t, 'equity_when_betting_postflop', 0.5,
                            ),
                            equity_when_raising_postflop=getattr(
                                t, 'equity_when_raising_postflop', 0.5,
                            ),
                            equity_when_calling_postflop=getattr(
                                t, 'equity_when_calling_postflop', 0.5,
                            ),
                            _equity_betting_count=getattr(
                                t, '_equity_betting_count', 0,
                            ),
                            _equity_raising_count=getattr(
                                t, '_equity_raising_count', 0,
                            ),
                            _equity_calling_count=getattr(
                                t, '_equity_calling_count', 0,
                            ),
                        )

            spots.append(OpponentSpot(
                name=p.name,
                stats=stats,
                is_active=is_active,
                is_aggressor=(is_active and p.name == live_aggressor),
                is_all_in=is_all_in,
                current_bet=bet,
                stack=stack,
                committed_this_street=bet,
                committed_this_hand=committed_hand,
                can_act_behind=can_act_behind,
                has_position_on_hero=(hero_idx is not None and i > hero_idx),
                is_blind=is_blind,
            ))
        return spots

    def _select_exploitation_stats(
        self, game_state, manager, hero_name,
        active_opponents, money_committed,
    ):
        """Legacy stats selector — preserved for tests / callers not yet on spots.

        Phase 6.7a routes both _apply_exploitation and _apply_value_override
        through _select_exploitation_stats_from_spots below. This method
        stays so existing unit tests that exercise the per-aggressor /
        aggregate path keep working, and to provide a behavior-identical
        fallback if a caller can't build spots.
        """
        call_amount = getattr(game_state, 'call_amount', 0) or 0
        if call_amount > 0:
            aggressor = self._identify_recent_aggressor(game_state)
            if aggressor:
                model = manager.get_model(hero_name, aggressor)
                t = model.tendencies
                if t.hands_observed > 0:
                    return AggregatedOpponentStats(
                        hands_observed=t.hands_observed,
                        vpip=t.vpip,
                        pfr=t.pfr,
                        aggression_factor=t.aggression_factor,
                        all_in_frequency=t.all_in_frequency,
                        fold_to_cbet=t.fold_to_cbet,
                        cbet_faced_count=t._cbet_faced_count,
                        # Opportunity-normalized preflop fields preserve
                        # the legacy-path behavior of single-aggressor
                        # facing-bet selection. Postflop Phase 7.5 fields
                        # are intentionally omitted here (legacy path was
                        # already incomplete — _select_exploitation_stats_
                        # from_spots is the canonical route). getattr-
                        # with-default keeps SimpleNamespace mocks
                        # backwards compatible.
                        pfr_per_open_opportunity=getattr(
                            t, 'pfr_per_open_opportunity', 0.5,
                        ),
                        vpip_per_voluntary_opportunity=getattr(
                            t, 'vpip_per_voluntary_opportunity', 0.5,
                        ),
                        preflop_open_opportunities=getattr(
                            t, '_preflop_open_opportunities', 0,
                        ),
                        preflop_voluntary_opportunities=getattr(
                            t, '_preflop_voluntary_opportunities', 0,
                        ),
                    )
        return manager.aggregate_active_opponents(
            observer=hero_name,
            active_opponents=active_opponents,
            money_committed=money_committed,
        )

    def _select_exploitation_stats_from_spots(
        self, spots, game_state,
    ):
        """Phase 6.7a: spot-aware facing-aggression selection.

        Returns (stats, primary_spot, ambiguous) where:
          - stats: AggregatedOpponentStats driving exploitation rules.
            Comes from the selected aggressor's spot when facing a bet
            with an unambiguous primary aggressor; otherwise from
            aggregate_from_spots (60%-rule preserved).
          - primary_spot: the OpponentSpot whose stats drove the
            decision (None if aggregate fallback). Callers extract
            both the name AND derived flags (e.g. is_all_in) from this
            spot rather than re-deriving them from the table — see
            _build_decision_context for facing_all_in handling.
          - ambiguous: True when facing a bet but select_primary_aggressor
            returned None (multiple opponents tied with no flag and no
            recent_aggressor_name) — used to bump the ambiguous-aggressor
            diagnostic counter.

        Behavior parity with the legacy _select_exploitation_stats:
          - Open spots / limped pots → aggregate stats (the live highest
            bet is 0 so select_primary_aggressor won't fire).
          - Single clear aggressor at the live highest bet → that
            opponent's stats verbatim (matches per-aggressor branch).
          - Ambiguous tied-bet spots → aggregate fallback (matches
            today's None-from-_identify_recent_aggressor path).
        """
        call_amount = getattr(game_state, 'call_amount', 0) or 0
        ambiguous = False

        if call_amount > 0:
            # Compute the live highest bet on the current street among
            # non-folded non-hero opponents.
            hero_name = self.player_name
            highest = 0
            for p in game_state.players:
                if p.name == hero_name or getattr(p, 'is_folded', False):
                    continue
                bet = getattr(p, 'bet', 0) or 0
                if bet > highest:
                    highest = bet

            recent = None
            mm = getattr(self, 'memory_manager', None)
            if mm is not None:
                recent = getattr(mm, 'recent_aggressor_name', None)
            else:
                recent = getattr(self, '_sim_recent_aggressor', None)

            if highest > 0:
                primary = select_primary_aggressor(spots, highest, recent)
                if primary is not None and primary.stats.hands_observed > 0:
                    return primary.stats, primary, False
                if primary is None:
                    ambiguous = True

        return aggregate_from_spots(spots), None, ambiguous

    def _identify_recent_aggressor(self, game_state):
        """Return the single non-hero opponent with the strictly highest
        current-street bet, or None if no clear aggressor.

        "Strictly highest" matters: in a limped pot everyone has the
        same bet (one BB) and there's no aggressor. When one player has
        raised and others have just called, the raiser is the aggressor.
        """
        hero_name = self.player_name
        candidates = []
        max_bet = 0
        for p in game_state.players:
            if p.name == hero_name or getattr(p, 'is_folded', False):
                continue
            opp_bet = getattr(p, 'bet', 0) or 0
            if opp_bet > max_bet:
                max_bet = opp_bet
                candidates = [p.name]
            elif opp_bet == max_bet and opp_bet > 0:
                candidates.append(p.name)
        if max_bet == 0 or len(candidates) != 1:
            return None
        return candidates[0]

    def _last_preflop_aggressor(self) -> Optional[str]:
        """Return the last-preflop-aggressor name, if known.

        Reads from `self.memory_manager.last_preflop_aggressor` when a
        MemoryManager is attached (production path). Falls back to
        `self._sim_last_preflop_aggressor` for simulator paths that
        bypass the memory pipeline. Returns None when neither is set.
        """
        mm = getattr(self, 'memory_manager', None)
        if mm is not None:
            return getattr(mm, 'last_preflop_aggressor', None)
        return getattr(self, '_sim_last_preflop_aggressor', None)

    def _build_decision_context(
        self, game_state, player_idx,
        primary_aggressor_spot: Optional[OpponentSpot] = None,
    ):
        """Build DecisionContext from game state.

        - is_preflop: phase.name == 'PRE_FLOP'
        - facing_all_in: derived from the selected primary aggressor's
          spot when one is provided — that opponent is who hero is
          actually responding to. Falls back to "any non-folded
          opponent at the live highest bet is all-in" only when no
          primary aggressor was selected (aggregate fallback path).
          The fallback matters for ambiguous tied-bet spots; the
          primary-spot path matters for multiway spots where a deep
          aggressor and a short-stack all-in caller are tied at the
          same bet (don't route deep-stack aggression through all-in
          exploit logic just because someone else is all-in for the
          same amount).
        - facing_big_bet: call_amount > 10 BB AND call_amount > pot/2,
          AND NOT facing_all_in
        - is_flop_as_preflop_aggressor (Phase 6.6): hero on flop, was the
          last preflop aggressor, no live bet facing hero, and has a legal
          bet/raise. Gate for HU c-bet exploit.
        - active_opponent_count (Phase 6.6): non-folded non-hero opponents.
        - facing_aggressor_name (Phase 6.7a): diagnostic — name of the
          opponent select_primary_aggressor returned for this decision.
        """
        phase = self.state_machine.current_phase
        is_preflop = phase is not None and phase.name == 'PRE_FLOP'
        is_flop = phase is not None and phase.name == 'FLOP'

        big_blind = big_blind_of(game_state)
        call_amount = getattr(game_state, 'call_amount', 0) or 0

        pot = getattr(game_state, 'pot', None)
        if isinstance(pot, dict):
            pot_total = pot.get('total', 0)
        else:
            pot_total = pot or 0

        facing_all_in = False
        hero_name = self.player_name
        if call_amount > 0:
            if primary_aggressor_spot is not None:
                # Phase 6.7a fix: derive facing_all_in from the SELECTED
                # aggressor, not "any tied-at-highest active opponent
                # is all-in". In multiway with a deep bettor + a short
                # stack calling all-in for the same amount, the
                # selector correctly picks the deep aggressor — the
                # all-in caller is a side-pot artifact, not the
                # opponent whose stats drive exploitation.
                facing_all_in = primary_aggressor_spot.is_all_in
            else:
                # Aggregate fallback path: no unambiguous primary
                # aggressor was selected, so use the legacy
                # "any tied at highest is all-in" semantics. This
                # matches today's behavior for open spots and
                # ambiguous tied-bet spots.
                highest_opponent_bet = max(
                    (
                        getattr(p, 'bet', 0) or 0
                        for p in game_state.players
                        if p.name != hero_name and not getattr(p, 'is_folded', False)
                    ),
                    default=0,
                )
                for p in game_state.players:
                    if p.name == hero_name:
                        continue
                    if getattr(p, 'is_folded', False):
                        continue
                    opponent_bet = getattr(p, 'bet', 0) or 0
                    if (
                        opponent_bet == highest_opponent_bet
                        and getattr(p, 'stack', 1) <= 0
                    ):
                        facing_all_in = True
                        break

        facing_big_bet = (
            not facing_all_in
            and call_amount > 10 * big_blind
            and call_amount > pot_total / 2
        )

        active_opponent_count = sum(
            1 for p in game_state.players
            if p.name != hero_name and not getattr(p, 'is_folded', False)
        )

        # Phase 6.6 HU c-bet: hero on flop, was last preflop aggressor,
        # no live bet, has a legal bet/raise action. The HU constraint
        # (active_opponent_count == 1) is enforced inside the offset rule
        # itself, not on this flag.
        valid_actions: List[str] = []
        try:
            valid_actions = list(game_state.current_player_options or [])
        except Exception:
            valid_actions = []
        hero_has_bet_raise = (
            'raise' in valid_actions
            or 'bet' in valid_actions
            or 'all_in' in valid_actions
        )
        is_flop_as_preflop_aggressor = (
            is_flop
            and call_amount == 0
            and hero_has_bet_raise
            and self._last_preflop_aggressor() == hero_name
        )

        facing_aggressor_name = (
            primary_aggressor_spot.name
            if primary_aggressor_spot is not None else None
        )

        # Phase 7.5 Item 1c: postflop spot detail for bluff-catch.
        # This is the price-to-call ratio, not a reconstructed original bet
        # or raise size. In raise chains the controller no longer has enough
        # history to derive the aggressor's incremental raise cleanly, but
        # the call price is exactly the value the bluff-catch matrix needs.
        # Field name is kept for API compatibility with the 7.5 plan/tests.
        bet_size_pot_ratio = 0.0
        pot_before_bet_calc = 0
        if call_amount > 0:
            pot_before_bet_calc = max(pot_total - call_amount, 1)
            bet_size_pot_ratio = float(call_amount) / float(pot_before_bet_calc)

        # Plan §4: bet-size bucket + required equity. Consumed by §2's
        # defense floor (joint with hand_class / nut_status) and by
        # bet-size-aware diagnostics. Uses the same call_amount and
        # pot_before_bet inputs as bet_size_pot_ratio above so the two
        # views are consistent.
        from .strategy.bet_size_classification import classify_bet_size
        bet_class = classify_bet_size(
            call_amount=call_amount,
            pot_before_bet=pot_before_bet_calc,
            facing_all_in=facing_all_in,
        )

        # Street label normalized lowercase ('flop' / 'turn' / 'river' / '').
        street_label = ''
        if phase is not None:
            phase_name = (phase.name or '').upper()
            if phase_name in ('FLOP', 'TURN', 'RIVER'):
                street_label = phase_name.lower()

        # Board texture + paired-board signal. Derived from community
        # cards if available; otherwise blank (preflop or no cards).
        board_texture = ''
        is_paired_board = False
        community = getattr(game_state, 'community_cards', None) or []
        if community and len(community) >= 3:
            try:
                from .card_utils import card_to_string
                from .board_analyzer import (
                    classify_texture_bucket, analyze_board_texture,
                )
                card_strs = [
                    c if isinstance(c, str) else card_to_string(c)
                    for c in community
                ]
                board_texture = classify_texture_bucket(card_strs) or ''
                analysis = analyze_board_texture(card_strs) or {}
                is_paired_board = bool(analysis.get('paired', False))
            except Exception:
                # Defensive: if cards aren't in expected format, leave
                # the fields blank — bluff-catch gate will treat that as
                # safe (no danger dampening + paired flag False).
                board_texture = ''
                is_paired_board = False

        return DecisionContext(
            is_preflop=is_preflop,
            facing_all_in=facing_all_in,
            facing_big_bet=facing_big_bet,
            is_flop_as_preflop_aggressor=is_flop_as_preflop_aggressor,
            active_opponent_count=active_opponent_count,
            facing_aggressor_name=facing_aggressor_name,
            bet_size_pot_ratio=bet_size_pot_ratio,
            street=street_label,
            board_texture=board_texture,
            is_paired_board=is_paired_board,
            bet_bucket=bet_class.bucket,
            required_equity=bet_class.required_equity,
        )

    def _try_push_fold_lookup(
        self,
        canonical_hand: str,
        game_state,
        player_idx: int,
        num_seated: int,
    ) -> Optional[str]:
        """Try to resolve this preflop decision via the short-stack
        push/fold chart instead of the deep-stack table.

        Returns the abstract action ('jam', 'fold', or 'call') when the
        situation is in scope for push/fold; None when the deep-stack
        table should handle it (deep stacks, multi-way, not HU, etc.).

        v1 scope: HU only (num_seated == 2), stack <= 15 BB effective.
        Multi-way short-stack falls through to the existing short_stack.py
        heuristic which suppresses medium raises rather than enforcing
        a strict push/fold.
        """
        # HU-only for v1
        if num_seated != 2:
            return None

        # Compute effective stack in big blinds
        try:
            big_blind = game_state.current_ante or 0
            if big_blind <= 0:
                return None
            player = game_state.players[player_idx]
            hero_stack = player.stack + player.bet
            # Effective stack: smaller of hero and the single opponent
            opp_stacks = [
                p.stack + p.bet
                for i, p in enumerate(game_state.players)
                if i != player_idx and not getattr(p, 'is_folded', False)
            ]
            if not opp_stacks:
                return None
            effective_stack = min(hero_stack, max(opp_stacks))
            effective_stack_bb = effective_stack / big_blind
        except (AttributeError, ZeroDivisionError, TypeError):
            return None

        if effective_stack_bb > PUSH_FOLD_THRESHOLD_BB:
            return None

        # Determine hero position (SB or BB only for HU)
        try:
            if player_idx == game_state.small_blind_idx:
                position = 'SB'
            elif player_idx == game_state.big_blind_idx:
                position = 'BB'
            else:
                return None
        except AttributeError:
            return None

        # Is hero facing a jam? BB facing an SB all-in is the only
        # situation where the push/fold chart's bb_vs_jam scenario fires.
        facing_jam = False
        if position == 'BB':
            # Check if SB has gone all-in on this street
            sb_idx = game_state.small_blind_idx
            sb_player = game_state.players[sb_idx]
            sb_stack_remaining = getattr(sb_player, 'stack', 1)
            if sb_stack_remaining == 0 and getattr(sb_player, 'bet', 0) > big_blind:
                facing_jam = True
            else:
                # BB with no jam to face → no push/fold decision yet
                return None

        return lookup_push_fold_action(
            hand=canonical_hand,
            position=position,
            effective_stack_bb=effective_stack_bb,
            num_opponents=1,
            facing_jam=facing_jam,
        )

    def _zone_to_tilt_factor(self, emotional_state) -> float:
        """Map emotional_state.state -> 3-phase tilt_factor.

        composed -> 1.0 (full exploitation)
        overconfident/tilted -> 0.5 (slight tilt, half-strength)
        shaken/dissociated -> 0.0 (heavy tilt, no exploitation)
        """
        if emotional_state is None:
            return 1.0
        state = getattr(emotional_state, 'state', 'composed')
        if state in ('shaken', 'dissociated'):
            return 0.0
        if state in ('tilted', 'overconfident'):
            return 0.5
        return 1.0

    def _get_money_committed(self, game_state):
        """Per-opponent chips committed this hand.

        Tries player.total_bet first (preferred if available), then falls
        back to player.bet (current street only). Returns empty dict if
        neither attribute is available.
        """
        money = {}
        hero_name = self.player_name
        for p in game_state.players:
            if p.name == hero_name:
                continue
            total = getattr(p, 'total_bet', None)
            if total is None:
                total = getattr(p, 'bet', 0) or 0
            money[p.name] = float(total)
        return money

    def _apply_math_floor(
        self, strategy, game_state, player_idx: int, valid_actions: List[str],
    ) -> Tuple['StrategyProfile', InterventionTrace]:
        """Run apply_pot_odds_floor with the right context pulled from game state.

        Returns the (possibly overridden) strategy and trace. Any
        unexpected error returns the strategy unchanged with a no-op
        trace tagged `math_floor_internal_error` — the floor is a
        safety net, not a critical path.
        """
        try:
            player = game_state.players[player_idx]
            # Use shared helper so a missing current_ante falls back to a
            # sane default (50) instead of zero. With 0, stack_bb becomes
            # inf and the short-stack rule never fires — inconsistent
            # with _build_decision_context elsewhere in this class.
            big_blind = big_blind_of(game_state)
            pot_total = (
                game_state.pot.get('total', 0)
                if isinstance(getattr(game_state, 'pot', None), dict) else 0
            )
            cost_to_call = getattr(game_state, 'call_amount', 0) or 0
            override, trace = apply_pot_odds_floor(
                strategy=strategy,
                cost_to_call=cost_to_call,
                pot_total=pot_total,
                player_stack=getattr(player, 'stack', 0) or 0,
                player_bet=getattr(player, 'bet', 0) or 0,
                big_blind=big_blind,
                legal_actions=valid_actions,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            if trace.fired and self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"math_floor={trace.reason_code} -> {override.action_probabilities}"
                )
            return override, trace
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"math_floor failed safely: {e}"
            )
            return strategy, make_no_op_trace(
                layer='math_floor', rule_id='default',
                layer_order=layer_order_for('math_floor'),
                reason_code='math_floor_internal_error',
            )

    def _attach_expression(
        self, decision: Dict, game_state, player_idx: int, phase: str,
    ) -> None:
        """Populate narration fields on a committed decision AND persist
        the decision-analysis row.

        Two responsibilities — character expression (Layer 3, optional)
        and analytics persistence (always wanted). Originally these were
        coupled: persistence was gated on the LLM capture_id, which meant
        a silent turn (or a sim with `expression: false`) silently
        dropped the per-decision intervention_trace + snapshot. This
        broke analytics for ablation matrices that rely on
        trace counters.

        Now: expression runs if configured and the gate passes;
        persistence runs unconditionally with whatever capture_id the
        expression layer produced (or None if it didn't fire).
        """
        capture_id = self._run_expression_layer(
            decision, game_state, player_idx, phase,
        )
        self._persist_decision_analysis(
            decision, game_state, player_idx, capture_id=capture_id,
        )

    def _run_expression_layer(
        self, decision: Dict, game_state, player_idx: int, phase: str,
    ) -> Optional[int]:
        """Run the Layer 3 character expression (LLM narration).

        Returns the prompt capture_id when the LLM fired, or None when
        expression is disabled, fully silent, or errored. The capture_id
        is passed through to the analytics persistence step so the
        decision_analysis row can link to its narration capture.
        """
        if getattr(self, 'expression_generator', None) is None:
            return None

        try:
            from .moment_analyzer import MomentAnalyzer
            from .card_utils import card_to_string

            player = game_state.players[player_idx]
            personality_config = getattr(
                getattr(self, 'ai_player', None), 'personality_config', {}
            ) or {}

            hand_cards = (
                [card_to_string(c) for c in player.hand] if player.hand else []
            )
            community_cards = (
                [card_to_string(c) for c in game_state.community_cards]
                if game_state.community_cards else []
            )

            try:
                moment = MomentAnalyzer.analyze(
                    game_state=game_state,
                    player=player,
                    cost_to_call=getattr(game_state, 'call_amount', 0) or 0,
                    big_blind=getattr(game_state, 'current_ante', 0) or 0,
                )
                drama_level = moment.level
                drama_tone = moment.tone
            except Exception:
                drama_level, drama_tone = 'routine', 'neutral'

            emotional = get_emotional_shift(self.psychology)
            active_count = sum(1 for p in game_state.players if not p.is_folded)

            # Richer situation context for Layer 3 narration: hand label,
            # BB-normalized stack/pot/cost, position, recent actions. All
            # best-effort — any sub-step that fails leaves the field empty
            # and the corresponding YAML section is skipped.
            extras = self._build_expression_extras(
                game_state, player, hand_cards, community_cards,
            )

            # Narration gates via the shared parent helper — identical to
            # hybrid/chaos's "when to speak" rolls. Tiered additionally
            # uses should_gesture (energy-driven) so silent characters can
            # still react physically; when BOTH are False we skip the LLM
            # call entirely since the decision already has empty defaults.
            gate = self.compute_narration_gate(game_state, drama_level=drama_level)
            should_speak = gate.should_speak
            should_gesture = gate.should_gesture
            if gate.fully_silent:
                return None

            # Phase 7.6 Step 5: build NarrationFacts from the per-decision
            # intervention trace. Best-effort — failure here logs WARN and
            # leaves narration_facts as None (LLM falls back to the
            # standard prompt template).
            narration_facts = self._build_narration_facts(phase)

            # Opponent narrative observations — surfaced so Layer 3
            # narration can riff on accumulated reads from prior hands.
            # Best-effort: any failure produces an empty list and the
            # generator's prompt template skips the corresponding block.
            opponent_observations = self._select_opponent_observations(
                game_state, player,
            )

            # Relationship-context block — shared with chaos and
            # standard via the same formatter, so narration here frames
            # rival/friendly labels identically to how those bots see
            # them in their decision prompts. Gated on the prompt_config
            # flag and graceful when no opponent_model_manager is wired.
            relationship_context = ''
            if (
                getattr(self.prompt_config, 'relationship_context', False)
                and self.opponent_model_manager is not None
            ):
                try:
                    from .memory.relationship_prompt import build_relationship_context
                    active_opponent_names = [
                        p.name for p in game_state.players
                        if not p.is_folded and p.name != player.name
                    ]
                    relationship_context = build_relationship_context(
                        observer_name=self.player_name,
                        opponents=active_opponent_names,
                        opponent_model_manager=self.opponent_model_manager,
                    )
                except Exception as e:  # noqa: BLE001 — narration is observability
                    logger.warning(
                        f"[TIERED_BOT] {self.player_name}: "
                        f"relationship_context build failed: {e}"
                    )
                    relationship_context = ''

            context = ExpressionContext(
                action_taken=decision['action'],
                raise_to=decision.get('raise_to', 0) or 0,
                hand_cards=hand_cards,
                community_cards=community_cards,
                phase=phase,
                pot_size=getattr(game_state, 'pot_total', 0) or 0,
                opponent_count=max(0, active_count - 1),
                personality_name=personality_config.get(
                    'name', self.player_name
                ),
                play_style=personality_config.get('play_style', ''),
                default_attitude=personality_config.get(
                    'default_attitude', 'neutral'
                ),
                verbal_tics=personality_config.get('verbal_tics', []) or [],
                physical_tics=personality_config.get('physical_tics', []) or [],
                drama_level=drama_level,
                drama_tone=drama_tone,
                emotional_state=emotional.state,
                emotional_severity=emotional.severity,
                position=extras['position'],
                stack_bb=extras['stack_bb'],
                pot_bb=extras['pot_bb'],
                cost_to_call_bb=extras['cost_to_call_bb'],
                hand_name=extras['hand_name'],
                hand_strength_tier=extras['hand_strength_tier'],
                short_stack=extras['short_stack'],
                pot_committed=extras['pot_committed'],
                recent_actions=extras['recent_actions'],
                recent_own_speech_beats=self.recent_own_speech_beats(),
                recent_own_action_beats=self.recent_own_action_beats(),
                callouts=self.find_callouts(
                    getattr(self, '_current_game_messages', None)
                ),
                should_speak=should_speak,
                should_gesture=should_gesture,
                narration_facts=narration_facts,
                opponent_observations=opponent_observations,
                relationship_context=relationship_context,
            )

            capture_id_holder = [None]
            narration = self.expression_generator.generate(
                context,
                call_type=getattr(self, '_expression_call_type', None),
                game_id=getattr(self, 'game_id', None),
                capture_id_holder=capture_id_holder,
            )
            for key in ('dramatic_sequence', 'addressing', 'inner_monologue', 'bluff_likelihood'):
                if key in narration:
                    decision[key] = narration[key]
            # Only overwrite hand_strategy if LLM produced one (preserves Layer 1+2 debug string otherwise)
            if narration.get('hand_strategy'):
                decision['hand_strategy'] = narration['hand_strategy']
            # Record this turn's speech beats for next turn's anti-
            # repetition prompt (action gestures filtered inside).
            self.remember_own_beats(narration.get('dramatic_sequence'))
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"expression failed safely: {e}"
            )
            return None

        return capture_id_holder[0]

    def _persist_decision_analysis(
        self, decision: Dict, game_state, player_idx: int,
        *, capture_id: Optional[int] = None,
    ) -> None:
        """Persist the per-decision intervention_trace + pipeline snapshot.

        Always called after `_attach_expression` regardless of whether
        the LLM expression layer fired. When the LLM did fire,
        `capture_id` links the analysis row to the narration capture.
        When the LLM didn't (silent turn, expression disabled, sim
        with `expression: false`), `capture_id` is None and the row is
        saved without the narration linkage — analytics still get the
        trace + snapshot, which is what they need.

        No-op when no decision_analysis repo is attached (sim path or
        test without the repo wired).
        """
        if getattr(self, '_decision_analysis_repo', None) is None:
            return
        try:
            cost_to_call = getattr(game_state, 'call_amount', 0) or 0
            player_obj = game_state.players[player_idx]
            self._analyze_decision(
                decision,
                {'call_amount': cost_to_call},
                capture_id=capture_id,
                player_bet=getattr(player_obj, 'bet', 0),
                all_players_bets=[
                    (p.bet, p.is_folded) for p in game_state.players
                ],
            )
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"decision_analysis persistence failed: {e}"
            )

    def _select_opponent_observations(
        self, game_state, player,
    ) -> List[Tuple[str, str]]:
        """Best-effort selection of narrative observations for Layer 3.

        Returns up to 2 (opponent_name, observation_text) tuples,
        weighted toward the opponent hero is facing and any nemesis.
        Empty list when the controller has no opponent_model_manager,
        no active opponents, or no stored observations.
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return []
        try:
            active_opponents = [
                p.name for p in game_state.players
                if p.name != player.name and not p.is_folded
            ]
            if not active_opponents:
                return []
            # Facing opponent: highest current bet among actives. Same
            # heuristic as AIPlayerController._infer_facing_opponent —
            # not extracted to a shared utility because the controllers
            # don't share a memory mixin and this is a 6-line guess.
            facing_opponent: Optional[str] = None
            opp_bets = [
                (p.name, getattr(p, 'bet', 0) or 0)
                for p in game_state.players
                if p.name in active_opponents
            ]
            if opp_bets:
                best_name, best_bet = max(opp_bets, key=lambda nb: nb[1])
                if best_bet > 0:
                    facing_opponent = best_name
            return manager.select_opponent_observations(
                player.name,
                active_opponents=active_opponents,
                facing_opponent=facing_opponent,
            )
        except Exception:
            return []

    def _build_expression_extras(
        self, game_state, player, hand_cards: List[str], community_cards: List[str],
    ) -> Dict[str, Any]:
        """Compute hand label, BB-normalized situation, and recent-actions
        text for the Layer 3 narration prompt.

        Each sub-step is best-effort: any failure populates the affected
        field with a safe default ('' for strings, 0.0 for floats), and the
        corresponding YAML section is skipped by ExpressionGenerator.
        """
        from .controllers import (
            evaluate_hand_strength,
            classify_preflop_hand,
            summarize_messages,
        )

        big_blind = getattr(game_state, 'current_ante', 0) or 0

        def _to_bb(amount: int) -> float:
            if not big_blind:
                return 0.0
            return round(amount / big_blind, 1)

        # Hand label: postflop uses eval7, preflop uses classifier
        hand_name = ''
        try:
            if community_cards:
                hand_name = evaluate_hand_strength(hand_cards, community_cards) or ''
            elif hand_cards:
                hand_name = classify_preflop_hand(hand_cards) or ''
        except Exception:
            hand_name = ''

        # Position from table_positions
        position = ''
        try:
            positions = getattr(game_state, 'table_positions', {}) or {}
            for pos, name in positions.items():
                if name == player.name:
                    position = pos
                    break
        except Exception:
            position = ''

        # BB-normalized stack/pot/cost
        try:
            stack_bb = _to_bb(player.stack)
        except Exception:
            stack_bb = 0.0
        try:
            pot_total = getattr(game_state, 'pot_total', 0) or 0
            pot_bb = _to_bb(pot_total)
        except Exception:
            pot_bb = 0.0
        try:
            raw_cost = max(0, game_state.highest_bet - player.bet)
            cost_to_call_bb = _to_bb(min(raw_cost, player.stack))
        except Exception:
            cost_to_call_bb = 0.0

        # Recent actions: game_messages from the flask layer is a list of
        # dicts (sender/content/action/...), not strings. Use the same
        # summarizer hybrid uses so dict messages — including chat — render
        # as readable lines with senders, actions, and quoted content.
        recent_actions = ''
        try:
            raw = getattr(self, '_current_game_messages', None)
            if raw:
                recent_actions = summarize_messages(raw, self.player_name) or ''
        except Exception:
            recent_actions = ''

        # Coarse strength tier — used for narration tone, derived from
        # the hand_name label. Postflop labels carry an explicit suffix
        # ("Two Pair - Strong"); preflop carry a category in the prefix
        # ("AKs - Suited broadway, Top 5%"). Mapped to one of
        # Monster/Strong/Marginal/Weak/Drawing/'' (unknown).
        hand_strength_tier = _coarse_strength_tier(hand_name)

        # Situational reads — borrowed from hybrid's prompt injections.
        # short_stack: classic push/fold zone. pot_committed: rough proxy
        # using cost_to_call vs remaining stack (player has invested
        # enough that folding would forfeit a large multiple of what's
        # left to call).
        short_stack = bool(stack_bb and stack_bb < 3.0)
        pot_committed = bool(
            cost_to_call_bb > 0 and stack_bb > 0
            and stack_bb < cost_to_call_bb * 3
        )

        return {
            'hand_name': hand_name,
            'position': position,
            'stack_bb': stack_bb,
            'pot_bb': pot_bb,
            'cost_to_call_bb': cost_to_call_bb,
            'recent_actions': recent_actions,
            'hand_strength_tier': hand_strength_tier,
            'short_stack': short_stack,
            'pot_committed': pot_committed,
        }

    def _postflop_fallback(self, valid_actions: List[str]) -> Dict:
        """Emergency fallback: check if possible, otherwise fold."""
        if 'check' in valid_actions:
            action = 'check'
        elif 'fold' in valid_actions:
            action = 'fold'
        elif 'call' in valid_actions:
            action = 'call'
        else:
            action = valid_actions[0] if valid_actions else 'fold'

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop_fallback={action}"
            )

        return {
            'action': action,
            'raise_to': 0,
            'dramatic_sequence': [],
            'hand_strategy': 'Postflop emergency fallback',
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }

    def _validate_action(
        self, action: str, raise_to: int, valid_actions: List[str]
    ) -> tuple:
        """Ensure the resolved action is legal, with fallback priority."""
        fallback_order = {
            'raise': ['call', 'check', 'fold'],
            'all_in': ['raise', 'call', 'check', 'fold'],
            'call': ['check', 'fold'],
            'check': ['fold'],
            'fold': ['check'],
        }

        fallbacks = fallback_order.get(action, ['fold'])
        for fb in fallbacks:
            if fb in valid_actions:
                logger.debug(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"Falling back from {action} to {fb}"
                )
                return (fb, 0)

        # Ultimate fallback
        if valid_actions:
            return (valid_actions[0], 0)
        return ('fold', 0)


class BaselineSolverBot(TieredBotController):
    """Layer-1-only reference bot for EV-ordering validation.

    System-only test entity per the tiered bot spec. Uses strategy tables,
    multiway adjustments, and the river bluff guardrail, but skips Layer 2
    (personality distortion) and Layer 3 (LLM expression). Not selectable
    in normal games — used to verify that personality deviations cost EV.
    """

    def __init__(
        self,
        player_name: str,
        strategy_table: StrategyTable,
        state_machine=None,
        llm_config=None,
        debug_logging: bool = False,
        rng_seed=None,
        hu_strategy_table: Optional[StrategyTable] = None,
        **kwargs,
    ):
        kwargs.pop('skip_personality_distortion', None)
        super().__init__(
            player_name=player_name,
            strategy_table=strategy_table,
            state_machine=state_machine,
            llm_config=llm_config,
            debug_logging=debug_logging,
            rng_seed=rng_seed,
            skip_personality_distortion=True,
            hu_strategy_table=hu_strategy_table,
            **kwargs,
        )
