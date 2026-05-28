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

The arms can also differ by *multistreet flag flavor* (same chart) via
--a-mode/--b-mode (off|h1|h2|on) — the small extension the POSTFLOP_NEXT_LEVER
plan calls for, to attribute the barrel-coherence layer per node vs realistic
folders (where the self-play CRN gate read it null):
    docker compose exec -T backend python -m experiments.ab_node_attribution \
        jeff 4000 42,4042,8042 --a base --b base --b-mode h1 --heads-up
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
    MODES,
    ROSTER_CLONE_PROFILE,
    ROSTERS,
    PassivityStats,
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


HU_CBET_TARGET = 0.55  # HU c-bet/barrel floor for the non-value (bluff/merge) range


def _hu_aggressive_transform(table):
    """Mutate `table._postflop` toward HU-appropriate aggression: in unopened
    spots (c-bet / barrel as the aggressor) with a non-value hand, raise the
    aggressive-action mass to >= HU_CBET_TARGET by shifting check mass into a
    bet action. Value classes (nuts/strong_made) and facing-bet spots are left
    alone — the diagnostic showed value-betting is already fine; the leak is
    under-c-betting/barreling the bluff range (air/weak ~18% vs HU ~55%).
    A directional candidate to QUANTIFY the leak, not a solved chart.
    """
    from poker.strategy.multiway import VALUE_CLASSES
    from poker.strategy.strategy_profile import StrategyProfile

    for key, profile in list(table._postflop.items()):
        parts = key.split('|')
        if len(parts) < 7:
            continue
        hand_class, action_context = parts[4], parts[6]
        if action_context != 'unopened' or hand_class in VALUE_CLASSES:
            continue
        probs = dict(profile.action_probabilities)
        agg = [a for a in probs if a.startswith(('bet_', 'raise_')) or a == 'jam']
        cur_agg = sum(probs[a] for a in agg)
        if cur_agg >= HU_CBET_TARGET or probs.get('check', 0.0) <= 0:
            continue
        take = min(HU_CBET_TARGET - cur_agg, probs['check'])
        probs['check'] -= take
        target_bet = max(agg, key=lambda a: probs[a]) if agg else 'bet_67'
        probs[target_bet] = probs.get(target_bet, 0.0) + take
        table._postflop[key] = StrategyProfile(action_probabilities=probs)
    return table


def _size_collapse_transform(table):
    """Collapse every postflop node's *bet sizing* to a single canonical size:
    all ``bet_*`` mass → ``bet_67``, all ``raise_*`` mass → ``raise_67``.
    Preserves the total bet / raise / jam / check / call / fold mass — only the
    SIZE choice within bets and within raises is flattened. The measure-first
    gate for "does the chart's size-mixing earn anything, or is size selection
    cosmetic?" (A=base mixed sizes vs B=size_collapse). ``jam`` (all-in) is left
    separate — this isolates sized-bet/raise granularity, not bet-vs-jam.
    """
    from poker.strategy.strategy_profile import StrategyProfile

    CANON_BET, CANON_RAISE = 'bet_67', 'raise_67'
    for key, profile in list(table._postflop.items()):
        probs = dict(profile.action_probabilities)
        bet_mass = sum(p for a, p in probs.items() if a.startswith('bet_'))
        raise_mass = sum(p for a, p in probs.items() if a.startswith('raise_'))
        if bet_mass <= 0 and raise_mass <= 0:
            continue
        new = {a: p for a, p in probs.items() if not a.startswith(('bet_', 'raise_'))}
        if bet_mass > 0:
            new[CANON_BET] = new.get(CANON_BET, 0.0) + bet_mass
        if raise_mass > 0:
            new[CANON_RAISE] = new.get(CANON_RAISE, 0.0) + raise_mass
        table._postflop[key] = StrategyProfile(action_probabilities=new)
    return table


