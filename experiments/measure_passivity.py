#!/usr/bin/env python3
"""Tier-A passivity instrumentation for the tiered (Baseline) bot.

Implements the §3 "Tier A — direct passivity metrics" of
docs/plans/STRUCTURAL_PASSIVITY_PLAN.md. bb/100 has proven too insensitive
to detect postflop changes vs rule bots, so this measures the *direct*
passivity signals that move on far fewer hands:

  - Postflop AggFactor (aggressive / passive), overall and by action_context
  - `unopened` action split by hand_class (esp. strong_made / nuts bet%)
  - `facing_bet` / `facing_raise` fold / call / raise split by hand_class
  - Barrel-continuation rate: P(bet/raise turn | hero bet/raised flop)
  - "C-bet then check/fold turn" rate (the continue_story failure)
  - Pay-off rate: call flop -> call turn -> reach river -> lose
  - Facing-double-barrel action split (the H2 target)

It also reports bb/100 (Tier B) for the same run so a single invocation
yields both the primary control (Tier A) and the secondary gate (Tier B).

The hero is the no-personality BaselineSolverBot (anchors=None) by default —
the analysis target named in the plan. The instrumented hand loop is a
trimmed copy of `simulate_bb100.run_hand`: it drops the opponent_manager /
equity-MC / c-bet machinery because none of it affects Baseline decisions or
final stacks (exploitation is a no-op at anchors=None, and equity recording
only writes to models). This is exactly the plan's "equity-MC disabled for
Baseline" requirement, and keeps the loop fast and deterministic.

The `--mode` flag selects the multi-street-context A/B arm
(off / h1 / h2 / on); it is inert until the layer + flag land on the
controller (Step 3 of the plan). Paired seeds are supported via
`--seeds 42,142,242` so a single run reports per-seed deltas (watch for
sign-disagreement = noise, as seen in the push/fold A/B).

Usage:
    docker compose exec backend python -m experiments.measure_passivity --opponents gto --hands 3000
    docker compose exec backend python -m experiments.measure_passivity --opponents mix --hands 3000 --seeds 42,142,242
    docker compose exec backend python -m experiments.measure_passivity --opponents gto --mode on --hands 3000
"""

import argparse
import logging
import os
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Baseline's SimpleNamespace psychology has no zone_effects, so
# get_emotional_shift() logs a benign warning every postflop decision and
# falls back to 'composed'. Silence it — at thousands of hands the I/O would
# dominate runtime and bury the report.
logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments._hand_loop import drive_hand
from experiments.simulate_bb100 import (
    ARCHETYPES,
    DEFAULT_RULE_OPPONENTS,
    _make_seat_names,
    compute_stats,
    make_controller,
    make_game_state,
)

# For per-node EV attribution (ab_node_attribution): build the same node key the
# controller looks up, so an attribution bucket maps directly to a chart entry.
from poker.card_utils import card_to_string
from poker.controllers import _get_canonical_hand
from poker.hand_ranges import _classify_hand_tier
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.multistreet_context import H1_BARREL_TARGET, H2_FOLD_TARGET, derive_signals
from poker.strategy.preflop_classifier import build_preflop_node
from poker.strategy.preflop_isolate import build_isolation_table
from poker.strategy.strategy_table import load_strategy_table

# Frozen clone profiles (Track 2 eval). Resolved relative to this module so
# they work regardless of cwd / worktree.
_CLONE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'clone_profiles')
DEFAULT_CLONE_PROFILE = os.path.join(_CLONE_DIR, 'jeff.json')
PUNISHER_CLONE_PROFILE = os.path.join(_CLONE_DIR, 'punisher.json')

# Opponent roster presets. GTO-Lite / MIX are rule bots (never fold preflop →
# always-multiway, insensitive to postflop quality — see STRUCTURAL_PASSIVITY
# §9-10). `jeff` is the Track-2 precision-rewarding eval: 5 Jeff_clones (a
# human model that folds ~45% to c-bets), which should create HU/short-handed
# pots and reward initiative. `punisher` is the EVAL_HARNESS_PLAN §P0.5
# non-station opponent: a disciplined aggressive reg that *folds correctly*
# (punishes over-calling) AND *barrels air* (punishes over-folding) — a win
# vs jeff that does NOT hold vs punisher is overfit to the station.
ROSTERS = {
    'gto': ['GTO-Lite'] * 5,
    'mix': DEFAULT_RULE_OPPONENTS,
    'jeff': ['Jeff_clone'] * 5,
    'punisher': ['Punisher_clone'] * 5,
}

# Default frozen profile per clone roster preset, so `--opponents punisher`
# loads punisher.json without an explicit --clone-profile (the auto-detect
# below would otherwise register jeff.json and leave Punisher_clone unknown).
ROSTER_CLONE_PROFILE = {
    'jeff': DEFAULT_CLONE_PROFILE,
    'punisher': PUNISHER_CLONE_PROFILE,
}


def _ensure_clone_registered(profile_path: str, oracle_punish_overbets: bool = False) -> str:
    """Load + register a frozen CloneProfile as a rule-bot ARCHETYPE.

    Idempotent. Mirrors simulate_bb100's --clone-profile wiring. Must run in
    each worker process (the ProcessPool children re-register so the ARCHETYPE
    + strategy registry exist before the matchup looks them up).
    Returns the archetype key (e.g. 'Jeff_clone').

    `oracle_punish_overbets` (eval-only) registers the perfect-overbet-punisher
    variant under the SAME archetype key — so the existing roster (e.g. 'jeff')
    transparently becomes the oracle opponent for measuring overbet exploitability.
    Also honoured via the `ORACLE_PUNISH_OVERBETS` env var so ProcessPool workers
    (which re-register without the kwarg) and the `--oracle-opp` CLI flag both turn
    the clone into the oracle without threading a flag through the work tuple.
    """
    oracle_punish_overbets = oracle_punish_overbets or bool(os.environ.get('ORACLE_PUNISH_OVERBETS'))
    from poker.human_clone import load_profile_from_file, register_clone_strategy

    profile = load_profile_from_file(profile_path)
    player_name = profile.source_player
    strategy_key = f"clone_{player_name.replace(' ', '_').lower()}"
    register_clone_strategy(strategy_key, profile, oracle_punish_overbets=oracle_punish_overbets)
    archetype_key = f"{player_name}_clone"
    ARCHETYPES[archetype_key] = {'kind': 'rule_bot', 'strategy': strategy_key}
    return archetype_key


_AGGRESSIVE = {'bet', 'raise', 'all_in'}
_POSTFLOP_STREETS = ('FLOP', 'TURN', 'RIVER')
_PREV_STREET = {'TURN': 'FLOP', 'RIVER': 'TURN'}

# ── Size→strength "tell map" (readability audit) ────────────────────────────
# For each hero bet/raise, bucket it by its size as a fraction of the pot BEFORE
# the action, then record the hand class. A face-up bot's big sizes are ~all
# value (bluff share → 0), so a sizing-reader folds to them for free; a balanced
# range holds the GTO-unexploitable bluff share s/(1+2s) at every size. The map
# shows WHERE the bot's sizing leaks strength and by how much vs that target.
_SIZE_BUCKETS = [
    ('xs', 0.0, 0.33),  # tiny / blocker bet
    ('s', 0.33, 0.55),  # small
    ('m', 0.55, 0.80),  # medium
    ('l', 0.80, 1.10),  # ~pot
    ('xl', 1.10, 1.60),  # overbet
    ('xxl', 1.60, 1e9),  # big overbet / jam
]
_VALUE_CLASSES = {'nuts', 'strong_made'}
_BLUFF_CLASSES = {'air', 'air_no_draw', 'air_strong_draw'}

# Preflop 3-bet readability: a 3-bet (or 4-bet) range is face-up when it's all
# value (premium/strong — hands happy to stack off / call a 4-bet), so a reader
# folds to every 3-bet and 4-bet-bluffs the capped raiser. A balanced range mixes
# in light/bluff 3-bets (playable/marginal/trash — hands that 3-bet to fold to a
# 4-bet). Tiers from hand_ranges._classify_hand_tier.
_PF_VALUE_TIERS = {'premium', 'strong'}
_PF_BLUFF_TIERS = {'playable', 'marginal', 'trash'}


def _size_bucket(frac: float) -> str:
    for name, lo, hi in _SIZE_BUCKETS:
        if lo <= frac < hi:
            return name
    return 'xxl'


def _gto_bluff_target(frac: float) -> float:
    """Unexploitable bluff share of a polar betting range for a bet of `frac`
    pot-fractions: s/(1+2s). 0.5→0.25, pot→0.33, 1.5x→0.375, 2x→0.40."""
    return frac / (1.0 + 2.0 * frac) if frac > 0 else 0.0


