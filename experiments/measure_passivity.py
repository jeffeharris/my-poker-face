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

from poker.poker_game import (
    play_turn, advance_to_next_active_player,
)
from poker.poker_state_machine import PokerStateMachine, PokerPhase
from poker.strategy.strategy_table import load_strategy_table
from poker.strategy.multistreet_context import derive_signals, H1_BARREL_TARGET, H2_FOLD_TARGET
from poker.strategy.preflop_isolate import build_isolation_table
from experiments.simulate_bb100 import (
    ARCHETYPES, make_controller, make_game_state, compute_stats,
    DEFAULT_RULE_OPPONENTS, MAX_ACTIONS_PER_HAND, TERMINAL_PHASES,
    _make_seat_names,
)

# Opponent roster presets. Per the plan, GTO-Lite is the precision-rewarding
# primary (Jeff_clone is unavailable — the DB has no observed hands). The MIX
# is the regression / guardrail reference.
ROSTERS = {
    'gto': ['GTO-Lite'] * 5,
    'mix': DEFAULT_RULE_OPPONENTS,
}

_AGGRESSIVE = {'bet', 'raise', 'all_in'}
_POSTFLOP_STREETS = ('FLOP', 'TURN', 'RIVER')
_PREV_STREET = {'TURN': 'FLOP', 'RIVER': 'TURN'}


