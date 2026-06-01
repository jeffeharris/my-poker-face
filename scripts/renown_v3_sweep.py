#!/usr/bin/env python3
"""Rung 3 — sweep weight grids over a FROZEN renown log; report balance.

Pure + host-runnable. Loads a frozen log (renown_v3_capture.py), re-scores it
under a grid of Weights, and answers the two Rung-3 questions:

  1. RANK STABILITY — is the leaderboard robust to weight choice, or does the
     top change identity as knobs move? (a stable top = the design, not the
     knob, is doing the work). Measured as mean pairwise Spearman of the full
     ranking + the Jaccard stability of the "high-renown" (figure) set across
     all configs.

  2. TREADMILL — does renown track PERFORMANCE or VOLUME? Spearman(renown,
     hand_count) vs Spearman(renown, performance_proxy). The anti-treadmill
     claim holds iff renown correlates at least as much with performance as
     with raw volume (ideally volume-corr is low).

The grid is baseline + one-knob-at-a-time perturbations (±50%) + both volume
denominators — enough to see how the leaderboard responds without a
combinatorial blow-up.

Run:  python3 scripts/renown_v3_sweep.py /tmp/renown_log.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Dict, List

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from renown_v2_scorer import (  # noqa: E402
    RenownInputs, Weights, score_field, total_renown, high_renown_cut,
)

# Performance drivers (hand-count-independent) vs volume drivers. The treadmill
# test asks whether renown tracks the former more than raw hand-count.
PERF_DRIVERS = ("scalps", "top1", "peak_worth", "backing", "legendary", "apex")
VOLUME_DRIVERS = ("tenure", "breadth", "stakes")


# ---------------------------------------------------------------------------
# Pure rank statistics (no scipy/numpy)
# ---------------------------------------------------------------------------


def _ranks(values: List[float]) -> List[float]:
    """Fractional (average) ranks, ascending."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def spearman(xs: List[float], ys: List[float]) -> float:
    return _pearson(_ranks(xs), _ranks(ys))


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Load + weight grid
# ---------------------------------------------------------------------------


def load_log(path: str) -> Dict[str, RenownInputs]:
    with open(path) as fh:
        payload = json.load(fh)
    return {eid: RenownInputs(**d) for eid, d in payload["entities"].items()}, payload.get("meta", {})


