"""Detection reachability / stat-fidelity probe (re-validation step 1).

Question this answers: when a TAG hero plays the frozen human clones
(`Punisher_clone`, `Jeff_clone`, ...), what does the exploitation detector
ACTUALLY see in `AggregatedOpponentStats`, which patterns fire, and how far do
those observed stats drift from the clone's AUTHORED profile?

The matrix (STRATEGY_REVALIDATION_MATRIX.md) flagged two things this measures
directly, reproducibly, instead of by agent estimate:
  - Punisher's authored fold_to_cbet=0.70 does NOT manifest in play (~0.06).
  - Jeff (vpip/vol ~0.35) sits in the dead zone — fires nothing — and his real
    leak is postflop AF, which the station gate (keyed on GLOBAL af) can't see.

It is read-only: it monkeypatches the detector to OBSERVE, changes no behavior.

Run:
  docker compose exec -T backend python -m experiments.detection_fidelity_probe
  docker compose exec -T backend python -m experiments.detection_fidelity_probe --hands 4000 --clones punisher,jeff
"""

import argparse
import dataclasses
import json
import os
from collections import Counter

import experiments.simulate_bb100 as sim
import poker.tiered_bot_controller as tbc
from poker.human_clone import load_profile_from_file, register_clone_strategy
from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    _is_high_fold_to_cbet,
    _is_hyper_aggressive,
    _is_hyper_passive,
    _is_loose_passive_station,
    _is_passive_with_jams,
    _is_tight_nit,
    classify_opponent_archetype,
    compute_pattern_intensity,
)

CLONE_DIR = os.path.join(os.path.dirname(__file__), "clone_profiles")

# Detected-stat field -> authored-profile key it should correspond to.
# (fold_to_cbet is the headline fidelity check: authored villain tendency vs
# hero's live observation of that villain folding to hero's c-bets.)
AUTHORED_MAP = {
    "vpip_per_voluntary_opportunity": "vpip",
    "pfr_per_open_opportunity": "pfr",
    "aggression_factor": "aggression_factor",
    "fold_to_cbet": "fold_to_cbet",
}

# Fields worth printing even with no authored counterpart.
EXTRA_FIELDS = [
    "hands_observed",
    "aggression_factor_postflop",
    "call_rate_facing_bet",
    "wtsd",
    "all_in_frequency",
    "barrel_frequency",
    "cbet_faced_count",
    "facing_bet_opportunities",
    "postflop_seen_as_pfr_count",
]

DETECTORS = {
    "hyper_passive": _is_hyper_passive,
    "loose_passive": _is_loose_passive_station,
    "tight_nit": _is_tight_nit,
    "high_fold_to_cbet": _is_high_fold_to_cbet,
    "hyper_aggressive": _is_hyper_aggressive,
    "passive_with_jams": _is_passive_with_jams,
}


def _register_clone(name):
    path = os.path.join(CLONE_DIR, f"{name}.json")
    profile = load_profile_from_file(path)
    src = profile.source_player
    strategy_key = f"clone_{src.replace(' ', '_').lower()}"
    register_clone_strategy(strategy_key, profile)
    archetype_key = f"{src}_clone"
    sim.ARCHETYPES[archetype_key] = {"kind": "rule_bot", "strategy": strategy_key}
    with open(path) as fh:
        authored = json.load(fh)
    return archetype_key, authored


