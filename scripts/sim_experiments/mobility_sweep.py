"""Circuit mobility sweep — can a skilled newcomer rise to the top?

One invocation = one (config, seed) job against its OWN db file, so a
fan-out driver can run many concurrently (each sqlite db is isolated).

Goal: nail starting conditions for a new circuit that feels lived-in
(character-driven wealth spread) AND climbable (a skilled player can
reach the top tier + #1 wealth over "a season"). We sweep the top-tail
height (apex cap) and measure mobility.

Config transforms (applied to each persona's
config_json.bankroll_knobs.starting_bankroll):
  * baseline  — identity (current 4k..250k spread)
  * cap150k   — min(start, 150_000)
  * cap100k   — min(start, 100_000)
  * cap50k    — min(start,  50_000)

A fixed SKILLED CHALLENGER (queen_of_hearts, a maniac dominator) is
forced to CHALLENGER_START chips in every config so we can watch a
strong player climb from the bottom regardless of apex height.

Metrics (from per-tick per-pid bankroll trajectories):
  * spearman(start_rank, end_rank) — 1.0 = totally entrenched, ~0 = mobile
  * bottom_to_top_frac — fraction of bottom-quartile (by start) AIs that
    end in the top quartile
  * top_turnover — # distinct wealth leaders over the run
  * challenger_* — the skilled newcomer's final rank, best rank, ticks to
    cross the top-tier buy-in threshold, ticks to reach #1 (None = never)
  * gini_first/final, max_chips_final (lived-in / runaway context)

Usage (one job):
  python -m scripts.sim_experiments.mobility_sweep \
    --config cap100k --seed 1 --ticks 2000 \
    --db-path /tmp/mob_cap100k_s1.db --out /tmp/mob_cap100k_s1.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from cash_mode.sim_runner import SimConfig, run_sim
from poker.repositories import create_repos
from scripts.seed_sim_sandbox import seed_sim_sandbox

CHALLENGER_ID = "queen_of_hearts"
CHALLENGER_START = 5_000
TOP_TIER_BUYIN = 40_000  # ~min buy-in to sit at the $1000 tier
_FIXED_START = datetime(2026, 1, 1, 0, 0, 0)

CONFIGS = {
    "baseline": lambda sb: sb,
    "cap150k": lambda sb: min(sb, 150_000),
    "cap100k": lambda sb: min(sb, 100_000),
    "cap50k": lambda sb: min(sb, 50_000),
}


def _apply_starting_bankrolls(db_path: str, transform) -> dict:
    """Rewrite config_json.bankroll_knobs.starting_bankroll per persona.

    Returns {pid: new_start}. The challenger is pinned to CHALLENGER_START
    regardless of the transform. Run BEFORE seeding so the fresh sandbox
    picks up the new knobs.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT personality_id, config_json FROM personalities "
        "WHERE personality_id IS NOT NULL"
    ).fetchall()
    out = {}
    for r in rows:
        pid = r["personality_id"]
        try:
            cfg = json.loads(r["config_json"]) if r["config_json"] else {}
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        knobs = cfg.get("bankroll_knobs") or {}
        cur = int(knobs.get("starting_bankroll", knobs.get("bankroll_cap", 0)) or 0)
        new = CHALLENGER_START if pid == CHALLENGER_ID else int(transform(cur))
        knobs["starting_bankroll"] = new
        knobs.pop("bankroll_cap", None)
        cfg["bankroll_knobs"] = knobs
        out[pid] = new
        conn.execute(
            "UPDATE personalities SET config_json = ? WHERE personality_id = ?",
            (json.dumps(cfg), pid),
        )
    conn.commit()
    conn.close()
    return out


def _ranks_by_chips(chips_by_pid: dict) -> dict:
    """Rank 1 = richest. Ordinal ranks (ties broken by pid for determinism)."""
    ordered = sorted(chips_by_pid.items(), key=lambda kv: (-kv[1], kv[0]))
    return {pid: i + 1 for i, (pid, _) in enumerate(ordered)}


