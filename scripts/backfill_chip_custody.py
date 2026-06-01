"""One-time idempotent backfill making AI bankrolls ledger-derivable.

The chip-custody Phase-1 gate (`scripts/audit_ledger_completeness.py`) wants each
`ai:<pid>` ledger balance (Σ sink − Σ source, per sandbox) to equal the stored
`ai_bankroll_state.chips`. Today they diverge by the lifetime table P&L + stake
payoffs that were never ledgered (the ~32.6M global gap — almost entirely
*cancelling* per-account noise, not lost chips). The go-forward chokepoint
wiring (`CHIP_CUSTODY_ENABLED`) keeps NEW movements ledgered; this seeds the
EXISTING balances so the ledger is authoritative from day one (D0).

Per (personality_id, sandbox) with a stored bankroll row:
  1. **Seed the seat balance** — if the AI is currently seated with stack S>0
     (`cash_tables.seats[].chips`), write an `ai_buy_in` transfer
     `ai:<pid> → seat:ai:<sandbox>:<pid>` of S. This represents the historical
     buy-in so the seat account starts at the AI's real at-table chips (the
     clean Phase-4 starting point) and the eventual cash-out pairs with it.
  2. **Reconcile the bankroll** — inject one `pre_ledger_universe` row closing
     the residual so the derived `ai:<pid>` balance equals stored:
        amount = stored − derived_existing + S
     positive → central_bank → ai:<pid>;  negative → ai:<pid> → central_bank.
     (`pre_ledger_universe` is the established migration-seed reason — it makes
     ledger_outstanding match actual so the closed-economy drift audit is
     unaffected; same mechanism as `_migrate_v94_seed_pre_ledger_universe`.)

`derived_existing` is read ONCE per account before any write, so the formula is
order-independent: final derived = derived_existing − S + amount = stored.

Idempotent at the SANDBOX level: a sandbox that already has custody-backfill
rows (context.site == 'chip_custody_backfill') is skipped. `--dry-run` prints
the plan. SAFE: only appends ledger rows; never touches bankroll/seat stores.

Usage (backend container):
    docker compose exec backend python -m scripts.backfill_chip_custody \\
        --db-path /app/data/poker_games.db --dry-run
    docker compose exec backend python -m scripts.backfill_chip_custody \\
        --db-path /app/data/poker_games.db
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

BACKFILL_SITE = "chip_custody_backfill"


def _sandbox_ids(conn: sqlite3.Connection) -> List[str]:
    ids = set()
    for tbl in ("ai_bankroll_state", "cash_tables", "chip_ledger_entries"):
        try:
            for (sid,) in conn.execute(
                f"SELECT DISTINCT sandbox_id FROM {tbl} WHERE sandbox_id IS NOT NULL"
            ):
                if sid:
                    ids.add(sid)
        except sqlite3.OperationalError:
            pass
    return sorted(ids)


def _already_backfilled(conn: sqlite3.Connection, sid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM chip_ledger_entries "
        "WHERE sandbox_id = ? AND context_json LIKE ? LIMIT 1",
        (sid, f'%"site": "{BACKFILL_SITE}"%'),
    ).fetchone()
    return row is not None


def _derived_ai(conn: sqlite3.Connection, sid: str) -> Dict[str, int]:
    """ai:<pid> -> derived balance (Σ sink − Σ source) within this sandbox."""
    bal: Dict[str, int] = defaultdict(int)
    for source, sink, amount in conn.execute(
        "SELECT source, sink, amount FROM chip_ledger_entries WHERE sandbox_id = ?",
        (sid,),
    ):
        a = int(amount)
        if sink.startswith("ai:"):
            bal[sink] += a
        if source.startswith("ai:"):
            bal[source] -= a
    return bal


def _seat_stacks(conn: sqlite3.Connection, sid: str) -> Dict[str, int]:
    """personality_id -> current at-table chips from cash_tables seats."""
    out: Dict[str, int] = defaultdict(int)
    for (seats_json,) in conn.execute(
        "SELECT seats_json FROM cash_tables WHERE sandbox_id = ?", (sid,)
    ):
        try:
            seats = json.loads(seats_json)
        except (ValueError, TypeError):
            continue
        for slot in seats:
            if slot.get("kind") == "ai":
                pid = slot.get("personality_id")
                if pid:
                    out[pid] += int(slot.get("chips") or 0)
    return out


def _plan_for_sandbox(conn: sqlite3.Connection, sid: str) -> Tuple[List[dict], dict]:
    """Return (rows_to_write, counts) for one sandbox.

    Each row: {kind: 'buy_in'|'reconcile', pid, source, sink, amount, reason}.
    """
    stored = {
        pid: int(chips)
        for pid, chips in conn.execute(
            "SELECT personality_id, chips FROM ai_bankroll_state WHERE sandbox_id = ?",
            (sid,),
        )
    }
    derived = _derived_ai(conn, sid)
    seats = _seat_stacks(conn, sid)

    rows: List[dict] = []
    counts = {"accounts": 0, "seat_seeds": 0, "reconcile_pos": 0,
              "reconcile_neg": 0, "noop": 0, "seat_chips": 0}
    for pid, stored_chips in sorted(stored.items()):
        counts["accounts"] += 1
        s = int(seats.get(pid, 0))
        derived_existing = derived.get(f"ai:{pid}", 0)
        if s > 0:
            rows.append({"kind": "buy_in", "pid": pid, "source": f"ai:{pid}",
                         "sink": f"seat:ai:{sid}:{pid}", "amount": s,
                         "reason": "ai_buy_in"})
            counts["seat_seeds"] += 1
            counts["seat_chips"] += s
        amount = stored_chips - derived_existing + s
        if amount > 0:
            rows.append({"kind": "reconcile", "pid": pid, "source": "central_bank",
                         "sink": f"ai:{pid}", "amount": amount,
                         "reason": "pre_ledger_universe"})
            counts["reconcile_pos"] += 1
        elif amount < 0:
            rows.append({"kind": "reconcile", "pid": pid, "source": f"ai:{pid}",
                         "sink": "central_bank", "amount": -amount,
                         "reason": "pre_ledger_universe"})
            counts["reconcile_neg"] += 1
        else:
            counts["noop"] += 1
    return rows, counts


def _player_already_backfilled(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM chip_ledger_entries "
        "WHERE context_json LIKE ? LIMIT 1",
        (f'%"site": "{BACKFILL_SITE}_player"%',),
    ).fetchone()
    return row is not None


def _reconcile_players(conn, repo, dry_run: bool) -> dict:
    """Reconcile GLOBAL player bankrolls to the ledger (mirror of the AI pass).

    `player_bankroll_state` is global (no sandbox_id), so a player's derived
    balance sums `player:<id>` rows across ALL sandboxes. The common gap is an
    unledgered first-time `player_seed` grant (predates Cut 2). Inject one
    `pre_ledger_universe` row (sandbox_id=NULL, counted in the global sum) to
    close stored − derived. Human at-table chips are already ledgered via Cut
    2's `player_buy_in`, so no seat seeding is needed here.
    """
    from core.economy import ledger as L

    counts = {"players": 0, "reconcile_pos": 0, "reconcile_neg": 0, "noop": 0, "rows": 0}
    # global derived balance per player:<id>
    derived: Dict[str, int] = defaultdict(int)
    for source, sink, amount in conn.execute(
        "SELECT source, sink, amount FROM chip_ledger_entries"
    ):
        a = int(amount)
        if sink.startswith("player:"):
            derived[sink] += a
        if source.startswith("player:"):
            derived[source] -= a
    for pid, chips in conn.execute("SELECT player_id, chips FROM player_bankroll_state"):
        counts["players"] += 1
        amount = int(chips) - derived.get(f"player:{pid}", 0)
        ctx = {"site": f"{BACKFILL_SITE}_player", "player_id": pid}
        if amount > 0:
            counts["reconcile_pos"] += 1
            if not dry_run:
                if L.record(repo, source="central_bank", sink=f"player:{pid}",
                            amount=amount, reason="pre_ledger_universe",
                            context=ctx, sandbox_id=None) is not None:
                    counts["rows"] += 1
        elif amount < 0:
            counts["reconcile_neg"] += 1
            if not dry_run:
                if L.record(repo, source=f"player:{pid}", sink="central_bank",
                            amount=-amount, reason="pre_ledger_universe",
                            context=ctx, sandbox_id=None) is not None:
                    counts["rows"] += 1
        else:
            counts["noop"] += 1
    return counts


def run(db_path: str, dry_run: bool, only_sandbox: Optional[str]) -> dict:
    from poker.repositories.chip_ledger_repository import ChipLedgerRepository
    from core.economy import ledger as L

    conn = sqlite3.connect(db_path)
    sandboxes = [only_sandbox] if only_sandbox else _sandbox_ids(conn)
    repo = None if dry_run else ChipLedgerRepository(db_path)

    logger.info("Chip-custody backfill %s over %d sandbox(es) (append-only ledger rows)",
                "DRY-RUN" if dry_run else "WRITE", len(sandboxes))

    totals = {"sandboxes_processed": 0, "sandboxes_skipped": 0, "rows_written": 0,
              "seat_seeds": 0, "reconcile_pos": 0, "reconcile_neg": 0,
              "seat_chips": 0}
    per_sandbox = []
    for sid in sandboxes:
        if _already_backfilled(conn, sid):
            totals["sandboxes_skipped"] += 1
            per_sandbox.append({"sandbox_id": sid, "skipped": "already backfilled"})
            continue
        rows, counts = _plan_for_sandbox(conn, sid)
        written = 0
        if not dry_run:
            for r in rows:
                ctx = {"site": BACKFILL_SITE, "kind": r["kind"], "pid": r["pid"]}
                if r["reason"] == "ai_buy_in":
                    eid = L.record_transfer(
                        repo, source=r["source"], sink=r["sink"], amount=r["amount"],
                        reason="ai_buy_in", context=ctx, sandbox_id=sid)
                else:
                    eid = L.record(
                        repo, source=r["source"], sink=r["sink"], amount=r["amount"],
                        reason=r["reason"], context=ctx, sandbox_id=sid)
                if eid is not None:
                    written += 1
        else:
            written = len(rows)
        totals["sandboxes_processed"] += 1
        totals["rows_written"] += written
        totals["seat_seeds"] += counts["seat_seeds"]
        totals["reconcile_pos"] += counts["reconcile_pos"]
        totals["reconcile_neg"] += counts["reconcile_neg"]
        totals["seat_chips"] += counts["seat_chips"]
        per_sandbox.append({"sandbox_id": sid, "counts": counts, "rows_written": written})
        logger.info("  %s: %s rows (%s seat-seeds, +%s/-%s reconcile)",
                    sid[:12], written, counts["seat_seeds"],
                    counts["reconcile_pos"], counts["reconcile_neg"])

    # Player pass — GLOBAL (not per-sandbox). Skip when scoped to one sandbox or
    # already backfilled (players are global; re-running would double-reconcile).
    player_counts = {"skipped": "scoped/already"}
    if only_sandbox is None and not _player_already_backfilled(conn):
        player_counts = _reconcile_players(conn, repo, dry_run)
        totals["rows_written"] += player_counts["rows"]
        logger.info("  players: %s rows (+%s/-%s reconcile, %s noop)",
                    player_counts["rows"], player_counts["reconcile_pos"],
                    player_counts["reconcile_neg"], player_counts["noop"])
    totals["player_pass"] = player_counts

    conn.close()
    if repo is not None:
        repo.close()
    logger.info("Totals: %s", totals)
    return {"dry_run": dry_run, "totals": totals, "per_sandbox": per_sandbox}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default="/app/data/poker_games.db")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sandbox-id", default=None, help="Backfill only this sandbox")
    args = ap.parse_args()
    run(args.db_path, args.dry_run, args.sandbox_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
