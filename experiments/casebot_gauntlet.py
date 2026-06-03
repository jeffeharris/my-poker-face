#!/usr/bin/env python3
"""CaseBot improvement gauntlet — one scorecard for a candidate rule bot across
the full battery (heads-up AND 6-max, vs tight/loose/aggressive/passive fields).

The goal: a bot that doesn't just crush leaky multiway fields but also holds up
heads-up vs a disciplined value-bettor (where the original CaseBot loses −29.6).
"best" = maximize the WORST cell (be at least break-even everywhere), not just
the average.

Reuses experiments.measure_passivity's per-seed worker so bb/100 is identical to
a hand `measure_passivity` run. HU cells pass a 1-opponent roster (the worker
makes a 2-handed game); 6-max cells pass 5.

Usage (inside the backend container):
  docker compose exec -T backend python -m experiments.casebot_gauntlet --hero CaseBot
  docker compose exec -T backend python -m experiments.casebot_gauntlet --hero CaseBotV2 --hands 1000 --seeds 42,3042,6042
"""

import argparse
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.measure_passivity import _run_seed_worker  # noqa: E402
from experiments.simulate_bb100 import compute_stats  # noqa: E402

# Battery: (label, opponents-list). 1 opp = HU, 5 opps = 6-max.
BATTERY = [
    # ── heads-up (the original CaseBot's blind spot) ──
    # NB: the always_call 'CallStation' rule bot is excluded — HU it sends every
    # hand to showdown and is pathologically slow; the tiered 'Calling Station'
    # 6-max cell covers the passive-field test instead.
    ('HU vs TAG', ['TAG']),
    ('HU vs Maniac', ['Maniac']),
    ('HU vs Nit', ['Nit']),
    ('HU vs Station', ['Calling Station']),
    ('HU vs CaseBot', ['CaseBot']),
    # ── 6-max ──
    ('6max vs TAG x5', ['TAG'] * 5),
    ('6max vs Nit x5', ['Nit'] * 5),
    ('6max vs Station x5', ['Calling Station'] * 5),
    ('6max vs Maniac x5', ['Maniac'] * 5),
    ('6max vs mixed(2LAG+3TAG)', ['LAG', 'LAG', 'TAG', 'TAG', 'TAG']),
    ('6max vs CaseBot x5', ['CaseBot'] * 5),
]


def run_gauntlet(hero, hands, seeds, stack_bb=100):
    work, index = [], []
    for label, opps in BATTERY:
        for s in seeds:
            # (hero, opponents, n_hands, seed, mode, entry, clone_profile,
            #  h1_classes, stack_bb, preflop_chart)
            work.append((hero, opps, hands, s, 'off', 'default', None, None, stack_bb, None))
            index.append(label)

    deltas_by_label = {label: [] for label, _ in BATTERY}
    max_workers = min(len(work), os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        for (seed, deltas, _stats), label in zip(
            ex.map(_run_seed_worker, work), index, strict=False
        ):
            deltas_by_label[label].append(compute_stats(deltas, big_blind=100).bb100)

    rows = []
    for label, _ in BATTERY:
        bbs = deltas_by_label[label]
        mean = sum(bbs) / len(bbs) if bbs else 0.0
        sign_disagree = len({(v > 0) for v in bbs}) > 1
        rows.append((label, mean, sign_disagree))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hero', required=True)
    p.add_argument('--hands', type=int, default=1000)
    p.add_argument('--seeds', default='42,3042,6042')
    args = p.parse_args()
    seeds = [int(s) for s in args.seeds.split(',')]
    rows = run_gauntlet(args.hero, args.hands, seeds)

    print(f"\n{'=' * 56}")
    print(f"GAUNTLET — {args.hero}  ({args.hands}h × {len(seeds)} seeds)")
    print('=' * 56)
    hu = [r for r in rows if r[0].startswith('HU')]
    six = [r for r in rows if r[0].startswith('6max')]
    for r in rows:
        label, mean, warn = r
        flag = ' ⚠' if warn else ''
        marker = '  ❌' if mean < -5 else ('  ✅' if mean > 5 else '  ➖')
        print(f"  {label:<28} {mean:+8.1f} bb/100{marker}{flag}")
    allv = [m for _, m, _ in rows]
    huv = [m for _, m, _ in hu]
    sixv = [m for _, m, _ in six]
    print('-' * 56)
    print(f"  WORST cell:   {min(allv):+8.1f}   (the number to maximize)")
    print(f"  mean all:     {sum(allv) / len(allv):+8.1f}")
    print(f"  mean HU:      {sum(huv) / len(huv):+8.1f}")
    print(f"  mean 6max:    {sum(sixv) / len(sixv):+8.1f}")


if __name__ == '__main__':
    main()
