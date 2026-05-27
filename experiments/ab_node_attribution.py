"""Paired-CRN per-node EV attribution for a strategy A/B.

The meta-lever (codex #2): an A/B that tells you not just "change X is +N bb/100"
but WHERE the value comes from — which chart node(s) the edge lives in.

Method (common random numbers + first-divergence attribution):
  - Run two arms (A=baseline table, B=candidate table) on the IDENTICAL deck
    (same per-hand seed + same fixed opponents), capturing each arm's ordered
    hero decision trace via run_passivity_hand(hero_trace=...).
  - Pre-divergence the two arms see identical state, so their traces match
    exactly up to the FIRST decision where the two charts prescribe a different
    action. That first-divergence node is the causal root of the hand splitting,
    so the whole hand's paired delta (Δ_B − Δ_A) is attributed to it.
  - Aggregate paired deltas by node → contribution to the overall bb/100, plus
    per-node frequency and per-occurrence edge. Contributions SUM to the total
    (conservation), so the decomposition is exact.

Hands that never diverge contribute exactly 0 (identical play on the same deck)
and land in the NO_DIVERGENCE bucket — a built-in sanity check.

Usage (validate against the known tight→wide ship: the +16 bb/100 vs jeff MUST
concentrate in rfi|CO/BTN/SB preflop nodes):
    docker compose exec -T backend python -m experiments.ab_node_attribution \
        jeff 3000 42,4042,8042 --a tight --b wide
"""
import argparse
import math
import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging

logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.measure_passivity import (
    PassivityStats,
    ROSTER_CLONE_PROFILE,
    ROSTERS,
    _apply_mode,
    _ensure_clone_registered,
    run_passivity_hand,
)
from experiments.simulate_bb100 import (
    ARCHETYPES,
    _make_seat_names,
    make_controller,
    make_game_state,
)
from poker.poker_state_machine import PokerStateMachine
from poker.strategy.strategy_table import load_strategy_table

BIG_BLIND = 100
STARTING_STACK = 10000

# Named chart arms. Preflop variants are loaded via json_path; the "slices" arm
# merges the restored low-SPR + 3BP postflop precision slices into the base
# postflop table (re-judging the cut slices per node — codex #1). "wide"/"base"
# = current production (shipped 2026-05-27); "tight" = preserved pre-widening.
CHARTS = {
    'tight': 'poker/strategy/data/preflop_100bb_6max_tight_rfi.json',
    'wide': 'poker/strategy/data/preflop_100bb_6max.json',  # production
    'base': 'poker/strategy/data/preflop_100bb_6max.json',  # alias for production
}

# Restored precision-slice postflop tables (from commit 0164ce64^). Merged into
# the base postflop dict for the "slices" arm.
SLICE_POSTFLOP_PATHS = [
    'poker/strategy/data/postflop_strategies_low_spr.json',
    'poker/strategy/data/postflop_strategies_3bp.json',
]


def _build_table(arm):
    """Return a StrategyTable for a named arm.

    'slices' = production preflop + base postflop + the restored low-SPR/3BP
    precision-slice entries merged into _postflop (so exact-match lookups hit
    them before the degrade ladder). Everything else = a preflop-chart variant
    (CHARTS key or a raw json_path) with the default postflop.
    """
    if arm == 'slices':
        import json
        from poker.strategy.strategy_table import _parse_postflop_json
        t = load_strategy_table()  # production preflop + authored (SRP,high) postflop
        for path in SLICE_POSTFLOP_PATHS:
            if not os.path.exists(path):
                raise FileNotFoundError(
                    f"slice table missing: {path}\n"
                    f"Restore the cut slices first:\n"
                    f"  git checkout 0164ce64^ -- poker/strategy/data/postflop_strategies_low_spr.json "
                    f"poker/strategy/data/postflop_strategies_3bp.json"
                )
            t._postflop.update(_parse_postflop_json(json.load(open(path))))
        return t
    return load_strategy_table(json_path=CHARTS.get(arm, arm))

# Rule-bot / clone rosters (mirror ab_preflop_width). Rule bots ignore the
# strategy table, so opponents are identical across both arms.
LOCAL_ROSTERS = {
    'station': ['CallStation'] * 5,
    'maniac': ['ManiacBot'] * 5,
    'lag': ['LAG'] * 5,
    'nit': ['Nit'] * 5,
    'rock': ['Rock'] * 5,
}


