"""Chip-custody go-forward conservation validator (the flip gate).

Seeds a FRESH isolated sandbox on a throwaway DB, turns `CHIP_CUSTODY_ENABLED`
ON, runs the economy sim, then checks the Phase-1 invariant per AI account:

    derived ai:<pid> ledger balance (Σ sink − Σ source)  ==  stored bankroll int

A fresh sandbox starts reconciled (every seed is `ai_seed`), so any residual
gap after the sim is a GO-FORWARD movement path that isn't ledgered yet — the
exact thing this validator exists to surface (buy-in/cash-out are wired at the
two chokepoints; stake payoffs / aspiration unwinds / whale folds are the
suspects). Unlike the live audit this needs no backfill: it proves the wiring,
not the history.

Reports the worst offending accounts + the reason-flow breakdown so an
unledgered path is identifiable. Checkpoints capture transient (mid-session,
chips-at-seat) states, not just the end snapshot.

SAFETY: refuses to run against the live DBs; pass a throwaway --db-path.

Usage (backend container):
    docker compose exec backend python -m scripts.validate_chip_custody \\
        --db-path /tmp/custody_validation.db --ticks 600 --checkpoints 6 \\
        --out /tmp/custody_validation.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_BASE_START = datetime(2026, 1, 1, 0, 0, 0)


def _audit_sandbox(db_path: str, sandbox_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    derived: Dict[str, int] = defaultdict(int)
    reason_into: Dict[str, int] = defaultdict(int)
    reason_out: Dict[str, int] = defaultdict(int)
    # Per-account balances for EVERY account (the seat-conservation + global
    # conservation checks the original ai-only audit missed — the gap that let
    # the seat double-drain pass green while prod minted).
    balances: Dict[str, int] = defaultdict(int)
    for r in conn.execute(
        "SELECT source, sink, amount, reason FROM chip_ledger_entries WHERE sandbox_id = ?",
        (sandbox_id,),
    ):
        a = int(r["amount"])
        balances[r["sink"]] += a
        balances[r["source"]] -= a
        if r["sink"].startswith("ai:"):
            derived[r["sink"]] += a
            reason_into[r["reason"]] += a
        if r["source"].startswith("ai:"):
            derived[r["source"]] -= a
            reason_out[r["reason"]] += a
    stored = {
        f"ai:{r['personality_id']}": int(r["chips"])
        for r in conn.execute(
            "SELECT personality_id, chips FROM ai_bankroll_state WHERE sandbox_id = ?",
            (sandbox_id,),
        )
    }
    # Live AI seat stacks from the cash_tables seat map (the authority the seat
    # ledger must equal). Sum per seat account so we can compare Σseat==Σstacks.
    live_seat_stacks = 0
    for (seats_json,) in conn.execute(
        "SELECT seats_json FROM cash_tables WHERE sandbox_id = ?", (sandbox_id,)
    ):
        try:
            for slot in json.loads(seats_json or "[]"):
                if slot.get("kind") == "ai":
                    live_seat_stacks += int(slot.get("chips", 0) or 0)
        except (ValueError, TypeError):
            continue
    conn.close()

    gaps = []
    for acct, s in stored.items():
        d = derived.get(acct, 0)
        gaps.append({"account": acct, "stored": s, "derived": d, "gap": s - d})
    unrecon = [g for g in gaps if g["gap"] != 0]

    # Seat-conservation invariants — the ones the double-drain violated.
    seat_balances = {a: b for a, b in balances.items() if a.startswith(f"seat:ai:{sandbox_id}:")}
    negative_seats = {a: b for a, b in seat_balances.items() if b < 0}
    sum_seat_ledger = sum(seat_balances.values())
    # Global conservation: Σ non-bank balances == −central_bank.
    non_bank_sum = sum(b for a, b in balances.items() if a != "central_bank")
    central_bank = balances.get("central_bank", 0)

    return {
        "n_accounts": len(gaps),
        "n_unreconciled": len(unrecon),
        "total_abs_gap": sum(abs(g["gap"]) for g in gaps),
        "signed_gap": sum(g["gap"] for g in gaps),
        "worst": sorted(unrecon, key=lambda g: -abs(g["gap"]))[:12],
        "reason_into_ai": dict(sorted(reason_into.items(), key=lambda x: -x[1])),
        "reason_out_of_ai": dict(sorted(reason_out.items(), key=lambda x: -x[1])),
        # Seat conservation (the mint guard)
        "n_negative_seats": len(negative_seats),
        "min_seat_balance": min(seat_balances.values()) if seat_balances else 0,
        "worst_negative_seats": dict(sorted(negative_seats.items(), key=lambda x: x[1])[:12]),
        "sum_seat_ledger": sum_seat_ledger,
        "live_seat_stacks": live_seat_stacks,
        "seat_vs_stack_gap": sum_seat_ledger - live_seat_stacks,
        # Global conservation
        "non_bank_sum": non_bank_sum,
        "central_bank": central_bank,
        "global_conservation_residual": non_bank_sum + central_bank,
    }


def run(db_path: str, ticks: int, rng_seed: int, checkpoints: int, out_path: str) -> dict:
    forbidden = {"/app/data/poker_games.db", str(Path(_project_root) / "data" / "poker_games.db")}
    if db_path in forbidden:
        raise SystemExit(
            f"REFUSING to run against live DB {db_path!r} — pass a throwaway --db-path"
        )

    from cash_mode import economy_flags
    from cash_mode.sim_runner import SimConfig, run_sim
    from poker.repositories import create_repos
    from scripts.seed_sim_sandbox import seed_sim_sandbox

    # Enable custody BEFORE seeding — the boot seat-fill (ensure_lobby_seeded)
    # debits bankrolls to seat AIs, and those debits only record `ai_buy_in`
    # when the flag is on. Setting it after seed_sim_sandbox would leave the
    # initial buy-ins unledgered (derived > stored from tick 0).
    economy_flags.CHIP_CUSTODY_ENABLED = True
    logger.info("CHIP_CUSTODY_ENABLED = %s", economy_flags.CHIP_CUSTODY_ENABLED)

    sandbox_id = seed_sim_sandbox(
        name="chip-custody-validation", owner_id="sim-bot", db_path=db_path
    )
    logger.info("Seeded sandbox %s", sandbox_id)
    repos = create_repos(db_path)

    tick_seconds = 8
    checkpoints = max(1, checkpoints)
    seg_ticks = max(1, ticks // checkpoints)
    snapshots: List[dict] = []
    cumulative = 0
    for seg in range(checkpoints):
        seg_start = _BASE_START + timedelta(seconds=cumulative * tick_seconds)
        config = SimConfig(
            sandbox_id=sandbox_id,
            num_ticks=seg_ticks,
            tick_seconds=tick_seconds,
            start_at=seg_start,
            rng_seed=rng_seed,
            progress_every=0,
        )
        run_sim(config, repos=repos)
        cumulative += seg_ticks
        snap = _audit_sandbox(db_path, sandbox_id)
        snap["segment"] = seg
        snap["cumulative_ticks"] = cumulative
        snapshots.append(snap)
        logger.info(
            "checkpoint %d/%d @%d ticks: %d/%d unreconciled abs_gap=%d | "
            "min_seat=%d neg_seats=%d seat_vs_stack=%d | global_residual=%d",
            seg + 1,
            checkpoints,
            cumulative,
            snap["n_unreconciled"],
            snap["n_accounts"],
            snap["total_abs_gap"],
            snap["min_seat_balance"],
            snap["n_negative_seats"],
            snap["seat_vs_stack_gap"],
            snap["global_conservation_residual"],
        )
        cumulative += 1

    final = snapshots[-1]
    # The full conservation gate — every invariant the seat double-drain
    # violated must hold, not just AI-account reconciliation (the original
    # check that passed green while prod minted because it never looked at the
    # seat accounts or the human-table paths).
    failures = []
    if final["n_unreconciled"] != 0:
        failures.append(
            f"{final['n_unreconciled']} AI accounts drifted (abs_gap="
            f"{final['total_abs_gap']}, signed={final['signed_gap']}) — unledgered path"
        )
    if final["n_negative_seats"] != 0:
        failures.append(
            f"{final['n_negative_seats']} NEGATIVE seat balances (min="
            f"{final['min_seat_balance']}) — minted chips (the double-drain signature)"
        )
    if final["seat_vs_stack_gap"] != 0:
        failures.append(
            f"seat ledger ({final['sum_seat_ledger']}) != live AI stacks "
            f"({final['live_seat_stacks']}), gap={final['seat_vs_stack_gap']}"
        )
    if final["global_conservation_residual"] != 0:
        failures.append(
            f"global conservation broken: Σnon-bank+central_bank="
            f"{final['global_conservation_residual']} (must be 0)"
        )

    passed = not failures
    report = {
        "sandbox_id": sandbox_id,
        "db_path": db_path,
        "rng_seed": rng_seed,
        "total_ticks": cumulative,
        "passed": passed,
        "final": final,
        "checkpoint_summary": [
            {
                "seg": s["segment"],
                "ticks": s["cumulative_ticks"],
                "n_unreconciled": s["n_unreconciled"],
                "abs_gap": s["total_abs_gap"],
                "n_negative_seats": s["n_negative_seats"],
                "min_seat_balance": s["min_seat_balance"],
                "seat_vs_stack_gap": s["seat_vs_stack_gap"],
                "global_conservation_residual": s["global_conservation_residual"],
            }
            for s in snapshots
        ],
        "verdict": (
            "PASS — no minted chips: every AI account reconciles, no seat is "
            "negative, the seat ledger equals live stacks, and the universe "
            "conserves globally."
            if passed
            else "FAIL — " + " | ".join(failures)
        ),
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2, default=str))
    logger.info("Wrote %s", out_path)
    logger.info("VERDICT: %s", report["verdict"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", required=True, help="Throwaway DB path (NOT the live DB)")
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--rng-seed", type=int, default=7)
    ap.add_argument("--checkpoints", type=int, default=6)
    ap.add_argument("--out", default="/tmp/custody_validation.json")
    args = ap.parse_args()
    report = run(args.db_path, args.ticks, args.rng_seed, args.checkpoints, args.out)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