def weight_grid() -> List[tuple]:
    """(name, Weights). Baseline first, then ±50% one-knob perturbations and
    the denomination/gate variants."""
    base = Weights()
    grid = [("baseline", base)]
    knobs = ["w_scalp", "w_top1", "w_peak_worth", "w_backing",
             "w_legendary", "w_tenure", "w_breadth", "w_stakes", "w_apex"]
    for k in knobs:
        v = getattr(base, k)
        grid.append((f"{k}=0.5x", replace(base, **{k: v * 0.5})))
        grid.append((f"{k}=1.5x", replace(base, **{k: v * 1.5})))
    grid.append(("denom=hands", replace(base, volume_denominator="hands")))
    grid.append(("cut_top=0.10", replace(base, high_renown_top_fraction=0.10)))
    grid.append(("cut_median=2x", replace(base, high_renown_median_multiple=2.0)))
    grid.append(("cut_median=4x", replace(base, high_renown_median_multiple=4.0)))
    return grid


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/renown_log.json"
    field, meta = load_log(path)
    ids = list(field)
    grid = weight_grid()

    print(f"RENOWN v2 — RUNG 3 SWEEP   log={path}")
    print(f"source={meta.get('source')} entities={len(ids)} "
          f"total_scalps={meta.get('total_scalps', 'n/a')}  configs={len(grid)}\n")

    # Score every config; record renown vector + high-renown set per config.
    renown_by_cfg: Dict[str, Dict[str, float]] = {}
    high_by_cfg: Dict[str, set] = {}
    comps_baseline = None
    for name, w in grid:
        scored = score_field(field, w)
        renowns = {e: total_renown(c) for e, c in scored.items()}
        cut = high_renown_cut(list(renowns.values()), w)
        renown_by_cfg[name] = renowns
        high_by_cfg[name] = {e for e, r in renowns.items() if r >= cut}
        if name == "baseline":
            comps_baseline = scored

    # ---- Q1: rank stability vs baseline ----
    base_vec = [renown_by_cfg["baseline"][e] for e in ids]
    print(f"{'='*78}\n[Q1] RANK STABILITY (Spearman of ranking vs baseline; "
          f"figure-set Jaccard)\n{'='*78}")
    print(f"{'config':22} {'rankρ':>7} {'figJacc':>8}  #figures")
    rho_sum = 0.0
    jac_sum = 0.0
    base_figset = high_by_cfg["baseline"]
    for name, _ in grid:
        vec = [renown_by_cfg[name][e] for e in ids]
        rho = spearman(base_vec, vec)
        jac = jaccard(base_figset, high_by_cfg[name])
        if name != "baseline":
            rho_sum += rho
            jac_sum += jac
        print(f"{name:22} {rho:7.3f} {jac:8.2f}  {len(high_by_cfg[name])}")
    n = len(grid) - 1
    print(f"\nmean (non-baseline): rankρ={rho_sum/n:.3f}  figJaccard={jac_sum/n:.2f}")

    # Per-entity figure frequency — who is robustly a figure across all configs?
    freq = {e: sum(1 for s in high_by_cfg.values() if e in s) for e in ids}
    robust = sorted([e for e in ids if freq[e] >= len(grid)], key=lambda e: -renown_by_cfg["baseline"][e])
    flicker = [e for e in ids if 0 < freq[e] < len(grid)]
    print(f"robust figures (high in ALL {len(grid)} configs): "
          f"{', '.join(field[e].label for e in robust) or '(none)'}")
    print(f"borderline (figure in some but not all configs): {len(flicker)}")

    # ---- Q2: treadmill correlation (baseline) ----
    print(f"\n{'='*78}\n[Q2] TREADMILL (baseline): does renown track PERFORMANCE "
          f"or VOLUME?\n{'='*78}")
    base_ren = [renown_by_cfg["baseline"][e] for e in ids]
    hand_count = [float(field[e].total_hands) for e in ids]
    perf = [sum(comps_baseline[e][d] for d in PERF_DRIVERS) for e in ids]
    vol = [sum(comps_baseline[e][d] for d in VOLUME_DRIVERS) for e in ids]
    rho_hands = spearman(base_ren, hand_count)
    rho_perf = spearman(base_ren, perf)
    rho_vol = spearman(base_ren, vol)
    total_scalps = sum(sum(field[e].scalps.values()) for e in ids)
    print(f"Spearman(renown, hand_count)        = {rho_hands:+.3f}   <- the treadmill axis")
    print(f"Spearman(renown, performance_drivers) = {rho_perf:+.3f}   <- scalps/top1/backing/apex/…")
    print(f"Spearman(renown, volume_drivers)    = {rho_vol:+.3f}   <- tenure/breadth/stakes")
    if total_scalps == 0:
        print("\nTREADMILL VERDICT: N/A — this log has NO scalps (the main "
              "performance signal), so the perf proxy is gutted and the verdict "
              "is meaningless. Run a --from-sim log (populates scalps) for the "
              "real answer.")
    else:
        verdict = "PASS ✅ (renown tracks performance ≥ raw volume)" if rho_perf >= rho_hands \
            else "FAIL ❌ (renown is volume-led — treadmill)"
        print(f"\nTREADMILL VERDICT ({total_scalps} scalps): {verdict}")

    # Leaderboard at baseline for the eyeball check.
    print(f"\n{'-'*78}\nBASELINE TOP 12\n{'-'*78}")
    order = sorted(ids, key=lambda e: -renown_by_cfg["baseline"][e])[:12]
    for rank, e in enumerate(order, 1):
        c = comps_baseline[e]
        top = max(c, key=c.get)
        ren = renown_by_cfg["baseline"][e]
        fig = "★" if e in base_figset else " "
        print(f"{rank:>2} {fig} {field[e].label:24} {ren:7.2f}  {top} "
              f"({c[top]/ren*100:.0f}%)" if ren > 0 else f"{rank:>2} {field[e].label}")


if __name__ == "__main__":
    main()