def _resolve_roster(name):
    return LOCAL_ROSTERS[name] if name in LOCAL_ROSTERS else ROSTERS[name]


def _run_one_hand(hero_name, config_arch, hero_table, opponent_seats, opp_configs, opp_table, hand_seed, dealer_idx, starting_stack=STARTING_STACK):
    """One hand for one arm; return (hero_delta, hero_trace). Mirrors
    run_passivity_matchup's per-hand setup exactly so both arms share deck +
    opponents and differ only in hero_table."""
    all_names = [hero_name] + opponent_seats
    random.seed(hand_seed)
    gs = make_game_state(
        player_names=all_names, big_blind=BIG_BLIND, starting_stack=starting_stack,
        dealer_idx=dealer_idx, seed=hand_seed,
    )
    sm = PokerStateMachine(gs)
    sm.current_hand_seed = hand_seed
    controllers = [make_controller(hero_name, config_arch, hero_table, sm, rng_seed=hand_seed)]
    controllers[0].opponent_model_manager = None
    _apply_mode(controllers[0], 'off')
    controllers[0].multistreet_h1_classes = None
    for i, (seat, cfg) in enumerate(zip(opponent_seats, opp_configs, strict=False)):
        controllers.append(
            make_controller(seat, cfg, opp_table, sm, rng_seed=hand_seed + 1_000_000 * (i + 1))
        )
    trace = []
    final_stacks, _ = run_passivity_hand(sm, controllers, hero_name, PassivityStats(), hero_trace=trace)
    delta = final_stacks.get(hero_name, starting_stack) - starting_stack
    return delta, trace


def _first_divergence(trace_a, trace_b):
    """Return (phase, node_key) of the first decision where the two arms differ,
    or None if the traces are identical (no divergence)."""
    m = min(len(trace_a), len(trace_b))
    for i in range(m):
        if trace_a[i] != trace_b[i]:
            return (trace_a[i][0], trace_a[i][1])
    if len(trace_a) == len(trace_b):
        return None  # identical → NO_DIVERGENCE
    # Identical prefix but different length (shouldn't happen on a shared deck
    # with identical actions — guard anyway): attribute to the first extra node.
    longer = trace_a if len(trace_a) > len(trace_b) else trace_b
    return (longer[m][0], longer[m][1])


def _run_seed(args):
    roster_name, n_hands, seed, hero_arch, arm_a, arm_b, stack_bb = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    if roster_name in ROSTER_CLONE_PROFILE:
        _ensure_clone_registered(ROSTER_CLONE_PROFILE[roster_name])
    opponents = _resolve_roster(roster_name)
    table_a = _build_table(arm_a)
    table_b = _build_table(arm_b)
    opp_table = load_strategy_table()  # opponents are rule/clone bots → table irrelevant
    starting_stack = stack_bb * BIG_BLIND

    hero_name = hero_arch if hero_arch not in opponents else f"{hero_arch}_hero"
    opponent_seats = _make_seat_names(opponents)
    if hero_name in opponent_seats:
        hero_name = f"{hero_arch}_hero"
    config_arch = ARCHETYPES[hero_arch]
    opp_configs = [ARCHETYPES[o] for o in opponents]

    # bucket -> [n, sum_delta, sumsq_delta]
    buckets = defaultdict(lambda: [0, 0.0, 0.0])
    for hand_num in range(n_hands):
        hand_seed = seed + hand_num
        dealer_idx = hand_num % 6
        da, ta = _run_one_hand(hero_name, config_arch, table_a, opponent_seats, opp_configs, opp_table, hand_seed, dealer_idx, starting_stack)
        db, tb = _run_one_hand(hero_name, config_arch, table_b, opponent_seats, opp_configs, opp_table, hand_seed, dealer_idx, starting_stack)
        paired = db - da
        div = _first_divergence(ta, tb)
        key = ('-', 'NO_DIVERGENCE') if div is None else div
        b = buckets[key]
        b[0] += 1
        b[1] += paired
        b[2] += paired * paired
    return dict(buckets)


def _merge(into, src):
    for k, (n, s, sq) in src.items():
        b = into[k]
        b[0] += n
        b[1] += s
        b[2] += sq