@dataclass
class PassivityStats:
    """Tier-A accumulator across a run (one hero archetype, all seeds)."""

    # action_context -> resolved_action -> count
    ctx_action: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # (action_context, hand_class) -> resolved_action -> count
    ctx_class_action: Dict[Tuple[str, str], Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    # facing-double-barrel decisions: resolved_action -> count
    double_barrel_action: Counter = field(default_factory=Counter)

    # Per-hand line metrics
    flop_aggressor_with_turn: int = 0  # hero bet/raised flop AND saw a turn action
    turn_barrel: int = 0  # ...and bet/raised the turn
    cbet_then_give_up: int = 0  # ...but checked/folded the turn

    callcall_river: int = 0  # called flop + called turn + reached river
    payoff_loss: int = 0  # ...and lost the hand

    postflop_decisions: int = 0

    # Multi-street layer fire tracking (the inert-trap check): how often the
    # layer fired and actually changed the action distribution.
    layer_fires: Counter = field(default_factory=Counter)  # rule_id -> count
    layer_action_changed: Counter = field(default_factory=Counter)  # rule_id -> count
    layer_noop_reasons: Counter = field(default_factory=Counter)  # reason_code -> count

    # Signal-frequency diagnostics (computed independently of whether the
    # layer fires — answers "do the spots even occur?"). The crux of the
    # honest-null vs gate-too-tight question.
    unopened_decisions: int = 0
    unopened_prev_aggressor: int = 0  # ...where hero had prior-round initiative
    # active-player count when H1's spot (unopened + prev_aggressor + value class) holds
    h1_spot_by_active: Counter = field(default_factory=Counter)
    facing_bet_decisions: int = 0
    facing_double_barrel: int = 0  # facing bet AND opp double-barreled
    h2_spot_marginal: int = 0  # ...with a marginal made hand (H2 target)

    # Field size at hero postflop decisions — the Track 1 leading indicator.
    # If sharpening preflop ENTRY (isolate) works, this distribution shifts
    # toward HU (2 players), creating the initiative spots the bot lacks.
    postflop_active_count: Counter = field(default_factory=Counter)

    # Per-signature leak surface: bucket the bot's decisions by a multi-street
    # LINE-SIGNATURE (street, action_context, hand_class, prev-aggressor bit,
    # double-barrel bit) and compare realized aggression to the chart's OWN
    # intended aggression (base_strategy_probs). A large gap = where the
    # pipeline/policy diverges from the chart for that signature → the input
    # to "which spots need a better policy" (vs hand-authoring 2^K on faith).
    sig_action: Dict[tuple, Counter] = field(default_factory=lambda: defaultdict(Counter))
    sig_chart_agg_sum: Dict[tuple, float] = field(default_factory=lambda: defaultdict(float))

    # Size→strength tell map: (street, size_bucket) -> Counter(hand_class), plus
    # the running sum of bet-fraction per bucket (for the bucket's mean size, used
    # to pick the GTO bluff target). Populated for every hero bet/raise/all_in.
    size_strength: Dict[Tuple[str, str], Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    size_frac_sum: Dict[Tuple[str, str], float] = field(
        default_factory=lambda: defaultdict(float)
    )

    # Diagnostic: overbet_context trace outcomes (fired effect / no-op reason).
    overbet_outcomes: Counter = field(default_factory=Counter)

    # Check-range composition (the capped-checking-range dual of the tell map):
    # (street) -> Counter(hand_class) for the hero's UNOPENED checks. If the river
    # check range has ~0% nuts/strong (they all bet), it's capped → a reader can
    # stab it; the question is whether the bot then over-folds (the stabber test).
    check_strength: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    # Preflop instrumentation — the 100bb-ranges-at-short-stacks leak is likely
    # mostly preflop (ranges too loose, raises too small, missed jams), which
    # the postflop surface can't see. Captures the hero's preflop decisions by
    # scenario (rfi/vs_open/vs_3bet/vs_4bet) so VPIP/PFR/jam%/avg-open-size are
    # readable and comparable across stack depths.
    pf_decisions: int = 0
    pf_action: Counter = field(default_factory=Counter)  # overall fold/check/call/raise/all_in
    pf_scenario_action: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    pf_raise_to_bb_sum: float = 0.0  # sum of resolved raise-to (in BB) for raises (excl all_in)
    pf_raise_n: int = 0  # count of raises (excl all_in) — for avg open size
    # Preflop 3-bet readability: (scenario, action) -> Counter(hand_tier). The
    # vs_open+raise cell is the 3-bet range's tier composition (value vs bluff).
    pf_tier_action: Dict[Tuple[str, str], Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )

    def record_decision(
        self, node_key: str, action: str, opp_bet_flop: bool, opp_bet_prev: bool, street: str
    ):
        """Record one hero postflop decision keyed by its node context."""
        parts = node_key.split('|')
        if len(parts) < 7:
            return
        hand_class = parts[4]
        action_context = parts[6]
        self.postflop_decisions += 1
        self.ctx_action[action_context][action] += 1
        self.ctx_class_action[(action_context, hand_class)][action] += 1
        # Facing-double-barrel: on turn/river, facing a bet, opp bet flop AND
        # the immediately-prior street (a sustained multi-street value line).
        if (
            street in ('TURN', 'RIVER')
            and action_context in ('facing_bet', 'facing_raise')
            and opp_bet_flop
            and opp_bet_prev
        ):
            self.double_barrel_action[action] += 1

    @staticmethod
    def _agg_passive(counter: Counter) -> Tuple[int, int]:
        agg = sum(counter[a] for a in _AGGRESSIVE)
        passive = counter['check'] + counter['call']
        return agg, passive

    def agg_factor(self) -> float:
        agg = passive = 0
        for ctx, counter in self.ctx_action.items():
            a, p = self._agg_passive(counter)
            agg += a
            passive += p
        return agg / max(1, passive)


def _aggregate(into: PassivityStats, src: PassivityStats):
    for ctx, c in src.ctx_action.items():
        into.ctx_action[ctx].update(c)
    for k, c in src.ctx_class_action.items():
        into.ctx_class_action[k].update(c)
    into.double_barrel_action.update(src.double_barrel_action)
    into.flop_aggressor_with_turn += src.flop_aggressor_with_turn
    into.turn_barrel += src.turn_barrel
    into.cbet_then_give_up += src.cbet_then_give_up
    into.callcall_river += src.callcall_river
    into.payoff_loss += src.payoff_loss
    into.postflop_decisions += src.postflop_decisions
    into.layer_fires.update(src.layer_fires)
    into.layer_action_changed.update(src.layer_action_changed)
    into.layer_noop_reasons.update(src.layer_noop_reasons)
    into.unopened_decisions += src.unopened_decisions
    into.unopened_prev_aggressor += src.unopened_prev_aggressor
    into.h1_spot_by_active.update(src.h1_spot_by_active)
    into.facing_bet_decisions += src.facing_bet_decisions
    into.facing_double_barrel += src.facing_double_barrel
    into.h2_spot_marginal += src.h2_spot_marginal
    into.postflop_active_count.update(src.postflop_active_count)
    for sig, c in src.sig_action.items():
        into.sig_action[sig].update(c)
    for sig, v in src.sig_chart_agg_sum.items():
        into.sig_chart_agg_sum[sig] += v
    for k, c in src.size_strength.items():
        into.size_strength[k].update(c)
    for k, v in src.size_frac_sum.items():
        into.size_frac_sum[k] += v
    into.overbet_outcomes.update(src.overbet_outcomes)
    for k, c in src.check_strength.items():
        into.check_strength[k].update(c)
    into.pf_decisions += src.pf_decisions
    into.pf_action.update(src.pf_action)
    for sc, c in src.pf_scenario_action.items():
        into.pf_scenario_action[sc].update(c)
    for k, c in src.pf_tier_action.items():
        into.pf_tier_action[k].update(c)
    into.pf_raise_to_bb_sum += src.pf_raise_to_bb_sum
    into.pf_raise_n += src.pf_raise_n


MODES = ('off', 'h1', 'h2', 'on')


def _apply_mode(controller, mode: str):
    """Set the multi-street-context A/B arm on the hero controller.

    'off' = current behavior. h1/h2/on = multi-street layer arms.
    (The value-bet-floor 'vbf' modes were retired — its win was baked into
    multiway.py's VALUE_CLASSES exemption; see STRUCTURAL_PASSIVITY_PLAN §14.)
    """
    controller.enable_multistreet_context = mode in ('h1', 'h2', 'on')
    controller.multistreet_h1_barrel = mode in ('h1', 'on')
    controller.multistreet_h2_foldbarrel = mode in ('h2', 'on')


def run_passivity_hand(
    sm,
    controllers,
    hero_name: str,
    stats: PassivityStats,
    hero_trace=None,
    hero_overbet_obs=None,
    hero_faced_raise_obs=None,
    hero_faced_bet_obs=None,
):
    """Drive one hand; instrument the hero's postflop decisions.

    Mirrors simulate_bb100.run_hand's action driving (run_until, run_it_out,
    play_turn, advance) so chip outcomes match, but adds:
      - per-hand hero/opp street-action tracking (barrel / pay-off / d-barrel)
      - the new _sim_hero_bet_by_street / _sim_opp_bet_by_street fields the
        multi-street layer reads (driven here the same way the existing
        _sim_* aggressor fields are).

    `hero_trace` (when a list is passed) records the hero's ordered decision
    sequence as `(phase, node_key, action, raise_to)` tuples — the input to the
    paired-CRN per-node attribution (ab_node_attribution.py). node_key is the
    exact chart key (preflop built via build_preflop_node; postflop from the
    pipeline snapshot), so the first point two arms' traces differ pinpoints the
    chart node that caused the hand to diverge.
    """
    controller_map = {c.player_name: c for c in controllers}
    hero = controller_map.get(hero_name)

    # Per-hand line tracking (hero perspective).
    hero_actions_by_street: Dict[str, List[str]] = defaultdict(list)
    opp_bet_by_street: Dict[str, bool] = defaultdict(bool)
    state = {'hero_reached_river': False}

    def _pre_decision(controller, current_player, phase_name):
        # Clear the snapshot before the hero acts so a stale postflop snapshot
        # from a prior street can't be misread as this decision's.
        if current_player.name == hero_name:
            controller._last_pipeline_snapshot = {}

    def _on_decision(
        current_player,
        controller,
        action,
        raise_to,
        phase_name,
        gs,
        sim_current_street,
        decision,
    ):
        is_hero = current_player.name == hero_name

        # ── Per-node attribution trace (hero only) ──────────────────────────
        # Record (phase, node_key, action, raise_to). node_key is the exact
        # chart key: postflop from the pipeline snapshot, preflop rebuilt here
        # (the preflop snapshot doesn't carry it). Pre-divergence both A/B arms
        # see identical state → identical entries; the first differing tuple is
        # the node that caused the hand to split.
        if is_hero and hero_trace is not None:
            snap0 = getattr(controller, '_last_pipeline_snapshot', {}) or {}
            node_id = snap0.get('node_key')
            if not node_id and phase_name == 'PRE_FLOP':
                hole = (
                    [card_to_string(c) for c in current_player.hand] if current_player.hand else []
                )
                canon = _get_canonical_hand(hole) if len(hole) == 2 else ''
                if canon:
                    try:
                        node_id = build_preflop_node(gs, gs.current_player_idx, canon).key
                    except Exception:
                        node_id = None
            hero_trace.append((phase_name, node_id or f'?{phase_name}', action, raise_to))

        # ── Instrument the hero's preflop decision ──────────────────────────
        # Bucket by scenario from raises_this_round (0=rfi, 1=vs_open,
        # 2=vs_3bet, 3+=vs_4bet) so VPIP/PFR/jam%/avg-open-size are readable
        # and comparable across stack depths (the short-stack range leak).
        if is_hero and phase_name == 'PRE_FLOP':
            raises = getattr(gs, 'raises_this_round', 0)
            scenario = {0: 'rfi', 1: 'vs_open', 2: 'vs_3bet'}.get(raises, 'vs_4bet')
            stats.pf_decisions += 1
            stats.pf_action[action] += 1
            stats.pf_scenario_action[scenario][action] += 1
            if action == 'raise':
                stats.pf_raise_to_bb_sum += raise_to / 100.0  # big_blind=100 in sim
                stats.pf_raise_n += 1
            # 3-bet readability: bucket the decision's hand by tier, keyed by
            # (scenario, action). vs_open+raise = the 3-bet range composition.
            hole = (
                [card_to_string(c) for c in current_player.hand]
                if current_player.hand
                else []
            )
            if len(hole) == 2:
                canon = _get_canonical_hand(hole)
                if canon:
                    tier = _classify_hand_tier(canon, None)
                    stats.pf_tier_action[(scenario, action)][tier] += 1

        # ── Instrument the hero's postflop decision ─────────────────────────
        if is_hero and phase_name in _POSTFLOP_STREETS:
            snap = getattr(controller, '_last_pipeline_snapshot', {}) or {}
            node_key = snap.get('node_key')
            if node_key:
                prev = _PREV_STREET.get(phase_name)
                opp_bet_flop = opp_bet_by_street.get('FLOP', False)
                opp_bet_prev = opp_bet_by_street.get(prev, False) if prev else False
                stats.record_decision(
                    node_key,
                    action,
                    opp_bet_flop=opp_bet_flop,
                    opp_bet_prev=opp_bet_prev,
                    street=phase_name,
                )
                # Signal-frequency diagnostics (mode-independent): do the
                # layer's spots actually occur? Uses the same signals +
                # hand_strength the layer gates on.
                action_context = node_key.split('|')[6]
                hand_strength = snap.get('hand_strength', '')
                active_count = sum(1 for p in gs.players if not p.is_folded)
                stats.postflop_active_count[active_count] += 1
                # Feed the adaptive bluff-raiser: when the hero faces a raise (HU
                # → from the aggressor), record whether the hero folded so the
                # aggressor learns the hero's fold-to-raise and escalates/backs off.
                if hero_faced_raise_obs is not None and action_context == 'facing_raise':
                    hero_faced_raise_obs.append(action == 'fold')
                # Check-range composition: when the hero CHECKS unopened, what
                # class is it? (the capped-checking-range readability fact).
                if action == 'check' and action_context == 'unopened':
                    stats.check_strength[phase_name][hand_strength] += 1
                # Feed the adaptive stabber: when the hero faces a bet (HU → a
                # stab into its checked/capped range), did it fold?
                if hero_faced_bet_obs is not None and action_context == 'facing_bet':
                    hero_faced_bet_obs.append(action == 'fold')
                sig = derive_signals(controller, phase_name.lower())
                if action_context == 'unopened':
                    stats.unopened_decisions += 1
                    if sig.was_prev_street_aggressor:
                        stats.unopened_prev_aggressor += 1
                        if hand_strength in H1_BARREL_TARGET:
                            stats.h1_spot_by_active[active_count] += 1
                elif action_context in ('facing_bet', 'facing_raise'):
                    stats.facing_bet_decisions += 1
                    if sig.facing_double_barrel:
                        stats.facing_double_barrel += 1
                        if hand_strength in H2_FOLD_TARGET:
                            stats.h2_spot_marginal += 1
                # Per-signature leak surface: realized action vs chart intent.
                signature = (
                    phase_name,
                    action_context,
                    hand_strength,
                    sig.was_prev_street_aggressor,
                    sig.facing_double_barrel,
                )
                stats.sig_action[signature][action] += 1
                base = snap.get('base_strategy_probs', {})
                stats.sig_chart_agg_sum[signature] += sum(
                    p
                    for a, p in base.items()
                    if a in ('jam', 'all_in') or a.startswith(('bet_', 'raise_'))
                )
                # Size→strength tell map: bucket this bet/raise by size-vs-pot.
                # pot['total'] already includes all increments committed so far
                # this street, so it is the pot the hero is betting INTO; the
                # hero's own increment is raise_to minus what it already put in
                # this street (the doc's correct raise accounting). all_in with
                # no explicit raise_to falls back to the pushed stack.
                if action in _AGGRESSIVE:
                    pot_before = gs.pot.get('total', 0) or 0
                    committed = current_player.bet or 0
                    inc = max(0, (raise_to or 0) - committed)
                    if inc == 0 and action == 'all_in':
                        inc = current_player.stack or 0
                    if pot_before > 0 and inc > 0:
                        frac = inc / pot_before
                        bucket = _size_bucket(frac)
                        # Split bet (unopened) from raise (facing a bet/raise) so
                        # raise-as-bluff readability is visible separately — a
                        # raise range that's pure value is as face-up as a bet one
                        # (a reader never pays the raise).
                        ctx_tag = 'bet' if action_context == 'unopened' else 'raise'
                        key = (phase_name, ctx_tag, bucket)
                        stats.size_strength[key][hand_strength] += 1
                        stats.size_frac_sum[key] += frac
                        # Feed the adaptive sizing-reader: record the hero's
                        # RIVER overbet (>=1.2x) class so the BR (perfect
                        # observation) learns the hero's overbet bluff freq.
                        if (
                            hero_overbet_obs is not None
                            and phase_name == 'RIVER'
                            and frac >= 1.2
                        ):
                            hero_overbet_obs.append(hand_strength)
            hero_actions_by_street[phase_name].append(action)
            if phase_name == 'RIVER':
                state['hero_reached_river'] = True
            # Inert-trap check: did the multi-street layer fire / change the
            # distribution this decision? Read its trace off the controller.
            for tr in getattr(controller, '_last_intervention_trace', []):
                if getattr(tr, 'layer', None) != 'multistreet_context':
                    continue
                if tr.fired:
                    stats.layer_fires[tr.rule_id] += 1
                    if tr.action_changed:
                        stats.layer_action_changed[tr.rule_id] += 1
                else:
                    stats.layer_noop_reasons[tr.reason_code] += 1
            # Diagnostic: overbet_context outcomes (why river-bluff did/didn't fire).
            for tr in getattr(controller, '_last_intervention_trace', []):
                if getattr(tr, 'layer', None) != 'overbet_context':
                    continue
                tag = (tr.effect if tr.fired else f'noop:{tr.reason_code}')
                if phase_name == 'RIVER':
                    stats.overbet_outcomes[f'RIVER/{tag}'] += 1
                else:
                    stats.overbet_outcomes[tag] += 1

    def _post_action(current_player, action, raise_to, phase_name, gs, new_gs):
        # Mirror into the per-hand line trackers (used for end-of-hand metrics).
        # The shared _sim_* aggressor bookkeeping is applied by drive_hand; this
        # only tracks the passivity-specific opp_bet_by_street view.
        if (
            phase_name in _POSTFLOP_STREETS
            and action in _AGGRESSIVE
            and current_player.name != hero_name
        ):
            opp_bet_by_street[phase_name] = True

    final_stacks = drive_hand(
        sm,
        controllers,
        hero_name=hero_name,
        hero_controller=hero,
        pre_decision=_pre_decision,
        on_decision=_on_decision,
        post_action=_post_action,
    )
    hero_reached_river = state['hero_reached_river']

    # ── End-of-hand line metrics ────────────────────────────────────────────
    flop_aggressor = any(a in _AGGRESSIVE for a in hero_actions_by_street.get('FLOP', []))
    saw_turn = 'TURN' in hero_actions_by_street
    if flop_aggressor and saw_turn:
        stats.flop_aggressor_with_turn += 1
        if any(a in _AGGRESSIVE for a in hero_actions_by_street['TURN']):
            stats.turn_barrel += 1
        elif any(a in ('check', 'fold') for a in hero_actions_by_street['TURN']):
            stats.cbet_then_give_up += 1

    called_flop = 'call' in hero_actions_by_street.get('FLOP', [])
    called_turn = 'call' in hero_actions_by_street.get('TURN', [])
    if called_flop and called_turn and hero_reached_river:
        stats.callcall_river += 1
        final_stacks.get(hero_name, 0)
        # delta computed by caller vs starting stack; here just flag a loss
        # via the returned stacks (caller passes starting_stack for the real
        # delta). We mark payoff_loss using the returned stacks below.
    return final_stacks, (called_flop and called_turn and hero_reached_river)


def run_passivity_matchup(
    hero_archetype: str,
    opponents: List[str],
    n_hands: int,
    strategy_table,
    big_blind: int = 100,
    starting_stack: int = 10000,
    base_seed: int = 42,
    mode: str = 'off',
    entry: str = 'default',
    h1_classes: Optional[frozenset] = None,
    hero_table: Optional[object] = None,
) -> Tuple[List[float], PassivityStats]:
    """Run n_hands of 6-max (hero + 5 opponents); return (deltas, Tier-A stats).

    Setup mirrors simulate_bb100.run_6max_matchup exactly (seat names, dealer
    rotation, per-hand global+rng seeding) so chip deltas / bb/100 are
    directly comparable to the main harness.

    `entry='isolate'` gives the HERO a preflop chart where OOP vs_open
    flat-calls are shifted to 3-bets (Track 1). Opponents keep the default
    chart, so the A/B isolates the hero's entry change.

    `hero_table` (when supplied) is the strategy table the HERO uses; opponents
    always use `strategy_table`. This is how `--preflop-chart` swaps the hero's
    preflop chart (e.g. the wider-RFI chart) without touching the live file or
    the opponents — the ONLY variable becomes the hero's open frequencies. When
    None, the hero uses `strategy_table` (current behavior), optionally
    transformed by `entry='isolate'`.
    """
    if len(opponents) < 1:
        raise ValueError(f"need >=1 opponent, got {len(opponents)}")

    # When an explicit hero chart is forced (--preflop-chart or entry=isolate),
    # it must win over the archetype width-tier auto-selection — so we clear the
    # hero's archetype_preflop_tables below. A plain `--hero X` (no forced chart)
    # keeps the auto-selected width table (the real acceptance-test path).
    hero_chart_forced = hero_table is not None or entry == 'isolate'
    if hero_table is None:
        hero_table = strategy_table
    hero_table = build_isolation_table(hero_table) if entry == 'isolate' else hero_table

    hero_name = hero_archetype if hero_archetype not in opponents else f"{hero_archetype}_hero"
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{hero_archetype}_hero"
    all_names = [hero_name] + opponent_seats

    config_arch = ARCHETYPES[hero_archetype]
    opp_configs = [ARCHETYPES[o] for o in opponents]

    # Adaptive sizing-reader best-responder (OVERBET_BALANCING.md §5g): the
    # "missing instrument" — an opponent that OBSERVES the hero's overbet hands
    # and best-responds its fold freq (over-folds a face-up bot, calls a balanced
    # one). Override every opponent seat with it; feed it the hero's river overbet
    # classes after each hand. Per-process state (each seed learns within its run).
    adaptive_reader_state = None
    if os.environ.get('ADAPTIVE_READER'):
        from poker.human_clone import load_profile_from_file, register_adaptive_reader

        profile = load_profile_from_file(PUNISHER_CLONE_PROFILE)  # competent-reg base
        # Fresh registration per matchup so each starts cold.
        adaptive_reader_state = register_adaptive_reader('clone_adaptive_reader', profile)
        ARCHETYPES['AdaptiveReader'] = {'kind': 'rule_bot', 'strategy': 'clone_adaptive_reader'}
        opp_configs = [ARCHETYPES['AdaptiveReader']] * len(opponent_seats)

    # Adaptive bluff-raiser (OVERBET_BALANCING.md §5h / DEFENSE_VS_AGGRESSION): the
    # dual of the reader — tests whether the bot bleeds to escalating bluff-raises.
    # AGGRESSOR_BLUFF=0 = static-reg control (no bluff-raising) for the A/B.
    adaptive_aggressor_state = None
    if os.environ.get('ADAPTIVE_AGGRESSOR'):
        from poker.human_clone import load_profile_from_file, register_adaptive_aggressor

        profile = load_profile_from_file(PUNISHER_CLONE_PROFILE)
        bluff_on = os.environ.get('AGGRESSOR_BLUFF', '1') != '0'
        _thr = float(os.environ.get('AGGRESSOR_THRESHOLD', '0.5'))  # 0 = relentless maniac
        adaptive_aggressor_state = register_adaptive_aggressor(
            'clone_adaptive_aggressor', profile, bluff_raise=bluff_on, threshold=_thr
        )
        ARCHETYPES['AdaptiveAggressor'] = {
            'kind': 'rule_bot',
            'strategy': 'clone_adaptive_aggressor',
        }
        opp_configs = [ARCHETYPES['AdaptiveAggressor']] * len(opponent_seats)

    # Adaptive stabber (OVERBET_BALANCING.md §5i): the capped-checking-range test —
    # bets junk when the bot checks to it, learns fold-to-stab, escalates.
    adaptive_stabber_state = None
    if os.environ.get('ADAPTIVE_STABBER'):
        from poker.human_clone import load_profile_from_file, register_adaptive_stabber

        profile = load_profile_from_file(PUNISHER_CLONE_PROFILE)
        stab_on = os.environ.get('STABBER_BLUFF', '1') != '0'
        _sthr = float(os.environ.get('STABBER_THRESHOLD', '0.34'))  # half-pot breakeven; 0 = relentless
        adaptive_stabber_state = register_adaptive_stabber(
            'clone_adaptive_stabber', profile, bluff_stab=stab_on, threshold=_sthr
        )
        ARCHETYPES['AdaptiveStabber'] = {'kind': 'rule_bot', 'strategy': 'clone_adaptive_stabber'}
        opp_configs = [ARCHETYPES['AdaptiveStabber']] * len(opponent_seats)

    stats = PassivityStats()
    deltas: List[float] = []

    for hand_num in range(n_hands):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % len(all_names)
        random.seed(hand_seed)  # per-hand global-random reset (rule bots)

        gs = make_game_state(
            player_names=all_names,
            big_blind=big_blind,
            starting_stack=starting_stack,
            dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed

        controllers = [make_controller(hero_name, config_arch, hero_table, sm, rng_seed=hand_seed)]
        if hero_chart_forced:
            # The forced --preflop-chart / isolate table is the hero's
            # strategy_table; drop archetype auto-selection so it isn't
            # overridden by a width-tier chart.
            controllers[0].archetype_preflop_tables = {}
        # No opponent_manager: Baseline (anchors=None) skips exploitation and
        # equity recording only writes to models, so omitting it is identical
        # for decisions/stacks and disables equity-MC (plan requirement).
        controllers[0].opponent_model_manager = None
        _apply_mode(controllers[0], mode)
        controllers[0].multistreet_h1_classes = h1_classes
        # River-bluff (OVERBET_BALANCING T2) validation knob. Env-var so it
        # crosses the ProcessPool boundary (children inherit env), mirroring the
        # REGPLUS_* convention. OFF (unset) = byte-identical. Drives the tell-map
        # check: does the river leak (blf%→target) close, and at what fish cost?
        _rbf = os.environ.get('RIVER_BLUFF_FRACTION')
        if _rbf:
            # make_controller bypasses __init__, so enable_overbet_context is
            # unset (the layer is dormant in this harness). Turn it on and
            # ISOLATE the river-bluff effect: overbet_fraction=0.0 keeps the
            # value side a no-op so the only change vs baseline is the new river
            # bluffs (give-up-air checks → bet at river_bluff_size).
            controllers[0].enable_overbet_context = True
            # overbet_fraction default 0.0 ISOLATES the bluff effect; set
            # OVERBET_FRACTION=1.0 to calibrate under the production config (value
            # relabel ON, so value + bluff both sit at the overbet size → the
            # tell-map xl bucket shows the BALANCED ratio to tune toward ~37.5%).
            controllers[0].overbet_fraction = float(os.environ.get('OVERBET_FRACTION', '0.0'))
            controllers[0].river_bluff_fraction = float(_rbf)
            _rbs = os.environ.get('RIVER_BLUFF_SIZE')
            if _rbs:
                controllers[0].river_bluff_size = int(_rbs)
            # Regime-gate read override (no model manager in this harness): set a
            # synthetic fold_to_big_bet so the gate sees a "reader" (high) or
            # "caller" (low). Unset → gate has no read → river bluff never fires.
            _ftbb = os.environ.get('RIVER_BLUFF_FTBB')
            if _ftbb:
                controllers[0].river_bluff_ftbb_override = float(_ftbb)
            # River-air SUPPLY build: barrel turn air so more reaches the river.
            # Fires inside multistreet_context → needs --mode on/h1 to be active.
            _abt = os.environ.get('AIR_BARREL_TARGET')
            if _abt:
                controllers[0].air_barrel_target = float(_abt)
        # Gated stab-defense (§5j) validation knob (independent of river bluff):
        # STAB_DEFENSE=intensity, STAB_DEFENSE_READ=synthetic stab-freq (default 1.0
        # = simulate a detected stabber, trips the 0.5 gate). Measured vs the
        # adaptive stabber (does it recover the −1.2?) + vs static (false-pos cost).
        _sd = os.environ.get('STAB_DEFENSE')
        if _sd:
            controllers[0].stab_defense_intensity = float(_sd)
            controllers[0].stab_defense_override = float(os.environ.get('STAB_DEFENSE_READ', '1.0'))
        # Skill-tier knob (PLAYER_SKILL_SPECTRUM.md Phase 3 monotonicity check):
        # SKILL_TIER=shark|reg|weak_reg|rec applies the whole tier bundle at once
        # (exploitation_strength + river_bluff_fraction + stab_defense_intensity +
        # overbet_fraction). Applied LAST so it's the single high-level lever — it
        # overrides any manual knob above. make_controller bypasses __init__, so we
        # also turn the overbet layer ON and inject synthetic opponent reads (so the
        # gated river-bluff / stab-defense layers actually fire at the tier's
        # intensity vs a simulated reader/stabber); override the reads via
        # RIVER_BLUFF_FTBB / STAB_DEFENSE_READ to probe other regimes (e.g. a caller).
        _skill = os.environ.get('SKILL_TIER')
        if _skill:
            from poker.strategy.skill_tiers import SKILL_TIERS

            # Two reasons we read the spec directly rather than calling
            # apply_skill_tier here:
            #  1. make_controller bypasses __init__, so every field must be
            #     materialized explicitly — apply_skill_tier no-ops the default
            #     `shark` tier by design, which would leave the ceiling
            #     un-materialized and the ladder incomparable.
            #  2. This block also wires enable_overbet_context + synthetic
            #     opponent reads below — harness-only scaffolding the tier spec
            #     doesn't (and shouldn't) own.
            _spec = SKILL_TIERS[_skill]
            controllers[0].exploitation_strength = _spec.exploitation_strength
            controllers[0].river_bluff_fraction = _spec.river_bluff_fraction
            controllers[0].stab_defense_intensity = _spec.stab_defense_intensity
            controllers[0].overbet_fraction = _spec.overbet_fraction
            controllers[0].enable_overbet_context = True
            controllers[0].river_bluff_ftbb_override = float(
                os.environ.get('RIVER_BLUFF_FTBB', '1.0')
            )
            controllers[0].stab_defense_override = float(
                os.environ.get('STAB_DEFENSE_READ', '1.0')
            )
        # Range-aware prototype: turn on equity-vs-range for the hero and feed it
        # perfect-read field stats (uniform-field assumption: all opponents share
        # the first opponent archetype's stats). Concept-test ceiling.
        if config_arch.get('use_range_equity'):
            from experiments.simulate_bb100 import ARCHETYPE_STATS

            controllers[0].use_range_equity = True
            controllers[0]._assumed_opp_stats = ARCHETYPE_STATS.get(opponents[0])

        for i, (seat, cfg) in enumerate(zip(opponent_seats, opp_configs, strict=False)):
            controllers.append(
                make_controller(
                    seat, cfg, strategy_table, sm, rng_seed=hand_seed + 1_000_000 * (i + 1)
                )
            )

        hero_overbet_obs = [] if adaptive_reader_state is not None else None
        hero_faced_raise_obs = [] if adaptive_aggressor_state is not None else None
        hero_faced_bet_obs = [] if adaptive_stabber_state is not None else None
        final_stacks, callcall_river = run_passivity_hand(
            sm,
            controllers,
            hero_name,
            stats,
            hero_overbet_obs=hero_overbet_obs,
            hero_faced_raise_obs=hero_faced_raise_obs,
            hero_faced_bet_obs=hero_faced_bet_obs,
        )
        if adaptive_reader_state is not None and hero_overbet_obs:
            for hs in hero_overbet_obs:
                adaptive_reader_state.observe(hs in _BLUFF_CLASSES)
        if adaptive_aggressor_state is not None and hero_faced_raise_obs:
            for folded in hero_faced_raise_obs:
                adaptive_aggressor_state.observe(folded)
        if adaptive_stabber_state is not None and hero_faced_bet_obs:
            for folded in hero_faced_bet_obs:
                adaptive_stabber_state.observe(folded)
        delta = final_stacks.get(hero_name, starting_stack) - starting_stack
        deltas.append(delta)
        if callcall_river and delta < 0:
            stats.payoff_loss += 1

    if adaptive_reader_state is not None:
        s = adaptive_reader_state
        print(
            f"[ADAPTIVE_READER seed={base_seed}] learned overbet bluff_freq="
            f"{s.bluff_freq():.2f} (value={s.value_obs} bluff={s.bluff_obs})"
        )
    if adaptive_aggressor_state is not None:
        a = adaptive_aggressor_state
        print(
            f"[ADAPTIVE_AGGRESSOR seed={base_seed}] hero fold_to_raise="
            f"{a.fold_to_raise():.2f} (raises_faced={a.raises_made} folds={a.folds_induced})"
        )
    if adaptive_stabber_state is not None:
        st = adaptive_stabber_state
        print(
            f"[ADAPTIVE_STABBER seed={base_seed}] hero fold_to_stab="
            f"{st.fold_to_raise():.2f} (stabs_faced={st.raises_made} folds={st.folds_induced})"
        )

    return deltas, stats


# ── Reporting ─────────────────────────────────────────────────────────────────


def _pct(counter: Counter, key: str) -> float:
    total = sum(counter.values())
    return 100.0 * counter[key] / total if total else 0.0


def _fmt_ctx(label: str, counter: Counter) -> str:
    n = sum(counter.values())
    if label == 'unopened':
        return (
            f"  {label:<12}(n={n:>4}): "
            f"check {_pct(counter,'check'):4.0f}%, "
            f"bet {_pct(counter,'bet'):4.0f}%, "
            f"raise {_pct(counter,'raise'):4.0f}%"
        )
    agg = sum(counter[a] for a in _AGGRESSIVE)
    raise_pct = 100.0 * agg / n if n else 0.0
    return (
        f"  {label:<12}(n={n:>4}): "
        f"fold {_pct(counter,'fold'):4.0f}%, "
        f"call {_pct(counter,'call'):4.0f}%, "
        f"RAISE {raise_pct:4.0f}%"
    )


def print_preflop(stats: PassivityStats):
    """Preflop summary: VPIP/PFR/jam%/avg-open-size overall + by scenario.

    The short-stack range leak surfaces here: does the bot tighten / jam more
    as stacks shorten, or play the same 100bb ranges at 25bb? (It uses one
    depth-agnostic preflop chart, so the expectation is little/no adjustment.)
    """
    n = stats.pf_decisions
    if not n:
        return
    a = stats.pf_action
    vpip = 100.0 * (a['call'] + a['raise'] + a['all_in']) / n
    pfr = 100.0 * (a['raise'] + a['all_in']) / n
    jam = 100.0 * a['all_in'] / n
    avg_open = stats.pf_raise_to_bb_sum / stats.pf_raise_n if stats.pf_raise_n else 0.0
    print("\n── PREFLOP (the short-stack range leak shows here) ──")
    print(
        f"  decisions {n} | VPIP {vpip:.0f}% | PFR {pfr:.0f}% | jam {jam:.1f}% | "
        f"avg raise-to {avg_open:.1f}bb"
    )
    print(f"  {'scenario':<10} {'n':>5}  {'fold':>4} {'call':>4} {'raise':>5} {'jam':>4}")
    for sc in ('rfi', 'vs_open', 'vs_3bet', 'vs_4bet'):
        c = stats.pf_scenario_action.get(sc)
        if not c:
            continue
        sn = sum(c.values())
        print(
            f"  {sc:<10} {sn:>5}  {_pct(c,'fold'):>4.0f} {_pct(c,'call'):>4.0f} "
            f"{_pct(c,'raise'):>5.0f} {_pct(c,'all_in'):>4.0f}"
        )


def print_leak_surface(stats: PassivityStats, min_n: int = 25, top: int = 20):
    """Per-signature leak surface: where realized aggression diverges most
    from the chart's intent, ranked by |gap| × volume.

    Signature = (street, action_context, hand_class, prev_aggressor,
    double_barrel). For each (with n >= min_n): the realized action split, the
    chart's intended aggression (mean base_strategy bet+raise mass), and the
    gap (realized − chart). Negative gap = pipeline/multiway STRIPPED the
    chart's aggression; positive = added. A passive realized policy where the
    chart *also* wanted passive (gap≈0) points at the chart itself, not the
    pipeline — i.e. a candidate for a better situation policy.
    """
    rows = []
    for sig, counter in stats.sig_action.items():
        n = sum(counter.values())
        if n < min_n:
            continue
        agg = sum(counter[a] for a in _AGGRESSIVE)
        realized = agg / n
        chart = stats.sig_chart_agg_sum[sig] / n
        rows.append((sig, n, counter, realized, chart, realized - chart))
    rows.sort(key=lambda r: -abs(r[5]) * r[1])  # biggest systematic divergence first

    print(f"\n── PER-SIGNATURE LEAK SURFACE (n≥{min_n}, top {top} by |gap|×vol) ──")
    print(
        f"  {'street':<6} {'ctx':<12} {'class':<14} {'agg?':<4} {'dbl?':<4} "
        f"{'n':>5}  {'fold':>4} {'chk':>4} {'call':>4} {'AGG':>4} | {'chart':>5} {'gap':>5}"
    )
    for sig, n, counter, realized, chart, gap in rows[:top]:
        street, ctx, cls, prev_aggr, dbl = sig
        print(
            f"  {street:<6} {ctx:<12} {cls:<14} "
            f"{'Y' if prev_aggr else '-':<4} {'Y' if dbl else '-':<4} "
            f"{n:>5}  {_pct(counter,'fold'):>4.0f} {_pct(counter,'check'):>4.0f} "
            f"{_pct(counter,'call'):>4.0f} {100*realized:>4.0f} | "
            f"{100*chart:>5.0f} {100*gap:>+5.0f}"
        )


def print_tell_map(stats: PassivityStats, min_n: int = 15):
    """Size→strength readability audit: for each (street, size bucket) the hero
    bet/raised into, the hand-class composition of that betting range.

    The readability tell is the bluff share at each size: a face-up bot collapses
    to ~0% bluffs at its big sizes (value=nuts+strong, bluff=air*), so a competent
    reader folds to those bets for free. A balanced range holds bluff ≈ the
    GTO-unexploitable target s/(1+2s). `gap = bluff% − target%`: large-negative at
    a frequently-used big size = a high-value readability leak. `read` flags it.
    """
    print("\n── SIZE→STRENGTH TELL MAP (the hero's own sizing readability) ──")
    print(
        "  value=nuts+strong  bluff=air*  merge=rest.  bluff%/polar = bluff/(bluff+value)."
    )
    print(
        "  ctx=bet (first-in/checked-to) vs raise (facing a bet/raise — the raise range)."
    )
    print(
        f"  {'street':<6} {'ctx':<5} {'size':<4} {'~x pot':>6} {'n':>5}  "
        f"{'val%':>5} {'blf%':>5} {'mrg%':>5} | {'blf/pol':>7} {'gto':>5} {'gap':>5}  read"
    )
    for ctx_tag in ('bet', 'raise'):
        for street in ('FLOP', 'TURN', 'RIVER'):
            for name, lo, hi in _SIZE_BUCKETS:
                key = (street, ctx_tag, name)
                counter = stats.size_strength.get(key)
                n = sum(counter.values()) if counter else 0
                if n < min_n:
                    continue
                mean_frac = stats.size_frac_sum[key] / n
                value = sum(counter[c] for c in _VALUE_CLASSES)
                bluff = sum(counter[c] for c in _BLUFF_CLASSES)
                merge = n - value - bluff
                polar = value + bluff
                bluff_polar = bluff / polar if polar else 0.0
                target = _gto_bluff_target(mean_frac)
                gap = bluff_polar - target
                # Flag a readability leak: a big-ish size (>=~0.6 pot) that is
                # value-heavy and bluffs well under the GTO target → fold-to-it.
                read = ''
                if mean_frac >= 0.6 and value >= 3 and bluff_polar < 0.5 * target:
                    read = 'FACE-UP' if bluff_polar < 0.10 else 'thin'
                print(
                    f"  {street:<6} {ctx_tag:<5} {name:<4} {mean_frac:>6.2f} {n:>5}  "
                    f"{100*value/n:>5.0f} {100*bluff/n:>5.0f} {100*merge/n:>5.0f} | "
                    f"{100*bluff_polar:>6.0f}% {100*target:>4.0f}% {100*gap:>+4.0f}%  {read}"
                )
    print(
        "  (read=FACE-UP: big size, ~0% bluffs → a reader folds to it for free;\n"
        "   thin: under-bluffed but not pure-value. Rank fixes by gap × n.)"
    )
    if stats.check_strength:
        print("\n── CHECK-RANGE COMPOSITION (the capped-checking-range dual) ──")
        print(
            f"  {'street':<6} {'n':>5}  {'nuts':>5} {'strg':>5} {'med':>5} "
            f"{'weak':>5} {'air':>5} | {'strong%':>7}  read"
        )
        for street in ('FLOP', 'TURN', 'RIVER'):
            counter = stats.check_strength.get(street)
            n = sum(counter.values()) if counter else 0
            if n < 15:
                continue

            def _cp(*classes):
                return 100.0 * sum(counter[c] for c in classes) / n

            strong = _cp('nuts', 'strong_made')
            read = 'CAPPED' if strong < 8 else ''
            print(
                f"  {street:<6} {n:>5}  {_cp('nuts'):>5.0f} {_cp('strong_made'):>5.0f} "
                f"{_cp('medium_made'):>5.0f} {_cp('weak_made'):>5.0f} "
                f"{_cp('air', 'air_no_draw', 'air_strong_draw'):>5.0f} | {strong:>6.0f}%  {read}"
            )
        print("  (CAPPED: <8% strong → the check range has no strong hands a stab must fear.)")

    if stats.overbet_outcomes:
        print("\n  overbet_context outcomes (diagnostic):")
        for tag, n in stats.overbet_outcomes.most_common(12):
            print(f"    {tag:<44} {n}")

    # ── Preflop 3-bet readability ────────────────────────────────────────────
    # vs_open+raise = the 3-bet range. value=premium+strong, bluff=light
    # (playable/marginal/trash). A face-up 3-bet range is ~all value (a reader
    # folds to every 3-bet + 4-bet-bluffs the capped raiser); a balanced range
    # mixes in light 3-bets.
    pf = stats.pf_tier_action
    if pf:
        print("\n── PREFLOP 3-BET READABILITY (raise range tier composition) ──")
        print(
            f"  {'scenario':<9} {'n':>5}  {'prem':>5} {'strg':>5} {'play':>5} "
            f"{'marg':>5} {'trsh':>5} | {'value%':>6} {'bluff%':>6}  read"
        )
        for scenario in ('rfi', 'vs_open', 'vs_3bet'):
            counter = pf.get((scenario, 'raise'))
            n = sum(counter.values()) if counter else 0
            if not n:
                continue

            def _p(tier):
                return 100.0 * counter[tier] / n

            value = sum(counter[t] for t in _PF_VALUE_TIERS)
            bluff = sum(counter[t] for t in _PF_BLUFF_TIERS)
            label = {'rfi': 'open', 'vs_open': '3bet', 'vs_3bet': '4bet'}[scenario]
            read = ''
            if scenario in ('vs_open', 'vs_3bet') and 100.0 * bluff / n < 8:
                read = 'FACE-UP (value-only)'
            print(
                f"  {label:<9} {n:>5}  {_p('premium'):>5.0f} {_p('strong'):>5.0f} "
                f"{_p('playable'):>5.0f} {_p('marginal'):>5.0f} {_p('trash'):>5.0f} | "
                f"{100.0*value/n:>5.0f}% {100.0*bluff/n:>5.0f}%  {read}"
            )
        # Fold-to-3bet (over-fold leak, secondary): how vs_3bet decisions split.
        v3 = {a: sum(pf.get(('vs_3bet', a), Counter()).values()) for a in ('fold', 'call', 'raise')}
        v3n = sum(v3.values())
        if v3n:
            print(
                f"  vs_3bet response: fold {100*v3['fold']/v3n:.0f}%  "
                f"call {100*v3['call']/v3n:.0f}%  4bet {100*v3['raise']/v3n:.0f}%  (n={v3n})"
            )


def print_report(
    hero: str,
    opponents: List[str],
    n_hands: int,
    seeds: List[int],
    stats: PassivityStats,
    per_seed_bb100: List[Tuple[int, float]],
    mode: str,
    entry: str = 'default',
    leak_report: bool = False,
    tell_map: bool = False,
    stack_bb: int = 100,
):
    opp_desc = (
        (f'{len(opponents)}x ' + opponents[0]) if len(set(opponents)) == 1 else '+'.join(opponents)
    )
    total_hands = n_hands * len(seeds)
    print("\n" + "=" * 72)
    print(
        f"PASSIVITY (Tier A): {hero} vs {opp_desc} | mode={mode} entry={entry} stack={stack_bb}bb"
    )
    print(f"{total_hands} hands ({n_hands} x seeds {seeds})")
    print("=" * 72)

    # Track 1 leading indicator: field size at hero's postflop decisions.
    pac = stats.postflop_active_count
    pac_total = sum(pac.values())
    hu_pct = 100.0 * pac.get(2, 0) / pac_total if pac_total else 0.0
    pac_desc = ', '.join(f"{k}p={v}" for k, v in sorted(pac.items()))
    print(f"\n  Field size @ postflop decisions: [{pac_desc}]")
    print(f"    → HU (2p): {hu_pct:.0f}%  (Track 1 target: ↑ = more initiative spots)")

    print_preflop(stats)

    print("\n── PER-CONTEXT ACTION SPLIT ──")
    for ctx in ('unopened', 'facing_bet', 'facing_raise'):
        if ctx in stats.ctx_action:
            print(_fmt_ctx(ctx, stats.ctx_action[ctx]))

    # By-class detail for the high-value contexts.
    print("\n── unopened: bet% by hand_class (the diagnosed 0% leak) ──")
    for (ctx, cls), counter in sorted(stats.ctx_class_action.items()):
        if ctx != 'unopened':
            continue
        n = sum(counter.values())
        if n == 0:
            continue
        agg = sum(counter[a] for a in _AGGRESSIVE)
        print(
            f"  {cls:<14} n={n:>4}  bet/raise {100.0*agg/n:4.0f}%  "
            f"check {_pct(counter,'check'):4.0f}%"
        )

    print("\n── facing_bet / facing_raise: raise% by hand_class ──")
    for (ctx, cls), counter in sorted(stats.ctx_class_action.items()):
        if ctx not in ('facing_bet', 'facing_raise'):
            continue
        n = sum(counter.values())
        if n == 0:
            continue
        agg = sum(counter[a] for a in _AGGRESSIVE)
        print(
            f"  {ctx:<12} {cls:<14} n={n:>4}  "
            f"fold {_pct(counter,'fold'):4.0f}%  call {_pct(counter,'call'):4.0f}%  "
            f"RAISE {100.0*agg/n:4.0f}%"
        )

    print(f"\n  Postflop AggFactor (agg / passive) = {stats.agg_factor():.3f}")

    print("\n── MULTI-STREET LINE METRICS ──")
    fa = stats.flop_aggressor_with_turn
    barrel_rate = 100.0 * stats.turn_barrel / fa if fa else 0.0
    giveup_rate = 100.0 * stats.cbet_then_give_up / fa if fa else 0.0
    print(
        f"  Barrel continuation P(bet turn | bet flop): "
        f"{barrel_rate:4.0f}%  ({stats.turn_barrel}/{fa})"
    )
    print(
        f"  C-bet then check/fold turn (give-up):        "
        f"{giveup_rate:4.0f}%  ({stats.cbet_then_give_up}/{fa})"
    )
    cc = stats.callcall_river
    payoff_rate = 100.0 * stats.payoff_loss / cc if cc else 0.0
    print(
        f"  Pay-off rate (call-call-river -> lose):      "
        f"{payoff_rate:4.0f}%  ({stats.payoff_loss}/{cc})"
    )
    db = stats.double_barrel_action
    db_n = sum(db.values())
    if db_n:
        print(
            f"  Facing double-barrel split (n={db_n}): "
            f"fold {_pct(db,'fold'):3.0f}%  call {_pct(db,'call'):3.0f}%  "
            f"RAISE {100.0*sum(db[a] for a in _AGGRESSIVE)/db_n:3.0f}%"
        )
    else:
        print("  Facing double-barrel split (n=0): (no such spots sampled)")

    print("\n── SIGNAL-FREQUENCY DIAGNOSTICS (do the layer's spots occur?) ──")
    ud = stats.unopened_decisions
    upa = stats.unopened_prev_aggressor
    print(
        f"  unopened decisions: {ud}; with prior-round initiative "
        f"(was_prev_street_aggressor): {upa} ({100.0*upa/ud if ud else 0:.0f}%)"
    )
    h1n = sum(stats.h1_spot_by_active.values())
    by_active = ', '.join(f"{k}p={v}" for k, v in sorted(stats.h1_spot_by_active.items()))
    print(
        f"  H1 spots (unopened + initiative + value class): {h1n}  "
        f"by active players: [{by_active}]"
    )
    hu_h1 = stats.h1_spot_by_active.get(2, 0)
    print(f"    → of those, HU (2 players, current H1 gate): {hu_h1}")
    fb = stats.facing_bet_decisions
    fdb = stats.facing_double_barrel
    print(
        f"  facing-bet decisions: {fb}; facing a double-barrel: {fdb} "
        f"({100.0*fdb/fb if fb else 0:.0f}%); of those marginal (H2 spot): "
        f"{stats.h2_spot_marginal}"
    )

    if mode != 'off':
        print("\n── MULTI-STREET LAYER ACTIVITY (inert-trap check) ──")
        if sum(stats.layer_fires.values()) == 0:
            print("  layer never fired ⚠ (INERT — gates never met / no aggressive key)")
        for rid in ('barrel', 'fold_barrel'):
            fires = stats.layer_fires.get(rid, 0)
            changed = stats.layer_action_changed.get(rid, 0)
            print(f"  {rid:<12} fired {fires:>4}  (changed primary action {changed})")
        if stats.layer_noop_reasons:
            top = ', '.join(f"{r}={n}" for r, n in stats.layer_noop_reasons.most_common(5))
            print(f"  no-op reasons: {top}")

    print("\n── bb/100 (Tier B) ──")
    vals = [bb for _, bb in per_seed_bb100]
    mean_bb = sum(vals) / len(vals) if vals else 0.0
    for s, bb in per_seed_bb100:
        print(f"  seed {s}: {bb:+8.1f} bb/100")
    sign_disagree = len({(v > 0) for v in vals}) > 1
    print(
        f"  MEAN:    {mean_bb:+8.1f} bb/100"
        + ("   ⚠ per-seed SIGN DISAGREEMENT (noise)" if sign_disagree else "")
    )

    if leak_report:
        print_leak_surface(stats)

    if tell_map:
        print_tell_map(stats)


def _run_seed_worker(
    args: Tuple[
        str, List[str], int, int, str, str, Optional[str], Optional[frozenset], int, Optional[str]
    ],
):
    """ProcessPool worker: run one (roster, seed) cell. Loads its own table.

    Returns (seed, deltas, stats). Module-level + picklable so it can run in
    a child process (mirrors the plan's 'ProcessPoolExecutor across cells').

    `preflop_chart` (when set) is loaded into a SEPARATE hero-only strategy
    table; opponents keep the default chart. Built inside the worker (not the
    parent) so the unpicklable StrategyTable never crosses the process boundary.
    """
    (
        hero,
        opponents,
        n_hands,
        seed,
        mode,
        entry,
        clone_profile,
        h1_classes,
        stack_bb,
        preflop_chart,
    ) = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    if clone_profile:
        _ensure_clone_registered(clone_profile)
    strategy_table = load_strategy_table()
    hero_table = load_strategy_table(json_path=preflop_chart) if preflop_chart else None
    deltas, stats = run_passivity_matchup(
        hero,
        opponents,
        n_hands,
        strategy_table,
        base_seed=seed,
        mode=mode,
        entry=entry,
        h1_classes=h1_classes,
        starting_stack=stack_bb * 100,  # big_blind=100 → stack_bb effective
        hero_table=hero_table,
    )
    return seed, deltas, stats


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hero', default='Baseline', help='hero archetype (default Baseline)')
    p.add_argument(
        '--opponents', default='gto', help="roster preset (gto|mix) or comma-separated 5 archetypes"
    )
    p.add_argument('--hands', type=int, default=2000, help='hands per seed')
    p.add_argument('--seeds', default='42', help='comma-separated base seeds (e.g. 42,142,242)')
    p.add_argument(
        '--mode',
        default='off',
        choices=list(MODES),
        help='A/B arm: off | h1 | h2 | on (multi-street layer)',
    )
    p.add_argument(
        '--stack-bb',
        type=int,
        default=100,
        help='effective starting stack in BB (default 100). Sweep '
        '100/50/25/15 to probe the 100bb-tables-at-short-stack leak.',
    )
    p.add_argument(
        '--entry',
        default='default',
        choices=['default', 'isolate'],
        help="preflop entry: 'isolate' shifts OOP vs_open flat-calls to 3-bets (Track 1)",
    )
    p.add_argument(
        '--clone-profile',
        default=None,
        help=f"frozen CloneProfile JSON for a *_clone opponent "
        f"(default {DEFAULT_CLONE_PROFILE} when roster uses a clone)",
    )
    p.add_argument(
        '--h1-classes',
        default='all',
        choices=['all', 'value'],
        help="H1 barrel classes: 'all' (incl. air_strong_draw bluff-barrel) "
        "or 'value' (nuts/strong/medium only — for high-WtSD opponents)",
    )
    p.add_argument(
        '--leak-report',
        action='store_true',
        help="print the per-signature leak surface (realized vs chart "
        "aggression by line-signature) — the leak finder",
    )
    p.add_argument(
        '--tell-map',
        action='store_true',
        help="print the size→strength tell map: per (street, bet size) hand-class "
        "composition + bluff share vs the GTO target — the readability audit",
    )
    p.add_argument(
        '--preflop-chart',
        default=None,
        help="path to an alternate preflop chart JSON loaded into a HERO-ONLY "
        "strategy table (opponents keep the default chart). Default None = "
        "current behavior. e.g. poker/strategy/data/preflop_100bb_6max_wider_rfi.json",
    )
    p.add_argument(
        '--heads-up',
        action='store_true',
        help="2-handed (hero + 1 opponent) so EVERY postflop decision is HU — "
        "the HU-postflop-leak diagnostic. Collapses the roster to a single "
        "opponent (e.g. --opponents jeff --heads-up = 1 Jeff_clone).",
    )
    p.add_argument(
        '--oracle-opp',
        action='store_true',
        help="turn the clone opponent(s) into the perfect overbet-PUNISHER "
        "(folds non-nuts to any >=1.2x-pot bet) — measures the HERO's face-up "
        "value-overbet exploitability (SIZING_AWARE_OPPONENT_MODELING.md §D). "
        "Run with/without and diff: the drop = how much a sizing-reader extracts.",
    )
    args = p.parse_args()
    if args.oracle_opp:
        os.environ['ORACLE_PUNISH_OVERBETS'] = '1'  # inherited by ProcessPool workers
    if args.preflop_chart and not os.path.exists(args.preflop_chart):
        print(f"--preflop-chart not found: {args.preflop_chart}")
        sys.exit(1)
    h1_classes = (
        frozenset({'nuts', 'strong_made', 'medium_made'}) if args.h1_classes == 'value' else None
    )

    if args.opponents in ROSTERS:
        opponents = ROSTERS[args.opponents]
    else:
        opponents = [o.strip() for o in args.opponents.split(',')]
    # Heads-up: collapse to a single opponent → a 2-handed game. ALL postflop
    # decisions are then HU, so the existing postflop diagnostics (c-bet/barrel/
    # AggFactor, per-context split, leak surface) describe HU postflop directly.
    # This is the HU-leak diagnostic: the bot has no HU postflop chart, so it
    # plays HU postflop from the 6-max chart (multiway suppression no-ops at 2
    # players) — does that leak, and where?
    if args.heads_up:
        opponents = opponents[:1]
    elif len(opponents) != 5:
        print(f"opponents must resolve to 5 entries (or use --heads-up for 1), got {opponents}")
        sys.exit(1)

    # Track 2: if the roster references a *_clone opponent, register the frozen
    # CloneProfile so it exists as an ARCHETYPE (in the parent for the
    # single-seed path + validation; workers re-register themselves). A named
    # preset (jeff/punisher) picks its own profile; an explicit comma roster
    # of clones falls back to jeff.
    clone_profile = args.clone_profile
    if clone_profile is None and args.opponents in ROSTER_CLONE_PROFILE:
        clone_profile = ROSTER_CLONE_PROFILE[args.opponents]
    elif clone_profile is None and any(o.endswith('_clone') for o in opponents):
        # Explicit comma roster of clones: infer the profile from the clone's
        # source name (Punisher_clone → punisher.json); fall back to jeff for an
        # unknown clone so the prior default still holds.
        first = next(o for o in opponents if o.endswith('_clone'))
        inferred = os.path.join(_CLONE_DIR, f"{first[: -len('_clone')].lower()}.json")
        clone_profile = inferred if os.path.exists(inferred) else DEFAULT_CLONE_PROFILE
    if clone_profile:
        key = _ensure_clone_registered(clone_profile)
        print(f"[CLONE] registered {key!r} from {clone_profile}")

    for o in opponents:
        if o not in ARCHETYPES:
            print(f"Unknown opponent archetype: {o}")
            sys.exit(1)
    if args.hero not in ARCHETYPES:
        print(f"Unknown hero archetype: {args.hero}")
        sys.exit(1)

    seeds = [int(s) for s in args.seeds.split(',')]

    # Run seeds concurrently (one child process per seed). The cost is the
    # opponents' equity-MC, so seeds are CPU-bound and parallelize cleanly.
    work = [
        (
            args.hero,
            opponents,
            args.hands,
            s,
            args.mode,
            args.entry,
            clone_profile,
            h1_classes,
            args.stack_bb,
            args.preflop_chart,
        )
        for s in seeds
    ]
    results = []
    if len(seeds) > 1:
        with ProcessPoolExecutor(max_workers=min(len(seeds), os.cpu_count() or 1)) as ex:
            results = list(ex.map(_run_seed_worker, work))
    else:
        results = [_run_seed_worker(work[0])]

    agg_stats = PassivityStats()
    per_seed_bb100: List[Tuple[int, float]] = []
    for seed, deltas, stats in sorted(results, key=lambda r: r[0]):
        _aggregate(agg_stats, stats)
        ms = compute_stats(deltas, big_blind=100)
        per_seed_bb100.append((seed, ms.bb100))

    print_report(
        args.hero,
        opponents,
        args.hands,
        sorted(seeds),
        agg_stats,
        per_seed_bb100,
        args.mode,
        args.entry,
        leak_report=args.leak_report,
        tell_map=args.tell_map,
        stack_bb=args.stack_bb,
    )


if __name__ == '__main__':
    main()
