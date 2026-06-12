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

from .archetypes import classify_from_anchors
from .bounded_options import get_emotional_shift
from .card_utils import card_to_string
from .controllers import AIPlayerController, _get_canonical_hand
from .hand_tiers import is_hand_in_range
from .stack_utils import big_blind_of, effective_stack_bb
from .strategy.action_mapper import resolve_postflop_sizing, resolve_preflop_sizing
from .strategy.deviation_profiles import DeviationProfile, select_deviation_profile
from .strategy.exploitation import (
    DEFAULT_MAX_TOTAL_SHIFT,
    GATING_FLOOR,
    MIN_HANDS_DEFAULT,
    AggregatedOpponentStats,
    DecisionContext,
    OpponentSpot,
    _determine_clamp,
    aggregate_from_spots,
    apply_exploitation_offsets,
    classify_detected_patterns,
    classify_opponent_archetype,
    compute_exploitation_offsets_with_traces,
    compute_multiway_cbet_intensity,
    compute_value_vs_station_intensity,
    is_value_vs_station_enabled,
    reshove_fold_equity_ok,
    select_primary_aggressor,
)
from .strategy.expression_context import ExpressionContext
from .strategy.expression_generator import ExpressionGenerator
from .strategy.hand_classification import simplify_hand_class
from .strategy.intervention_trace import (
    InterventionTrace,
    layer_order_for,
    make_no_op_trace,
)
from .strategy.math_floor import apply_pot_odds_floor
from .strategy.multiway import apply_multiway_adjustment
from .strategy.personality_modifier import apply_river_bluff_guardrail, modify_strategy
from .strategy.postflop_classifier import build_postflop_node
from .strategy.postflop_commit import apply_postflop_commit
from .strategy.preflop_classifier import build_preflop_node, get_6max_position
from .strategy.push_fold import (
    PUSH_FOLD_THRESHOLD_BB,
    lookup_push_fold_action,
    lookup_push_fold_action_6max,
    reshove_action_6max,
)
from .strategy.short_stack import apply_short_stack_heuristics
from .strategy.sizing_tendencies import (
    SizeContext,
    SizingPersonality,
    parse_sizing_tendencies,
    resolve_size_multiplier,
    sample_sizing_personality,
)
from .strategy.strategy_profile import StrategyProfile
from .strategy.strategy_table import StrategyTable, nearest_depth_bucket
from .strategy.tilt_conditioning import apply_tilt_conditioning
from .strategy.value_override import (
    BLUFF_CATCH_TRIGGER_CLASSES,
    HandStrengthClass,
    compute_bluff_catch_strategy,
    compute_sizing_defense_strategy,
    compute_value_override_strategy,
    should_apply_bluff_catch_override,
    should_apply_value_override,
)

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
    if (
        'very strong' in s
        or 'full house' in s
        or 'quads' in s
        or 'four of a kind' in s
        or 'straight flush' in s
    ):
        return 'Monster'
    if (
        'strong' in s
        or 'flush' in s
        or 'straight' in s
        or 'trip' in s
        or 'three of a kind' in s
        or 'two pair' in s
    ):
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
    if (
        'top 35%' in s
        or 'top 45%' in s
        or 'offsuit broadway' in s
        or 'suited ace' in s
        or 'low pocket pair' in s
    ):
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
    for layer, rule_id in _EXPLOITATION_RULE_ORDER:
        if is_rule_disabled(disable_rules, layer, rule_id):
            out.append(
                make_disabled_trace(
                    layer=layer,
                    rule_id=rule_id,
                    layer_order=layer_order_for(layer),
                )
            )
        else:
            out.append(
                make_no_op_trace(
                    layer=layer,
                    rule_id=rule_id,
                    layer_order=layer_order_for(layer),
                    reason_code=reason_code,
                )
            )
    return out


# Facing-an-all-in equity veto (preflop). Fixed seed + iteration count so the
# Monte-Carlo is deterministic per (hand, opponent-count) and uses a LOCAL RNG
# — it never touches the controller's own `self.rng` stream, preserving the
# byte-identical-sim invariant. 600 iters preflop is ~1-2 ms and stable to
# ~±2% equity, ample for a fold/call pot-odds bar.
_ALLIN_VETO_EQUITY_SEED = 20260610
_ALLIN_VETO_EQUITY_ITERS = 600

# Stop-bluffing-vs-station hard override (see _maybe_stop_bluff_override).
# Min station-read intensity before the override hard-sets the give-up line.
# compute_value_vs_station_intensity returns ~1.0 for a clear station; 0.5
# keeps the override off marginal/ambiguous reads while firing on real ones.
STOP_BLUFF_MIN_INTENSITY = 0.5


def _preflop_allin_equity(hole_cards: List[str], num_opponents: int) -> Optional[float]:
    """Hero's preflop all-in equity vs `num_opponents` random hands.

    Cheap eval7 Monte-Carlo with a fixed-seed local RNG (see constants above).
    Returns a win probability in [0, 1], or None when eval7 is unavailable or
    the cards don't parse — the caller then keeps the normal chart path rather
    than vetoing on a bad number.

    Equity vs *random* hands (not the villain's actual all-in range) is a
    deliberately hero-generous estimate: a real 4-bet-shove range is far
    stronger, so vs-random under-folds rather than over-folds. That's the safe
    bias for a guardrail whose only job is to stop trash JAMS — calling a
    marginal hand for the right pot odds is fine; shoving 100bb of 47o is not.
    """
    try:
        import random as _random

        import eval7

        from .card_utils import normalize_card_string

        hero = [eval7.Card(normalize_card_string(c)) for c in hole_cards]
        if len(hero) != 2:
            return None
        known = set(hero)
        deck = [c for c in eval7.Deck().cards if c not in known]
        rng = _random.Random(_ALLIN_VETO_EQUITY_SEED)
        n_opp = max(1, num_opponents)

        wins = 0.0
        for _ in range(_ALLIN_VETO_EQUITY_ITERS):
            rng.shuffle(deck)
            idx = 0
            opp_hands = []
            for _ in range(n_opp):
                opp_hands.append([deck[idx], deck[idx + 1]])
                idx += 2
            board = deck[idx : idx + 5]
            hero_score = eval7.evaluate(hero + board)
            best_opp = max(eval7.evaluate(oh + board) for oh in opp_hands)
            if hero_score > best_opp:
                wins += 1.0
            elif hero_score == best_opp:
                wins += 0.5  # split — count chop equity, not a loss
        return wins / _ALLIN_VETO_EQUITY_ITERS
    except Exception:
        return None


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


# Tilt telegraph (TILT_EXCURSION_DESIGN.md §4). On ENTERING a tilt episode, with
# probability TILT_TELEGRAPH_PROB, hand the LLM the tilt cause + a loose
# suggestion to react in its own words (not a fixed line). Cause phrases are keyed
# on composure_state.pressure_source; {nemesis} is filled when known.
TILT_TELEGRAPH_PROB = 0.7
_TILT_CAUSE_PHRASES = {
    'bad_beat': "just took a brutal bad beat",
    'got_sucked_out': "just got sucked out on",
    'big_loss': "just shipped a big pot the wrong way",
    'losing_streak': "have been card-dead and losing for a while",
    'nemesis_loss': "just lost another one to {nemesis}",
    'crippled': "just got crippled down to a short stack",
    'bluff_called': "just got your bluff snapped off",
}
_TILT_CAUSE_FALLBACK = "are rattled and off your game right now"


def _tilt_erratic_enabled() -> bool:
    """Live read of TILT_ERRATIC_READS_ENABLED; False if the registry is
    unavailable (sim/test isolation) so the off-path keeps the legacy cliff."""
    try:
        from core.feature_flags import is_enabled

        return is_enabled('TILT_ERRATIC_READS_ENABLED')
    except Exception:
        return False


def _reshove_6max_enabled() -> bool:
    """Live read of PUSH_FOLD_6MAX_RESHOVE_ENABLED; False if the registry is
    unavailable so the off-path falls through (byte-identical to no reshove)."""
    try:
        from core.feature_flags import is_enabled

        return is_enabled('PUSH_FOLD_6MAX_RESHOVE_ENABLED')
    except Exception:
        return False


def _iso_over_limper_enabled() -> bool:
    """Live read of PUSH_FOLD_FIRST_IN_OVER_LIMPER_ENABLED; False if the registry
    is unavailable so the off-path falls through (the limped pot keeps going to the
    deep-stack / short_stack.py path, byte-identical to before)."""
    try:
        from core.feature_flags import is_enabled

        return is_enabled('PUSH_FOLD_FIRST_IN_OVER_LIMPER_ENABLED')
    except Exception:
        return False


# ── Bluff-aware vs_3bet exploit (per-persona `vs3bet_exploit` knob) ─────────────
# Facing a 3-bet, defending to MDF (the base chart) is unexploitable but leaves EV
# on the table vs a villain whose 3-bet range is value-heavy. This runtime layer
# reads the villain's bluff fraction β from vs_open and, when the villain
# under-bluffs (β < the balanced reference), folds more of hero's MARGINAL continue
# — the thin calls at the bottom of the range — while leaving value 4-bets and core
# flats. The base chart stays the GTO/MDF baseline; this is the per-player dial.
VS3BET_EXPLOIT_DEFAULT = 0.5  # moderate — the field default for every tiered persona
VS3BET_BLUFF_REF = 0.40  # balanced 3-bet bluff fraction; at/above this → no exploit
VS3BET_EXPLOIT_SCALE = 0.6  # maps (ref − β)·knob to a per-hand call→fold shift
VS3BET_VALUE_CLIFF = 0.50  # villain raise_3x ≥ this = value (mirrors build_vs3bet_defense)


def _resolve_vs3bet_exploit(pcfg, skill) -> float:
    """Resolve a persona's vs3bet_exploit knob. Precedence: an explicit
    ``vs3bet_exploit`` in the personality config wins; else the persona's skill
    tier grades it (shark 0.85 … rec 0.0 — it's an exploitation behaviour); else
    VS3BET_EXPLOIT_DEFAULT for an un-tiered persona."""
    if isinstance(pcfg, dict) and 'vs3bet_exploit' in pcfg:
        return float(pcfg['vs3bet_exploit'])
    if skill:
        from poker.strategy.skill_tiers import SKILL_TIERS

        if skill in SKILL_TIERS:
            return SKILL_TIERS[skill].vs3bet_exploit
    return VS3BET_EXPLOIT_DEFAULT


# Punish-limpers exploit (LIMP_EXPLOIT.md): iso-raise a weak limper. The MEASURED
# spot (a single limper folded around to the hero) is ~100% the BB checking its
# option ({check: 1.0}) — a lone limp stands alone almost only when it folds to the
# BB. So the exploit converts PASSIVE give-up mass (check, or a folded open) into an
# iso-raise; the check→raise case is the real one.
LIMP_EXPLOIT_DEFAULT = 0.5  # field default for an un-tiered persona
LIMP_GAP_MIN = 0.20  # min (vpip_per_vol − pfr_per_open) to read a habitual limper
LIMP_EXPLOIT_SCALE = 1.1  # maps knob·foldiness to a per-hand passive→raise shift (capped)
LIMP_EXPLOIT_MAX_SHIFT = 0.5  # never convert more than this much of a hand's passive mass
LIMP_ISO_RAISE_KEY = 'raise_2.5bb'  # the open/iso-raise action to inject when none present
_BROADWAY = frozenset('TJQKA')


def _is_iso_hand(hand: str) -> bool:
    """Hands worth iso-raising a weak limper with: any pair, any suited, and offsuit
    BROADWAYS (both ranks T+). Excludes offsuit junk (72o) — iso-raising junk is the
    spew this guards (LIMP_EXPLOIT.md)."""
    if len(hand) == 2:
        return True  # pair
    if len(hand) == 3 and hand[2] == 's':
        return True  # suited
    if len(hand) == 3 and hand[2] == 'o':
        return hand[0] in _BROADWAY and hand[1] in _BROADWAY  # offsuit broadway only
    return False


def _resolve_limp_exploit(pcfg, skill) -> float:
    """Resolve a persona's limp_exploit knob. Precedence: explicit
    ``limp_exploit`` in config wins; else the skill tier grades it (shark 0.85 …
    rec 0.0); else LIMP_EXPLOIT_DEFAULT for an un-tiered persona."""
    if isinstance(pcfg, dict) and 'limp_exploit' in pcfg:
        return float(pcfg['limp_exploit'])
    if skill:
        from poker.strategy.skill_tiers import SKILL_TIERS

        if skill in SKILL_TIERS:
            return SKILL_TIERS[skill].limp_exploit
    return LIMP_EXPLOIT_DEFAULT


def _limp_exploit_enabled() -> bool:
    """Live read of LIMP_EXPLOIT_ENABLED; False if the registry is unavailable so
    the off-path is byte-identical (no widening)."""
    try:
        from core.feature_flags import is_enabled

        return is_enabled('LIMP_EXPLOIT_ENABLED')
    except Exception:
        return False


def _compute_vs3bet_bluff_fraction(preflop_table, hero: str, villain: str):
    """β = bluff combos / total combos of the villain's 3-bet range, read from
    ``vs_open[villain_vs_hero].raise_3x`` (a hand is a bluff when its 3-bet weight
    is < VS3BET_VALUE_CLIFF). Returns None when the villain's vs_open node isn't
    populated (e.g. HU tables or a chart without that matchup)."""
    from .strategy.lints import _combos, canonical_hands
    from .strategy.nodes import PreflopNode

    total = bluff = 0.0
    found = False
    for h in canonical_hands():
        prof = preflop_table.lookup_preflop(
            PreflopNode(hand=h, position=villain, scenario='vs_open', opener_position=hero)
        )
        if prof is None:
            continue
        w = prof.action_probabilities.get('raise_3x', 0.0)
        if w <= 0:
            continue
        found = True
        c = _combos(h) * w
        total += c
        if w < VS3BET_VALUE_CLIFF:
            bluff += c
    if not found or total <= 0:
        return None
    return bluff / total