def _bb(chips):
    return 100.0 * (chips / BIG_BLIND)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('roster', help='roster preset (jeff/punisher/station/maniac/lag/nit/rock/gto/mix)')
    p.add_argument('hands', type=int, help='hands per seed')
    p.add_argument('seeds', help='comma-separated base seeds (space them >= hands apart to stay independent)')
    p.add_argument('--hero', default='Baseline', help='hero archetype (default Baseline)')
    p.add_argument('--a', default='tight', help='baseline arm chart (CHARTS key or path)')
    p.add_argument('--b', default='wide', help='candidate arm chart (CHARTS key or path)')
    p.add_argument('--top', type=int, default=25, help='top-N nodes by |contribution|')
    p.add_argument('--stack-bb', type=int, default=100,
                   help='effective starting stack in BB (default 100). Use 50/25 to put the '
                   'low-SPR slices in play (they barely fire at 100bb).')
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(',')]

    work = [(args.roster, args.hands, s, args.hero, args.a, args.b, args.stack_bb) for s in seeds]
    merged = defaultdict(lambda: [0, 0.0, 0.0])
    if len(seeds) > 1:
        with ProcessPoolExecutor(max_workers=min(len(seeds), os.cpu_count() or 1)) as ex:
            for res in ex.map(_run_seed, work):
                _merge(merged, res)
    else:
        _merge(merged, _run_seed(work[0]))

    total_n = sum(b[0] for b in merged.values())
    total_sum = sum(b[1] for b in merged.values())
    total_sumsq = sum(b[2] for b in merged.values())
    # total bb/100 + CI over per-hand paired deltas
    mean = total_sum / total_n if total_n else 0.0
    var = (total_sumsq - total_n * mean * mean) / (total_n - 1) if total_n > 1 else 0.0
    se = math.sqrt(var / total_n) if total_n else 0.0
    tot_bb, ci_bb = _bb(mean), _bb(1.96 * se)

    print(f"\n=== PER-NODE ATTRIBUTION: B={args.b} vs A={args.a} | roster={args.roster} | "
          f"stack={args.stack_bb}bb | {args.hands}h x {len(seeds)} seeds = {total_n} hands ===")
    print(f"TOTAL paired (B-A) = {tot_bb:+.2f} bb/100  95% CI [{tot_bb-ci_bb:+.2f}, {tot_bb+ci_bb:+.2f}]")
    nd = merged.get(('-', 'NO_DIVERGENCE'), [0, 0.0, 0.0])
    print(f"NO_DIVERGENCE: {nd[0]} hands ({100.0*nd[0]/total_n:.1f}%), residual {_bb(nd[1]/total_n):+.3f} bb/100 (should be ~0)")

    # Per-node rows: contribution = sum/total_N (sums to TOTAL); when-fires = sum/n.
    rows = []
    for (phase, node), (n, s, sq) in merged.items():
        if (phase, node) == ('-', 'NO_DIVERGENCE'):
            continue
        contrib_bb = _bb(s / total_n)
        whenfires_bb = _bb(s / n) if n else 0.0
        rows.append((node, phase, n, 100.0 * n / total_n, contrib_bb, whenfires_bb))
    rows.sort(key=lambda r: -abs(r[4]))

    print(f"\n  {'node_key':<34} {'ph':<5} {'n':>5} {'freq%':>6} {'contrib bb/100':>15} {'when-fires':>11}")
    for node, phase, n, freq, contrib, whenfires in rows[:args.top]:
        print(f"  {node[:34]:<34} {phase[:5]:<5} {n:>5} {freq:>6.1f} {contrib:>+15.2f} {whenfires:>+11.1f}")

    # Rollup by phase and by preflop scenario|position.
    def rollup(keyfn, title):
        agg = defaultdict(lambda: [0, 0.0])
        for (phase, node), (n, s, sq) in merged.items():
            if (phase, node) == ('-', 'NO_DIVERGENCE'):
                continue
            k = keyfn(phase, node)
            agg[k][0] += n
            agg[k][1] += s
        print(f"\n  -- rollup by {title} --")
        for k, (n, s) in sorted(agg.items(), key=lambda kv: -abs(kv[1][1])):
            print(f"     {k:<22} n={n:>6}  {_bb(s/total_n):+.2f} bb/100")

    rollup(lambda phase, node: phase, "phase")
    rollup(lambda phase, node: '|'.join(node.split('|')[:2]) if phase == 'PRE_FLOP' else phase,
           "preflop scenario|position (postflop folded into phase)")


if __name__ == '__main__':
    main()