def _spearman(a: dict, b: dict) -> float:
    """Spearman rank corr between two {pid: rank} maps over shared pids."""
    pids = [p for p in a if p in b]
    n = len(pids)
    if n < 2:
        return 0.0
    xs = [a[p] for p in pids]
    ys = [b[p] for p in pids]
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs) ** 0.5
    vy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def _quartile_cut(values: list, top: bool) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    idx = int(0.75 * (n - 1)) if top else int(0.25 * (n - 1))
    return s[idx]


def compute_metrics(metrics: list) -> dict:
    captured = [m for m in metrics if m.per_pid_chips]
    if not captured:
        return {}
    first = captured[0].per_pid_chips
    last = captured[-1].per_pid_chips

    start_rank = _ranks_by_chips(first)
    end_rank = _ranks_by_chips(last)
    spearman = _spearman(start_rank, end_rank)

    # bottom-quartile (by start) → top-quartile (by end) transition.
    start_bottom_cut = _quartile_cut(list(first.values()), top=False)
    end_top_cut = _quartile_cut(list(last.values()), top=True)
    bottom_pids = [p for p, c in first.items() if c <= start_bottom_cut]
    risers = [p for p in bottom_pids if last.get(p, 0) >= end_top_cut]
    bottom_to_top = (len(risers) / len(bottom_pids)) if bottom_pids else 0.0

    # top turnover — distinct wealth leaders across captured ticks.
    leaders = []
    for m in captured:
        if m.per_pid_chips:
            leaders.append(max(m.per_pid_chips.items(), key=lambda kv: (kv[1], kv[0]))[0])
    top_turnover = len(set(leaders))

    # challenger climb.
    ch = CHALLENGER_ID
    ticks_to_tier = None
    ticks_to_1 = None
    best_rank = None
    for m in captured:
        if ch not in m.per_pid_chips:
            continue
        rk = _ranks_by_chips(m.per_pid_chips)[ch]
        best_rank = rk if best_rank is None else min(best_rank, rk)
        if ticks_to_tier is None and m.per_pid_chips[ch] >= TOP_TIER_BUYIN:
            ticks_to_tier = m.tick
        if ticks_to_1 is None and rk == 1:
            ticks_to_1 = m.tick

    return {
        "spearman_start_end": round(spearman, 4),
        "bottom_to_top_frac": round(bottom_to_top, 4),
        "top_turnover": top_turnover,
        "n_ai": len(last),
        "challenger_start_rank": start_rank.get(ch),
        "challenger_final_rank": end_rank.get(ch),
        "challenger_best_rank": best_rank,
        "challenger_final_chips": last.get(ch),
        "challenger_ticks_to_top_tier": ticks_to_tier,
        "challenger_ticks_to_rank1": ticks_to_1,
        "gini_first": round(captured[0].gini, 4),
        "gini_final": round(captured[-1].gini, 4),
        "max_chips_final": captured[-1].max_chips,
        "total_chips_final": captured[-1].total_chips,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True, choices=list(CONFIGS))
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--ticks", type=int, default=2000)
    ap.add_argument("--db-path", required=True, help="Unique db file for this job")
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args()

    starts = _apply_starting_bankrolls(args.db_path, CONFIGS[args.config])
    sandbox_id = seed_sim_sandbox(
        name=f"mob-{args.config}-s{args.seed}",
        owner_id="mob-bot",
        db_path=args.db_path,
    )
    repos = create_repos(args.db_path)
    config = SimConfig(
        sandbox_id=sandbox_id,
        num_ticks=args.ticks,
        start_at=_FIXED_START,
        rng_seed=args.seed,
        metrics_every=20,
        audit_every=100000,  # mobility run — skip the costly cross-cut audit
        progress_every=0,
    )
    result = run_sim(config, repos=repos)
    payload = {
        "config": args.config,
        "seed": args.seed,
        "ticks": args.ticks,
        "challenger_id": CHALLENGER_ID,
        "challenger_start_chips": starts.get(CHALLENGER_ID),
        "metrics": compute_metrics(result.metrics),
        "wall_seconds": round(result.wall_seconds, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload["metrics"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
