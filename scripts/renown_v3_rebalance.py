#!/usr/bin/env python3
"""Rung 3 (a) — does down-weighting volume close the treadmill VOLUME-LEAN?

The baseline sweep found renown leans to volume (hands +0.66) over performance
(net worth +0.59) on the real field. The designed fix is wall-clock
denomination (untestable on a static snapshot); the lever testable NOW is
down-weighting the volume drivers (breadth/tenure/stakes) — a blunt *proxy* for
what wall-clock denomination would do.

This probe first measures the data's INTRINSIC volume↔performance correlation
(the floor — if hands and net worth are collinear in the field, no weighting
can fully separate them), then sweeps a volume-weight scale and an alternative
standing-up-weight, reporting where (if anywhere) renown↔performance overtakes
renown↔volume. Targets (hand_count, net_worth, chips_won) are weight-INDEPENDENT,
so this is a real measurement, not a circular one.

Run:  python3 scripts/renown_v3_rebalance.py /tmp/renown_log_db.json
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from renown_v3_sweep import load_log, spearman  # noqa: E402
from renown_v2_scorer import Weights, score_field, total_renown  # noqa: E402


def corrs(field, ids, w):
    scored = score_field(field, w)
    ren = [total_renown(scored[e]) for e in ids]
    hands = [float(field[e].total_hands) for e in ids]
    worth = [field[e].peak_net_worth for e in ids]
    money = [field[e].roster_net for e in ids]
    return (spearman(ren, hands), spearman(ren, worth), spearman(ren, money))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/renown_log_db.json"
    field, meta = load_log(path)
    ids = list(field)
    base = Weights()

    # --- the confound floor: how collinear are volume and performance? ---
    hands = [float(field[e].total_hands) for e in ids]
    worth = [field[e].peak_net_worth for e in ids]
    money = [field[e].roster_net for e in ids]
    print(f"REBALANCE PROBE   log={path}  entities={len(ids)}\n")
    print("INTRINSIC field correlations (weight-independent — the separability floor):")
    print(f"  hand_count ↔ net_worth = {spearman(hands, worth):+.3f}   "
          f"(if high, volume & performance can't be fully separated by ANY weights)")
    print(f"  hand_count ↔ chips_won = {spearman(hands, money):+.3f}")
    print(f"  net_worth  ↔ chips_won = {spearman(worth, money):+.3f}\n")

    h0, w0, m0 = corrs(field, ids, base)
    print(f"baseline:                  renown↔hands {h0:+.3f}  ↔net_worth {w0:+.3f}  "
          f"↔chips {m0:+.3f}  → {'PASS' if w0>=h0 else 'VOLUME-LEAN'}")

    # --- lever 1: scale DOWN volume drivers (breadth/tenure/stakes) ---
    print("\n[lever 1] down-weight volume drivers (breadth/tenure/stakes ×scale):")
    print(f"{'scale':>6} {'↔hands':>8} {'↔networth':>10} {'↔chips':>8}  verdict")
    for scale in (1.0, 0.75, 0.5, 0.25, 0.1, 0.0):
        w = replace(base, w_breadth=base.w_breadth*scale,
                    w_tenure=base.w_tenure*scale, w_stakes=base.w_stakes*scale)
        h, wo, m = corrs(field, ids, w)
        print(f"{scale:>6.2f} {h:>8.3f} {wo:>10.3f} {m:>8.3f}  "
              f"{'PASS ✅' if wo>=h else 'volume-lean'}")

    # --- lever 2: scale UP standing drivers (top1/peak_worth/apex) ---
    print("\n[lever 2] up-weight standing drivers (top1/peak_worth/apex ×scale):")
    print(f"{'scale':>6} {'↔hands':>8} {'↔networth':>10} {'↔chips':>8}  verdict")
    for scale in (1.0, 1.5, 2.0, 3.0, 5.0):
        w = replace(base, w_top1=base.w_top1*scale,
                    w_peak_worth=base.w_peak_worth*scale, w_apex=base.w_apex*scale)
        h, wo, m = corrs(field, ids, w)
        print(f"{scale:>6.2f} {h:>8.3f} {wo:>10.3f} {m:>8.3f}  "
              f"{'PASS ✅' if wo>=h else 'volume-lean'}")

    print("\nNote: down-weighting volume on a HANDS-denominated log is a blunt "
          "proxy for the real fix (wall-clock denomination), which can't be "
          "tested on a static snapshot. Read the crossover as 'how much volume "
          "pressure must come off', not a final weight.")


if __name__ == "__main__":
    main()