def _overbet_transform(table, classes, streets=('TURN', 'RIVER'), contexts=('unopened',), overbet_size=150):
    """Convert the betting mass in *polarized aggressor spots* to a 150% pot
    OVERBET (bet_150 — the menu has no overbet today; the resolver handles it).

    Directional probe (like `hu_aggro`): in `streets` × `contexts` nodes whose
    hand_class is in `classes`, relabel ALL bet_* mass to `bet_150`. Theory:
    overbets earn with a polarized range on later streets — value classes
    extract more from callers, air classes get more fold equity from folders
    (but spew vs stations). `classes={nuts,strong_made}` isolates the robust
    value side; adding `air_no_draw` tests the opponent-dependent bluff side.
    Tune the mix later only if the directional probe pays.
    """
    from poker.strategy.strategy_profile import StrategyProfile

    streets = {s.upper() for s in streets}
    classes = set(classes)
    for key, profile in list(table._postflop.items()):
        parts = key.split('|')
        if len(parts) < 8:
            continue
        street, hand_class, action_context = parts[0].upper(), parts[4], parts[6]
        if street not in streets or hand_class not in classes or action_context not in contexts:
            continue
        probs = dict(profile.action_probabilities)
        bet_mass = sum(p for a, p in probs.items() if a.startswith('bet_'))
        if bet_mass <= 0:
            continue
        new = {a: p for a, p in probs.items() if not a.startswith('bet_')}
        ob = f'bet_{overbet_size}'
        new[ob] = new.get(ob, 0.0) + bet_mass
        table._postflop[key] = StrategyProfile(action_probabilities=new)
    return table


def _build_table(arm):
    """Return a StrategyTable for a named arm.

    'slices' = production preflop + base postflop + the restored low-SPR/3BP
    precision-slice entries merged into _postflop (so exact-match lookups hit
    them before the degrade ladder).
    'hu_aggro' = production preflop + base postflop with the HU-aggressive
    transform applied (the HU-postflop-leak quantification candidate).
    'size_collapse' = base postflop with every node's bet/raise sizing flattened
    to one canonical size (the "does size-mixing matter?" gate).
    Everything else = a preflop-chart variant (CHARTS key or a raw json_path)
    with the default postflop.
    """
    if arm == 'size_collapse':
        return _size_collapse_transform(load_strategy_table())
    # overbet arms: 'overbet_value' / 'overbet_polar', optional '_<size>' suffix
    # (default 150% pot). value = nuts/strong only; polar adds the air_no_draw
    # bluff side. e.g. overbet_value_200 = 200% pot value overbets.
    if arm.startswith('overbet_value') or arm.startswith('overbet_polar'):
        polar = arm.startswith('overbet_polar')
        classes = {'nuts', 'strong_made', 'air_no_draw'} if polar else {'nuts', 'strong_made'}
        suffix = arm.split('_')[-1]
        size = int(suffix) if suffix.isdigit() else 150
        return _overbet_transform(load_strategy_table(), classes, overbet_size=size)
    if arm == 'hu_aggro':
        return _hu_aggressive_transform(load_strategy_table())
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


