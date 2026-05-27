"""Paired base-vs-wider preflop-RFI A/B vs a real (folding) opponent roster.

For each seed and each hand, run the hero with BOTH the base chart and the
wider chart on the IDENTICAL deck (same base_seed → same cards/positions), and
take the per-hand paired delta (wider_stack - base_stack). Most hands are 0
(identical play); only late-position-RFI spots differ. Paired differencing
cancels the shared poker variance, so the mean paired delta is a far tighter
estimate of the chart change's value than two independent bb/100 means.

Reports mean paired bb/100 + normal CI (per-hand paired deltas are independent:
each hand is a fresh reseeded 6-max deal) and per-seed means (watch for sign
disagreement = noise).

Usage:
  docker compose exec -T backend python ab_preflop_width.py <roster> <hands> <seeds-csv>
  e.g. ... ab_preflop_width.py jeff 3000 42,142,242
"""
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logging
logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.measure_passivity import (
    ROSTERS, ROSTER_CLONE_PROFILE, _ensure_clone_registered, run_passivity_matchup,
)
from poker.strategy.strategy_table import load_strategy_table

# Explicit chart paths. NOTE: wide is now PRODUCTION (shipped 2026-05-27), so
# load_strategy_table() defaults to wide — we must name the tight chart
# explicitly. Paired metric is (wide − tight), matching the pre-ship jeff/punisher
# numbers (+15.97 / +5.33). A CI-clear NEGATIVE result vs some opponent is the
# precondition that justifies building opponent-adaptive width (EXP_003 Phase 0).
TIGHT_PATH = 'poker/strategy/data/preflop_100bb_6max_tight_rfi.json'
WIDE_PATH = 'poker/strategy/data/preflop_100bb_6max.json'  # production (now wide)
BIG_BLIND = 100

# Sticky / punishing rule-bot rosters for the Phase-0 "does wide ever lose?"
# gate, plus tight folders as a positive control. Rule bots ignore the strategy
# table, so opponents are unaffected by which chart the hero holds. Merged over
# measure_passivity's clone rosters (gto/mix/jeff/punisher).
LOCAL_ROSTERS = {
    'station': ['CallStation'] * 5,   # pure never-folder — wide should lose here if anywhere
    'maniac': ['ManiacBot'] * 5,      # relentless raiser — punishes wide opens
    'lag': ['LAG'] * 5,               # loose-aggressive 3-bettor
    'nit': ['Nit'] * 5,               # tight folder — positive control (expect wide +EV)
    'rock': ['Rock'] * 5,             # tight-aggressive — positive control
}


def _resolve_roster(name):
    if name in LOCAL_ROSTERS:
        return LOCAL_ROSTERS[name]
    return ROSTERS[name]


def _run_seed(args):
    roster_name, n_hands, seed = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    if roster_name in ROSTER_CLONE_PROFILE:
        _ensure_clone_registered(ROSTER_CLONE_PROFILE[roster_name])
    opp = _resolve_roster(roster_name)
    opp_table = load_strategy_table()  # opponents are rule/clone bots → table irrelevant to them
    tight = load_strategy_table(json_path=TIGHT_PATH)
    wide = load_strategy_table(json_path=WIDE_PATH)
    dt, _ = run_passivity_matchup('Baseline', opp, n_hands, opp_table, base_seed=seed, mode='off', hero_table=tight)
    dw, _ = run_passivity_matchup('Baseline', opp, n_hands, opp_table, base_seed=seed, mode='off', hero_table=wide)
    paired = [w - t for w, t in zip(dw, dt)]  # wide − tight
    n_diff = sum(1 for p in paired if p != 0)
    return seed, paired, n_diff, sum(dt), sum(dw)


def main():
    roster_name = sys.argv[1] if len(sys.argv) > 1 else 'jeff'
    n_hands = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    seeds = [int(s) for s in (sys.argv[3] if len(sys.argv) > 3 else '42,142,242').split(',')]

    work = [(roster_name, n_hands, s) for s in seeds]
    if len(seeds) > 1:
        with ProcessPoolExecutor(max_workers=min(len(seeds), os.cpu_count() or 1)) as ex:
            results = list(ex.map(_run_seed, work))
    else:
        results = [_run_seed(work[0])]
    results.sort()

    all_paired = []
    print(f"\n=== preflop-width A/B (WIDE - TIGHT) vs {roster_name} | {n_hands}h x {len(seeds)} seeds ===")
    print(f"{'seed':>6} {'n_diff':>7} {'tight_bb/100':>13} {'wide_bb/100':>12} {'paired_bb/100':>14}")
    for seed, paired, n_diff, st, sw in results:
        all_paired.extend(paired)
        n = len(paired)
        tight_bb = 100.0 * (st / BIG_BLIND) / n
        wide_bb = 100.0 * (sw / BIG_BLIND) / n
        paired_bb = 100.0 * (sum(paired) / BIG_BLIND) / n
        print(f"{seed:>6} {n_diff:>7} {tight_bb:>13.2f} {wide_bb:>12.2f} {paired_bb:>+14.2f}")

    N = len(all_paired)
    mean = sum(all_paired) / N
    var = sum((p - mean) ** 2 for p in all_paired) / (N - 1)
    se = math.sqrt(var / N)
    mean_bb = 100.0 * (mean / BIG_BLIND)
    ci_bb = 100.0 * (1.96 * se / BIG_BLIND)
    n_diff_total = sum(1 for p in all_paired if p != 0)
    print(f"\n  N hands={N}  hands differing={n_diff_total} ({100.0*n_diff_total/N:.1f}%)")
    print(f"  PAIRED mean = {mean_bb:+.2f} bb/100   95% CI [{mean_bb-ci_bb:+.2f}, {mean_bb+ci_bb:+.2f}]")
    verdict = "POSITIVE (wide wins)" if mean_bb - ci_bb > 0 else (
        "NEGATIVE (wide loses → adaptive-tighten target!)" if mean_bb + ci_bb < 0 else "NEUTRAL (CI spans 0)")
    print(f"  VERDICT: {verdict}")


if __name__ == '__main__':
    main()