@dataclass
class PassivityStats:
    """Tier-A accumulator across a run (one hero archetype, all seeds)."""
    # action_context -> resolved_action -> count
    ctx_action: Dict[str, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    # (action_context, hand_class) -> resolved_action -> count
    ctx_class_action: Dict[Tuple[str, str], Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    # facing-double-barrel decisions: resolved_action -> count
    double_barrel_action: Counter = field(default_factory=Counter)

    # Per-hand line metrics
    flop_aggressor_with_turn: int = 0   # hero bet/raised flop AND saw a turn action
    turn_barrel: int = 0                # ...and bet/raised the turn
    cbet_then_give_up: int = 0          # ...but checked/folded the turn

    callcall_river: int = 0             # called flop + called turn + reached river
    payoff_loss: int = 0                # ...and lost the hand

    postflop_decisions: int = 0

    # Multi-street layer fire tracking (the inert-trap check): how often the
    # layer fired and actually changed the action distribution.
    layer_fires: Counter = field(default_factory=Counter)        # rule_id -> count
    layer_action_changed: Counter = field(default_factory=Counter)  # rule_id -> count
    layer_noop_reasons: Counter = field(default_factory=Counter)  # reason_code -> count

    # Signal-frequency diagnostics (computed independently of whether the
    # layer fires — answers "do the spots even occur?"). The crux of the
    # honest-null vs gate-too-tight question.
    unopened_decisions: int = 0
    unopened_prev_aggressor: int = 0       # ...where hero had prior-round initiative
    # active-player count when H1's spot (unopened + prev_aggressor + value class) holds
    h1_spot_by_active: Counter = field(default_factory=Counter)
    facing_bet_decisions: int = 0
    facing_double_barrel: int = 0          # facing bet AND opp double-barreled
    h2_spot_marginal: int = 0              # ...with a marginal made hand (H2 target)

    # Field size at hero postflop decisions — the Track 1 leading indicator.
    # If sharpening preflop ENTRY (isolate) works, this distribution shifts
    # toward HU (2 players), creating the initiative spots the bot lacks.
    postflop_active_count: Counter = field(default_factory=Counter)

    def record_decision(self, node_key: str, action: str,
                        opp_bet_flop: bool, opp_bet_prev: bool, street: str):
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
            and opp_bet_flop and opp_bet_prev
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


def _apply_mode(controller, mode: str):
    """Set the multi-street-context A/B arm on the hero controller.

    Inert until the layer + flag land on TieredBotController (plan Step 3).
    'off' is the current behavior (no flag set). The other arms set the flag
    plus per-hypothesis sub-toggles the layer reads.
    """
    controller.enable_multistreet_context = (mode != 'off')
    # Per-hypothesis gating the layer honors (default both on for 'on').
    controller.multistreet_h1_barrel = mode in ('h1', 'on')
    controller.multistreet_h2_foldbarrel = mode in ('h2', 'on')


def run_passivity_hand(sm, controllers, hero_name: str, stats: PassivityStats):
    """Drive one hand; instrument the hero's postflop decisions.

    Mirrors simulate_bb100.run_hand's action driving (run_until, run_it_out,
    play_turn, advance) so chip outcomes match, but adds:
      - per-hand hero/opp street-action tracking (barrel / pay-off / d-barrel)
      - the new _sim_hero_bet_by_street / _sim_opp_bet_by_street fields the
        multi-street layer reads (driven here the same way the existing
        _sim_* aggressor fields are).
    """
    controller_map = {c.player_name: c for c in controllers}
    hero = controller_map.get(hero_name)
    action_count = 0

    # Reset sim-path multi-street state at hand start (mirrors
    # MemoryManager.on_hand_start in production).
    if hero is not None:
        hero._sim_last_preflop_aggressor = None
        hero._sim_recent_aggressor = None
        hero._sim_hero_bet_by_street = {}   # {phase: True} streets hero bet/raised
        hero._sim_opp_bet_by_street = {}    # {phase: True} streets any opp bet/raised
    sim_current_street: Optional[str] = None

    # Per-hand line tracking (hero perspective).
    hero_actions_by_street: Dict[str, List[str]] = defaultdict(list)
    opp_bet_by_street: Dict[str, bool] = defaultdict(bool)
    hero_reached_river = False

    while sm.phase not in TERMINAL_PHASES:
        sm.run_until(list(TERMINAL_PHASES))
        if sm.phase in TERMINAL_PHASES:
            break
        gs = sm.game_state

        if gs.run_it_out:
            sm.game_state = gs.update(run_it_out=False, awaiting_action=False)
            next_phase = {
                PokerPhase.PRE_FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.FLOP: PokerPhase.DEALING_CARDS,
                PokerPhase.TURN: PokerPhase.DEALING_CARDS,
                PokerPhase.RIVER: PokerPhase.EVALUATING_HAND,
            }.get(sm.phase, PokerPhase.EVALUATING_HAND)
            sm.phase = next_phase
            continue

        current_player = gs.current_player
        controller = controller_map[current_player.name]
        controller.state_machine = sm

        is_hero = current_player.name == hero_name
        phase_name = sm.phase.name

        # Clear the snapshot before the hero acts so a stale postflop snapshot
        # from a prior street can't be misread as this decision's.
        if is_hero:
            controller._last_pipeline_snapshot = {}

        decision = controller.decide_action()
        action = decision['action']
        raise_to = decision.get('raise_to', 0) or 0

        # ── Instrument the hero's postflop decision ─────────────────────────
        if is_hero and phase_name in _POSTFLOP_STREETS:
            snap = getattr(controller, '_last_pipeline_snapshot', {}) or {}
            node_key = snap.get('node_key')
            if node_key:
                prev = _PREV_STREET.get(phase_name)
                opp_bet_flop = opp_bet_by_street.get('FLOP', False)
                opp_bet_prev = opp_bet_by_street.get(prev, False) if prev else False
                stats.record_decision(
                    node_key, action,
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
            hero_actions_by_street[phase_name].append(action)
            if phase_name == 'RIVER':
                hero_reached_river = True
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

        new_gs = play_turn(gs, action, raise_to)

        # ── Drive sim aggressor / multi-street state (accepted actions) ─────
        if phase_name == 'PRE_FLOP' and action in ('raise', 'all_in') and hero is not None:
            hero._sim_last_preflop_aggressor = current_player.name

        if hero is not None:
            if sim_current_street != phase_name:
                hero._sim_recent_aggressor = None
                sim_current_street = phase_name
            if phase_name in _POSTFLOP_STREETS and action in _AGGRESSIVE:
                hero._sim_recent_aggressor = current_player.name
                # New fields: split hero's own line from opponents' line.
                if is_hero:
                    hero._sim_hero_bet_by_street[phase_name] = True
                else:
                    hero._sim_opp_bet_by_street[phase_name] = True

        # Mirror into the per-hand line trackers (used for end-of-hand metrics).
        if phase_name in _POSTFLOP_STREETS and action in _AGGRESSIVE and not is_hero:
            opp_bet_by_street[phase_name] = True

        advanced = advance_to_next_active_player(new_gs)
        sm.game_state = advanced if advanced is not None else new_gs

        action_count += 1
        if action_count >= MAX_ACTIONS_PER_HAND:
            break

    final_stacks = {p.name: p.stack for p in sm.game_state.players}

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
        delta = final_stacks.get(hero_name, 0)
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
) -> Tuple[List[float], PassivityStats]:
    """Run n_hands of 6-max (hero + 5 opponents); return (deltas, Tier-A stats).

    Setup mirrors simulate_bb100.run_6max_matchup exactly (seat names, dealer
    rotation, per-hand global+rng seeding) so chip deltas / bb/100 are
    directly comparable to the main harness.

    `entry='isolate'` gives the HERO a preflop chart where OOP vs_open
    flat-calls are shifted to 3-bets (Track 1). Opponents keep the default
    chart, so the A/B isolates the hero's entry change.
    """
    if len(opponents) != 5:
        raise ValueError(f"opponents must have 5 entries, got {len(opponents)}")

    hero_table = (
        build_isolation_table(strategy_table) if entry == 'isolate'
        else strategy_table
    )

    hero_name = (
        hero_archetype if hero_archetype not in opponents
        else f"{hero_archetype}_hero"
    )
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{hero_archetype}_hero"
    all_names = [hero_name] + opponent_seats

    config_arch = ARCHETYPES[hero_archetype]
    opp_configs = [ARCHETYPES[o] for o in opponents]

    stats = PassivityStats()
    deltas: List[float] = []

    for hand_num in range(n_hands):
        hand_seed = base_seed + hand_num
        dealer_idx = hand_num % 6
        random.seed(hand_seed)  # per-hand global-random reset (rule bots)

        gs = make_game_state(
            player_names=all_names, big_blind=big_blind,
            starting_stack=starting_stack, dealer_idx=dealer_idx,
            seed=hand_seed,
        )
        sm = PokerStateMachine(gs)
        sm.current_hand_seed = hand_seed

        controllers = [
            make_controller(hero_name, config_arch, hero_table, sm,
                            rng_seed=hand_seed)
        ]
        # No opponent_manager: Baseline (anchors=None) skips exploitation and
        # equity recording only writes to models, so omitting it is identical
        # for decisions/stacks and disables equity-MC (plan requirement).
        controllers[0].opponent_model_manager = None
        _apply_mode(controllers[0], mode)

        for i, (seat, cfg) in enumerate(zip(opponent_seats, opp_configs)):
            controllers.append(
                make_controller(seat, cfg, strategy_table, sm,
                                rng_seed=hand_seed + 1_000_000 * (i + 1))
            )

        final_stacks, callcall_river = run_passivity_hand(
            sm, controllers, hero_name, stats,
        )
        delta = final_stacks.get(hero_name, starting_stack) - starting_stack
        deltas.append(delta)
        if callcall_river and delta < 0:
            stats.payoff_loss += 1

    return deltas, stats


# ── Reporting ─────────────────────────────────────────────────────────────────

def _pct(counter: Counter, key: str) -> float:
    total = sum(counter.values())
    return 100.0 * counter[key] / total if total else 0.0


def _fmt_ctx(label: str, counter: Counter) -> str:
    n = sum(counter.values())
    if label == 'unopened':
        return (f"  {label:<12}(n={n:>4}): "
                f"check {_pct(counter,'check'):4.0f}%, "
                f"bet {_pct(counter,'bet'):4.0f}%, "
                f"raise {_pct(counter,'raise'):4.0f}%")
    agg = sum(counter[a] for a in _AGGRESSIVE)
    raise_pct = 100.0 * agg / n if n else 0.0
    return (f"  {label:<12}(n={n:>4}): "
            f"fold {_pct(counter,'fold'):4.0f}%, "
            f"call {_pct(counter,'call'):4.0f}%, "
            f"RAISE {raise_pct:4.0f}%")


def print_report(hero: str, opponents: List[str], n_hands: int,
                 seeds: List[int], stats: PassivityStats,
                 per_seed_bb100: List[Tuple[int, float]], mode: str,
                 entry: str = 'default'):
    opp_desc = ('5x ' + opponents[0]) if len(set(opponents)) == 1 else '+'.join(opponents)
    total_hands = n_hands * len(seeds)
    print("\n" + "=" * 72)
    print(f"PASSIVITY (Tier A): {hero} vs {opp_desc} | mode={mode} entry={entry}")
    print(f"{total_hands} hands ({n_hands} x seeds {seeds})")
    print("=" * 72)

    # Track 1 leading indicator: field size at hero's postflop decisions.
    pac = stats.postflop_active_count
    pac_total = sum(pac.values())
    hu_pct = 100.0 * pac.get(2, 0) / pac_total if pac_total else 0.0
    pac_desc = ', '.join(f"{k}p={v}" for k, v in sorted(pac.items()))
    print(f"\n  Field size @ postflop decisions: [{pac_desc}]")
    print(f"    → HU (2p): {hu_pct:.0f}%  (Track 1 target: ↑ = more initiative spots)")

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
        print(f"  {cls:<14} n={n:>4}  bet/raise {100.0*agg/n:4.0f}%  "
              f"check {_pct(counter,'check'):4.0f}%")

    print("\n── facing_bet / facing_raise: raise% by hand_class ──")
    for (ctx, cls), counter in sorted(stats.ctx_class_action.items()):
        if ctx not in ('facing_bet', 'facing_raise'):
            continue
        n = sum(counter.values())
        if n == 0:
            continue
        agg = sum(counter[a] for a in _AGGRESSIVE)
        print(f"  {ctx:<12} {cls:<14} n={n:>4}  "
              f"fold {_pct(counter,'fold'):4.0f}%  call {_pct(counter,'call'):4.0f}%  "
              f"RAISE {100.0*agg/n:4.0f}%")

    print(f"\n  Postflop AggFactor (agg / passive) = {stats.agg_factor():.3f}")

    print("\n── MULTI-STREET LINE METRICS ──")
    fa = stats.flop_aggressor_with_turn
    barrel_rate = 100.0 * stats.turn_barrel / fa if fa else 0.0
    giveup_rate = 100.0 * stats.cbet_then_give_up / fa if fa else 0.0
    print(f"  Barrel continuation P(bet turn | bet flop): "
          f"{barrel_rate:4.0f}%  ({stats.turn_barrel}/{fa})")
    print(f"  C-bet then check/fold turn (give-up):        "
          f"{giveup_rate:4.0f}%  ({stats.cbet_then_give_up}/{fa})")
    cc = stats.callcall_river
    payoff_rate = 100.0 * stats.payoff_loss / cc if cc else 0.0
    print(f"  Pay-off rate (call-call-river -> lose):      "
          f"{payoff_rate:4.0f}%  ({stats.payoff_loss}/{cc})")
    db = stats.double_barrel_action
    db_n = sum(db.values())
    if db_n:
        print(f"  Facing double-barrel split (n={db_n}): "
              f"fold {_pct(db,'fold'):3.0f}%  call {_pct(db,'call'):3.0f}%  "
              f"RAISE {100.0*sum(db[a] for a in _AGGRESSIVE)/db_n:3.0f}%")
    else:
        print(f"  Facing double-barrel split (n=0): (no such spots sampled)")

    print("\n── SIGNAL-FREQUENCY DIAGNOSTICS (do the layer's spots occur?) ──")
    ud = stats.unopened_decisions
    upa = stats.unopened_prev_aggressor
    print(f"  unopened decisions: {ud}; with prior-round initiative "
          f"(was_prev_street_aggressor): {upa} ({100.0*upa/ud if ud else 0:.0f}%)")
    h1n = sum(stats.h1_spot_by_active.values())
    by_active = ', '.join(f"{k}p={v}" for k, v in sorted(stats.h1_spot_by_active.items()))
    print(f"  H1 spots (unopened + initiative + value class): {h1n}  "
          f"by active players: [{by_active}]")
    hu_h1 = stats.h1_spot_by_active.get(2, 0)
    print(f"    → of those, HU (2 players, current H1 gate): {hu_h1}")
    fb = stats.facing_bet_decisions
    fdb = stats.facing_double_barrel
    print(f"  facing-bet decisions: {fb}; facing a double-barrel: {fdb} "
          f"({100.0*fdb/fb if fb else 0:.0f}%); of those marginal (H2 spot): "
          f"{stats.h2_spot_marginal}")

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
    print(f"  MEAN:    {mean_bb:+8.1f} bb/100"
          + ("   ⚠ per-seed SIGN DISAGREEMENT (noise)" if sign_disagree else ""))


def _run_seed_worker(args: Tuple[str, List[str], int, int, str, str]):
    """ProcessPool worker: run one (roster, seed) cell. Loads its own table.

    Returns (seed, deltas, stats). Module-level + picklable so it can run in
    a child process (mirrors the plan's 'ProcessPoolExecutor across cells').
    """
    hero, opponents, n_hands, seed, mode, entry = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    strategy_table = load_strategy_table()
    deltas, stats = run_passivity_matchup(
        hero, opponents, n_hands, strategy_table, base_seed=seed, mode=mode,
        entry=entry,
    )
    return seed, deltas, stats


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hero', default='Baseline', help='hero archetype (default Baseline)')
    p.add_argument('--opponents', default='gto',
                   help="roster preset (gto|mix) or comma-separated 5 archetypes")
    p.add_argument('--hands', type=int, default=2000, help='hands per seed')
    p.add_argument('--seeds', default='42', help='comma-separated base seeds (e.g. 42,142,242)')
    p.add_argument('--mode', default='off', choices=['off', 'h1', 'h2', 'on'],
                   help='multi-street-context A/B arm (postflop layer)')
    p.add_argument('--entry', default='default', choices=['default', 'isolate'],
                   help="preflop entry: 'isolate' shifts OOP vs_open flat-calls to 3-bets (Track 1)")
    args = p.parse_args()

    if args.opponents in ROSTERS:
        opponents = ROSTERS[args.opponents]
    else:
        opponents = [o.strip() for o in args.opponents.split(',')]
    if len(opponents) != 5:
        print(f"opponents must resolve to 5 entries, got {opponents}")
        sys.exit(1)
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
    work = [(args.hero, opponents, args.hands, s, args.mode, args.entry) for s in seeds]
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

    print_report(args.hero, opponents, args.hands, sorted(seeds), agg_stats,
                 per_seed_bb100, args.mode, args.entry)


if __name__ == '__main__':
    main()
