#!/usr/bin/env python3
"""Variety + fish validation driver — the executable backing for
docs/plans/VARIETY_VALIDATION_AND_DEPLOY_HANDOFF.md (tasks A / B / D) and the
recurring eval (task E).

It reuses experiments.measure_passivity's per-seed worker (`_run_seed_worker`)
and `compute_stats` so the numbers are byte-identical to running
`measure_passivity` by hand, but sweeps a whole archetype × depth (× field)
grid in ONE process pool and emits a compact markdown table per sweep — the
deliverable Jeff asked to be documented.

Sweeps:
  A (short-stack):  measure each archetype's VPIP/PFR/jam/AF/bb100 at
                    {100,50,25}bb vs a foldy Baseline field. Red-flag the
                    precedence flip (100bb width tables forced at all depths).
  D (buy-in depth): Calling Station / WeakFish drain vs depth {40,60,80,100}bb
                    vs a TAG-grinder field — the cycling-lever curve.
  B (pricing):      aggressive archetypes vs a CALLING field at {40,100}bb,
                    paired with the foldy-field number, so the honest cost the
                    foldy field hid is visible side by side.

Usage (inside the backend container):
  docker compose exec -T backend python -m experiments.variety_eval A --hands 1500 --seeds 42,3042,6042
  docker compose exec -T backend python -m experiments.variety_eval D --hands 1500 --seeds 42,3042,6042
  docker compose exec -T backend python -m experiments.variety_eval B --hands 1500 --seeds 42,3042,6042
  docker compose exec -T backend python -m experiments.variety_eval all --hands 1500 --seeds 42,3042,6042
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.measure_passivity import (  # noqa: E402
    DEFAULT_CLONE_PROFILE,
    PUNISHER_CLONE_PROFILE,
    _run_seed_worker,
)
from experiments.simulate_bb100 import compute_stats  # noqa: E402

# ── Field rosters (5 opponents each) ────────────────────────────────────────
FOLDY_FIELD = ['Baseline'] * 5  # over-folds → makes aggression look cheap
TAG_GRINDER_FIELD = ['TAG'] * 5  # disciplined regs — the drain-vs-depth field
# Honest "calling field" for pricing aggression: a field that DOESN'T fold, so
# bluffs get called and the true cost shows. Two flavors:
#   - JEFF: a realistic calls-down human clone (vpip ~0.39, WtSD 0.59) — folds
#     ~45% to c-bets but pays off rivers. The realistic skill-gradient target.
#   - NEVERFOLD: the always_call rule bot — the EXTREME that punishes bluffs
#     hardest (calls every bet). Upper bound on the cost of over-aggression.
# NB: the tiered 'Calling Station' ARCHETYPE is a weak passive *donator* (VPIP
# 45 / AF 0.26), not a calls-down grinder — aggression EXTRACTS from it rather
# than being punished, so it is the WRONG instrument for pricing aggression's
# cost. Kept only as a hero, not a field.
JEFF_FIELD = ['Jeff_clone'] * 5  # realistic calls-down human
NEVERFOLD_FIELD = ['CallStation'] * 5  # always_call rule bot — punishes bluffs hardest
# The competent field: a disciplined aggressive reg that folds CORRECTLY
# (punishes over-calling) AND barrels air (punishes over-folding). This is the
# only field that prices the true cost of OVER-BLUFFING (B's callers are donors,
# not punishers; B's foldy Baseline over-folds). See measure_passivity ROSTERS.
PUNISHER_FIELD = ['Punisher_clone'] * 5


def _infer_clone_profile(roster):
    """If the roster references a *_clone opponent, return the frozen profile
    path so the worker registers it (mirrors measure_passivity). Resolves the
    profile from the clone's source name so Punisher_clone → punisher.json and
    Jeff_clone → jeff.json (not all clones default to jeff)."""
    for o in roster:
        if o.endswith('_clone'):
            return PUNISHER_CLONE_PROFILE if o.startswith('Punisher') else DEFAULT_CLONE_PROFILE
    return None


def _cell_key(hero, field_label, depth):
    return (hero, field_label, depth)


def _summarize(stats, per_seed_deltas):
    """Reduce a PassivityStats + per-seed delta lists to headline metrics."""
    n = stats.pf_decisions
    a = stats.pf_action
    vpip = 100.0 * (a['call'] + a['raise'] + a['all_in']) / n if n else 0.0
    pfr = 100.0 * (a['raise'] + a['all_in']) / n if n else 0.0
    jam = 100.0 * a['all_in'] / n if n else 0.0
    avg_open = stats.pf_raise_to_bb_sum / stats.pf_raise_n if stats.pf_raise_n else 0.0
    af = stats.agg_factor()
    bb100s = [compute_stats(d, big_blind=100).bb100 for d in per_seed_deltas]
    mean_bb = sum(bb100s) / len(bb100s) if bb100s else 0.0
    sign_disagree = len({(v > 0) for v in bb100s}) > 1
    return {
        'vpip': vpip,
        'pfr': pfr,
        'jam': jam,
        'avg_open': avg_open,
        'af': af,
        'bb100': mean_bb,
        'bb100_seeds': bb100s,
        'sign_disagree': sign_disagree,
    }


def run_grid(cells, hands, seeds):
    """cells: list of (hero, field_roster, field_label, depth). Returns
    {cell_key: summary}. Runs every (cell, seed) in one process pool."""
    from experiments.measure_passivity import PassivityStats, _aggregate

    work = []
    work_index = []  # parallel to `work`: (cell_key,)
    for hero, roster, field_label, depth in cells:
        ck = _cell_key(hero, field_label, depth)
        clone_profile = _infer_clone_profile(roster)
        for s in seeds:
            # _run_seed_worker arg tuple:
            # (hero, opponents, n_hands, seed, mode, entry, clone_profile,
            #  h1_classes, stack_bb, preflop_chart)
            work.append((hero, roster, hands, s, 'off', 'default', clone_profile, None, depth, None))
            work_index.append(ck)

    # Accumulate per-cell stats + per-seed deltas.
    agg = {}  # ck -> PassivityStats
    deltas_by_cell = {}  # ck -> list[list[delta]]
    for ck in {wi for wi in work_index}:
        agg[ck] = PassivityStats()
        deltas_by_cell[ck] = []

    max_workers = min(len(work), os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        for (seed, deltas, stats), ck in zip(ex.map(_run_seed_worker, work), work_index):
            _aggregate(agg[ck], stats)
            deltas_by_cell[ck].append(deltas)

    return {ck: _summarize(agg[ck], deltas_by_cell[ck]) for ck in agg}


# ── Sweep A: short-stack validation ─────────────────────────────────────────
A_ARCHETYPES = ['Nit', 'Rock', 'TAG', 'LAG', 'Calling Station', 'Maniac']
A_DEPTHS = [100, 50, 25]


def sweep_A(hands, seeds):
    cells = [
        (h, FOLDY_FIELD, 'foldy', d) for h in A_ARCHETYPES for d in A_DEPTHS
    ]
    res = run_grid(cells, hands, seeds)
    print("\n" + "=" * 72)
    print(f"# SWEEP A — short-stack validation (vs foldy Baseline×5, "
          f"{hands}h × {len(seeds)} seeds)")
    print("=" * 72)
    print("\nMetric columns: VPIP% / PFR% / jam% / avgOpen(bb) / AF / bb100")
    for h in A_ARCHETYPES:
        print(f"\n**{h}**")
        print("| depth | VPIP | PFR | jam | avgOpen | AF | bb/100 |")
        print("|---|---|---|---|---|---|---|")
        for d in A_DEPTHS:
            s = res[_cell_key(h, 'foldy', d)]
            warn = " ⚠SIGN" if s['sign_disagree'] else ""
            print(f"| {d}bb | {s['vpip']:.0f} | {s['pfr']:.0f} | {s['jam']:.1f} | "
                  f"{s['avg_open']:.1f} | {s['af']:.2f} | {s['bb100']:+.1f}{warn} |")
    _flag_A(res)
    return res


def _flag_A(res):
    """Red-flag heuristic: jam% blowing up or VPIP cratering/exploding at 25bb."""
    print("\n#### Red-flag scan (25bb vs 100bb)")
    flags = []
    for h in A_ARCHETYPES:
        s100 = res[_cell_key(h, 'foldy', 100)]
        s25 = res[_cell_key(h, 'foldy', 25)]
        # A spewy shallow archetype: jam% jumps a lot OR VPIP stays absurdly
        # high while stacks are short (100bb-wide ranges shoved at 25bb).
        jam_jump = s25['jam'] - s100['jam']
        if s25['jam'] > 25:
            flags.append(f"- **{h}**: jam% {s25['jam']:.1f} at 25bb (>25% — check for blind jamming)")
        if jam_jump > 20:
            flags.append(f"- **{h}**: jam% +{jam_jump:.1f}pts 100→25bb")
        if s25['bb100'] < -150:
            flags.append(f"- **{h}**: bb/100 {s25['bb100']:+.1f} at 25bb (severe bleed)")
    if flags:
        print("\n".join(flags))
    else:
        print("No red flags: no archetype shows runaway jam% or severe shallow bleed.")


# ── Sweep D: buy-in depth diff ──────────────────────────────────────────────
D_ARCHETYPES = ['Calling Station', 'WeakFish']
D_DEPTHS = [40, 60, 80, 100]


def sweep_D(hands, seeds):
    cells = [
        (h, TAG_GRINDER_FIELD, 'tag', d) for h in D_ARCHETYPES for d in D_DEPTHS
    ]
    res = run_grid(cells, hands, seeds)
    print("\n" + "=" * 72)
    print(f"# SWEEP D — buy-in depth diff (fish vs TAG-grinder×5, "
          f"{hands}h × {len(seeds)} seeds)")
    print("=" * 72)
    print("\nDrain (bb/100, negative = fish loses) vs effective depth:")
    print("\n| archetype | 40bb | 60bb | 80bb | 100bb |")
    print("|---|---|---|---|---|")
    for h in D_ARCHETYPES:
        cells_bb = []
        for d in D_DEPTHS:
            s = res[_cell_key(h, 'tag', d)]
            warn = "⚠" if s['sign_disagree'] else ""
            cells_bb.append(f"{s['bb100']:+.1f}{warn}")
        print(f"| {h} | " + " | ".join(cells_bb) + " |")
    return res


# ── Sweep B: aggressive end vs calling field ────────────────────────────────
# StationPBlind isolates the position_blind lever (vs plain Calling Station hero).
B_ARCHETYPES = ['Maniac', 'LAG', 'StationPBlind', 'Calling Station']
B_DEPTHS = [40, 100]
B_FIELDS = [
    (FOLDY_FIELD, 'foldy'),       # over-folds — the optimistic number
    (JEFF_FIELD, 'jeff'),         # realistic calls-down human — the honest number
    (NEVERFOLD_FIELD, 'neverfold'),  # always-call — bluff-punishing upper bound
]


def sweep_B(hands, seeds):
    cells = []
    for h in B_ARCHETYPES:
        for d in B_DEPTHS:
            for roster, label in B_FIELDS:
                cells.append((h, roster, label, d))
    res = run_grid(cells, hands, seeds)
    print("\n" + "=" * 72)
    print(f"# SWEEP B — aggression priced across fields, {hands}h × {len(seeds)} seeds")
    print("=" * 72)
    print("\nbb/100 by field. FOLDY=Baseline×5 (over-folds), JEFF=Jeff_clone×5 "
          "(realistic calls-down human), NEVERFOLD=CallStation×5 (always_call — "
          "punishes bluffs hardest).")
    print("\nThe honest cost the foldy field hid = (FOLDY − JEFF) and (FOLDY − NEVERFOLD).")
    for h in B_ARCHETYPES:
        print(f"\n**{h}**")
        print("| depth | vs FOLDY | vs JEFF | vs NEVERFOLD | foldy−jeff | foldy−neverfold |")
        print("|---|---|---|---|---|---|")
        for d in B_DEPTHS:
            sf = res[_cell_key(h, 'foldy', d)]
            sj = res[_cell_key(h, 'jeff', d)]
            sn = res[_cell_key(h, 'neverfold', d)]

            def w(s):
                return f"{s['bb100']:+.1f}" + ("⚠" if s['sign_disagree'] else "")
            print(f"| {d}bb | {w(sf)} | {w(sj)} | {w(sn)} | "
                  f"{sf['bb100']-sj['bb100']:+.1f} | {sf['bb100']-sn['bb100']:+.1f} |")
    return res


# ── Sweep P: over-bluff / aggression priced vs the PUNISHER (competent) field ─
# StationOverBluff isolates over_bluff; StationPBlind isolates position_blind;
# Calling Station is the no-leak baseline. Maniac/LAG are the aggressive ends.
P_ARCHETYPES = ['Calling Station', 'StationOverBluff', 'StationPBlind', 'WeakFish', 'LAG', 'Maniac']
P_DEPTHS = [40, 100]


def sweep_P(hands, seeds):
    cells = []
    for h in P_ARCHETYPES:
        for d in P_DEPTHS:
            cells.append((h, PUNISHER_FIELD, 'punisher', d))
            cells.append((h, FOLDY_FIELD, 'foldy', d))  # contrast (over-folder)
    res = run_grid(cells, hands, seeds)
    print("\n" + "=" * 72)
    print(f"# SWEEP P — priced vs the PUNISHER (competent folder+barreler), "
          f"{hands}h × {len(seeds)} seeds")
    print("=" * 72)
    print("\nbb/100. PUNISHER=Punisher_clone×5 (folds correctly AND barrels air — "
          "the only field that prices over-bluffing honestly). FOLDY=Baseline×5 "
          "(over-folder) for contrast.")
    print("\n| hero | depth | vs PUNISHER | vs FOLDY | punisher−foldy |")
    print("|---|---|---|---|---|")
    for h in P_ARCHETYPES:
        for d in P_DEPTHS:
            sp = res[_cell_key(h, 'punisher', d)]
            sf = res[_cell_key(h, 'foldy', d)]
            warn = "⚠" if sp['sign_disagree'] else ""
            print(f"| {h} | {d}bb | {sp['bb100']:+.1f}{warn} | {sf['bb100']:+.1f} | "
                  f"{sp['bb100']-sf['bb100']:+.1f} |")
    # Lever isolations vs the punisher (marginal cost of each leak).
    print("\n**Lever isolation vs PUNISHER** (hero − Calling Station baseline):")
    print("\n| lever | depth | Δ bb/100 vs punisher |")
    print("|---|---|---|")
    for lever, h in [('over_bluff', 'StationOverBluff'), ('position_blind', 'StationPBlind')]:
        for d in P_DEPTHS:
            base = res[_cell_key('Calling Station', 'punisher', d)]['bb100']
            val = res[_cell_key(h, 'punisher', d)]['bb100']
            print(f"| {lever} | {d}bb | {val-base:+.1f} |")
    return res


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('sweep', choices=['A', 'B', 'D', 'P', 'all'])
    p.add_argument('--hands', type=int, default=1500)
    p.add_argument('--seeds', default='42,3042,6042')
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(',')]
    if args.sweep in ('A', 'all'):
        sweep_A(args.hands, seeds)
    if args.sweep in ('D', 'all'):
        sweep_D(args.hands, seeds)
    if args.sweep in ('B', 'all'):
        sweep_B(args.hands, seeds)
    if args.sweep in ('P', 'all'):
        sweep_P(args.hands, seeds)


if __name__ == '__main__':
    main()
