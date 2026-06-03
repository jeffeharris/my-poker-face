"""Knob sweep for the field_liquid lever percentiles.

One invocation = one (knob-config, seed) run against its own db, so a
fan-out can run many in parallel. Forces LEVER_REFERENCE_MODE=field_liquid
+ real vice (honest), monkeypatches the four FIELD_* constants in
economy_flags (the levers read them as module attrs at call time), runs
the economy sim, and emits the decision-relevant outcome metrics.

Knobs:
  --cf  FIELD_CONCENTRATION_FLOOR      (vice fires above N× field median)
  --he  FIELD_HUSTLE_ELIGIBLE_PERCENTILE (bottom X% → hustle candidate)
  --ht  FIELD_HUSTLE_TARGET_PERCENTILE   (hustle tops up toward this pct)
  --gh  FIELD_GRINDER_HUNGER_PERCENTILE  (below this pct → hungry grinder)

Usage:
  python -m scripts.sim_experiments.knob_sweep --label cf2.0 \
    --cf 2.0 --he 0.10 --ht 0.25 --gh 0.35 --seed 1 --ticks 1500 \
    --db-path /app/data/ks/cf2.0_s1.db --out /app/data/ks_out/cf2.0_s1.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

_FIXED_START = datetime(2026, 1, 1, 0, 0, 0)


def _gini(vals):
    s = sorted(v for v in vals if v >= 0)
    n, tot = len(s), sum(v for v in vals if v >= 0)
    if n == 0 or tot == 0:
        return 0.0
    w = sum((i + 1) * v for i, v in enumerate(s))
    return round((2 * w) / (n * tot) - (n + 1) / n, 4)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", required=True)
    ap.add_argument("--cf", type=float, default=2.5)
    ap.add_argument("--he", type=float, default=0.10)
    ap.add_argument("--ht", type=float, default=0.25)
    ap.add_argument("--gh", type=float, default=0.35)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--ticks", type=int, default=1500)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.environ["LEVER_REFERENCE_MODE"] = "field_liquid"

    from cash_mode import economy_flags as ef

    ef.FIELD_CONCENTRATION_FLOOR = args.cf
    ef.FIELD_HUSTLE_ELIGIBLE_PERCENTILE = args.he
    ef.FIELD_HUSTLE_TARGET_PERCENTILE = args.ht
    ef.FIELD_GRINDER_HUNGER_PERCENTILE = args.gh

    from cash_mode.sim_runner import SimConfig, run_sim
    from poker.repositories import create_repos
    from scripts.seed_sim_sandbox import seed_sim_sandbox

    sb = seed_sim_sandbox(name=f"ks-{args.label}-s{args.seed}", owner_id="ks", db_path=args.db_path)
    repos = create_repos(args.db_path)
    cfg = SimConfig(
        sandbox_id=sb,
        num_ticks=args.ticks,
        start_at=_FIXED_START,
        rng_seed=args.seed,
        metrics_every=25,
        audit_every=500,
        progress_every=0,
    )
    r = run_sim(cfg, repos=repos)

    captured = [m for m in r.metrics if m.per_pid_networth]
    first_nw = list(captured[0].per_pid_networth.values())
    last_nw = list(captured[-1].per_pid_networth.values())
    hungry = [m.hungry_grinder_count for m in captured]

    conn = sqlite3.connect(args.db_path)
    led = {
        reason: int(amt or 0)
        for reason, amt in conn.execute(
            "SELECT reason, SUM(amount) FROM chip_ledger_entries WHERE sandbox_id=? GROUP BY reason",
            (sb,),
        ).fetchall()
    }
    conn.close()

    payload = {
        "label": args.label,
        "seed": args.seed,
        "knobs": {"cf": args.cf, "he": args.he, "ht": args.ht, "gh": args.gh},
        "gini_nw_first": _gini(first_nw),
        "gini_nw_final": _gini(last_nw),
        "networth_total_final": sum(last_nw),
        "max_networth_final": max(last_nw) if last_nw else 0,
        "vice_drained": led.get("vice_spending", 0),
        "side_hustle_paid": led.get("side_hustle_earning", 0),
        "casino_seat_seed": led.get("casino_seat_seed", 0),
        "hungry_grinders_avg": round(sum(hungry) / len(hungry), 1) if hungry else 0,
        "casino_count_final": captured[-1].casino_count,
        "drift": r.summary["max_abs_audit_drift"],
        "wall_seconds": round(r.wall_seconds, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(
        f"[{args.label} s{args.seed}] giniNW={payload['gini_nw_final']} "
        f"vice={payload['vice_drained']} hustle={payload['side_hustle_paid']} "
        f"hungry={payload['hungry_grinders_avg']} casinos={payload['casino_count_final']} "
        f"drift={payload['drift']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
