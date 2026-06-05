#!/usr/bin/env python3
"""Accrue REAL renown-v2 over a headless cash-world sim, peeking at intervals (EXP_007).

The tournament draw's renown/field terms can only be tuned once a real renown
distribution exists. This drives the cash world headlessly to generate it:

  loop: play hands (refresh_unseated_tables → updates cash_pair_stats / scalps /
        relationships) → every N ticks recompute + persist per-AI renown by
        REUSING the production ticker path (ticker_service._maybe_recompute_prestige,
        with extensions populated + the 5-min rate-limit bypassed). No renown math
        is reimplemented here — it's the same code the live ticker runs.

Every --peek-every ticks it prints the AI renown_v2 distribution so you can watch
it build. Run on a COPY of the dev DB (default) so it never contends with the live
backend's writes:

    # make a WAL-safe copy first (see --db), then:
    docker compose exec -d backend python scripts/sim_renown_accrual.py \
        --db /app/data/poker_games.renown_sim.db --ticks 2000 --recompute-every 50 --peek-every 100

Requires RENOWN_V2_ENABLED + RENOWN_V2_PERSIST_AI on (the recompute's own gates).
See docs/experiments/EXP_007_TOURNAMENT_DRAW_WEIGHTS.md.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _discover(db_path: str):
    """(sandbox_id, owner_id) with the most AI bankroll rows / a cash owner."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sb = conn.execute(
            "SELECT sandbox_id, COUNT(*) c FROM ai_bankroll_state "
            "GROUP BY sandbox_id ORDER BY c DESC LIMIT 1"
        ).fetchone()
        sandbox_id = sb["sandbox_id"] if sb else None
        owner = None
        if sandbox_id:
            row = conn.execute(
                "SELECT owner_id FROM cash_sessions WHERE sandbox_id=? LIMIT 1", (sandbox_id,)
            ).fetchone()
            owner = row["owner_id"] if row else None
        return sandbox_id, owner
    finally:
        conn.close()


def _peek(db_path: str, sandbox_id: str) -> dict:
    """AI renown_v2 distribution (peak per AI) — the gather progress."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT owner_id, MAX(renown_v2) v FROM prestige_snapshots "
            "WHERE sandbox_id=? AND entity_kind='ai' AND renown_v2 IS NOT NULL "
            "GROUP BY owner_id",
            (sandbox_id,),
        ).fetchall()
    finally:
        conn.close()
    vals = sorted(v for _, v in rows if v is not None)
    nonzero = [v for v in vals if v > 0]
    return {
        "ai_rows": len(vals),
        "ai_renown_gt0": len(nonzero),
        "min": min(vals) if vals else 0.0,
        "median": statistics.median(vals) if vals else 0.0,
        "max": max(vals) if vals else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/app/data/poker_games.renown_sim.db")
    ap.add_argument("--sandbox", default=None)
    ap.add_argument("--owner", default=None)
    ap.add_argument("--ticks", type=int, default=2000)
    ap.add_argument("--recompute-every", type=int, default=50)
    ap.add_argument("--peek-every", type=int, default=100)
    ap.add_argument("--hand-sim-prob", type=float, default=0.6)
    ap.add_argument("--rng-seed", type=int, default=0)
    ap.add_argument("--tick-seconds", type=int, default=8)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db} — make a WAL-safe copy of the dev DB first.")
        return 1

    sandbox_id = args.sandbox
    owner_id = args.owner
    if not sandbox_id or not owner_id:
        d_sb, d_owner = _discover(args.db)
        sandbox_id = sandbox_id or d_sb
        owner_id = owner_id or d_owner
    if not sandbox_id or not owner_id:
        print("Could not resolve sandbox/owner — pass --sandbox and --owner.")
        return 1

    from cash_mode import economy_flags
    from cash_mode.lobby import refresh_unseated_tables
    from flask_app import extensions
    from flask_app.services import ticker_service
    from poker.repositories import create_repos

    if not (economy_flags.RENOWN_V2_ENABLED and economy_flags.RENOWN_V2_PERSIST_AI):
        print(
            "WARNING: RENOWN_V2_ENABLED / RENOWN_V2_PERSIST_AI are off — the recompute "
            "won't persist AI renown. Enable both, then re-run."
        )

    repos = create_repos(args.db)
    # Point the production recompute (which reads flask_app.extensions) at THIS db.
    for k in (
        "prestige_snapshots_repo",
        "relationship_repo",
        "cash_session_repo",
        "renown_field_repo",
    ):
        setattr(extensions, k, repos[k])

    print(
        f"db={args.db}\nsandbox={sandbox_id} owner={owner_id} ticks={args.ticks} "
        f"recompute_every={args.recompute_every} peek_every={args.peek_every} "
        f"hand_sim_prob={args.hand_sim_prob}",
        flush=True,
    )
    p0 = _peek(args.db, sandbox_id)
    print(
        f"[tick 0] AI renown>0={p0['ai_renown_gt0']}/{p0['ai_rows']} "
        f"renown_v2 min/med/max={p0['min']:.2f}/{p0['median']:.2f}/{p0['max']:.2f}",
        flush=True,
    )

    rng = random.Random(args.rng_seed)
    start = datetime.utcnow()
    for tick in range(args.ticks):
        now = start + timedelta(seconds=tick * args.tick_seconds)
        try:
            refresh_unseated_tables(
                cash_table_repo=repos["cash_table_repo"],
                personality_repo=repos["personality_repo"],
                bankroll_repo=repos["bankroll_repo"],
                sandbox_id=sandbox_id,
                now=now,
                rng=rng,
                hand_sim_prob=args.hand_sim_prob,
                relationship_repo=repos["relationship_repo"],
                stake_repo=repos["stake_repo"],
                chip_ledger_repo=repos["chip_ledger_repo"],
                side_hustle_repo=repos.get("side_hustle_state_repo"),
                vice_repo=repos.get("vice_state_repo"),
            )
        except Exception as e:  # noqa: BLE001 — keep the sim going; report
            print(f"[tick {tick + 1}] refresh error: {e}", flush=True)

        if (tick + 1) % args.recompute_every == 0:
            # Bypass the 5-min rate-limit so renown recomputes on the sim clock.
            ticker_service._last_prestige_at.pop(sandbox_id, None)
            ticker_service._maybe_recompute_prestige(owner_id, sandbox_id)

        if (tick + 1) % args.peek_every == 0:
            p = _peek(args.db, sandbox_id)
            print(
                f"[tick {tick + 1}/{args.ticks}] AI renown>0={p['ai_renown_gt0']}/{p['ai_rows']} "
                f"renown_v2 min/med/max={p['min']:.3f}/{p['median']:.3f}/{p['max']:.3f}",
                flush=True,
            )

    print("done.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
