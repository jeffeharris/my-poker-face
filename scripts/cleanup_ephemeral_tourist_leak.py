"""One-shot cleanup: reconcile chip-conservation damage caused by
movement-evicting ephemeral tourists.

Background
----------
Before the movement-skip fix (CASH_MODE_EPHEMERAL_TOURISTS.md), the
`refresh_table_roster` movement evaluation treated ephemeral tourists
as broke (their `bankroll_lookup` returned 0 — no bankroll row exists).
Tourists got evicted via the standard `take_break` path, which:

  1. Wrote their seat chips to a phantom row in `ai_bankroll_state`
     (using `save_ai_bankroll`). Tourists were supposed to have NO
     bankroll rows.
  2. Added their synthetic pid to `cash_idle_pool` (where live-fill
     correctly rejects them due to bankroll<buy_in, so they stay there
     stranded).
  3. Did NOT write a `casino_seat_return` ledger row — the standard
     eviction path uses `to_bankroll` semantics, not the
     casino-specific return helper. Conservation invariant breaks:
     chips appear in bankroll rows that no ledger entry justifies.

This script cleans up the damage:

  A. For each phantom `tourist-*` bankroll row, write a synthetic
     `casino_seat_return` ledger row for the stranded chips. This
     re-credits the bank pool and restores `drift == 0`.
  B. Zero those bankroll rows (so a future audit doesn't see them).
  C. Delete tourist pids from `cash_idle_pool` (they're never picked
     up by live-fill anyway, but the rows clutter the table).

After cleanup + the movement-skip fix, tourists will:
  - Stay seated until they truly bust (stack=0) via hand play
  - Get their residual chips returned via `_return_seat_residuals_to_pool`
    when the casino tears down

Usage
-----
  python3 scripts/cleanup_ephemeral_tourist_leak.py            # preview
  python3 scripts/cleanup_ephemeral_tourist_leak.py --apply    # write

Preview prints the planned ledger writes and deletes without touching
the DB. Apply wraps every write in a single transaction so the cleanup
is all-or-nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import List, Tuple


DB_PATH_DOCKER = "/app/data/poker_games.db"
DB_PATH_LOCAL = "poker_games.db"

CENTRAL_BANK = "central_bank"
TOURIST_PID_PREFIX = "tourist-"


def _resolve_db_path() -> str:
    """Pick the right DB path for the environment. Docker mounts the
    canonical DB at /app/data/poker_games.db; outside Docker the project
    root has poker_games.db.
    """
    if os.path.exists(DB_PATH_DOCKER):
        return DB_PATH_DOCKER
    return DB_PATH_LOCAL


def find_phantom_bankrolls(conn: sqlite3.Connection) -> List[Tuple[str, int, str]]:
    """Return (personality_id, chips, sandbox_id) for every tourist-*
    bankroll row. Even zero-chip rows are returned so we can clean them up.
    """
    cursor = conn.execute(
        "SELECT personality_id, chips, sandbox_id "
        "FROM ai_bankroll_state "
        "WHERE personality_id LIKE ? "
        "ORDER BY personality_id",
        (f"{TOURIST_PID_PREFIX}%",),
    )
    return [(row[0], int(row[1]), row[2]) for row in cursor.fetchall()]


def find_tourist_idle_pool_entries(
    conn: sqlite3.Connection,
) -> List[Tuple[str, str]]:
    """Return (personality_id, sandbox_id) for every tourist-* idle pool
    row. These are stranded — live-fill rejects them on bankroll<buy_in."""
    cursor = conn.execute(
        "SELECT personality_id, sandbox_id "
        "FROM cash_idle_pool "
        "WHERE personality_id LIKE ? "
        "ORDER BY personality_id",
        (f"{TOURIST_PID_PREFIX}%",),
    )
    return [(row[0], row[1]) for row in cursor.fetchall()]


def find_currently_seated_tourist_pids(conn: sqlite3.Connection) -> set:
    """Tourist pids that are still on a casino seat with non-zero chips.
    These should be LEFT ALONE — they're live tourists, and their seat
    chips are still in play. Returns a set of pids.
    """
    seated: set = set()
    cursor = conn.execute(
        "SELECT seats_json FROM cash_tables WHERE table_type='casino'"
    )
    for (seats_json,) in cursor.fetchall():
        try:
            seats = json.loads(seats_json)
        except (TypeError, ValueError):
            continue
        for slot in seats:
            if not isinstance(slot, dict):
                continue
            if slot.get("kind") != "ai":
                continue
            pid = slot.get("personality_id")
            if pid and pid.startswith(TOURIST_PID_PREFIX):
                seated.add(pid)
    return seated


def plan_cleanup(conn: sqlite3.Connection):
    """Build the cleanup plan. Returns (ledger_writes, bankroll_zeros,
    idle_pool_deletes) — all lists of dicts describing the operations.

    A tourist currently seated at a casino (with chips on the seat) is
    EXEMPT from cleanup: their bankroll row is a leftover from a prior
    eviction cycle, but the chips on the seat are correctly tracked.
    Cleaning up their bankroll row is safe IFF it's also zero. We zero
    it without writing a return ledger row (no stranded chips).
    """
    phantoms = find_phantom_bankrolls(conn)
    idle_entries = find_tourist_idle_pool_entries(conn)
    seated_pids = find_currently_seated_tourist_pids(conn)

    ledger_writes: list = []
    bankroll_zeros: list = []
    for pid, chips, sandbox_id in phantoms:
        is_seated = pid in seated_pids
        if chips > 0:
            if is_seated:
                # Pathological: tourist seated AND has bankroll chips.
                # Likely shouldn't happen — the seat seed never wrote
                # a bankroll. Skip the ledger write (don't double-count)
                # but still zero the bankroll row.
                print(
                    f"[WARN] tourist {pid} is seated AND has {chips} "
                    f"phantom bankroll chips — zeroing without ledger return"
                )
                bankroll_zeros.append({
                    "pid": pid, "sandbox_id": sandbox_id,
                    "prior_chips": chips, "stranded_returned": 0,
                })
            else:
                ledger_writes.append({
                    "pid": pid, "amount": chips, "sandbox_id": sandbox_id,
                })
                bankroll_zeros.append({
                    "pid": pid, "sandbox_id": sandbox_id,
                    "prior_chips": chips, "stranded_returned": chips,
                })
        else:
            # Zero chips — just clean up the phantom row, no ledger needed.
            bankroll_zeros.append({
                "pid": pid, "sandbox_id": sandbox_id,
                "prior_chips": 0, "stranded_returned": 0,
            })

    idle_pool_deletes = [
        {"pid": pid, "sandbox_id": sandbox_id}
        for pid, sandbox_id in idle_entries
    ]
    return ledger_writes, bankroll_zeros, idle_pool_deletes


def render_plan(ledger_writes, bankroll_zeros, idle_pool_deletes):
    print("=" * 70)
    print(f"PHANTOM TOURIST CLEANUP PLAN")
    print("=" * 70)

    print(
        f"\n[1/3] casino_seat_return ledger writes "
        f"({len(ledger_writes)} rows):"
    )
    total_returned = 0
    for w in ledger_writes:
        print(
            f"  ai:{w['pid']} -> {CENTRAL_BANK}  "
            f"amount={w['amount']:>6}  sandbox={w['sandbox_id']}"
        )
        total_returned += w["amount"]
    print(f"  TOTAL chips returned to bank pool: {total_returned}")

    print(
        f"\n[2/3] ai_bankroll_state zeroes/deletes "
        f"({len(bankroll_zeros)} rows):"
    )
    for z in bankroll_zeros:
        print(
            f"  DELETE pid={z['pid']:>20}  "
            f"prior={z['prior_chips']:>4}  "
            f"sandbox={z['sandbox_id']}"
        )

    print(
        f"\n[3/3] cash_idle_pool deletes "
        f"({len(idle_pool_deletes)} rows):"
    )
    for d in idle_pool_deletes:
        print(f"  DELETE pid={d['pid']:>20}  sandbox={d['sandbox_id']}")


def apply_cleanup(
    conn: sqlite3.Connection,
    ledger_writes,
    bankroll_zeros,
    idle_pool_deletes,
):
    """Apply the cleanup in a single transaction. Conservation invariant
    is preserved: every chip removed from a phantom bankroll row is
    matched by a `casino_seat_return` ledger entry (= bank pool deposit).
    """
    now = datetime.utcnow().isoformat()
    cleanup_id = uuid.uuid4().hex[:8]
    with conn:
        # 1. Write casino_seat_return ledger entries (destruction at ai
        # side, deposit at bank side). Mirrors record_casino_seat_return.
        for w in ledger_writes:
            context = {
                "site": "phantom_tourist_cleanup",
                "cleanup_id": cleanup_id,
                "reason_detail": "movement_evicted_tourist_pre_skip_fix",
            }
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason, context_json, sandbox_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"ai:{w['pid']}",
                    CENTRAL_BANK,
                    int(w["amount"]),
                    "casino_seat_return",
                    json.dumps(context),
                    w["sandbox_id"],
                    now,
                ),
            )
        # 2. Delete phantom bankroll rows. (Zeroing would also work but
        # delete is cleaner — tourists shouldn't have bankrolls at all.)
        for z in bankroll_zeros:
            conn.execute(
                "DELETE FROM ai_bankroll_state "
                "WHERE personality_id = ? AND sandbox_id = ?",
                (z["pid"], z["sandbox_id"]),
            )
        # 3. Delete stranded idle pool entries.
        for d in idle_pool_deletes:
            conn.execute(
                "DELETE FROM cash_idle_pool "
                "WHERE personality_id = ? AND sandbox_id = ?",
                (d["pid"], d["sandbox_id"]),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the changes. Without this flag, only the plan is printed.",
    )
    parser.add_argument(
        "--db", default=None,
        help="Override DB path. Auto-detects Docker vs local otherwise.",
    )
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    if not os.path.exists(db_path):
        print(f"[ERR] DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        ledger_writes, bankroll_zeros, idle_pool_deletes = plan_cleanup(conn)
        render_plan(ledger_writes, bankroll_zeros, idle_pool_deletes)

        if not (ledger_writes or bankroll_zeros or idle_pool_deletes):
            print("\nNothing to clean up — sandbox is already healthy.")
            return 0

        if args.apply:
            apply_cleanup(conn, ledger_writes, bankroll_zeros, idle_pool_deletes)
            print(f"\n[OK] Cleanup applied at {db_path}")
        else:
            print("\n(preview only — re-run with --apply to write)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