def _run_one_hand(hero_name, config_arch, hero_table, opponent_seats, opp_configs, opp_table, hand_seed, dealer_idx, starting_stack=STARTING_STACK, mode='off', h1_streets=None, overbet=False):
    """One hand for one arm; return (hero_delta, hero_trace). Mirrors
    run_passivity_matchup's per-hand setup exactly so both arms share deck +
    opponents and differ only in hero_table AND the multistreet `mode`
    (off|h1|h2|on) — letting an arm pair differ by flag flavor (same chart) as
    well as by chart, so the gate can attribute the multistreet_context layer."""
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
    _apply_mode(controllers[0], mode)
    controllers[0].multistreet_h1_classes = None
    controllers[0].multistreet_h1_streets = h1_streets
    # Overbet runtime layer flag (default off; production __init__ also defaults
    # False until validated). Set per-arm so the gate can A/B the LAYER on the
    # SAME chart — symmetric to the multistreet flag-flavor arm.
    controllers[0].enable_overbet_context = overbet
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
    roster_name, n_hands, seed, hero_arch, arm_a, arm_b, stack_bb, heads_up, a_mode, b_mode, h1_streets, a_overbet, b_overbet, adaptive_opp = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    if roster_name in ROSTER_CLONE_PROFILE:
        # `adaptive_opp` registers the perfect-overbet-punisher clone variant under
        # the same archetype key (D1 measurement instrument) — both arms then face
        # the oracle, but only the overbet-ON arm produces the bet_150 that trips it.
        _ensure_clone_registered(
            ROSTER_CLONE_PROFILE[roster_name], oracle_punish_overbets=adaptive_opp
        )
    opponents = _resolve_roster(roster_name)
    if heads_up:
        opponents = opponents[:1]  # 2-handed → all postflop decisions are HU
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
        dealer_idx = hand_num % (1 + len(opponents))
        da, ta = _run_one_hand(hero_name, config_arch, table_a, opponent_seats, opp_configs, opp_table, hand_seed, dealer_idx, starting_stack, a_mode, h1_streets, a_overbet)
        db, tb = _run_one_hand(hero_name, config_arch, table_b, opponent_seats, opp_configs, opp_table, hand_seed, dealer_idx, starting_stack, b_mode, h1_streets, b_overbet)
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
    p.add_argument('--heads-up', action='store_true',
                   help='2-handed (collapse roster to 1 opponent) → every postflop decision is '
                   'HU. Use with --b hu_aggro to quantify the HU-postflop-aggression leak.')
    p.add_argument('--a-mode', default='off', choices=list(MODES),
                   help='multistreet flag flavor for arm A (off|h1|h2|on). Default off. '
                   'Set --a base --b base --b-mode h1 to A/B the multistreet barrel layer '
                   '(same chart, flag flavor) and attribute it per node.')
    p.add_argument('--b-mode', default='off', choices=list(MODES),
                   help='multistreet flag flavor for arm B (off|h1|h2|on). Default off.')
    p.add_argument('--h1-streets', default='all',
                   help="streets H1 barrel-continuation fires on: 'all' (default) or a "
                   "comma-separated subset (e.g. 'flop,turn' to drop the toxic river "
                   "barrel found by per-node attribution). Applies to whichever arm runs H1.")
    p.add_argument('--overbet-a', action='store_true',
                   help="enable the overbet_context runtime layer on arm A. With --overbet-b on "
                   "the other arm this A/Bs the production layer (vs the load-time "
                   "_overbet_transform arm in --b).")
    p.add_argument('--overbet-b', action='store_true',
                   help="enable the overbet_context runtime layer on arm B (default off). Set "
                   "--a base --b base --overbet-b to A/B the layer flag-flavor cleanly.")
    p.add_argument('--adaptive-opp', action='store_true',
                   help="make a CLONE opponent (jeff/punisher rosters) the perfect-overbet-PUNISHER "
                   "(D1, SIZING_AWARE_OPPONENT_MODELING.md): it max-folds all but near-nuts vs a "
                   ">=1.2x-pot bet. Pair with --overbet-b to measure the overbet's exploitability "
                   "CEILING; attribution stays clean (only the overbet-ON arm makes that size).")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(',')]

    h1_streets = (
        None if args.h1_streets == 'all'
        else frozenset(s.strip().upper() for s in args.h1_streets.split(','))
    )
    work = [(args.roster, args.hands, s, args.hero, args.a, args.b, args.stack_bb, args.heads_up, args.a_mode, args.b_mode, h1_streets, args.overbet_a, args.overbet_b, args.adaptive_opp) for s in seeds]
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

    a_label = f"{args.a}/{args.a_mode}" if args.a_mode != 'off' else args.a
    b_label = f"{args.b}/{args.b_mode}" if args.b_mode != 'off' else args.b
    if args.overbet_a:
        a_label += "+overbet"
    if args.overbet_b:
        b_label += "+overbet"
    roster_label = f"{args.roster}{'+oracle' if args.adaptive_opp else ''}"
    print(f"\n=== PER-NODE ATTRIBUTION: B={b_label} vs A={a_label} | roster={roster_label}"
          f"{' HU' if args.heads_up else ''} | stack={args.stack_bb}bb | "
          f"{args.hands}h x {len(seeds)} seeds = {total_n} hands ===")
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