class TieredBotController(AIPlayerController):
    """AI player using 3-layer tiered architecture.

    Layer 1: Solver-derived baselines (strategy table lookup)
    Layer 2: Personality distortion (logit-space modification)
    Layer 3: Expression (LLM narrates - not implemented in Phase 1)
    """

    # Solver path: decisions never inject psychology.get_prompt_section(), so the
    # post-hand emotional narration is only worth generating in heads-up (where
    # the opponent panel displays it). See PsychologyPipeline._update_composure.
    USES_EMOTIONAL_NARRATION = False

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
        depth_strategy_tables: Optional[Dict[int, StrategyTable]] = None,
        archetype_preflop_tables: Optional[Dict[str, StrategyTable]] = None,
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
        # Shallow 6-max preflop charts keyed by depth bucket (e.g. {50:.., 25:..}).
        # Empty dict → no depth adjustment (the base table is used at every
        # depth, the pre-depth-aware behavior). See _select_preflop_table.
        self.depth_strategy_tables: Dict[int, StrategyTable] = depth_strategy_tables or {}
        # Width-tier preflop charts keyed by deviation-profile key (e.g.
        # {'lag': .., 'maniac': .., 'calling_station': ..}). The loose/station
        # archetypes need a wider base TABLE than distortion can reach (it can't
        # open hands the base chart folds ~100%); the tight archetypes get a
        # tighter base. Empty dict → every archetype uses the base table (the
        # pre-width-tier behavior). PREFLOP-ONLY — postflop stays on
        # self.strategy_table. See _select_preflop_table + ARCHETYPE_WIDTH_TABLE.
        self.archetype_preflop_tables: Dict[str, StrategyTable] = archetype_preflop_tables or {}
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
        # Per-personality spot-tendency override (item 3,
        # PERSONALITY_PRICING_AND_VARIETY.md): a specific character can carry its
        # own ((name, strength), ...) tendencies independent of the shared
        # archetype profile. None = inherit the archetype profile's
        # spot_tendencies. Set explicitly (sim/tests), else resolved once from
        # the personality config's 'spot_tendencies' key. `_resolved` guards the
        # one-time config read (and lets an explicit () mean "override to none").
        self._spot_tendencies_override: Optional[Tuple[Tuple[str, float], ...]] = None
        self._spot_tendencies_resolved: bool = False
        # Per-PLAYER preflop sizing personality (sizing_tendencies P1,
        # docs/plans/SIZING_TENDENCIES.md). Deterministically SAMPLED per persona
        # (persona-seeded RNG) so a character's go-to raise size is stable but
        # same-archetype players size differently — a read you EARN, not a one-shot
        # tell. None until lazily resolved on first preflop sizing (mirrors the
        # deviation_profile lazy-resolve). An explicit override (sims/tests) wins.
        # The per-personality `sizing_tendencies` config key is the P2+ override
        # lane (parsed here, carried onto the sampled struct's `behaviors`).
        self._sizing_personality: Optional[SizingPersonality] = None
        self._sizing_personality_override: Optional[SizingPersonality] = None
        self._sizing_tendencies_override: Optional[Tuple[Tuple[str, float], ...]] = None
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

        # Multi-street context layer (docs/plans/STRUCTURAL_PASSIVITY_PLAN.md +
        # POSTFLOP_NEXT_LEVER.md). The postflop pipeline reads hero's-own-line +
        # sustained-aggression context (which the memoryless table lacks) and
        # applies a narrowly-gated barrel-continuation (H1) override.
        #
        # ON as of the per-node attribution A/B (2026-05-27): flop+turn H1
        # barrel-continuation measured CI-clear +EV vs an over-folder
        # (jeff +3.33 / +4.01 OOS bb/100 HU), strongly +vs a station (+11.94 —
        # value extraction, NOT spew), neutral vs a balanced reg
        # (punisher −0.34) and neutral-positive in 6-max (+0.65). Never bleeds.
        #   - H1 RIVER barrel is DROPPED (multistreet_h1_streets): the attribution
        #     gate localized it as the one −EV leg vs *both* opponents (by the
        #     river a "strong draw" has resolved → bluffing busted equity into a
        #     caller). Dropping it lifted H1 from null (+1.73, CI∋0) to CI-clear.
        #   - H2 (fold to a double barrel) is OFF: inert vs the over-folder,
        #     slightly −EV vs the air-barreler (folding marginal made = folding
        #     to bluffs). It never read +EV on the sound gate.
        self.enable_multistreet_context: bool = True
        self.multistreet_h1_barrel: bool = True
        self.multistreet_h2_foldbarrel: bool = False
        self.multistreet_h1_streets: frozenset = frozenset({'FLOP', 'TURN'})

        # Overbet sizing layer (docs/plans/POSTFLOP_NEXT_LEVER.md). The chart bet
        # menu caps at bet_100 — the bot can't overbet without this. Per-node
        # attribution measured value overbets (nuts/strong_made, ~150% pot,
        # turn+river) +EV or neutral vs every opponent type, never negative:
        # punisher (reg) +13 [+8.5, +17.5], jeff +42 HU / +73 6-max, station +159,
        # nit +11.5, lag +12.2. Multistreet sets the bet frequency; this layer
        # sets the bet size for value classes in polarized aggressor spots.
        #
        # ON as of 2026-05-28 (runtime layer validated against the load-time
        # `_overbet_transform` measure: vs jeff +42.50 vs the probe's +42.47,
        # matched to 0.03 bb/100; vs punisher +13.80 vs +13.02, matched within
        # seed noise; top per-node contributions identical).
        #
        # The face-up balance concern (always-overbet pure value is exploitable
        # vs a sizing-aware adapter) does not bite vs the current opponent set:
        # no clone reads bet-sizing tells, and the tiered bot's own exploitation
        # layer keys on opponent frequencies (vpip/ftc/AF), not sizing. Future:
        # build sizing-aware opponent modeling, then tune `overbet_fraction`
        # down + add an overbet-bluff frequency for adapters.
        self.enable_overbet_context: bool = True
        self.overbet_size: int = 150  # bet_150 = 150% pot (smallest validated)
        self.overbet_fraction: float = 1.0  # share of bet mass → overbet (1.0 = the measured probe)
        self.overbet_classes: Optional[frozenset] = None  # None = default {nuts, strong_made}
        self.overbet_streets: Optional[frozenset] = None  # None = default {TURN, RIVER}
        self.overbet_max_active: Optional[int] = (
            None  # None = no multiway gate (matches measured 6-max +73)
        )
        # Overbet BLUFF side (OVERBET_BALANCING.md T1): share of air bet-mass routed
        # to the overbet size, polarizing it so a sizing-reader can't fold to it.
        # 0.0 = OFF (value-only, byte-identical). Production gating (multiway veto /
        # regime) lives in the caller; this is the raw lever the eval harness drives.
        self.overbet_bluff_fraction: float = 0.0
        self.overbet_bluff_classes: Optional[frozenset] = (
            None  # None = default {air_strong_draw, air_no_draw}
        )
        # River-bluff side (OVERBET_BALANCING.md T2): CREATES river bluff supply by
        # promoting give-up-air CHECK mass to a bet at the value size — the only
        # path that fixes the face-up river (tell map: river big bets ~95-100%
        # value). T1 can't (no river air bet-mass to relabel). river_bluff_size
        # None = match overbet_size. ON at 1.0 (calibrated): give-up-air supply
        # caps the river overbet's bluff share at ~31% even at full injection
        # (< the ~37% GTO target → no over-bluff risk; takes the overbet from
        # face-up gap −28 to −7). FIRES only behind the regime gate below (a
        # detected over-folder), so it's value-only vs the fish / cold-start.
        # Set 0.0 to disable. (Eval harnesses bypass __init__ → unaffected unless
        # they set it explicitly; this default only turns it on in real games.)
        self.river_bluff_fraction: float = 1.0
        self.river_bluff_classes: Optional[frozenset] = (
            None  # None = default {air_strong_draw, air_no_draw}
        )
        self.river_bluff_size: Optional[int] = None  # None = match overbet_size
        # Regime gate: river bluffs fire ONLY vs a detected over-folder/sizing-
        # reader (opponent fold_to_big_bet >= min). Cold-start / caller → value-
        # only (the river bluff costs −7.18 bb/100 vs a caller, gains only +1.90
        # vs a reader). _override forces the read in eval/tests (no model mgr).
        self.river_bluff_min_ftbb: float = 0.6
        self.river_bluff_ftbb_override: Optional[float] = None
        # Phase B — sizing defense (SIZING_AWARE_OPPONENT_MODELING.md §B). The dual
        # of the river bluff: FOLD MORE marginal bluff-catchers to a detected
        # FACE-UP value bettor's big bet (one whose big bets are never bluffs).
        # Fires at the DEFAULT clamp tier (the EXTREME bluff-catch gate is vpip/AF-
        # driven and would re-trap the effect in the sizing dead zone; the sizing
        # read is orthogonal to aggression frequency). Gated on a MATURED, face-up
        # `sizing_polarization_score` of the bettor + a big (>= 0.75 pot) bet.
        # Default OFF → byte-identical. _override forces the read in eval/tests
        # (no model manager). The §B leverage is concentrated vs a loose human who
        # value-bets big OFTEN (modest in the AI pool — see the LooseFaceUp probe).
        self.sizing_defense_enabled: bool = False
        self.sizing_defense_min_polar: float = 0.15  # face-up gate: big − small bet eq
        # PROPORTIONAL dampener: the call-retention multiplier scales with HOW
        # face-up the read is — 1.0 (no change) at the min_polar threshold, ramping
        # down to `call_multiplier` (the most-aggressive floor) at `full_polar`.
        # A barely-face-up read barely folds; a blatantly face-up one folds hard.
        # This bounds the misfire cost on weak/false-positive reads (a tiny sample
        # rarely scores high) and shrinks the surface an adapting adversary can
        # exploit. Set call_multiplier=full_polar-equal to recover flat behavior.
        self.sizing_defense_call_multiplier: float = 0.55  # retain at FULL face-up
        self.sizing_defense_full_polar: float = 0.40  # score at which the floor applies
        self.sizing_defense_min_bet_ratio: float = 0.75  # only vs a "big bet"
        self.sizing_defense_polar_override: Optional[float] = None
        # River-air SUPPLY build (OVERBET_BALANCING.md §5e): T2's bluff supply is
        # capped at ~31% because little air survives to the river. This barrels a
        # fraction of TURN air (air_no_draw) so more reaches the checked-to river
        # for T2 to convert. Gated on the SAME reader read + HU + turn-only via
        # multistreet_context. OFF by default (measure-first: it costs EV vs
        # callers and only helps if barreled air actually reaches the river).
        self.air_barrel_target: float = 0.0
        # Gated stab-defense (OVERBET_BALANCING.md §5j): vs a detected frequent
        # stabber, shift fold→call facing a postflop bet (the bot over-folds ~41%
        # to stabs into its capped check range). ON at intensity 0.5 (recovers the
        # ~-1.2 leak by over-calling past MDF — a stabber over-bluffs). Gate at 0.6:
        # the stab read is validated high-precision on 57k casino hands (CallStation
        # 0.00, stations/regs 0.07-0.20, genuine stabbers 0.55-0.88 → ~2/47 trip
        # 0.6), so the unfavorable asymmetry (-2.5 misfire vs +1.0 gain) is managed
        # by the 0.30+ margin between the caller bulk and the gate. Matures via
        # _stab_opp_count; cold-start / non-stabber → no defense (value-only).
        # _override forces the read in eval/tests (no model manager).
        self.stab_defense_intensity: float = 0.5
        self.stab_defense_min: float = 0.6
        self.stab_defense_override: Optional[float] = None
        # Adaptive overbet (PERSONALITY_PRICING_AND_VARIETY.md "Attacker side"):
        # when True, scale the overbet's fraction by the live value-vs-station
        # detection intensity (× sample confidence, already baked into the
        # signal). The static overbet fires vs everyone (+42 vs payers but −24 vs
        # a sizing-reader); the adaptive one fires ONLY on a detected payer and
        # no-ops vs balanced/sizing-readers — the surgical attacker / dynamic
        # clamp keyed on a confident read. Default OFF = static behavior preserved
        # (byte-identical). Requires an attached opponent_model_manager to read.
        self.adaptive_overbet: bool = False
        # Per-personality attack config (production): a character can carry
        # `"adaptive_overbet": true` in personalities.json to enable the surgical
        # overbet (the "skill" / attacker side of the gradient). Sims/tests set
        # the flag directly after __new__ (bypassing __init__), so this read only
        # affects the live path. psychology is set by super().__init__ above.
        _pcfg = getattr(getattr(self, 'psychology', None), 'personality_config', None)
        if isinstance(_pcfg, dict) and 'adaptive_overbet' in _pcfg:
            self.adaptive_overbet = bool(_pcfg['adaptive_overbet'])
        # Per-personality opt-in for Phase B sizing defense (the "skill" of folding
        # to a face-up bettor's big bets). A character carries `"sizing_defense":
        # true` in personalities.json to enable it. Default OFF — measured ~+4.27
        # bb/100 [−8.20, +16.74] vs a maximally face-up bot (real but marginal, CI
        # spans 0), so it ships opt-in per persona, not as a global default. Same
        # bypassed-__init__ caveat as adaptive_overbet: only affects the live path.
        if isinstance(_pcfg, dict) and 'sizing_defense' in _pcfg:
            self.sizing_defense_enabled = bool(_pcfg['sizing_defense'])

        # Per-personality skill tier (PLAYER_SKILL_SPECTRUM.md): a character can
        # carry `"skill": "reg"` (etc.) in its config to set its sharpness across
        # the exploitation / river-bluff / stab-defense / overbet intensities.
        # Mirrors the `adaptive_overbet` read above — native to every live build
        # path. No key (or the default `shark` ceiling) is a no-op, so an
        # un-tiered persona is byte-identical to today. Sims/tests bypass __init__
        # and set the intensity fields directly, so this read only affects the
        # live path. An explicit `skill=` at the factory runs after this and wins.
        _skill = _pcfg.get('skill') if isinstance(_pcfg, dict) else None
        if _skill:
            from poker.strategy.skill_tiers import SKILL_TIERS, apply_skill_tier

            if _skill in SKILL_TIERS:
                apply_skill_tier(self, _skill)
            else:
                logger.warning(
                    "Unknown skill tier %r for persona %r; using default ceiling.",
                    _skill,
                    player_name,
                )

        # Per-personality opt-in for the short-stack Nash push/fold charts (HU +
        # 6max). The charts are GTO-perfect open-jam / call-off ranges below
        # PUSH_FOLD_THRESHOLD_BB; handing them to the whole tiered field makes
        # even the donors (calling_station / weak_fish) play a flawless 15bb
        # game, which contradicts fish-as-donor and reads as unbelievable. So
        # the weapon is opt-in: only personas carrying `"push_fold_nash": true`
        # use it; everyone else falls through to the deep-stack / short_stack.py
        # heuristic (jam-or-fold mass suppression — leaky but human). Skill tier
        # is the wrong proxy (flavour-assigned, postflop-aggression axis, ~half
        # the cast is "shark"), so this is a dedicated flag on a curated few.
        # Default OFF on the live path. Sims/tests bypass __init__ (build via
        # __new__) and never set this attribute, so the gate reads a default of
        # True for them — existing push/fold routing tests stay byte-identical.
        self.push_fold_nash_enabled: bool = bool(
            isinstance(_pcfg, dict) and _pcfg.get('push_fold_nash')
        )

        # Per-player bluff-aware vs_3bet exploit knob (0=off/sticky, 1.0=strong/
        # disciplined): over-fold the marginal continue vs a value-heavy 3-bettor
        # (_apply_vs3bet_bluff_exploit). It's an EXPLOITATION behaviour, so it GRADES
        # with the persona's skill tier (shark folds disciplined 0.85 … rec stays
        # sticky 0.0) — auto-differentiating the whole field, unlike push_fold_nash
        # (a binary elite weapon curated per-persona). Precedence: an explicit
        # "vs3bet_exploit" in config wins; else the skill-tier default; else 0.5 for
        # an un-tiered persona (gate-safe — the probe's archetypes bypass __init__,
        # so they stay off and this can't move the bands). Sims/tests bypass __init__;
        # the live path reads getattr(..., 0.0) so they no-op unless set.
        self.vs3bet_exploit: float = _resolve_vs3bet_exploit(_pcfg, _skill)
        self._vs3bet_beta_cache: Dict = {}

        # Per-player punish-limpers knob (0=off … 1.0=hammer): iso-raise wider over
        # a weak limper to attack its dead money + capped range (_apply_limp_exploit,
        # LIMP_EXPLOIT.md). EXPLOITATION behaviour → grades with skill (shark 0.85 …
        # rec 0.0), like vs3bet_exploit. Gated behind LIMP_EXPLOIT_ENABLED. Live path
        # only (sims/tests bypass __init__ → getattr default 0.0 → no-op unless set).
        self.limp_exploit: float = _resolve_limp_exploit(_pcfg, _skill)

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
        # Deviation profile name — via the robust reverse-lookup (handles
        # explicit loadouts like weak_fish AND the spot_tendencies `replace()`
        # copy that an `is` check against the property would miss → 'unknown').
        try:
            snap['deviation_profile_name'] = self._table_archetype_key()
        except Exception:
            pass

    def _build_narration_facts(self, phase: str, spoken_read=None):
        """Phase 7.6 Step 5: build a NarrationFacts payload from the
        controller's per-decision intervention trace.

        Returns None when no trace is available or the adapter raises
        — the ExpressionContext.narration_facts field stays None and
        the LLM prompt falls back to the standard template.

        `phase` here is the controller's narrow string (e.g. 'flop',
        'pre_flop'); we normalize to the NarrationContext.street
        convention.

        Backlog #12 Phase 1 (both-channels surfacing): when a `spoken_read`
        (a `strategy.spoken_reads.SpokenRead`) is supplied, it is folded in
        as an always-in-context NarrationFact so the earned "figuring you
        out" arc reaches the LLM even on hands the speech channel is gated.
        The arc tier maps straight onto NarrationFact.certainty_bucket
        (tentative/confident/sure), the same escalation cue narration_facts
        already uses.
        """
        traces = getattr(self, '_last_intervention_trace', None)
        if not traces and spoken_read is None:
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
                risk_posture='',  # ditto
            )
            facts = traces_to_narration_facts(traces or [], ctx)
            if spoken_read is not None:
                facts = self._inject_spoken_read_fact(facts, spoken_read, ctx)
            return facts
        except Exception as e:  # noqa: BLE001 — narration is observability
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: " f"narration_facts build failed: {e}"
            )
            return None

    def _inject_spoken_read_fact(self, facts, spoken_read, ctx):
        """Fold a SpokenRead into a NarrationFacts as an always-in-context
        fact (the second of the two surfacing channels).

        The spoken read becomes the LEAD fact (primary_factor) — it is the
        earned, perceptible read the believability work is about. Mechanical
        trace facts stay in the list below it. The observation text is the
        already-intuition-framed line (no raw numbers/stats), and the arc
        tier is the certainty bucket.
        """
        from .strategy.narration_facts import NarrationFact, NarrationFacts

        read_fact = NarrationFact(
            observation=spoken_read.observation,
            why_it_matters="I've watched them long enough to trust this read.",
            decision_taken="Playing into the read I've built on them",
            action_intent='bluff_catch',
            intensity_bucket='noticeable',
            certainty_bucket=spoken_read.arc_tier,
            importance=1.0,  # the read leads
            layer='spoken_read',
            rule_id=spoken_read.read_key,
        )
        existing = list(getattr(facts, 'facts', []) or [])
        merged = [read_fact] + existing
        context = getattr(facts, 'context', None) or ctx
        return NarrationFacts(
            facts=merged,
            primary_factor=read_fact,
            context=context,
            summary_intensity=getattr(facts, 'summary_intensity', 'subtle'),
            suppressed_facts_count=getattr(facts, 'suppressed_facts_count', 0),
        )

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
                if isinstance(getattr(game_state, 'pot', None), dict)
                else 0
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
        self,
        *,
        stats,
        decision_context,
        adaptation_bias: float,
        tilt_factor: float,
        exploitation_strength: float,
        multiway_cbet_intensity: float,
        vvs_intensity_used: float,
        clamp_value: float = 0.4,
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
        snap['clamp_value'] = float(clamp_value)
        snap['clamp_tier_label'] = str(clamp_tier_label)

    @property
    def deviation_profile(self) -> DeviationProfile:
        """Deviation profile for this player.

        The base profile is lazy-resolved from personality anchors (one of the
        six shared archetype profiles). A per-personality spot-tendency override
        (item 3) is then merged on top so a specific character can carry its own
        ((name, strength), ...) tendencies independent of its archetype. With no
        override the archetype profile is returned unchanged (byte-identical).
        """
        if self._deviation_profile is None:
            if self.psychology and self.psychology.anchors:
                self._deviation_profile = select_deviation_profile(self.psychology.anchors)
            else:
                # Fallback to TAG if no psychology loaded yet
                from .strategy.deviation_profiles import DEVIATION_PROFILES

                self._deviation_profile = DEVIATION_PROFILES['tag']
        base = self._deviation_profile
        override = self._effective_spot_tendencies()
        if override is not None and override != base.spot_tendencies:
            return dataclasses.replace(base, spot_tendencies=override)
        return base

    def _effective_spot_tendencies(self) -> Optional[Tuple[Tuple[str, float], ...]]:
        """Per-personality spot-tendency override, or None to inherit the profile's.

        An explicit `_spot_tendencies_override` (set by sims/tests) wins;
        otherwise the personality config's `spot_tendencies` key is read once and
        cached. A character's non-empty config replaces the archetype profile's
        spot_tendencies for that player; absent/empty config inherits.
        """
        # getattr defaults mirror the disable_rules idiom: controllers built via
        # __new__ in tests/sims may not have run __init__.
        override = getattr(self, '_spot_tendencies_override', None)
        if override is not None:
            return override
        if getattr(self, '_spot_tendencies_resolved', False):
            return None
        self._spot_tendencies_resolved = True
        config = getattr(self.psychology, 'personality_config', None)
        raw = config.get('spot_tendencies') if isinstance(config, dict) else None
        if raw:
            from .strategy.deviation_profiles import parse_spot_tendencies

            self._spot_tendencies_override = parse_spot_tendencies(raw)
            return self._spot_tendencies_override
        return None

    def _effective_sizing_tendencies(self) -> Tuple[Tuple[str, float], ...]:
        """Per-personality `sizing_tendencies` override lane (P2+ behaviors).

        Mirrors `_effective_spot_tendencies`: an explicit
        `_sizing_tendencies_override` (set by sims/tests) wins; otherwise the
        personality config's `sizing_tendencies` key is read once and cached.
        In P1 stock personas carry no key, so this returns `()` (the sampled
        `base_size_bias` is the whole personality). The behaviors it returns ride
        along on the sampled SizingPersonality's `behaviors` for P2+ to consult.
        """
        override = getattr(self, '_sizing_tendencies_override', None)
        if override is not None:
            return override
        config = getattr(self.psychology, 'personality_config', None)
        raw = config.get('sizing_tendencies') if isinstance(config, dict) else None
        return parse_sizing_tendencies(raw)

    @property
    def sizing_personality(self) -> SizingPersonality:
        """The per-player preflop sizing personality (sizing_tendencies P1).

        Deterministically SAMPLED once per persona (persona-seeded RNG keyed on
        the player name), then cached — so a character's go-to raise size is
        stable across calls/sessions while same-archetype players differ. The
        Baseline-GTO reference (`skip_personality_distortion`) and any controller
        without anchors get the NEUTRAL personality (multiplier always 1.0), so
        the deterministic sim stays byte-identical.

        An explicit `_sizing_personality_override` (sims/tests) wins. Lazy-resolve
        mirrors `deviation_profile`; getattr guards controllers built via __new__.
        """
        override = getattr(self, '_sizing_personality_override', None)
        if override is not None:
            return override
        cached = getattr(self, '_sizing_personality', None)
        if cached is not None:
            return cached
        # Resolve once and cache. Any failure (e.g. a malformed `sizing_tendencies`
        # persona config) degrades to the neutral no-op personality (multiplier
        # 1.0) rather than crashing the decision or re-raising every hand — sizing
        # is a frequency-neutral cosmetic layer, so neutral is the safe default.
        try:
            if getattr(self, 'skip_personality_distortion', False):
                resolved = SizingPersonality.neutral()
            else:
                psych = getattr(self, 'psychology', None)
                anchors = psych.anchors if psych else None
                if anchors is None:
                    resolved = SizingPersonality.neutral()
                else:
                    resolved = sample_sizing_personality(
                        anchors,
                        persona_seed=self.player_name,
                        archetype_key=self._table_archetype_key(),
                        sizing_tendencies=self._effective_sizing_tendencies(),
                    )
        except Exception:
            logger.warning(
                'sizing_personality resolution failed for %s; using neutral',
                getattr(self, 'player_name', '?'),
                exc_info=True,
            )
            resolved = SizingPersonality.neutral()
        self._sizing_personality = resolved
        return resolved

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
        base = classify_from_anchors(anchors.baseline_looseness, anchors.baseline_aggression)
        return {
            'tight_passive': 'rock',
            'tight_aggressive': 'tag',
            'loose_passive': 'calling_station',
            'loose_aggressive': 'lag',
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
            return self._get_postflop_decision(game_state, player_idx, valid_actions, context)

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
        node = self._apply_position_blindness(node)

        # Phase 7: route to HU chart when the hand started 2-handed. Gate on
        # seated count (not non-folded count) so 6-max spots that collapse to
        # 2 players after folds still use the 6-max chart.
        num_seated = len(game_state.players)
        # Depth-aware: pick the 6-max chart calibrated for the effective
        # stack (100/50/25bb). Computed here (not just at the short_stack
        # step below) because the base ranges themselves are depth-dependent.
        effective_stack_bb = self._compute_effective_stack_bb(game_state, player_idx)
        preflop_table, chart_label = self._select_preflop_table(num_seated, effective_stack_bb)
        # Record WHICH base chart fed this decision (e.g. '6max:loose_mid', '50bb',
        # 'HU') so decision analysis can show the chart the line started from.
        self._last_pipeline_snapshot['chart_label'] = chart_label
        # Chart-opportunity census instrumentation: the exact node served and
        # whether push/fold is even enabled for this persona (most prod donors
        # have it off). chart_source is finalized below once we know which layer
        # produced the line. Record money context (pot/cost/stack/big_blind) up
        # front so the early-returning veto path also carries it for the census's
        # bb-at-risk metric — the later call (post-sample) is an idempotent
        # overwrite with the same values.
        self._last_pipeline_snapshot['node_key'] = node.key
        self._last_pipeline_snapshot['push_fold_enabled'] = bool(
            getattr(self, 'push_fold_nash_enabled', True)
        )
        self._snapshot_math_floor_inputs(game_state, player_idx)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"hand={canonical_hand} node_key={node.key} "
                f"chart={chart_label} eff_bb={effective_stack_bb:.1f}"
            )

        # Layer 1: Lookup base strategy. Short-stack HU spots bypass the
        # deep-stack table and use the dedicated push/fold chart instead;
        # the deep-stack ranges are mis-calibrated below ~15 BB because
        # standard raise sizes commit too much of the stack to be coherent
        # short of jamming.
        push_fold_action = self._try_push_fold_lookup(
            canonical_hand,
            game_state,
            player_idx,
            num_seated,
        )
        if push_fold_action is not None:
            # push_fold_action is an abstract token ('jam'/'fold'/'call'). A
            # caller-table 'call' that is a call-off (engine offers only
            # 'all_in') is resolved to all_in centrally in resolve_preflop_sizing
            # (it's passed valid_actions) — no per-producer pre-translation here.
            base_strategy = StrategyProfile(action_probabilities={push_fold_action: 1.0})
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"push_fold={push_fold_action} hand={canonical_hand}"
                )
            self._last_pipeline_snapshot['push_fold_routed'] = True
            self._last_pipeline_snapshot['chart_source'] = 'push_fold'
        else:
            base_strategy, chart_lookup_source = preflop_table.lookup_with_fallback_traced(
                node, valid_actions
            )
            self._last_pipeline_snapshot['push_fold_routed'] = False
            # chart_lookup_source ∈ {hit, squeeze_degrade, masked_out, miss}.
            # masked_out/miss both land on the conservative default = a true
            # chart fall-through; the census buckets the rest as a chart hit.
            self._last_pipeline_snapshot['chart_lookup_source'] = chart_lookup_source
            self._last_pipeline_snapshot['chart_source'] = (
                'chart_fallback' if chart_lookup_source in ('miss', 'masked_out') else 'chart_hit'
            )
            base_strategy = self._apply_vs3bet_bluff_exploit(base_strategy, node, preflop_table)
            base_strategy = self._apply_limp_exploit(base_strategy, node, game_state, player_idx)

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"base_strategy={base_strategy.action_probabilities}"
            )

        # Snapshot preflop base_strategy (already an input to personality).
        self._last_pipeline_snapshot['base_strategy_probs'] = dict(
            base_strategy.action_probabilities
        )

        # Facing-an-all-in equity veto (see _facing_all_in_preflop_veto).
        # Facing a cold all-in the chart's coarse vs_3bet/vs_4bet stub can
        # sample a trash JAM/CALL — the root cause of the prod "47o jams into
        # a 4-bet all-in" bug. There's no skill in re-jamming vs calling a
        # shove, so we decide call/fold on pot odds and return immediately,
        # bypassing the distortion/exploitation layers that would otherwise
        # resample the stub into a jam. A base-strategy-level short-circuit,
        # like the push/fold route above — not a pipeline layer.
        veto = self._facing_all_in_preflop_veto(game_state, player_idx, valid_actions)
        if veto is not None:
            veto_profile, veto_action, veto_equity, veto_required = veto
            # Sample the (pure) profile through self.rng so the RNG stream
            # stays aligned with the normal sample_action draw downstream.
            veto_action = veto_profile.sample_action(self.rng)
            self._last_pipeline_snapshot['base_strategy_probs'] = dict(
                veto_profile.action_probabilities
            )
            self._last_pipeline_snapshot['facing_all_in_veto'] = True
            self._last_pipeline_snapshot['chart_source'] = 'facing_all_in_veto'
            self._last_pipeline_snapshot['veto_equity'] = veto_equity
            self._last_pipeline_snapshot['veto_required_equity'] = veto_required
            self._last_pipeline_snapshot['sampled_abstract_action'] = veto_action

            game_action, raise_to = resolve_preflop_sizing(
                veto_action, game_state, player_idx, rng=self.rng, valid_actions=valid_actions
            )
            if game_action not in valid_actions:
                game_action, raise_to = self._validate_action(game_action, raise_to, valid_actions)
            self._last_pipeline_snapshot['resolved_action'] = game_action
            self._last_pipeline_snapshot['resolved_raise_to'] = raise_to

            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: facing_all_in_veto "
                    f"action={veto_action} eq={veto_equity:.3f} "
                    f"req={veto_required:.3f} -> {game_action} raise_to={raise_to}"
                )

            decision = {
                'action': game_action,
                'raise_to': raise_to,
                'dramatic_sequence': [],
                'hand_strategy': (
                    f"Tiered bot: facing all-in with {canonical_hand} "
                    f"eq={veto_equity:.2f} req={veto_required:.2f} -> {veto_action}"
                ),
                'inner_monologue': '',
                'bluff_likelihood': 0,
            }
            self._attach_expression(decision, game_state, player_idx, phase='pre_flop')
            return decision

        # Layer 2: Personality distortion (skipped for BaselineSolverBot)
        emotional_state = get_emotional_shift(self.psychology)
        anchors = self.psychology.anchors if self.psychology else None

        # Snapshot personality inputs.
        self._snapshot_personality_inputs(anchors, emotional_state)

        if anchors and not self.skip_personality_distortion:
            # Pre/postflop aggression split: at facing-a-raise preflop nodes
            # (3-bet / 4-bet spots) swap in the archetype's reraise-scoped
            # aggression knob when set, so we tame re-raise FREQUENCY without
            # touching opening width or postflop aggression. RFI (opening) and
            # postflop keep the full profile.
            distortion_profile = self.deviation_profile
            if (
                getattr(node, 'scenario', '') in ('vs_open', 'vs_3bet', 'vs_4bet')
                and distortion_profile.reraise_aggression_scale is not None
            ):
                distortion_profile = dataclasses.replace(
                    distortion_profile,
                    aggression_scale=distortion_profile.reraise_aggression_scale,
                    max_per_action_shift=(
                        distortion_profile.reraise_max_per_action_shift
                        if distortion_profile.reraise_max_per_action_shift is not None
                        else distortion_profile.max_per_action_shift
                    ),
                )
            modified_strategy, personality_trace = modify_strategy(
                base=base_strategy,
                legal_actions=valid_actions,
                anchors=anchors,
                emotional_state=emotional_state,
                deviation_profile=distortion_profile,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
        else:
            modified_strategy = base_strategy
            personality_trace = make_no_op_trace(
                layer='personality',
                rule_id='default',
                layer_order=layer_order_for('personality'),
                reason_code='distortion_skipped',
            )
        self._last_intervention_trace.append(personality_trace)

        # Phase 6.b (preflop): scenario-scoped spot tendencies (e.g. tag's
        # `defend_3bet`, which de-polarizes the 4-bet-or-fold vs_3bet response
        # toward flatting). Separate from the postflop _layer_spot_tendencies
        # helper because that reads PostflopNode-only fields (street/
        # facing_action) a PreflopNode lacks; this passes street=None + the
        # preflop scenario. Every street-gated postflop tendency no-ops here
        # (street=None matches no street set), so profiles without a
        # preflop-scoped tendency are byte-identical.
        modified_strategy = self._layer_preflop_spot_tendencies(
            modified_strategy,
            node=node,
            anchors=anchors,
            hand_strength=self._classify_preflop_hand_strength(canonical_hand, anchors),
        )

        # Phase 2 (PERCEPTIBILITY_CONDITIONING.md): tilt_conditioning runs
        # between spot-tendencies and exploitation. Inert (no-op, byte-identical)
        # unless the flag is on AND the profile opts in (cap > 0.0).
        modified_strategy = self._layer_tilt_conditioning(
            modified_strategy,
            node=node,
            valid_actions=valid_actions,
            emotional_state=emotional_state,
        )

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
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            emotional_state,
            hand_strength=None,
        )
        self._last_intervention_trace.extend(exploitation_traces)

        # Phase 6.5: strong-hand value override.
        # Replaces strategy entirely when hero has a top-tier hand vs a
        # detected hyper-aggressive opponent — offsets can't shift
        # probability mass enough for these spots.
        modified_strategy, value_override_trace = self._apply_value_override(
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            emotional_state,
            hand_strength=self._classify_preflop_hand_strength(canonical_hand, anchors),
        )
        self._last_intervention_trace.append(value_override_trace)

        # Playstyle-gated rule diagnostics. Preflop sees no playstyle
        # counters fire (value_vs_station is postflop-only); the call
        # still runs to reset the per-decision stash. Same call site
        # shape as postflop so the method is symmetric.
        self._tally_playstyle_rule_event()

        # Phase 6 Step B: short-stack heuristic. Depth-aware suppression
        # of medium-raise probability mass below 20 BB effective stack.
        # Independent of opponent type — always fires when stack is short.
        # (effective_stack_bb already computed above for chart selection.)
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
            math_floor_trace,
            self._last_intervention_trace,
        )
        self._last_intervention_trace.append(math_floor_trace)

        abstract_action = modified_strategy.sample_action(self.rng)
        self._last_pipeline_snapshot['sampled_abstract_action'] = abstract_action

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"sampled={abstract_action} emotional={emotional_state.state}"
            )

        # Per-player sizing personality (sizing_tendencies P1). Center the raise
        # size on this player's sampled `base_size_bias` BEFORE jitter+rounding,
        # so same-archetype players visibly size differently (a read you earn).
        # Frequency-neutral: only the magnitude is scaled, never which action
        # fires. The multiplier is 1.0 for Baseline-GTO / no-anchor controllers,
        # keeping the deterministic sim byte-identical. P1 ignores the context;
        # it's built so P2+ palette behaviors plug in without changing this seam.
        size_context = SizeContext(
            scenario=getattr(node, 'scenario', None),
            hand_strength=self._classify_preflop_hand_strength(canonical_hand, anchors),
            position=getattr(node, 'position', None),
            big_blind=getattr(game_state, 'current_ante', 0),
        )
        size_multiplier = resolve_size_multiplier(self.sizing_personality, size_context)

        game_action, raise_to = resolve_preflop_sizing(
            abstract_action,
            game_state,
            player_idx,
            rng=self.rng,
            sizing_jitter=getattr(self, 'sizing_jitter', 0.0),
            size_multiplier=size_multiplier,
            valid_actions=valid_actions,
        )

        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(game_action, raise_to, valid_actions)

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
        self,
        game_state,
        player_idx: int,
        valid_actions: List[str],
        context: dict,
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
        community_cards = (
            [card_to_string(c) for c in game_state.community_cards]
            if game_state.community_cards
            else []
        )

        if not hole_cards or len(community_cards) < 3:
            return self._postflop_fallback(valid_actions)

        # 2. Build PostflopNode
        try:
            node = build_postflop_node(game_state, player_idx, hole_cards, community_cards)
            node = self._apply_postflop_position_blindness(node)
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop_classifier error: {e}, using fallback"
            )
            return self._postflop_fallback(valid_actions)

        if self.debug_logging:
            logger.info(f"[TIERED_BOT] {self.player_name}: " f"postflop node_key={node.key}")
        # Snapshot the node key (encodes street|position|pot_type|texture|
        # hand_class|draw|action_context|spr) so passivity instrumentation can
        # pair the resolved action with its full postflop context without
        # re-deriving the node. Cheap; the snapshot already exists for replay.
        self._last_pipeline_snapshot['node_key'] = node.key

        # 3. Lookup base strategy. Only the (SRP, high) chart is authored;
        # shallow-SPR / 3-bet-pot spots ride the degrade ladder (low → high,
        # 3BP → SRP). The authored precision slices were cut after the hardened
        # SNG gate measured them neutral (docs/plans/SNG_RUNNER_HARDENING.md).
        base_strategy = self.strategy_table.lookup_postflop_with_fallback(
            node,
            valid_actions,
        )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: "
                f"postflop base_strategy={base_strategy.action_probabilities}"
            )

        # 4. Multiway adjustment (if > 2 active players)
        active_count = sum(1 for p in game_state.players if not p.is_folded)
        if active_count > 2:
            base_strategy = apply_multiway_adjustment(
                base_strategy,
                active_count,
                node.position,
                # §13 value exemption: don't suppress value-hand aggression in
                # multiway (you value-bet the nuts into a field). Pure on node.
                hand_class=self._classify_postflop_hand_strength(node),
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
                layer='personality',
                rule_id='default',
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
            simplified_class = simplify_hand_class(node.made_tier, node.draw_modifier)
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
            game_state,
            player_idx,
        )
        # Plan §4: snapshot bet-size bucket + required_equity for
        # diagnostics. The DecisionContext already carries these for
        # strategy rules; snapshotting here mirrors the pattern used for
        # nut_status/danger_flags so post-hand analysis
        # (casebot_breakdown etc.) can read them off the controller's
        # last-decision state.
        self._last_pipeline_snapshot['bet_bucket'] = outer_decision_context.bet_bucket
        self._last_pipeline_snapshot['required_equity'] = outer_decision_context.required_equity
        # Plan §6: opponent_archetype is snapshotted inside
        # `_tally_exploitation_event` (where `stats` is already
        # selected) — see that method. Done as a side effect of the
        # tally call so we don't duplicate _select_exploitation_stats_from_spots.

        # 6.b Spot/line-specific personality tendencies (item 3,
        # PERSONALITY_PRICING_AND_VARIETY.md). Runs in the personality block
        # (layer_order 0), right after the global-scalar distortion and before
        # exploitation. Reshapes only on the node/line spots a configured
        # tendency targets (e.g. slow-play a strong hand with initiative).
        # OFF (profile.spot_tendencies empty) is byte-identical.
        modified_strategy = self._layer_spot_tendencies(
            modified_strategy,
            node=node,
            anchors=anchors,
            hand_strength=hand_strength,
        )

        # 6.c Phase 2 (PERCEPTIBILITY_CONDITIONING.md): tilt_conditioning runs
        # between spot-tendencies and exploitation. Inert (no-op, byte-identical)
        # unless the flag is on AND the profile opts in (cap > 0.0).
        modified_strategy = self._layer_tilt_conditioning(
            modified_strategy,
            node=node,
            valid_actions=valid_actions,
            emotional_state=emotional_state,
        )

        # 6a. Phase 6: opponent exploitation (between personality and math floor)
        modified_strategy, exploitation_traces = self._apply_exploitation(
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            emotional_state,
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
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            emotional_state,
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
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            emotional_state,
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
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            emotional_state,
            hand_strength=hand_strength,
        )
        # Fill in prior_action_source — if an earlier layer fired (made
        # `replaced_prior_action=True` true), record which one. Today
        # only value_override is the candidate; later steps add more
        # earlier layers and the same loop covers them.
        bluff_catch_trace = _fill_prior_action_source(
            bluff_catch_trace,
            self._last_intervention_trace,
        )
        self._last_intervention_trace.append(bluff_catch_trace)

        # 6a.5b.1b Phase B sizing defense (SIZING_AWARE_OPPONENT_MODELING.md §B).
        # Behind sizing_defense_enabled (default off → byte-identical). Folds more
        # marginal bluff-catchers to a detected FACE-UP value bettor's big bet, at
        # the DEFAULT clamp tier. Defers when bluff_catch already fired so the two
        # facing-bet rules don't compound on the same decision.
        modified_strategy, sizing_defense_trace = self._apply_sizing_defense(
            modified_strategy,
            game_state,
            player_idx,
            valid_actions,
            anchors,
            hand_strength=hand_strength,
            prior_layer_fired=bluff_catch_trace.fired,
        )
        sizing_defense_trace = _fill_prior_action_source(
            sizing_defense_trace,
            self._last_intervention_trace,
        )
        self._last_intervention_trace.append(sizing_defense_trace)

        # 6a.5b.2 Multi-street context (STRUCTURAL_PASSIVITY_PLAN.md).
        # Behind enable_multistreet_context (default off). Reads hero's-own-
        # line (was_prev_street_aggressor) + sustained-aggression
        # (facing_double_barrel) — signals the memoryless table can't see —
        # and applies a narrowly-gated barrel-continuation (H1, HU only) /
        # fold-to-double-barrel (H2) override. Sits before defense_floor and
        # feeds prior_layer_fired so the floor defers when it replaces the
        # distribution; downstream math_floor keeps final say on pot-odds
        # mandates. OFF arm is byte-identical to current behavior.
        modified_strategy, multistreet_trace = self._layer_multistreet_context(
            modified_strategy,
            node=node,
            hand_strength=hand_strength,
            active_count=active_count,
            game_state=game_state,
            induce_override_trace=induce_override_trace,
            value_override_trace=value_override_trace,
            bluff_catch_trace=bluff_catch_trace,
        )
        self._last_intervention_trace.append(multistreet_trace)

        # 6a.5b.3 Overbet sizing (docs/plans/POSTFLOP_NEXT_LEVER.md).
        # The chart bet menu caps at bet_100 — the bot is structurally incapable
        # of overbetting. Per-node attribution (HU + 6-max paired-CRN) measured
        # value overbets (nuts/strong_made, 150% pot, turn+river) +EV or neutral
        # vs every opponent, never negative: punisher (reg) +13 [+8.5, +17.5],
        # jeff +42 HU / +73 6-max, station +159. Multistreet sets the bet
        # *frequency*; this layer sets the *size* — so it runs immediately after.
        # Behind enable_overbet_context; OFF arm is byte-identical.
        modified_strategy, overbet_trace = self._layer_overbet_context(
            modified_strategy,
            node=node,
            hand_strength=hand_strength,
            active_count=active_count,
            game_state=game_state,
            induce_override_trace=induce_override_trace,
            value_override_trace=value_override_trace,
            bluff_catch_trace=bluff_catch_trace,
            multistreet_trace=multistreet_trace,
        )
        self._last_intervention_trace.append(overbet_trace)

        # NOTE: the value-bet floor override (§12) was retired (§14) once its
        # win was traced to multiway over-suppressing value hands and baked
        # into apply_multiway_adjustment's VALUE_CLASSES exemption (the root
        # fix). The exemption reproduced the floor's GTO win in full and the
        # majority elsewhere; the residual was an exploitative over-bet that
        # belongs in the exploitation layer, not the base policy.

        # 6a.5c Plan §2: price-sensitive defense floor. Pumps call
        # probability for legitimate made hands at favorable prices
        # that the upstream rules left fold-heavy. Sits *after* both
        # overrides so it defers when either has already replaced the
        # distribution (prior_layer_fired). Reads §1's hand_class +
        # nut_status + danger_flags from the postflop node and §4's
        # required_equity + facing_bet from DecisionContext.
        modified_strategy, defense_floor_trace = self._layer_defense_floor(
            modified_strategy,
            node=node,
            hand_strength=hand_strength,
            outer_decision_context=outer_decision_context,
            induce_override_trace=induce_override_trace,
            value_override_trace=value_override_trace,
            bluff_catch_trace=bluff_catch_trace,
            multistreet_trace=multistreet_trace,
            overbet_trace=overbet_trace,
        )
        self._last_intervention_trace.append(defense_floor_trace)

        # 6a.6b Gated stab-defense (OVERBET_BALANCING §5j): vs a detected frequent
        # stabber, widen the bot's defense facing a postflop bet (shift fold→call)
        # — the bot over-folds (~41%) to stabs into its capped check range. Gated
        # on a stab-frequency read so it never costs vs the fish (who don't stab).
        # OFF by default (stab_defense_intensity=0) → byte-identical.
        stab_intensity = getattr(self, 'stab_defense_intensity', 0.0)
        stab_defense_trace = make_no_op_trace(
            layer='stab_defense',
            rule_id='default',
            layer_order=layer_order_for('stab_defense'),
            reason_code='flag_disabled',
        )
        if stab_intensity > 0.0:
            from .strategy.stab_defense import apply_stab_defense

            stab_prior_fired = (
                induce_override_trace.fired
                or value_override_trace.fired
                or bluff_catch_trace.fired
                or overbet_trace.fired
            )
            modified_strategy, stab_defense_trace = apply_stab_defense(
                modified_strategy,
                action_context=node.facing_action,
                street=node.street,
                stab_read=self._resolve_stabber_read(game_state),
                intensity=stab_intensity,
                min_stab=getattr(self, 'stab_defense_min', 0.5),
                prior_layer_fired=stab_prior_fired,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            stab_defense_trace = _fill_prior_action_source(
                stab_defense_trace, self._last_intervention_trace
            )
        self._last_intervention_trace.append(stab_defense_trace)

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

        # 6a.7 Postflop commit: at low SPR, funnel value-hand (nuts/
        # strong_made) passive + small-bet mass into a jam — get the money in
        # when committed instead of checking the nuts / flatting (the
        # diagnosed low-SPR passivity). Pairs with the SPR fallback in the
        # postflop lookup. No-op preflop (node.spr_bucket is postflop-only).
        modified_strategy, postflop_commit_trace = apply_postflop_commit(
            modified_strategy,
            spr_bucket=node.spr_bucket,
            hand_class=hand_strength,
            facing_action=node.facing_action,
            legal_actions=valid_actions,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )
        self._last_intervention_trace.append(postflop_commit_trace)

        # 6b. Math floor — override when arithmetic mandates a call/jam.
        # Runs AFTER personality + river guardrail so it has final say.
        self._snapshot_math_floor_inputs(game_state, player_idx)
        modified_strategy, math_floor_trace = self._apply_math_floor(
            modified_strategy, game_state, player_idx, valid_actions
        )
        math_floor_trace = _fill_prior_action_source(
            math_floor_trace,
            self._last_intervention_trace,
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
            abstract_action,
            game_state,
            player_idx,
            rng=self.rng,
            sizing_jitter=getattr(self, 'sizing_jitter', 0.0),
            valid_actions=valid_actions,
        )

        # 9. Validate action is legal
        if game_action not in valid_actions:
            game_action, raise_to = self._validate_action(game_action, raise_to, valid_actions)

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

    # ── Postflop pipeline layers (extracted from _get_postflop_decision) ──────
    # Each helper preserves the EXACT body, ordering, thresholds, and side
    # effects of the inline block it replaced (trace extends/appends and
    # snapshot writes). Extraction-only: byte-identical decisions.

    def _layer_spot_tendencies(
        self,
        modified_strategy,
        *,
        node,
        anchors,
        hand_strength,
    ):
        """6.b Spot/line-specific personality tendencies.

        Reshapes the strategy only on the node/line spots a configured
        tendency targets. OFF (profile.spot_tendencies empty) is byte-identical.
        Extends `self._last_intervention_trace` with the spot traces, exactly as
        the inline block did.
        """
        if (
            anchors
            and not self.skip_personality_distortion
            and self.deviation_profile.spot_tendencies
        ):
            from .strategy.multistreet_context import derive_signals
            from .strategy.spot_tendencies import apply_spot_tendencies

            spot_signals = derive_signals(self, node.street)
            modified_strategy, spot_traces = apply_spot_tendencies(
                modified_strategy,
                spot_tendencies=self.deviation_profile.spot_tendencies,
                max_per_action_shift=self.deviation_profile.max_per_action_shift,
                hand_class=hand_strength,
                action_context=node.facing_action,
                street=node.street,
                has_initiative=spot_signals.was_prev_street_aggressor,
                facing_double_barrel=spot_signals.facing_double_barrel,
                position=node.position,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            self._last_intervention_trace.extend(spot_traces)
            if self.debug_logging:
                logger.info(
                    f"[TIERED_BOT] {self.player_name}: "
                    f"spot_tendencies={modified_strategy.action_probabilities}"
                )
        return modified_strategy

    def _layer_preflop_spot_tendencies(
        self,
        modified_strategy,
        *,
        node,
        anchors,
        hand_strength,
    ):
        """6.b (preflop) Scenario-scoped spot tendencies.

        The postflop `_layer_spot_tendencies` reads PostflopNode fields
        (`street`/`facing_action`) a `PreflopNode` lacks, so the preflop call is a
        separate, narrower one: ``street=None`` (every street-gated postflop
        tendency no-ops), ``action_context='facing_raise'``, and the preflop
        ``scenario`` threaded through so a tendency can gate on it (e.g.
        ``defend_3bet`` on ``'vs_3bet'``). OFF / no-preflop-tendency profiles are
        byte-identical (the guard + the street gates).
        """
        if (
            anchors
            and not self.skip_personality_distortion
            and self.deviation_profile.spot_tendencies
        ):
            from .strategy.spot_tendencies import apply_spot_tendencies

            modified_strategy, spot_traces = apply_spot_tendencies(
                modified_strategy,
                spot_tendencies=self.deviation_profile.spot_tendencies,
                max_per_action_shift=self.deviation_profile.max_per_action_shift,
                hand_class=hand_strength,
                action_context='facing_raise',
                street=None,
                has_initiative=False,
                position=getattr(node, 'position', None),
                scenario=getattr(node, 'scenario', None),
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            self._last_intervention_trace.extend(spot_traces)
            self._last_pipeline_snapshot['preflop_spot_tendency_probs'] = dict(
                modified_strategy.action_probabilities
            )
        return modified_strategy

    def _layer_tilt_conditioning(
        self,
        modified_strategy,
        *,
        node,
        valid_actions,
        emotional_state,
    ):
        """Phase 2 (PERCEPTIBILITY_CONDITIONING.md): state-conditioned aggression.

        Runs between the spot-tendencies layer and exploitation (a conditioner,
        not an override — the math floor still runs after it). Double-gated for
        zero overhead AND zero effect when off/inert:
          - the TILT_CONDITIONING_ENABLED feature flag, AND
          - the profile's `tilt_conditioning_cap > 0.0` (the Phase-2 default for
            EVERY shipped archetype is 0.0, so this is byte-identical until an
            archetype opts in — Phase 3).
        Appends the layer's trace to `self._last_intervention_trace`. Shared by
        the preflop and postflop decision paths. `composure_state` read via
        getattr for sim/__new__ safety.
        """
        profile = getattr(self, 'deviation_profile', None)
        if profile is None or float(getattr(profile, 'tilt_conditioning_cap', 0.0) or 0.0) <= 0.0:
            return modified_strategy
        try:
            from core.feature_flags import is_enabled

            if not is_enabled('TILT_CONDITIONING_ENABLED'):
                return modified_strategy
        except Exception:
            # Flag registry unavailable (sim/test isolation) → treat as off.
            return modified_strategy

        psychology = getattr(self, 'psychology', None)
        composure_state = getattr(psychology, 'composure_state', None) if psychology else None
        modified_strategy, tilt_trace = apply_tilt_conditioning(
            modified_strategy,
            legal_actions=valid_actions,
            emotional_state=emotional_state,
            composure_state=composure_state,
            node=node,
            archetype_rules=getattr(profile, 'tilt_scenario_rules', ()),
            profile=profile,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )
        self._last_intervention_trace.append(tilt_trace)
        return modified_strategy

    def _layer_multistreet_context(
        self,
        modified_strategy,
        *,
        node,
        hand_strength,
        active_count,
        game_state,
        induce_override_trace,
        value_override_trace,
        bluff_catch_trace,
    ):
        """6a.5b.2 Multi-street context (STRUCTURAL_PASSIVITY_PLAN.md).

        Behind enable_multistreet_context (default off). OFF arm is
        byte-identical. Returns (modified_strategy, multistreet_trace); the
        orchestrator appends the trace (preserving append order).
        """
        multistreet_trace = make_no_op_trace(
            layer='multistreet_context',
            rule_id='default',
            layer_order=layer_order_for('multistreet_context'),
            reason_code='flag_disabled',
        )
        if getattr(self, 'enable_multistreet_context', False):
            from .strategy.multistreet_context import (
                apply_multistreet_context,
                derive_signals,
            )

            signals = derive_signals(self, node.street)
            ms_prior_fired = (
                induce_override_trace.fired or value_override_trace.fired or bluff_catch_trace.fired
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
                h1_streets=getattr(self, 'multistreet_h1_streets', None),
                street=node.street,
                air_barrel_target=getattr(self, 'air_barrel_target', 0.0),
                air_barrel_fold_to_big_bet=(
                    self._resolve_river_bluff_ftbb(game_state)
                    if getattr(self, 'air_barrel_target', 0.0) > 0.0
                    else None
                ),
                air_barrel_min_ftbb=getattr(self, 'river_bluff_min_ftbb', 0.6),
                prior_layer_fired=ms_prior_fired,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            multistreet_trace = _fill_prior_action_source(
                multistreet_trace,
                self._last_intervention_trace,
            )
        return modified_strategy, multistreet_trace

    def _layer_overbet_context(
        self,
        modified_strategy,
        *,
        node,
        hand_strength,
        active_count,
        game_state,
        induce_override_trace,
        value_override_trace,
        bluff_catch_trace,
        multistreet_trace,
    ):
        """6a.5b.3 Overbet sizing (docs/plans/POSTFLOP_NEXT_LEVER.md).

        Behind enable_overbet_context; OFF arm is byte-identical. Returns
        (modified_strategy, overbet_trace); the orchestrator appends the trace.
        """
        overbet_trace = make_no_op_trace(
            layer='overbet_context',
            rule_id='default',
            layer_order=layer_order_for('overbet_context'),
            reason_code='flag_disabled',
        )
        # Adaptive gate: scale the overbet fraction by the live station-detection
        # intensity (set by _apply_exploitation above, which runs earlier this
        # decision). 0.0 when no manager / no detected payer → the overbet
        # no-ops, so we don't bloat the pot vs balanced or sizing-reading
        # opponents. The static path (adaptive_overbet=False) is unchanged.
        _overbet_fraction = self._effective_overbet_fraction()
        _overbet_bluff_fraction = getattr(self, 'overbet_bluff_fraction', 0.0)
        _river_bluff_fraction = getattr(self, 'river_bluff_fraction', 0.0)
        if getattr(self, 'enable_overbet_context', False) and (
            _overbet_fraction > 0.0 or _overbet_bluff_fraction > 0.0 or _river_bluff_fraction > 0.0
        ):
            from .strategy.overbet_context import apply_overbet_context

            overbet_prior_fired = (
                induce_override_trace.fired
                or value_override_trace.fired
                or bluff_catch_trace.fired
                or multistreet_trace.fired
            )
            modified_strategy, overbet_trace = apply_overbet_context(
                modified_strategy,
                hand_class=hand_strength,
                action_context=node.facing_action,
                street=node.street,
                active_count=active_count,
                overbet_size=getattr(self, 'overbet_size', 150),
                overbet_fraction=_overbet_fraction,
                overbet_classes=getattr(self, 'overbet_classes', None),
                overbet_streets=getattr(self, 'overbet_streets', None),
                overbet_max_active=getattr(self, 'overbet_max_active', None),
                overbet_bluff_fraction=_overbet_bluff_fraction,
                overbet_bluff_classes=getattr(self, 'overbet_bluff_classes', None),
                river_bluff_fraction=_river_bluff_fraction,
                river_bluff_classes=getattr(self, 'river_bluff_classes', None),
                river_bluff_size=getattr(self, 'river_bluff_size', None),
                river_bluff_fold_to_big_bet=(
                    self._resolve_river_bluff_ftbb(game_state)
                    if _river_bluff_fraction > 0.0
                    else None
                ),
                river_bluff_min_ftbb=getattr(self, 'river_bluff_min_ftbb', 0.6),
                prior_layer_fired=overbet_prior_fired,
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )
            overbet_trace = _fill_prior_action_source(
                overbet_trace,
                self._last_intervention_trace,
            )
        return modified_strategy, overbet_trace

    def _layer_defense_floor(
        self,
        modified_strategy,
        *,
        node,
        hand_strength,
        outer_decision_context,
        induce_override_trace,
        value_override_trace,
        bluff_catch_trace,
        multistreet_trace,
        overbet_trace,
    ):
        """6a.5c Plan §2: price-sensitive defense floor.

        Pumps call probability for legitimate made hands at favorable prices.
        Defers when any prior override fired (prior_layer_fired). Returns
        (modified_strategy, defense_floor_trace); the orchestrator appends it.
        """
        from .strategy.defense_floor import apply_defense_floor

        prior_layer_fired = (
            induce_override_trace.fired
            or value_override_trace.fired
            or bluff_catch_trace.fired
            or multistreet_trace.fired
            or overbet_trace.fired
        )
        defense_floor_facing_bet = outer_decision_context.bet_bucket is not None
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
            defense_floor_trace,
            self._last_intervention_trace,
        )
        return modified_strategy, defense_floor_trace

    def _effective_overbet_fraction(self) -> float:
        """Overbet fraction after the adaptive gate.

        Static (default): the configured `overbet_fraction` unchanged. Adaptive
        (`adaptive_overbet=True`): the configured fraction scaled by the live
        value-vs-station detection intensity (`_last_value_vs_station_intensity_raw`,
        set by `_apply_exploitation` earlier this decision; already
        sample-confidence-weighted). Returns 0.0 vs a balanced/undetected
        opponent → the overbet no-ops, so the bot only overbets a detected payer.
        """
        base = getattr(self, 'overbet_fraction', 1.0)
        if not getattr(self, 'adaptive_overbet', False):
            return base
        intensity = getattr(self, '_last_value_vs_station_intensity_raw', 0.0)
        return base * max(0.0, min(1.0, intensity))

    def _apply_exploitation(
        self,
        strategy,
        game_state,
        player_idx,
        valid_actions,
        anchors,
        emotional_state,
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
                'manager_unavailable',
                disable_rules=getattr(self, "disable_rules", frozenset()),
            )

        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        # Phase 6.7a: build spots once, then route stat selection through
        # the spot-aware path. The legacy aggregate fields are preserved
        # via aggregate_from_spots(), so unmigrated rules see identical
        # behavior in unambiguous cases.
        spots = self._build_opponent_spots(game_state, manager)
        stats, primary_spot, ambiguous = self._select_exploitation_stats_from_spots(
            spots, game_state
        )

        decision_context = self._build_decision_context(
            game_state,
            player_idx,
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
            a == 'bet'
            or a.startswith('bet_')
            or a == 'raise'
            or a.startswith('raise_')
            or a == 'all_in'
            for a in valid_actions
        )

        vvs_intensity_raw = 0.0
        if (
            hand_strength
            in {
                HandStrengthClass.STRONG_MADE.value,
                HandStrengthClass.NUTS.value,
            }
            and call_amount == 0
            and has_bet_legal
        ):
            vvs_intensity_raw = compute_value_vs_station_intensity(spots)
        vvs_intensity_used = vvs_intensity_raw if is_value_vs_station_enabled(archetype) else 0.0

        # Plan §5: bluff reduction vs stations. Mirrors value_vs_station
        # but with the inverse hand-strength gate — fires on air-class
        # hands when a station is in the field. Shares the same station
        # detection (compute_value_vs_station_intensity returns >0 iff a
        # qualifying station is present), so reusing it keeps the
        # "what's a station" definition consistent. Hand-strength gate
        # below disjoint from vvs's strong+ gate; the two rules cannot
        # fire on the same decision.
        bluff_reduction_intensity_raw = 0.0
        if hand_strength in {'air_no_draw', 'air_strong_draw'} and has_bet_legal:
            bluff_reduction_intensity_raw = compute_value_vs_station_intensity(spots)
        # Re-use the value_vs_station playstyle gate — same archetypes
        # benefit (nit/rock/tag postflop archetypes that face stations).
        bluff_reduction_intensity_used = (
            bluff_reduction_intensity_raw if is_value_vs_station_enabled(archetype) else 0.0
        )

        exploitation_strength = getattr(self, 'exploitation_strength', 1.0)
        # Phase 8.1c: pass through whether at least one continuing
        # non-all-in opponent is station-like. Gates the base
        # hyper_passive rule against misfiring when the stake-weighted
        # aggregate looks station-y purely because an all-in station
        # dominated the weight. Reuses compute_value_vs_station_intensity
        # — it returns >0 iff a continuing non-all-in opponent passes
        # _is_hyper_passive with adequate sample.
        non_all_in_station_continuing = compute_value_vs_station_intensity(spots) > 0.0
        offsets, exploitation_traces = compute_exploitation_offsets_with_traces(
            stats=stats,
            adaptation_bias=anchors.adaptation_bias,
            decision_context=decision_context,
            available_actions=list(strategy.action_probabilities.keys()),
            tilt_factor=tilt_factor,
            exploitation_strength=exploitation_strength,
            multiway_cbet_intensity=multiway_cbet_intensity,
            value_vs_station_intensity=vvs_intensity_used,
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
        self._last_phase_8_will_emit = phase_8_will_emit
        self._last_exploitation_archetype = archetype

        # Diagnostic counters: track detection vs firing per rule. Useful
        # for sim runs to see if exploitation is actually engaging.
        self._tally_exploitation_event(
            stats,
            offsets,
            decision_context,
            spots=spots,
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
            logger.info(f"[TIERED_BOT] {self.player_name}: " f"exploitation offsets={offsets}")

        # Phase 7.5 Item 2c: route the L1 clamp through _determine_clamp,
        # replacing the legacy two-tier _pick_max_total_shift. Tier is
        # determined by opponent's postflop signal axes (AF_postflop OR
        # all_in_per_facing_bet OR postflop_jam_open_rate) with the
        # sliding-window ratchet-down applied when recent stats diverge.
        clamp_value, clamp_tier, winning_axis = self._compute_clamp(
            stats,
            manager,
            primary_spot,
        )

        # Stash tier diagnostic for downstream callers / capture.
        self._last_clamp_tier = clamp_tier
        self._last_clamp_axis = winning_axis

        # Phase 7.6 Step 6: snapshot exploitation inputs for replay.
        clamp_tier_label = (
            clamp_tier.value.lower() if hasattr(clamp_tier, 'value') else str(clamp_tier).lower()
        )
        self._snapshot_exploitation_inputs(
            stats=stats,
            decision_context=decision_context,
            adaptation_bias=anchors.adaptation_bias,
            tilt_factor=tilt_factor,
            exploitation_strength=exploitation_strength,
            multiway_cbet_intensity=multiway_cbet_intensity,
            vvs_intensity_used=vvs_intensity_used,
            clamp_value=clamp_value,
            clamp_tier_label=clamp_tier_label,
        )

        updated_strategy = apply_exploitation_offsets(
            strategy=strategy,
            offsets=offsets,
            legal_actions=valid_actions,
            max_total_shift=clamp_value,
        )

        # Stop-bluffing-vs-station HARD OVERRIDE (the behavioral half).
        # The bluff_reduction OFFSET above is a soft logit nudge; measured
        # behaviorally it does NOT change the sampled action even at full
        # intensity vs a pure station (bluff rate 59.9%→59.8% — see
        # STRATEGY_REVALIDATION_MATRIX.md). The canonical exploit "a station
        # never folds, so stop bluffing them" needs a REAL range change, like
        # value_override / the all-in veto: hard-set the give-up line rather
        # than nudge it.
        updated_strategy = self._maybe_stop_bluff_override(
            updated_strategy,
            valid_actions=valid_actions,
            hand_strength=hand_strength,
            bluff_reduction_intensity=bluff_reduction_intensity_used,
            tilt_factor=tilt_factor,
            adaptation_bias=anchors.adaptation_bias,
            exploitation_strength=exploitation_strength,
        )
        return updated_strategy, exploitation_traces

    def _maybe_stop_bluff_override(
        self,
        strategy: 'StrategyProfile',
        *,
        valid_actions: List[str],
        hand_strength,
        bluff_reduction_intensity: float,
        tilt_factor: float,
        adaptation_bias: float,
        exploitation_strength: float,
    ) -> 'StrategyProfile':
        """Hard 'stop bluffing vs a station' override — the behavioral half of
        the bluff_reduction offset.

        Replaces the strategy with a pure give-up line (check if free, else
        fold) when hero holds *pure air* against a confidently-read station
        while composed. Unlike the offset it does not nudge — it removes all
        bet/raise/all-in mass, the only thing that actually moves the sampled
        action (proven: the offset alone leaves the bluff rate unchanged).

        Gates (all required):
          - hand_strength == 'air_no_draw' — pure air, no equity. A draw
            (air_strong_draw) can still legitimately semi-bluff / value-bet a
            station, so it is deliberately excluded.
          - bluff_reduction_intensity >= STOP_BLUFF_MIN_INTENSITY — a confident
            station read (same spot signal the offset uses).
          - exploitation enabled: exploitation_strength > 0 AND
            adaptation_bias > GATING_FLOOR (a recreational persona barely
            adapts; an exploit-OFF twin never does).
          - COMPOSED: tilt_factor >= 1.0. You can't be on tilt and out-
            levelling someone — a tilted bot reverts to its base line.
        """
        if hand_strength != 'air_no_draw':
            return strategy
        if bluff_reduction_intensity < STOP_BLUFF_MIN_INTENSITY:
            return strategy
        if exploitation_strength <= 0.0 or adaptation_bias <= GATING_FLOOR:
            return strategy
        if tilt_factor < 1.0:  # not composed → stop adapting, keep base line
            return strategy
        if 'check' in valid_actions:
            give_up = 'check'
        elif 'fold' in valid_actions:
            give_up = 'fold'
        else:
            return strategy  # no give-up line available (shouldn't happen on air)
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if snap is not None:
            snap['stop_bluff_override'] = give_up
        return StrategyProfile(action_probabilities={give_up: 1.0})

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
        eligible = [s for s in spots if s.is_active and not s.is_all_in]
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
        self,
        stats,
        manager,
        primary_spot,
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
        self,
        strategy,
        game_state,
        player_idx,
        valid_actions,
        anchors,
        emotional_state,
        *,
        node,
        hand_strength,
        active_opponent_count: int,
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
            is_rule_disabled,
            make_disabled_trace,
        )

        if is_rule_disabled(
            getattr(self, "disable_rules", frozenset()),
            'induce_override',
            'default',
        ):
            return strategy, make_disabled_trace(
                layer='induce_override',
                rule_id='default',
                layer_order=layer_order_for('induce_override'),
            )

        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None or anchors is None:
            return strategy, make_no_op_trace(
                layer='induce_override',
                rule_id='default',
                layer_order=layer_order_for('induce_override'),
                reason_code='manager_unavailable',
            )

        tilt_factor = self._zone_to_tilt_factor(emotional_state)

        # Reuse value_override's stat selection so both layers see the
        # same aggressor when both gates evaluate the same decision.
        spots = self._build_opponent_spots(game_state, manager)
        stats, primary_spot, _ambiguous = self._select_exploitation_stats_from_spots(
            spots, game_state
        )

        decision_context = self._build_decision_context(
            game_state,
            player_idx,
            primary_aggressor_spot=primary_spot,
        )

        effective_stack_bb = self._compute_effective_stack_bb(
            game_state,
            player_idx,
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
        self,
        strategy,
        game_state,
        player_idx,
        valid_actions,
        anchors,
        emotional_state,
        hand_strength,
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
                layer='strong_hand_override',
                rule_id='default',
                layer_order=layer_order_for('strong_hand_override'),
                reason_code='deferred_to_induce_override',
            )

        # Phase 7.6 Step 5: ablation short-circuit.
        from .strategy.intervention_trace import is_rule_disabled, make_disabled_trace

        if is_rule_disabled(
            getattr(self, "disable_rules", frozenset()), 'strong_hand_override', 'default'
        ):
            return strategy, make_disabled_trace(
                layer='strong_hand_override',
                rule_id='default',
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
        stats, primary_spot, _ambiguous = self._select_exploitation_stats_from_spots(
            spots, game_state
        )

        decision_context = self._build_decision_context(
            game_state,
            player_idx,
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
                f"[TIERED_BOT] {self.player_name}: " f"value_override fired hand={hand_strength}"
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
            threshold = 0.10  # Nit / Rock
        elif looseness < 0.50:
            threshold = 0.15  # TAG (Calling Station also lands here)
        elif looseness <= 0.70:
            threshold = 0.20  # LAG (0.70) — top 20% includes 88/AJo
        else:
            threshold = 0.15  # Maniac (0.85+) — tightened to avoid coinflips
        if is_hand_in_range(canonical_hand, threshold):
            return HandStrengthClass.STRONG.value
        return HandStrengthClass.NOT_STRONG.value

    def _compute_effective_stack_bb(self, game_state, player_idx):
        """Effective stack in big blinds — delegates to `stack_utils`."""
        return effective_stack_bb(game_state, game_state.players[player_idx])

    def _archetype_base_table(self):
        """The 100bb base preflop chart for this archetype. Returns (table, label).

        The loose/station/tight archetypes select a width-tier chart (the table
        carries the VPIP envelope distortion can't reach); everyone else uses
        the shared base table. PREFLOP-only — postflop stays on
        self.strategy_table. getattr guards controllers built via __new__
        (sims/tests) that skipped __init__.
        """
        tables = getattr(self, 'archetype_preflop_tables', None) or {}
        if tables:
            key = self._table_archetype_key()
            tbl = tables.get(key)
            if tbl is not None:
                return tbl, f'6max:{key}'
        return self.strategy_table, '6max'

    # Preflop seat order, early→late (looser). Blinds excluded — a position-blind
    # fish overplays by treating itself as a LATER opener; we don't reshuffle the
    # blinds (they're already the widest defenders) or the opener's position.
    _PBLIND_ORDER = ('UTG', 'HJ', 'CO', 'BTN')

    def _apply_position_blindness(self, node):
        """Shift the hero's preflop seat LATER (looser) by the profile's
        position_blind strength — the recreational 'doesn't respect position'
        leak (opens/defends a BTN-wide range from EP). Returns the node unchanged
        when position_blind is 0 or the seat isn't in the shiftable order (blinds).

        A node-LOOKUP-level reshape: it changes which (looser) chart cell the bot
        reads; distortion + the math/defense floors still layer on top. −EV on
        every hand from every seat → not capped by stack depth (the point).
        """
        strength = getattr(self.deviation_profile, 'position_blind', 0.0) or 0.0
        if strength <= 0:
            return node
        # Skip RFI: shifting the opening seat later just opens a WIDER range,
        # which is extra *stealing* (aggression) — +EV vs a foldy field, the
        # opposite of a fish leak (measured: it HELPED at 100bb). The position
        # leak we want is over-defending from bad position (facing scenarios) +
        # the postflop OOP→IP overplay (_apply_postflop_position_blindness).
        if getattr(node, 'scenario', '') == 'rfi':
            return node
        order = self._PBLIND_ORDER
        try:
            i = order.index(node.position)
        except ValueError:
            return node  # blind or unknown seat — leave it
        shifted = min(i + round(strength * (len(order) - 1)), len(order) - 1)
        if shifted == i:
            return node
        import dataclasses

        return dataclasses.replace(node, position=order[shifted])

    def _apply_postflop_position_blindness(self, node):
        """Overplay out of position: look the postflop chart up as IP when
        actually OOP — the clean −EV positional mistake (c-bet/barrel/float OOP
        like you have position), with no stealing confound. Gated by the
        profile's position_blind strength via the controller rng so it's graded
        (strength = P(collapse this OOP decision to IP)). Node-lookup level, so
        distortion + floors still layer on top. Returns node unchanged when not
        position-blind or already IP.
        """
        strength = getattr(self.deviation_profile, 'position_blind', 0.0) or 0.0
        if strength <= 0 or getattr(node, 'position', '') != 'OOP':
            return node
        if self.rng.random() >= strength:
            return node
        import dataclasses

        return dataclasses.replace(node, position='IP')

    def _table_archetype_key(self) -> str:
        """The archetype key used to pick the width-tier table.

        Prefers the explicit deviation-profile key (reverse-looked-up from the
        bound `_deviation_profile`), which is what handles loadouts like
        `weak_fish` that are NOT reachable via anchor classification
        (`archetype_name` would mis-classify a weak_fish's loose-passive anchors
        as `calling_station`). Falls back to `archetype_name` when no profile is
        bound yet (the 6 anchor-derived archetypes, where the two agree).
        """
        prof = getattr(self, '_deviation_profile', None)
        if prof is None:
            # Trigger lazy resolution — the `deviation_profile` property populates
            # `_deviation_profile` with the BASE archetype object (the property
            # itself may return a spot_tendencies `replace()` copy, which is why
            # we reverse-look-up the raw base, not the property: an `is` check
            # against a copy is the bug that mislabels personas as 'unknown').
            try:
                _ = self.deviation_profile
                prof = getattr(self, '_deviation_profile', None)
            except Exception:
                prof = None
        if prof is not None:
            from .strategy.deviation_profiles import DEVIATION_PROFILES

            for k, v in DEVIATION_PROFILES.items():
                if v is prof:
                    return k
        return self.archetype_name

    def _select_preflop_table(self, num_seated, effective_stack_bb):
        """Pick the preflop chart for this spot. Returns (table, label).

        - 2-handed: the HU chart (depth selection is 6-max-only for now —
          the HU chart has no shallow variants, and short stacks there are
          covered by the push/fold chart at the lookup step).
        - 6-max/multiway, archetype WITH a width-tier chart (loose / station /
          weak / tight): that chart, at EVERY depth. The archetype's looseness is
          its identity — a fish/maniac must not collapse to the standard depth
          chart at the shallow casino buy-in (~40bb), and the math/defense floors
          handle pot-commitment shallow. (This is why the width table wins over
          the depth chart — casino fish sit at ~40bb.)
        - 6-max/multiway, archetype WITHOUT a width chart (tag / baseline — the
          depth-aware competent bot): the shallow depth chart nearest the
          effective stack (50/25bb) when available, else the base table.
        """
        if num_seated == 2 and self.hu_strategy_table is not None:
            return self.hu_strategy_table, 'HU'
        base, base_label = self._archetype_base_table()
        # A width-tier archetype chart takes precedence at every depth (label is
        # '6max:<key>'); only archetypes with NO width chart ('6max') fall through
        # to the depth charts.
        if base_label != '6max':
            return base, base_label
        # getattr default keeps controllers built by bypassing __init__
        # (test fixtures, factories) working with no depth adjustment.
        depth_tables = getattr(self, 'depth_strategy_tables', None) or {}
        if not depth_tables:
            return base, base_label
        bucket = nearest_depth_bucket(effective_stack_bb)
        table = depth_tables.get(bucket)
        if table is None:  # 100bb (base supplied by caller) or missing bucket
            return base, f'6max@{bucket}bb'
        return table, f'6max@{bucket}bb'

    def _classify_postflop_hand_strength(self, node):
        """Map PostflopNode → simplified hand class string ('nuts',
        'strong_made', 'medium_made', etc.). Reuses the same classifier
        used by the river bluff guardrail.
        """
        return simplify_hand_class(node.made_tier, node.draw_modifier)

    def _apply_bluff_catch_override(
        self,
        strategy,
        game_state,
        player_idx,
        valid_actions,
        anchors,
        emotional_state,
        hand_strength,
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

        if is_rule_disabled(
            getattr(self, "disable_rules", frozenset()), 'bluff_catch_override', 'default'
        ):
            return strategy, make_disabled_trace(
                layer='bluff_catch_override',
                rule_id='default',
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
        stats, primary_spot, _ambiguous = self._select_exploitation_stats_from_spots(
            spots, game_state
        )

        decision_context = self._build_decision_context(
            game_state,
            player_idx,
            primary_aggressor_spot=primary_spot,
        )

        # Re-compute clamp (with recent_stats from the primary aggressor's
        # sliding window) to determine if EXTREME tier is active.
        # _compute_clamp was added in Item 2c.
        clamp_value, clamp_tier, _winning_axis = self._compute_clamp(
            stats,
            manager,
            primary_spot,
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
            tier_label=clamp_tier.value.lower()
            if hasattr(clamp_tier, 'value')
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

    def _apply_sizing_defense(
        self,
        strategy,
        game_state,
        player_idx,
        valid_actions,
        anchors,
        hand_strength,
        prior_layer_fired: bool,
    ) -> Tuple['StrategyProfile', InterventionTrace]:
        """Phase B (SIZING_AWARE_OPPONENT_MODELING.md §B): fold MORE marginal
        bluff-catchers to a detected FACE-UP value bettor's big bet.

        The dual of bluff_catch — but gated on the bettor's matured, face-up
        `sizing_polarization_score` at the DEFAULT clamp tier, NOT the vpip/AF-
        driven EXTREME tier (which would re-trap the effect in the sizing dead
        zone — the read is orthogonal to aggression frequency). Default OFF
        (`sizing_defense_enabled`) → emits a no-op trace, byte-identical.

        Defers (no-op) when an earlier facing-bet override already replaced the
        distribution (`prior_layer_fired`) so the two don't compound.
        """
        from .strategy.intervention_trace import is_rule_disabled, make_disabled_trace

        if is_rule_disabled(
            getattr(self, "disable_rules", frozenset()), 'sizing_defense', 'default'
        ):
            return strategy, make_disabled_trace(
                layer='sizing_defense',
                rule_id='default',
                layer_order=layer_order_for('sizing_defense'),
            )

        def _noop(reason: str):
            return strategy, make_no_op_trace(
                layer='sizing_defense',
                rule_id='default',
                layer_order=layer_order_for('sizing_defense'),
                reason_code=reason,
            )

        if not getattr(self, 'sizing_defense_enabled', False):
            return _noop('disabled')
        if prior_layer_fired:
            return _noop('prior_layer_fired')
        if anchors is None:
            return _noop('manager_unavailable')
        # Cheap gate first: only marginal made hands bluff-catch.
        if hand_strength not in BLUFF_CATCH_TRIGGER_CLASSES:
            return _noop('hand_class_not_eligible')

        decision_context = self._build_decision_context(game_state, player_idx)
        bet_ratio = getattr(decision_context, 'bet_size_pot_ratio', 0.0) or 0.0
        if bet_ratio < getattr(self, 'sizing_defense_min_bet_ratio', 0.75):
            return _noop('not_a_big_bet')

        polar = self._resolve_sizing_defense_polar(game_state)
        if polar is None:
            return _noop('no_mature_read')
        if polar < getattr(self, 'sizing_defense_min_polar', 0.15):
            return _noop('not_face_up')

        override, trace = compute_sizing_defense_strategy(
            strategy,
            polar_score=polar,
            min_polar=getattr(self, 'sizing_defense_min_polar', 0.15),
            full_polar=getattr(self, 'sizing_defense_full_polar', 0.40),
            call_multiplier_floor=getattr(self, 'sizing_defense_call_multiplier', 0.55),
            bet_ratio=bet_ratio,
            hand_strength=hand_strength,
            max_total_shift=DEFAULT_MAX_TOTAL_SHIFT,
            legal_actions=valid_actions,
            disable_rules=getattr(self, "disable_rules", frozenset()),
        )

        if self.debug_logging:
            logger.info(
                f"[TIERED_BOT] {self.player_name}: SIZING-DEFENSE {hand_strength} "
                f"vs face-up bettor (polar={polar:+.2f}) @ bet_ratio={bet_ratio:.2f} "
                f"→ {dict(override.action_probabilities)}"
            )

        return override, trace

    def _tally_playstyle_rule_event(self):
        """Diagnostic counters for the playstyle-gated value_vs_station rule.

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

        # Reset per-decision stash so the next decision starts clean.
        # Without this, an early-out _apply_exploitation could leave
        # stale intensities visible to the next tally call.
        self._last_value_vs_station_intensity_raw = 0.0
        self._last_value_vs_station_intensity_used = 0.0
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
        self,
        stats,
        offsets,
        decision_context,
        spots=None,
        ambiguous_aggressor=False,
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
                        s.stats.fold_to_cbet > 0.60 and s.stats.cbet_faced_count >= 5
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
                        cbet_fired = any(a.startswith('bet_') or a == 'check' for a in offsets)
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
            cbet_fired = any(a.startswith('bet_') or a == 'check' for a in offsets)
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

            is_blind = (sb_idx is not None and i == sb_idx) or (bb_idx is not None and i == bb_idx)

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
                                t,
                                'cbet_attempt_rate',
                                0.5,
                            ),
                            postflop_seen_as_pfr_count=getattr(
                                t,
                                '_postflop_seen_as_pfr_count',
                                0,
                            ),
                            # Phase B Item 1 barrel fields.
                            barrel_frequency=getattr(
                                t,
                                'barrel_frequency',
                                0.5,
                            ),
                            barrel_opportunities=getattr(
                                t,
                                '_barrel_opportunity_count',
                                0,
                            ),
                            third_barrel_frequency=getattr(
                                t,
                                'third_barrel_frequency',
                                0.5,
                            ),
                            third_barrel_opportunities=getattr(
                                t,
                                '_third_barrel_opportunity_count',
                                0,
                            ),
                            # Phase 7.5 Step 0 fields — populated for
                            # diagnostic visibility. Item 2 consumes them
                            # for tier classification.
                            aggression_factor_postflop=t.aggression_factor_postflop,
                            all_in_per_facing_bet=t.all_in_per_facing_bet,
                            facing_bet_opportunities=t._facing_bet_opportunities,
                            call_rate_facing_bet=getattr(t, 'call_rate_facing_bet', 0.0),
                            wtsd=getattr(t, 'wtsd', 0.0),
                            postflop_jam_open_rate=t.postflop_jam_open_rate,
                            postflop_open_opportunities=t._postflop_open_opportunities,
                            # Opportunity-normalized preflop fields.
                            # getattr-with-default so SimpleNamespace test
                            # mocks built before this field landed still
                            # work (they fall back to neutral prior / 0).
                            pfr_per_open_opportunity=getattr(
                                t,
                                'pfr_per_open_opportunity',
                                0.5,
                            ),
                            vpip_per_voluntary_opportunity=getattr(
                                t,
                                'vpip_per_voluntary_opportunity',
                                0.5,
                            ),
                            preflop_open_opportunities=getattr(
                                t,
                                '_preflop_open_opportunities',
                                0,
                            ),
                            preflop_voluntary_opportunities=getattr(
                                t,
                                '_preflop_voluntary_opportunities',
                                0,
                            ),
                            # Polarization Phase A equity-at-action fields.
                            # getattr-with-default for SimpleNamespace
                            # tests predating the field.
                            equity_when_betting_postflop=getattr(
                                t,
                                'equity_when_betting_postflop',
                                0.5,
                            ),
                            equity_when_raising_postflop=getattr(
                                t,
                                'equity_when_raising_postflop',
                                0.5,
                            ),
                            equity_when_calling_postflop=getattr(
                                t,
                                'equity_when_calling_postflop',
                                0.5,
                            ),
                            _equity_betting_count=getattr(
                                t,
                                '_equity_betting_count',
                                0,
                            ),
                            _equity_raising_count=getattr(
                                t,
                                '_equity_raising_count',
                                0,
                            ),
                            _equity_calling_count=getattr(
                                t,
                                '_equity_calling_count',
                                0,
                            ),
                        )

            spots.append(
                OpponentSpot(
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
                )
            )
        return spots

    def _resolve_river_bluff_ftbb(self, game_state) -> Optional[float]:
        """Resolve the opponent read that gates the river bluff (OVERBET_BALANCING
        T2 regime gate). Returns the continuing opponent's `fold_to_big_bet` when
        there is a single mature read, else None (→ value-only, the safe default).

        - `river_bluff_ftbb_override` (eval/tests, no model manager) wins outright.
        - Production: HU only for the MVP — multiway returns None (don't bluff into
          a field we can't read). Requires a matured read (`_big_bet_faced_count`)
          so a cold start stays value-only rather than bluffing on a neutral prior.
        """
        override = getattr(self, 'river_bluff_ftbb_override', None)
        if override is not None:
            return override
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return None
        opps = [
            p.name for p in game_state.players if p.name != self.player_name and not p.is_folded
        ]
        if len(opps) != 1:  # MVP: HU only; multiway → don't fire (safe)
            return None
        try:
            tendencies = manager.get_model(self.player_name, opps[0]).tendencies
        except Exception:  # noqa: BLE001 — no model yet → cold start
            return None
        if getattr(tendencies, '_big_bet_faced_count', 0) < 8:
            return None  # immature read → value-only
        return tendencies.fold_to_big_bet

    def _resolve_sizing_defense_polar(self, game_state) -> Optional[float]:
        """Resolve the bettor's `sizing_polarization_score` that gates Phase B
        (SIZING_AWARE_OPPONENT_MODELING.md §B). Returns the score when there is a
        single MATURED read of the player who made the bet hero is facing, else
        None (no defense, the safe default — call/fold per the base policy).

        - `sizing_defense_polar_override` (eval/tests, no model manager) wins.
        - Production: requires BOTH size bins matured (>= SIZING_MIN_BIN_SAMPLE) —
          the score is big_bet_eq minus small_bet_eq, meaningless until each bin
          has a sample — so a cold start stays neutral, not folding on a prior.
        """
        override = getattr(self, 'sizing_defense_polar_override', None)
        if override is not None:
            return override
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return None
        aggressor = self._identify_recent_aggressor(game_state)
        if not aggressor:
            return None
        try:
            tendencies = manager.get_model(self.player_name, aggressor).tendencies
        except Exception:  # noqa: BLE001 — no model yet -> cold start
            return None
        from .memory.opponent_model import SIZING_MIN_BIN_SAMPLE

        big_n = getattr(tendencies, '_equity_betting_big_count', 0)
        small_n = getattr(tendencies, '_equity_betting_small_count', 0)
        if big_n < SIZING_MIN_BIN_SAMPLE or small_n < SIZING_MIN_BIN_SAMPLE:
            return None  # immature read -> no defense
        # Kill switch (Surface B `stability`): if the face-up tell is going stale
        # (recent big bets weakening = they've started bluffing big), stop folding
        # into it rather than feeding an adapting adversary.
        if hasattr(tendencies, 'sizing_tell_is_mixing') and tendencies.sizing_tell_is_mixing():
            return None
        return tendencies.sizing_polarization_score

    def _resolve_stabber_read(self, game_state) -> Optional[float]:
        """Resolve the opponent read that gates the stab-defense (OVERBET_BALANCING
        §5j): the opponent's stab frequency (how often it bets when checked to).
        `stab_defense_override` (eval/tests) wins. Production read is not yet
        tracked on OpponentTendencies — until it is, returns the override or None
        (no stab-defense, the safe value-only default)."""
        override = getattr(self, 'stab_defense_override', None)
        if override is not None:
            return override
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return None
        opps = [
            p.name for p in game_state.players if p.name != self.player_name and not p.is_folded
        ]
        if len(opps) != 1:  # HU only for the MVP
            return None
        try:
            tendencies = manager.get_model(self.player_name, opps[0]).tendencies
        except Exception:  # noqa: BLE001 — no model yet -> cold start
            return None
        if getattr(tendencies, '_stab_opp_count', 0) < 12:
            return None  # immature read -> no stab-defense (value-only)
        return tendencies.stab_frequency

    def _select_exploitation_stats(
        self,
        game_state,
        manager,
        hero_name,
        active_opponents,
        money_committed,
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
                            t,
                            'pfr_per_open_opportunity',
                            0.5,
                        ),
                        vpip_per_voluntary_opportunity=getattr(
                            t,
                            'vpip_per_voluntary_opportunity',
                            0.5,
                        ),
                        preflop_open_opportunities=getattr(
                            t,
                            '_preflop_open_opportunities',
                            0,
                        ),
                        preflop_voluntary_opportunities=getattr(
                            t,
                            '_preflop_voluntary_opportunities',
                            0,
                        ),
                    )
        return manager.aggregate_active_opponents(
            observer=hero_name,
            active_opponents=active_opponents,
            money_committed=money_committed,
        )

    def _select_exploitation_stats_from_spots(
        self,
        spots,
        game_state,
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

    def _facing_all_in_preflop_veto(
        self,
        game_state,
        player_idx: int,
        valid_actions: List[str],
    ) -> Optional[Tuple[StrategyProfile, str, float, float]]:
        """Pot-odds call/fold override when facing a cold all-in preflop.

        The chart's `vs_3bet`/`vs_4bet` rows are a coarse stub (3 distinct
        distributions across 169 hands) that can sample a trash JAM/CALL facing
        a shove — the root cause of the prod "47o jams into a 4-bet all-in"
        bug. Facing an all-in there is no skill in re-jamming vs calling (every
        live player is already committed), so the decision collapses to pure
        pot odds. We make that call/fold decision on equity here and skip the
        downstream distortion layers that would otherwise push a correct fold
        back toward a jam.

        Returns `(profile, abstract_action, equity, required_equity)` when it
        fires — a pure `{action: 1.0}` profile where `action` is `'call'` or
        `'fold'`. Returns None when not facing an all-in, when the
        pot-odds/equity can't be computed, or when neither call nor fold is
        legal (caller keeps the normal chart path).

        Never emits a *voluntary* re-jam. The continue action is always the
        abstract `'call'`; `resolve_preflop_sizing` (passed valid_actions) turns
        it into all_in only when calling the shove is itself a call-off (call
        illegal, only all_in legal) — i.e. jam == call mechanically. When hero
        covers the shove it stays a flat 'call' — the over-commit the stub got
        wrong.
        """
        ctx = self._build_decision_context(game_state, player_idx)
        if not (ctx.is_preflop and ctx.facing_all_in):
            return None
        required = ctx.required_equity
        if required is None:
            return None

        player = game_state.players[player_idx]
        hole = [card_to_string(c) for c in player.hand] if player.hand else []
        if len(hole) != 2:
            return None

        equity = _preflop_allin_equity(hole, ctx.active_opponent_count)
        if equity is None:
            return None

        if equity >= required:
            # Continue. Emit the abstract 'call'; resolve_preflop_sizing turns
            # it into all_in iff calling is a call-off (call illegal, only
            # all_in legal). Bail only if neither continue action is legal.
            if 'call' not in valid_actions and 'all_in' not in valid_actions:
                return None
            action = 'call'
        else:
            if 'fold' not in valid_actions:
                return None
            action = 'fold'

        return (
            StrategyProfile(action_probabilities={action: 1.0}),
            action,
            equity,
            required,
        )

    def _build_decision_context(
        self,
        game_state,
        player_idx,
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
                    if opponent_bet == highest_opponent_bet and getattr(p, 'stack', 1) <= 0:
                        facing_all_in = True
                        break

        facing_big_bet = (
            not facing_all_in and call_amount > 10 * big_blind and call_amount > pot_total / 2
        )

        active_opponent_count = sum(
            1
            for p in game_state.players
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
            'raise' in valid_actions or 'bet' in valid_actions or 'all_in' in valid_actions
        )
        is_flop_as_preflop_aggressor = (
            is_flop
            and call_amount == 0
            and hero_has_bet_raise
            and self._last_preflop_aggressor() == hero_name
        )

        facing_aggressor_name = (
            primary_aggressor_spot.name if primary_aggressor_spot is not None else None
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
                from .board_analyzer import (
                    analyze_board_texture,
                    classify_texture_bucket,
                )
                from .card_utils import card_to_string

                card_strs = [c if isinstance(c, str) else card_to_string(c) for c in community]
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

    def _vs3bet_bluff_fraction(self, hero: str, villain: str, preflop_table):
        """Villain's 3-bet bluff fraction β for this node, cached per
        (table, hero, villain). See _compute_vs3bet_bluff_fraction."""
        cache = getattr(self, '_vs3bet_beta_cache', None)
        if cache is None:
            cache = self._vs3bet_beta_cache = {}
        key = (id(preflop_table), hero, villain)
        if key not in cache:
            cache[key] = _compute_vs3bet_bluff_fraction(preflop_table, hero, villain)
        return cache[key]

    def _apply_vs3bet_bluff_exploit(self, strategy, node, preflop_table):
        """Per-persona bluff-aware vs_3bet exploit (see __init__ `vs3bet_exploit`).

        Against a value-heavy 3-bettor (bluff fraction β below the balanced
        reference) shift `s = (BLUFF_REF − β)·knob·SCALE` of hero's `call` mass to
        `fold`. The shift is a per-hand absolute subtraction, so it folds the thin
        marginal continues (the bottom of the range, at the junk-call floor) almost
        entirely while barely touching core flats, and never touches the value
        4-bet (`raise_2.2x`). No-op outside vs_3bet, with knob 0, or vs a villain
        at/above the balanced bluff reference (don't over-fold vs a polarized
        3-bettor). The base chart stays the GTO/MDF baseline."""
        if getattr(node, 'scenario', '') != 'vs_3bet':
            return strategy
        knob = getattr(self, 'vs3bet_exploit', 0.0)
        if knob <= 0:
            return strategy
        beta = self._vs3bet_bluff_fraction(node.position, node.opener_position, preflop_table)
        if beta is None or beta >= VS3BET_BLUFF_REF:
            return strategy
        s = (VS3BET_BLUFF_REF - beta) * knob * VS3BET_EXPLOIT_SCALE
        probs = dict(strategy.action_probabilities)
        call = probs.get('call', 0.0)
        new_call = max(0.0, call - s)
        moved = call - new_call
        if moved <= 0:
            return strategy
        probs['call'] = new_call
        probs['fold'] = probs.get('fold', 0.0) + moved
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if isinstance(snap, dict):
            snap['vs3bet_bluff_exploit'] = {
                'beta': round(beta, 4),
                'knob': knob,
                'call_to_fold_shift': round(moved, 4),
            }
        return StrategyProfile(action_probabilities=probs)

    def _foldy_limper_read(self, game_state, player_idx) -> float:
        """Foldiness ∈ [0,1] of a SINGLE weak limper in front, else 0.0 (no-op).

        Limper = a non-blind, non-all-in opponent who matched the BB without raising
        (raises_this_round == 0). Returns 0 unless there is exactly ONE, with a
        sufficient read, that is a habitual limper (vpip−pfr gap ≥ LIMP_GAP_MIN) and
        NOT a station/jammer — those call the iso, so no fold equity (LIMP_EXPLOIT.md).
        Foldiness = the limp gap (more passive entry → more fold equity to attack).
        """
        if getattr(game_state, 'raises_this_round', 0) != 0:
            return 0.0
        big_blind = getattr(game_state, 'current_ante', 0) or 0
        if big_blind <= 0:
            return 0.0
        limpers = [
            i
            for i, p in enumerate(game_state.players)
            if i != player_idx
            and not getattr(p, 'is_folded', False)
            and get_6max_position(game_state, i) != 'BB'
            and getattr(p, 'bet', 0) >= big_blind
            and getattr(p, 'stack', 1) > 0
        ]
        if len(limpers) != 1:
            return 0.0  # v1 models a single limper
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return 0.0
        try:
            limper_name = game_state.players[limpers[0]].name
        except (AttributeError, IndexError):
            return 0.0
        model = manager.get_model(self.player_name, limper_name)
        t = getattr(model, 'tendencies', None)
        if t is None or getattr(t, 'hands_observed', 0) < MIN_HANDS_DEFAULT:
            return 0.0
        stats = AggregatedOpponentStats(
            hands_observed=t.hands_observed,
            all_in_frequency=getattr(t, 'all_in_frequency', 0.0),
            vpip_per_voluntary_opportunity=getattr(t, 'vpip_per_voluntary_opportunity', 0.5),
            pfr_per_open_opportunity=getattr(t, 'pfr_per_open_opportunity', 0.5),
            aggression_factor=getattr(t, 'aggression_factor', 1.0),
        )
        if classify_opponent_archetype(stats) in ('pure_station', 'sticky_jammer'):
            return 0.0  # calls the iso → no fold equity → value-only (v1 no-op)
        gap = stats.vpip_per_voluntary_opportunity - stats.pfr_per_open_opportunity
        if gap < LIMP_GAP_MIN:
            return 0.0  # not a habitual limper
        return max(0.0, min(1.0, gap))

    def _apply_limp_exploit(self, strategy, node, game_state, player_idx):
        """Punish-limpers: iso-RAISE a single weak foldy limper.

        No-op unless: flag on, knob>0, scenario rfi (a limped pot classifies as rfi),
        the hand is worth iso-raising (`_is_iso_hand` — pair / suited / offsuit
        broadway; NEVER iso junk), and `_foldy_limper_read` finds one foldy habitual
        limper. Converts a capped slice of PASSIVE give-up mass — `check` (the BB
        checking its option, the measured ~100% spot) OR a folded open — into the
        iso-raise (`raise_2.5bb`, injected if the node has no raise), scaled by
        knob·foldiness. vs a sticky limper the read gate no-ops (no fold equity).
        Base chart stays the baseline. See LIMP_EXPLOIT.md."""
        knob = getattr(self, 'limp_exploit', 0.0)
        if knob <= 0 or getattr(node, 'scenario', '') != 'rfi':
            return strategy
        if not _limp_exploit_enabled():
            return strategy
        if not _is_iso_hand(getattr(node, 'hand', '')):
            return strategy
        foldiness = self._foldy_limper_read(game_state, player_idx)
        if foldiness <= 0:
            return strategy
        probs = dict(strategy.action_probabilities)
        # Passive give-up mass: check (BB option) and/or a folded open. Convert a
        # slice into the iso-raise — reuse an existing raise action, else inject one.
        passive = probs.get('check', 0.0) + probs.get('fold', 0.0)
        if passive <= 0:
            return strategy
        raise_key = next(
            (a for a in probs if a.startswith('raise') or a == 'jam'),
            LIMP_ISO_RAISE_KEY,
        )
        s = min(LIMP_EXPLOIT_MAX_SHIFT, knob * LIMP_EXPLOIT_SCALE * foldiness)
        moved = passive * s
        if moved <= 0:
            return strategy
        for k in ('check', 'fold'):  # drain proportionally from the passive actions
            if probs.get(k, 0.0) > 0:
                probs[k] = probs[k] - moved * (probs[k] / passive)
        probs[raise_key] = probs.get(raise_key, 0.0) + moved
        snap = getattr(self, '_last_pipeline_snapshot', None)
        if isinstance(snap, dict):
            snap['limp_exploit'] = {
                'knob': knob,
                'foldiness': round(foldiness, 3),
                'passive_to_raise_shift': round(moved, 4),
                'raise_action': raise_key,
            }
        return StrategyProfile(action_probabilities=probs)

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
        table should handle it (deep stacks, out-of-scope spot, etc.).

        Scope: effective stack <= 15 BB.
          - HU (num_seated == 2)        -> HU chart (SB open / BB call-vs-jam).
          - 3-6 handed (num_seated > 2) -> 6max chart (per-position unopened
            jams + the bb_vs_sb / bb_vs_late caller tables). 7+ handed is out of
            the chart's calibration and falls through (the lookup gates it).
        Spots not covered (a non-blind hero facing a non-all-in raise, a BB
        walk, etc.) fall through to the deep-stack / short_stack.py path.

        Gated on the per-persona `push_fold_nash` opt-in (see __init__): only
        blessed "skilled" characters use the Nash charts; everyone else falls
        through so the donors keep their leaky-but-human short game. Sims/tests
        build via __new__ and don't set the attribute, so they default to True.
        """
        if not getattr(self, 'push_fold_nash_enabled', True):
            return None

        # Effective stack in big blinds (shared by both paths). Routed through
        # the shared stack_utils helper (total = stack + committed bet) so the
        # HU and multi-way paths can't drift from the deep-stack accounting.
        try:
            big_blind = game_state.current_ante or 0
            if big_blind <= 0:
                return None
            player = game_state.players[player_idx]
            if not any(
                not getattr(p, 'is_folded', False)
                for i, p in enumerate(game_state.players)
                if i != player_idx
            ):
                return None  # no active opponent → not a push/fold spot
            eff_bb = effective_stack_bb(game_state, player, big_blind=big_blind)
        except (AttributeError, ZeroDivisionError, TypeError):
            return None

        if eff_bb > PUSH_FOLD_THRESHOLD_BB:
            return None

        if num_seated == 2:
            return self._try_push_fold_hu(canonical_hand, game_state, player_idx, big_blind, eff_bb)
        return self._try_push_fold_6max(
            canonical_hand, game_state, player_idx, num_seated, big_blind, eff_bb
        )

    def _try_push_fold_hu(
        self,
        canonical_hand: str,
        game_state,
        player_idx: int,
        big_blind: float,
        eff_bb: float,
    ) -> Optional[str]:
        """HU short-stack push/fold (SB open / BB call-vs-SB-jam)."""
        try:
            if player_idx == game_state.small_blind_idx:
                position = 'SB'
            elif player_idx == game_state.big_blind_idx:
                position = 'BB'
            else:
                return None
        except AttributeError:
            return None

        # Is hero facing a jam? BB facing an SB all-in is the only HU spot
        # where the chart's bb_vs_jam scenario fires.
        facing_jam = False
        if position == 'BB':
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
            effective_stack_bb=eff_bb,
            num_opponents=1,
            facing_jam=facing_jam,
        )

    def _try_push_fold_6max(
        self,
        canonical_hand: str,
        game_state,
        player_idx: int,
        num_seated: int,
        big_blind: float,
        eff_bb: float,
    ) -> Optional[str]:
        """Multi-way short-stack push/fold via the 6max chart.

        Three in-scope spots:
          1. Unopened, truly first-in (folded to hero, no raise AND no limper):
             hero (UTG/HJ/CO/BTN/SB) jams or folds from the `unopened` chart.
             BB unopened (a walk) isn't in the chart -> None.
          2. BB facing a SINGLE all-in with no larger live raise on top of it:
             call or fold from the caller tables -- bb_vs_sb when the jammer is
             the SB, else bb_vs_late. The caller tables are BB-vs-jam only, so a
             non-BB hero facing a jam falls through.
          3. Reshove: hero facing a SINGLE non-all-in open jams or folds from the
             `reshove` table. Gated behind PUSH_FOLD_6MAX_RESHOVE_ENABLED (the
             ranges are [L]); detection lives in the shared, controller-agnostic
             `reshove_action_6max`, which fail-closes on 3-bet+ wars, cold-caller
             multiway, and limped pots.

        Returns None for any other multi-way short-stack spot (limped / iso
        pots, a 3-bet+ war, a short all-in under a larger live raise, 2+
        opponents already all-in, or reshove with the flag off) so the
        deep-stack / short_stack.py path keeps handling them.
        """
        position = get_6max_position(game_state, player_idx)

        active_opps = [
            (i, p)
            for i, p in enumerate(game_state.players)
            if i != player_idx and not getattr(p, 'is_folded', False)
        ]

        # All-in opponents to face on this street: active opponents with 0 stack
        # remaining whose committed bet exceeds the big blind.
        jammer_indices = [
            i
            for i, p in active_opps
            if getattr(p, 'stack', 1) == 0 and getattr(p, 'bet', 0) > big_blind
        ]

        # The caller tables (bb_vs_sb / bb_vs_late) model a SINGLE jammer. A
        # multi-way all-in (2+ opponents already jammed) is a distinct, tighter
        # spot the v1 chart doesn't represent — applying a single-jammer range
        # there would over-call. Defer it (like reshove) to the deep-stack /
        # short_stack.py path rather than return a wrong range.
        if len(jammer_indices) > 1:
            return None

        if jammer_indices:
            jammer_idx = jammer_indices[0]
            jammer_bet = getattr(game_state.players[jammer_idx], 'bet', 0)
            # If a LIVE (non-all-in) raise tops the all-in, hero is facing that
            # raise — the short all-in is just a covered side-pot, not the jam
            # the caller table models. That's a facing-a-raise / reshove spot
            # (v2) → fall through.
            highest_opp_bet = max((getattr(p, 'bet', 0) for _, p in active_opps), default=0)
            if highest_opp_bet > jammer_bet:
                return None
            opener_position = get_6max_position(game_state, jammer_idx)
            return lookup_push_fold_action_6max(
                hand=canonical_hand,
                position=position,
                effective_stack_bb=eff_bb,
                num_players=num_seated,
                facing_jam=True,
                opener_position=opener_position,
            )

        # Unopened = truly first-in (folded to hero). Two things break that and
        # must fall through, since the unopened chart assumes no prior action:
        #   - a raise (raises_this_round > 0), or
        #   - a limper: a non-blind opponent who voluntarily matched the BB
        #     without raising. A call doesn't bump raises_this_round (see
        #     poker_game.player_call), so over-limp / iso spots would otherwise
        #     wrongly get first-in jam ranges.
        if getattr(game_state, 'raises_this_round', 0) != 0:
            # Facing a live (non-all-in) raise — not first-in. Reshove (jam over
            # a single open) is the in-scope spot here, gated behind
            # PUSH_FOLD_6MAX_RESHOVE_ENABLED; reshove_action_6max fail-closes on
            # 3-bet wars / cold-callers / multiway (returns None → fall through).
            # The fold-equity gate declines reshoves vs openers who won't fold
            # (stations/maniacs) — reshoving them is pure spew (-EV, validated).
            if _reshove_6max_enabled():
                return reshove_action_6max(
                    canonical_hand,
                    game_state,
                    player_idx,
                    num_seated,
                    big_blind,
                    eff_bb,
                    opener_fold_equity_ok=lambda oi: self._opponent_fold_equity_ok(oi, game_state),
                )
            return None
        # Limpers: non-blind opponents who matched the BB without raising. The BB
        # is legitimately in for its blind; any OTHER such opponent is a limper
        # (no all-in here — those took the jam branch).
        limpers = [
            i
            for i, p in active_opps
            if get_6max_position(game_state, i) != 'BB' and getattr(p, 'bet', 0) >= big_blind
        ]
        if limpers:
            # Not truly first-in. With exactly ONE limper (and the flag on) this is
            # a short-stack ISO jam: route to the over-limper range (the unopened
            # jam range in v1 — a conservative, low-spew proxy, far better than the
            # deep-stack chart this spot falls to today). 2+ limpers is a multiway
            # limped field the v1 range doesn't model → fall through.
            # Fold-equity gate (mirrors the reshove): only iso-jam over a limper
            # we read as foldy. A sticky limp-call-wide fish never folds → the jam
            # has zero fold equity (validated -4 to -8 bb/100), so decline and let
            # the deep-stack path play it. No read → decline (conservative).
            if (
                len(limpers) == 1
                and _iso_over_limper_enabled()
                and self._opponent_fold_equity_ok(limpers[0], game_state)
            ):
                return lookup_push_fold_action_6max(
                    hand=canonical_hand,
                    position=position,
                    effective_stack_bb=eff_bb,
                    num_players=num_seated,
                    over_limper=True,
                )
            return None  # multi-limper, flag off, or no fold equity → deep-stack path

        return lookup_push_fold_action_6max(
            hand=canonical_hand,
            position=position,
            effective_stack_bb=eff_bb,
            num_players=num_seated,
            facing_jam=False,
        )

    def _opponent_fold_equity_ok(self, opp_idx: int, game_state) -> bool:
        """Read-based fold-equity gate for a preflop ALL-IN over the opponent at
        `opp_idx` (the opener for a reshove, the limper for an iso-over-limp). True
        only when this hero has a confident read that the opponent folds enough for
        the jam to carry fold equity (see exploitation.reshove_fold_equity_ok — the
        question is the same: loose-VPIP/station opponents never fold to the jam).
        No opponent model, no read, or a station/maniac → False (decline the jam →
        fall through).

        Conservative by design: jamming into an opponent who won't fold is heavily
        -EV (validated: reshove ~-35 bb/100, iso-over-limper ~-4 to -8 bb/100),
        while declining is ~neutral, so the no-read default is False.
        """
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return False
        try:
            opp_name = game_state.players[opp_idx].name
        except (AttributeError, IndexError):
            return False
        model = manager.get_model(self.player_name, opp_name)
        t = getattr(model, 'tendencies', None)
        if t is None:
            return False
        # Only the fields reshove_fold_equity_ok actually reads: hands_observed,
        # vpip_per_voluntary_opportunity, all_in_frequency (the rest default).
        stats = AggregatedOpponentStats(
            hands_observed=t.hands_observed,
            all_in_frequency=t.all_in_frequency,
            vpip_per_voluntary_opportunity=getattr(t, 'vpip_per_voluntary_opportunity', 0.5),
        )
        return reshove_fold_equity_ok(stats)

    def _zone_to_tilt_factor(self, emotional_state) -> float:
        """Map emotional_state -> exploitation-strength multiplier (0..1).

        OFF (default): the original deterministic 3-phase cliff —
        composed 1.0, tilted/overconfident 0.5, shaken/dissociated 0.0.

        ON (TILT_ERRATIC_READS_ENABLED, §4): tilt makes the bot's reads ERRATIC
        instead of cleanly halved. A single random draw per decision (memoized on
        the threaded emotional_state object so every layer in one decision agrees)
        tapers the multiplier with tilt intensity: `factor = 1 - intensity·U(0,1)`.
        So a tilted bot sometimes trusts a read and sometimes loses the plot, with
        no hard 0.0 cliff. Random, not character-keyed (for now). Changes
        decisions -> flag-gated + sim-validated.
        """
        if emotional_state is None:
            return 1.0
        state = getattr(emotional_state, 'state', 'composed')
        if state == 'composed':
            return 1.0
        if not _tilt_erratic_enabled():
            if state in ('shaken', 'dissociated'):
                return 0.0
            if state in ('tilted', 'overconfident'):
                return 0.5
            return 1.0
        # Erratic taper: one draw per decision, memoized on the emotional_state
        # identity (it is computed once per decision and threaded to every layer).
        cache = getattr(self, '_erratic_tilt_cache', None)
        if cache is not None and cache[0] is emotional_state:
            return cache[1]
        intensity = float(getattr(emotional_state, 'intensity', 0.5) or 0.5)
        factor = max(0.0, min(1.0, 1.0 - intensity * self.rng.random()))
        self._erratic_tilt_cache = (emotional_state, factor)
        return factor

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
        self,
        strategy,
        game_state,
        player_idx: int,
        valid_actions: List[str],
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
                if isinstance(getattr(game_state, 'pot', None), dict)
                else 0
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
            logger.warning(f"[TIERED_BOT] {self.player_name}: " f"math_floor failed safely: {e}")
            return strategy, make_no_op_trace(
                layer='math_floor',
                rule_id='default',
                layer_order=layer_order_for('math_floor'),
                reason_code='math_floor_internal_error',
            )

    def _attach_expression(
        self,
        decision: Dict,
        game_state,
        player_idx: int,
        phase: str,
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
            decision,
            game_state,
            player_idx,
            phase,
        )
        self._persist_decision_analysis(
            decision,
            game_state,
            player_idx,
            capture_id=capture_id,
        )

    def _compute_tilt_telegraph(self, emotional) -> str:
        """TILT_EXCURSION_DESIGN.md §4: when the bot has just ENTERED a tilt
        episode, return (with probability TILT_TELEGRAPH_PROB) a loose suggestion
        block for the LLM to voice in its own words — NOT a fixed line. Tracks
        `_was_tilted` so it fires once per entry, not every hand of a long
        episode. Returns '' when the flag is off, not entering tilt, or the roll
        misses. Frequency-neutral (Layer 3 only). A non-empty return is also the
        signal to force a spoken beat (see caller)."""
        # Track the tilt-entry edge UNCONDITIONALLY — before (and independent of)
        # the feature-flag gate. If this only updated while the flag was on, a bot
        # that entered tilt with the flag off would be seen as a *fresh* entry the
        # first time the flag flips on, firing a spurious one-time telegraph for an
        # episode already in progress. Tracking every call closes that runtime-toggle
        # edge. (A cold-loaded mid-tilt bot still telegraphs on its first decision —
        # `_was_tilted` is not serialized — but that is the correct "fire once when
        # first observed tilted" behavior, not a spurious mid-episode fire.)
        state = getattr(emotional, 'state', 'composed') if emotional is not None else 'composed'
        now_tilted = state == 'tilted'
        was_tilted = getattr(self, '_was_tilted', False)
        self._was_tilted = now_tilted
        try:
            from core.feature_flags import is_enabled

            if not is_enabled('TILT_TELEGRAPH_ENABLED'):
                return ''
        except Exception:
            return ''
        if not now_tilted or was_tilted:
            return ''  # not a fresh entry
        if self.rng.random() >= TILT_TELEGRAPH_PROB:
            return ''
        composure_state = getattr(getattr(self, 'psychology', None), 'composure_state', None)
        source = getattr(composure_state, 'pressure_source', '') if composure_state else ''
        nemesis = getattr(composure_state, 'nemesis', None) if composure_state else None
        cause = _TILT_CAUSE_PHRASES.get(source, _TILT_CAUSE_FALLBACK)
        if '{nemesis}' in cause:
            cause = cause.format(nemesis=nemesis or 'them')
        return (
            "=== You're rattled ===\n"
            f"You {cause}, and it's gotten under your skin. Let it leak into your "
            "table talk the way YOUR character would when tilted — your own words, "
            "fresh each time, never a stock line. It might be needling, sulking, "
            "going quiet then snapping, or stubborn bravado. Never quote stats or numbers."
        )

    def _run_expression_layer(
        self,
        decision: Dict,
        game_state,
        player_idx: int,
        phase: str,
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
            from .card_utils import card_to_string
            from .moment_analyzer import MomentAnalyzer

            player = game_state.players[player_idx]
            personality_config = (
                getattr(getattr(self, 'ai_player', None), 'personality_config', {}) or {}
            )

            hand_cards = [card_to_string(c) for c in player.hand] if player.hand else []
            community_cards = (
                [card_to_string(c) for c in game_state.community_cards]
                if game_state.community_cards
                else []
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
                game_state,
                player,
                hand_cards,
                community_cards,
            )

            # Narration gates via the shared parent helper — identical to
            # hybrid/chaos's "when to speak" rolls. Tiered additionally
            # uses should_gesture (energy-driven) so silent characters can
            # still react physically; when BOTH are False we skip the LLM
            # call entirely since the decision already has empty defaults.
            gate = self.compute_narration_gate(game_state, drama_level=drama_level)
            should_speak = gate.should_speak
            should_gesture = gate.should_gesture
            # Tilt telegraph (§4): on a fresh tilt entry, force a spoken beat so
            # the spike is *read*, overriding the chattiness gate (incl. an
            # otherwise-fully-silent turn). Empty string => no telegraph.
            tilt_telegraph = self._compute_tilt_telegraph(emotional)
            if tilt_telegraph:
                should_speak = True
            elif gate.fully_silent:
                return None

            # Opponent narrative observations — surfaced so Layer 3
            # narration can riff on accumulated reads from prior hands.
            # Best-effort: any failure produces an empty list and the
            # generator's prompt template skips the corresponding block.
            # Runs BEFORE _build_narration_facts so the spoken-read it
            # selects (backlog #12 Phase 1) can also feed the
            # narration_facts channel — both channels carry the read.
            opponent_observations = self._select_opponent_observations(
                game_state,
                player,
            )

            # Phase 7.6 Step 5: build NarrationFacts from the per-decision
            # intervention trace. Best-effort — failure here logs WARN and
            # leaves narration_facts as None (LLM falls back to the
            # standard prompt template). Backlog #12 Phase 1: the chosen
            # spoken read is folded in as an always-in-context fact so the
            # "figuring you out" arc persists even on silent hands.
            narration_facts = self._build_narration_facts(
                phase,
                spoken_read=getattr(self, '_last_spoken_read', None),
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
                        p.name
                        for p in game_state.players
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

            # The human's self-description (set per-decision by the game
            # handler). Pre-format here — where the human's name is known —
            # so tiered narration can needle them about it just like chaos and
            # standard do in their decision prompts. Sanitize the section
            # delimiter so a crafted bio can't forge a fake prompt block.
            human_bio_block = ''
            if getattr(self, 'human_bio', ''):
                who = (
                    next(
                        (p.name for p in game_state.players if getattr(p, 'is_human', False)),
                        None,
                    )
                    or "The human player"
                )
                safe_bio = self.human_bio.replace('===', '==')
                human_bio_block = (
                    f"=== About {who} (in their own words) ===\n"
                    f"{safe_bio}\n"
                    "(Feel free to needle them about this at the table.)"
                )

            context = ExpressionContext(
                action_taken=decision['action'],
                raise_to=decision.get('raise_to', 0) or 0,
                hand_cards=hand_cards,
                community_cards=community_cards,
                phase=phase,
                pot_size=getattr(game_state, 'pot_total', 0) or 0,
                opponent_count=max(0, active_count - 1),
                personality_name=personality_config.get('name', self.player_name),
                play_style=personality_config.get('play_style', ''),
                default_attitude=personality_config.get('default_attitude', 'neutral'),
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
                callouts=self.find_callouts(getattr(self, '_current_game_messages', None)),
                should_speak=should_speak,
                should_gesture=should_gesture,
                narration_facts=narration_facts,
                opponent_observations=opponent_observations,
                relationship_context=relationship_context,
                human_bio=human_bio_block,
                human_reputation_tone=getattr(self, 'human_reputation_tone', '') or '',
                tilt_telegraph=tilt_telegraph,
            )

            capture_id_holder = [None]
            narration = self.expression_generator.generate(
                context,
                call_type=getattr(self, '_expression_call_type', None),
                game_id=getattr(self, 'game_id', None),
                owner_id=getattr(self, 'owner_id', None),
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
            logger.warning(f"[TIERED_BOT] {self.player_name}: " f"expression failed safely: {e}")
            return None

        return capture_id_holder[0]

    def _persist_decision_analysis(
        self,
        decision: Dict,
        game_state,
        player_idx: int,
        *,
        capture_id: Optional[int] = None,
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
                all_players_bets=[(p.bet, p.is_folded) for p in game_state.players],
            )
        except Exception as e:
            logger.warning(
                f"[TIERED_BOT] {self.player_name}: " f"decision_analysis persistence failed: {e}"
            )

    def _select_opponent_observations(
        self,
        game_state,
        player,
    ) -> List[Tuple[str, str]]:
        """Best-effort selection of narrative observations for Layer 3.

        Returns up to 2 (opponent_name, observation_text) tuples,
        weighted toward the opponent hero is facing and any nemesis.
        Empty list when the controller has no opponent_model_manager,
        no active opponents, or no stored observations.

        Backlog #12 Phase 1 (perceptibility): the EARNED *spoken read* —
        an intuition-framed "I'm figuring you out" line grounded in the
        opponent model's matured stats — is preferred over the model's
        generic narrative observations, then we backfill from the generic
        observations up to the 2-slot cap. The chosen spoken read is also
        stashed on `self._last_spoken_read` so the narration_facts
        (always-in-context) channel can carry the same arc even on hands
        the bot stays silent. Frequency-neutral: this is post-decision
        Layer-3 narration only.
        """
        # The lead spoken read feeds the narration_facts channel, which —
        # unlike the cooldown-gated speech channel — must PERSIST across the
        # cooldown so the "figuring you out" arc stays in context on gated
        # hands. Capture the prior read; below we keep it only while its
        # opponent is still in the hand, so a stale read can't leak into a
        # table that opponent has left.
        prev_read = getattr(self, '_last_spoken_read', None)
        self._last_spoken_read = None
        manager = getattr(self, 'opponent_model_manager', None)
        if manager is None:
            return []
        try:
            active_opponents = [
                p.name for p in game_state.players if p.name != player.name and not p.is_folded
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

            spoken: List[Tuple[str, str]] = []
            try:
                from .strategy.spoken_reads import (
                    SpokenReadConfig,
                    SpokenReadState,
                    select_spoken_reads,
                )

                if getattr(self, '_spoken_read_state', None) is None:
                    self._spoken_read_state = SpokenReadState()
                if getattr(self, '_spoken_read_config', None) is None:
                    self._spoken_read_config = SpokenReadConfig()
                spoken, self._spoken_read_state, spoken_reads = select_spoken_reads(
                    observer_name=player.name,
                    active_opponents=active_opponents,
                    facing_opponent=facing_opponent,
                    opponent_model_manager=manager,
                    state=self._spoken_read_state,
                    config=self._spoken_read_config,
                )
            except Exception as e:  # noqa: BLE001 — narration is observability
                logger.warning(f"[TIERED_BOT] {self.player_name}: spoken_reads failed: {e}")
                spoken = []
                spoken_reads = []

            if spoken_reads:
                # Fresh voiced read this hand → it leads both channels.
                self._last_spoken_read = spoken_reads[0]
            elif prev_read is not None and prev_read.opponent in active_opponents:
                # Speech channel cooled down, but the opponent is still in
                # the hand — keep the prior read so narration_facts carries
                # the arc across the gate (the always-in-context channel).
                self._last_spoken_read = prev_read
            # else: no fresh read and the prior read's opponent is gone →
            # leave it None so a stale read can't leak into an unrelated hand.

            cap = getattr(self, '_spoken_read_config', None)
            max_obs = cap.max_observations_per_decision if cap else 2

            generic = manager.select_opponent_observations(
                player.name,
                active_opponents=active_opponents,
                facing_opponent=facing_opponent,
            )

            # Spoken reads take priority; backfill from generic observations
            # for opponents not already covered, up to the cap.
            merged: List[Tuple[str, str]] = list(spoken[:max_obs])
            covered = {opp for opp, _ in merged}
            for opp, obs in generic:
                if len(merged) >= max_obs:
                    break
                if opp in covered:
                    continue
                merged.append((opp, obs))
                covered.add(opp)
            return merged
        except Exception:
            return []

    def _build_expression_extras(
        self,
        game_state,
        player,
        hand_cards: List[str],
        community_cards: List[str],
    ) -> Dict[str, Any]:
        """Compute hand label, BB-normalized situation, and recent-actions
        text for the Layer 3 narration prompt.

        Each sub-step is best-effort: any failure populates the affected
        field with a safe default ('' for strings, 0.0 for floats), and the
        corresponding YAML section is skipped by ExpressionGenerator.
        """
        from .controllers import (
            classify_preflop_hand,
            evaluate_hand_strength,
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
            cost_to_call_bb > 0 and stack_bb > 0 and stack_bb < cost_to_call_bb * 3
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
            logger.info(f"[TIERED_BOT] {self.player_name}: " f"postflop_fallback={action}")

        return {
            'action': action,
            'raise_to': 0,
            'dramatic_sequence': [],
            'hand_strategy': 'Postflop emergency fallback',
            'inner_monologue': '',
            'bluff_likelihood': 0,
        }

    def _validate_action(self, action: str, raise_to: int, valid_actions: List[str]) -> tuple:
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
                    f"[TIERED_BOT] {self.player_name}: " f"Falling back from {action} to {fb}"
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
        depth_strategy_tables: Optional[Dict[int, StrategyTable]] = None,
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
            depth_strategy_tables=depth_strategy_tables,
            **kwargs,
        )