def _run_arm(hero, villain_archetype, hands, seed, mode):
    """Run TAG vs the clone, capturing the stats the detector sees per decision.

    Returns (final_stats_dict, pattern_fire_counts, archetype_counts, total).
    `final_stats` = the stats object with the MOST hands_observed (the mature
    read at the end of the run); fire counts are over all mature decisions.
    """
    captured = []  # (hands_observed, stats_dict, patterns, archetype)
    fire = Counter()
    arch = Counter()
    total = {"n": 0}

    orig = tbc.classify_detected_patterns

    def wrapped(stats):
        patterns = orig(stats)
        # Only mature reads (the detector early-returns < 15 internally for
        # offsets, but classify still runs; mirror the cold-start floor here).
        if stats.hands_observed >= 15:
            total["n"] += 1
            for p in patterns:
                fire[p] += 1
            arch[classify_opponent_archetype(stats) or "unmatched"] += 1
            captured.append((stats.hands_observed, dataclasses.asdict(stats)))
        return patterns

    tbc.classify_detected_patterns = wrapped
    try:
        st = sim.load_strategy_table()
        if mode == "hu":
            sim.run_matchup(hero, villain_archetype, hands, st, base_seed=seed)
        else:
            sim.run_6max_matchup(
                hero,
                hands,
                st,
                base_seed=seed,
                opponents=[villain_archetype] * 5,
            )
    finally:
        tbc.classify_detected_patterns = orig

    final = max(captured, key=lambda c: c[0])[1] if captured else {}
    return final, fire, arch, total["n"]


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _report(clone, authored, mode, final, fire, arch, total):
    print(f"\n{'='*70}\n{clone}  [{mode}]   mature decisions observed: {total}\n{'='*70}")
    if not final:
        print("  (no mature reads — opponent never reached 15 observed hands)")
        return
    print("  AUTHORED vs DETECTED (what the leak IS vs what hero SEES):")
    print(f"    {'stat':<34}{'authored':>10}{'detected':>12}")
    for det_field, auth_key in AUTHORED_MAP.items():
        a = authored.get(auth_key)
        d = final.get(det_field)
        astr = _fmt(a) if a is not None else "  —"
        dstr = _fmt(d) if d is not None else "  —"
        flag = ""
        if a is not None and d is not None and abs(a - d) >= 0.20:
            flag = "  <-- GAP"
        print(f"    {det_field:<34}{astr:>10}{dstr:>12}{flag}")
    print("  context (detected, no authored counterpart):")
    for f in EXTRA_FIELDS:
        if f in final:
            print(f"    {f:<34}{'':>10}{_fmt(final[f]):>12}")
    # final-state detector booleans + intensities (reconstruct a real stats
    # object so the detector fns type-check and behave exactly as in play)
    fake = AggregatedOpponentStats(**final)
    print("  detectors on final read:")
    for name, fn in DETECTORS.items():
        try:
            fires = fn(fake)
        except Exception as e:  # noqa: BLE001 - diagnostic only
            fires = f"err:{e}"
        print(f"    {name:<24}{str(fires)}")
    try:
        inten = compute_pattern_intensity(fake)
        nz = {k: round(v, 3) for k, v in inten.items() if v}
        print(f"  intensities (final): {nz or '{}'}")
    except Exception as e:  # noqa: BLE001
        print(f"  intensities: err:{e}")
    print(f"  archetype classified (final): {classify_opponent_archetype(fake) or 'unmatched'}")
    print("  pattern fire-rate across mature decisions:")
    if not fire:
        print("    (none fired)")
    for p, c in fire.most_common():
        print(f"    {p:<24}{c:>7}  ({100.0*c/total:.1f}%)")
    print(f"  archetype distribution: {dict(arch)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--hero", default="TAG")
    ap.add_argument(
        "--clones",
        default="punisher,jeff",
        help="comma list of clone_profiles/<name>.json basenames",
    )
    ap.add_argument("--modes", default="hu,6max")
    args = ap.parse_args()

    clones = [c.strip() for c in args.clones.split(",") if c.strip()]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    print(f"Detection-fidelity probe — hero={args.hero}  hands={args.hands}  seed={args.seed}")
    for clone in clones:
        archetype_key, authored = _register_clone(clone)
        for mode in modes:
            final, fire, arch, total = _run_arm(
                args.hero, archetype_key, args.hands, args.seed, mode
            )
            _report(archetype_key, authored, mode, final, fire, arch, total)


if __name__ == "__main__":
    main()
