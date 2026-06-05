"""Circuit climb-pacing experiment — how fast does a STRONG bot rise?

Repurposes 3 eligible low personas as identical-start challengers, each
driven by a different controller (via config_json the cash sim reads):
  * TIERED  — clean TieredBotController (solver tables, no deviation) =
    the strongest steady player ("best tieredbot")
  * CASEBOT — rule_strategy='case_based_v2' (CaseBotV2, the fish-hunter)
  * REG     — rule_strategy='reg_plus' (the strong reg that beats CaseBotV2)

All three start at --start chips (swept) so we can read, per controller,
the time to reach the $1000-tier buy-in (40k) and to reach #1, plus the
final rank — i.e. whether the climb is paced to "a season".

Usage (one job):
  python -m scripts.sim_experiments.circuit_pacing \
    --start 8000 --seed 1 --ticks 8000 \
    --db-path /tmp/pace_8k_s1.db --out /tmp/pace_8k_s1.json
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

# pid -> controller label. These 3 are ordinary eligible personas we
# repurpose as bots; only their controller + starting bankroll change.
CHALLENGERS = {
    "a_mime": "TIERED",
    "alice": "CASEBOT",
    "jesus_christ": "REG",
}
# label -> rule_strategy (None = clean TieredBot solver path)
STRATEGY = {"TIERED": None, "CASEBOT": "case_based_v2", "REG": "reg_plus"}
TOP_TIER_BUYIN = 40_000
_FIXED_START = datetime(2026, 1, 1, 0, 0, 0)


def _configure_challengers(db_path: str, start: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for pid, label in CHALLENGERS.items():
        row = conn.execute(
            "SELECT config_json FROM personalities WHERE personality_id = ?", (pid,)
        ).fetchone()
        if row is None:
            raise SystemExit(f"challenger pid {pid!r} not found in {db_path}")
        cfg = json.loads(row["config_json"]) if row["config_json"] else {}
        # Clear any inherited routing, then set the intended controller.
        cfg.pop("archetype", None)
        cfg.pop("fish_leak", None)
        cfg.pop("rule_strategy", None)
        rs = STRATEGY[label]
        if rs is not None:
            cfg["rule_strategy"] = rs
        knobs = cfg.get("bankroll_knobs") or {}
        knobs["starting_bankroll"] = int(start)
        knobs.pop("bankroll_cap", None)
        cfg["bankroll_knobs"] = knobs
        conn.execute(
            "UPDATE personalities SET config_json = ? WHERE personality_id = ?",
            (json.dumps(cfg), pid),
        )
    conn.commit()
    conn.close()


def _ranks(chips: dict) -> dict:
    o = sorted(chips.items(), key=lambda kv: (-kv[1], kv[0]))
    return {p: i + 1 for i, (p, _) in enumerate(o)}


def _challenger_metrics(metrics: list) -> dict:
    captured = [m for m in metrics if m.per_pid_chips]
    out = {}
    for pid, label in CHALLENGERS.items():
        ticks_tier = ticks_1 = best_rank = peak = None
        final_rank = final_chips = None
        for m in captured:
            if pid not in m.per_pid_chips:
                continue
            c = m.per_pid_chips[pid]
            rk = _ranks(m.per_pid_chips)[pid]
            best_rank = rk if best_rank is None else min(best_rank, rk)
            peak = c if peak is None else max(peak, c)
            if ticks_tier is None and c >= TOP_TIER_BUYIN:
                ticks_tier = m.tick
            if ticks_1 is None and rk == 1:
                ticks_1 = m.tick
            final_rank, final_chips = rk, c
        out[label] = {
            "pid": pid,
            "ticks_to_top_tier": ticks_tier,
            "ticks_to_rank1": ticks_1,
            "best_rank": best_rank,
            "final_rank": final_rank,
            "peak_chips": peak,
            "final_chips": final_chips,
        }
    last = captured[-1] if captured else None
    return {
        "challengers": out,
        "n_ai": len(last.per_pid_chips) if last else 0,
        "gini_final": round(last.gini, 4) if last else None,
        "max_chips_final": last.max_chips if last else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, required=True, help="challenger starting chips")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--ticks", type=int, default=8000)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    _configure_challengers(args.db_path, args.start)
    sandbox_id = seed_sim_sandbox(
        name=f"pace-{args.start}-s{args.seed}", owner_id="pace-bot", db_path=args.db_path
    )
    repos = create_repos(args.db_path)
    cfg = SimConfig(
        sandbox_id=sandbox_id,
        num_ticks=args.ticks,
        start_at=_FIXED_START,
        rng_seed=args.seed,
        metrics_every=25,
        audit_every=100000,
        progress_every=0,
    )
    result = run_sim(cfg, repos=repos)
    payload = {
        "start": args.start,
        "seed": args.seed,
        "ticks": args.ticks,
        "metrics": _challenger_metrics(result.metrics),
        "wall_seconds": round(result.wall_seconds, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload["metrics"]["challengers"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
