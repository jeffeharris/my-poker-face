"""Climb pacing × tax-reference A/B — the clean version.

Fixes the earlier confound: instead of injecting stripped bots, we
DEMOTE a NATIVE strong persona (archetype/config intact, only its
starting bankroll lowered) and watch it try to climb. Run under two
vice-tax references:
  * starting — tax relative to the AI's own starting bankroll (current
    prod behaviour; structurally punishes climbing above your origin)
  * median   — tax relative to the field median (VICE_REFERENCE_MODE=
    median); climbing toward the median is untaxed

Instrumentation separates WHY the challenger does/doesn't climb:
chip+rank trajectory, time-to-top-tier (40k) / time-to-#1, and a ledger
breakdown of chips IN (side-hustle/regen/seed) vs OUT (vice tax/buy-in),
so table_net = Δchips − (ledger_in − ledger_out).

Usage (one job):
  python -m scripts.sim_experiments.circuit_tax_ab \
    --vice-ref median --challenger blackbeard --start 8000 --seed 1 \
    --ticks 8000 --db-path /tmp/ab_med_s1.db --out /tmp/ab_med_s1.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

TOP_TIER_BUYIN = 40_000
_FIXED_START = datetime(2026, 1, 1, 0, 0, 0)


def _demote_challenger(db_path: str, pid: str, start: int) -> None:
    """Lower the challenger's starting bankroll; keep archetype/config intact."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT config_json FROM personalities WHERE personality_id = ?", (pid,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"challenger {pid!r} not found")
    cfg = json.loads(row["config_json"]) if row["config_json"] else {}
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


def _median(vals):
    s = sorted(vals)
    if not s:
        return 0
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _gini(vals):
    s = sorted(v for v in vals if v >= 0)
    n = len(s)
    tot = sum(s)
    if n == 0 or tot == 0:
        return 0.0
    w = sum((i + 1) * v for i, v in enumerate(s))
    return (2 * w) / (n * tot) - (n + 1) / n


def _challenger_trajectory(metrics, pid):
    # NET WORTH (bankroll + seat stacks), not bankroll — a seated climber's
    # stack is on the felt; bankroll alone mis-reads them as poor.
    captured = [m for m in metrics if m.per_pid_networth]
    tt = t1 = best = peak = None
    final_rank = final_chips = start_chips = start_rank = None
    medians = []
    for m in captured:
        nw = m.per_pid_networth
        med = _median(list(nw.values()))
        medians.append(med)
        if pid not in nw:
            continue
        c = nw[pid]
        rk = _ranks(nw)[pid]
        if start_chips is None:
            start_chips, start_rank = c, rk
        best = rk if best is None else min(best, rk)
        peak = c if peak is None else max(peak, c)
        if tt is None and c >= TOP_TIER_BUYIN:
            tt = m.tick
        if t1 is None and rk == 1:
            t1 = m.tick
        final_rank, final_chips = rk, c
    return {
        "start_chips": start_chips,
        "start_rank": start_rank,
        "peak_chips": peak,
        "best_rank": best,
        "final_chips": final_chips,
        "final_rank": final_rank,
        "ticks_to_top_tier": tt,
        "ticks_to_rank1": t1,
        "field_median_first": medians[0] if medians else None,
        "field_median_final": medians[-1] if medians else None,
    }


def _ledger_breakdown(db_path, sandbox_id, pid):
    acct = f"ai:{pid}"
    conn = sqlite3.connect(db_path)
    out_rows = conn.execute(
        "SELECT reason, COALESCE(SUM(amount),0) FROM chip_ledger_entries "
        "WHERE sandbox_id=? AND source=? GROUP BY reason", (sandbox_id, acct)
    ).fetchall()
    in_rows = conn.execute(
        "SELECT reason, COALESCE(SUM(amount),0) FROM chip_ledger_entries "
        "WHERE sandbox_id=? AND sink=? GROUP BY reason", (sandbox_id, acct)
    ).fetchall()
    conn.close()
    out = {r: int(a) for r, a in out_rows}
    inn = {r: int(a) for r, a in in_rows}
    return {"ledger_out": out, "ledger_in": inn,
            "out_total": sum(out.values()), "in_total": sum(inn.values())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Reference mode now unified under LEVER_REFERENCE_MODE:
    #   own_start    — each AI vs its own starting bankroll (legacy)
    #   field_liquid — vs the field's liquid net worth (vice + side-hustle
    #                  + grinder-hunger all field-relative)
    ap.add_argument("--vice-ref", required=True,
                    choices=["own_start", "field_liquid"])
    ap.add_argument("--challenger", default="blackbeard")
    ap.add_argument("--start", type=int, default=8000)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--ticks", type=int, default=8000)
    ap.add_argument("--median-mult", default="1.0")
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # Must be set before economy_flags is imported (module reads it once).
    os.environ["LEVER_REFERENCE_MODE"] = args.vice_ref

    from cash_mode.sim_runner import SimConfig, run_sim
    from poker.repositories import create_repos
    from scripts.seed_sim_sandbox import seed_sim_sandbox

    _demote_challenger(args.db_path, args.challenger, args.start)
    sandbox_id = seed_sim_sandbox(
        name=f"ab-{args.vice_ref}-s{args.seed}", owner_id="ab-bot", db_path=args.db_path
    )
    repos = create_repos(args.db_path)
    cfg = SimConfig(
        sandbox_id=sandbox_id, num_ticks=args.ticks, start_at=_FIXED_START,
        rng_seed=args.seed, metrics_every=25, audit_every=100000, progress_every=0,
    )
    result = run_sim(cfg, repos=repos)
    last = [m for m in result.metrics if m.per_pid_networth][-1]
    nw_last = list(last.per_pid_networth.values())
    payload = {
        "vice_ref": args.vice_ref, "challenger": args.challenger,
        "start": args.start, "seed": args.seed, "ticks": args.ticks,
        "challenger_traj": _challenger_trajectory(result.metrics, args.challenger),
        "challenger_ledger": _ledger_breakdown(args.db_path, sandbox_id, args.challenger),
        # Gini on NET WORTH (the bankroll-only last.gini is mis-stated).
        "field_gini_networth_final": round(_gini(nw_last), 4),
        "field_gini_bankroll_final": round(last.gini, 4),
        "field_max_networth_final": max(nw_last) if nw_last else 0,
        "n_ai": len(last.per_pid_networth),
        "wall_seconds": round(result.wall_seconds, 1),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    t = payload["challenger_traj"]
    print(f"[{args.vice_ref} s{args.seed}] {args.challenger}: "
          f"start={t['start_chips']} peak={t['peak_chips']} final={t['final_chips']} "
          f"bestRk={t['best_rank']} endRk={t['final_rank']} "
          f"toTier={t['ticks_to_top_tier']} to#1={t['ticks_to_rank1']} "
          f"vice_out={payload['challenger_ledger']['ledger_out'].get('bank_pool_deposit', 0)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
