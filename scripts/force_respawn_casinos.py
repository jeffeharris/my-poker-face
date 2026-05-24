"""One-shot: force-respawn all casino tables.

Drains every casino's seats (returning chips properly per seat type)
and deletes the table row. The next lobby refresh will see no casino
at the stake and spawn a fresh one with 4 tourists.

Why this is needed: the pre-EPHEMERAL_TOURISTS movement-eviction bug
let casinos drift into bad states where DB-AI personalities took over
tourist seats. The casino spawn logic skips when a casino at the stake
already exists, so the bad state persists. This script clears the
slate.

Per-seat handling (conservation-preserving):

  * Ephemeral tourist seats (`ephemeral_personality` present) →
    `record_casino_seat_return` ledger row (chips → bank pool).
    Tourists have no bankroll; chips MUST go back to the pool to
    preserve drift==0.

  * DB-AI seats (regular personality, no ephemeral) → increment the
    AI's `ai_bankroll_state` row by the seat's chips. This is a pure
    transfer (no ledger row); when the AI live-filled in, their
    bankroll was decremented by buy_in. Returning seat_chips to the
    bankroll just reverses that flow.

  * Open / human seats → ignored.

Usage
-----
  python3 scripts/force_respawn_casinos.py            # preview
  python3 scripts/force_respawn_casinos.py --apply    # write
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import List, Tuple


DB_PATH_DOCKER = "/app/data/poker_games.db"
DB_PATH_LOCAL = "data/poker_games.db"

CENTRAL_BANK = "central_bank"
TOURIST_PID_PREFIX = "tourist-"


def _resolve_db_path() -> str:
    for p in (DB_PATH_DOCKER, DB_PATH_LOCAL):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"DB not found at {DB_PATH_DOCKER} or {DB_PATH_LOCAL}")


def list_casinos(conn: sqlite3.Connection):
    """Return list of (table_id, sandbox_id, seats) for every casino."""
    cursor = conn.execute(
        "SELECT table_id, sandbox_id, seats_json "
        "FROM cash_tables WHERE table_type='casino'"
    )
    out = []
    for table_id, sandbox_id, seats_json in cursor.fetchall():
        try:
            seats = json.loads(seats_json)
        except (TypeError, ValueError):
            seats = []
        out.append((table_id, sandbox_id, seats))
    return out


def plan_drain(seats):
    """Categorize each AI seat for the drain. Returns
    (tourist_returns, bankroll_credits, ignored_count).

    * tourist_returns: list of (pid, chips) — go to bank pool
    * bankroll_credits: list of (pid, chips) — added to AI's bankroll
    * ignored_count: open/human seats (no action)
    """
    tourist_returns: List[Tuple[str, int]] = []
    bankroll_credits: List[Tuple[str, int]] = []
    ignored = 0
    for slot in seats:
        if not isinstance(slot, dict):
            ignored += 1
            continue
        if slot.get("kind") != "ai":
            ignored += 1
            continue
        chips = int(slot.get("chips", 0))
        pid = slot.get("personality_id")
        if not pid:
            ignored += 1
            continue
        if slot.get("ephemeral_personality") is not None or pid.startswith(TOURIST_PID_PREFIX):
            if chips > 0:
                tourist_returns.append((pid, chips))
            # zero-chip tourists: just vanish (no ledger needed)
        else:
            if chips > 0:
                bankroll_credits.append((pid, chips))
    return tourist_returns, bankroll_credits, ignored


def render_plan(casinos):
    print("=" * 70)
    print("FORCE-RESPAWN CASINOS PLAN")
    print("=" * 70)
    total_pool_returns = 0
    total_bankroll_credits = 0
    for table_id, sandbox_id, seats in casinos:
        tourist_returns, bankroll_credits, ignored = plan_drain(seats)
        print(f"\n[{table_id}] sandbox={sandbox_id}")
        print(f"  tourist seat returns ({len(tourist_returns)}):")
        for pid, chips in tourist_returns:
            print(f"    casino_seat_return: ai:{pid:>22} -> bank  amount={chips}")
            total_pool_returns += chips
        print(f"  DB-AI bankroll credits ({len(bankroll_credits)}):")
        for pid, chips in bankroll_credits:
            print(f"    bankroll[{pid:>30}] += {chips}")
            total_bankroll_credits += chips
        print(f"  other seats (open/human/empty): {ignored}")
        print(f"  DELETE cash_tables[{table_id}]")
    print(
        f"\n--- TOTAL: "
        f"{total_pool_returns} chips -> bank pool, "
        f"{total_bankroll_credits} chips -> AI bankrolls"
    )


def apply_drain(conn: sqlite3.Connection, casinos):
    """Apply the drain. Wraps everything in a single transaction so
    either ALL casinos clear cleanly or none do.
    """
    now = datetime.utcnow().isoformat()
    with conn:
        for table_id, sandbox_id, seats in casinos:
            tourist_returns, bankroll_credits, _ = plan_drain(seats)

            # 1. Write casino_seat_return ledger rows for tourists.
            for pid, chips in tourist_returns:
                context = {
                    "site": "force_respawn_casinos",
                    "table_id": table_id,
                    "reason_detail": "manual_force_respawn",
                }
                conn.execute(
                    "INSERT INTO chip_ledger_entries "
                    "(source, sink, amount, reason, context_json, "
                    "sandbox_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"ai:{pid}",
                        CENTRAL_BANK,
                        int(chips),
                        "casino_seat_return",
                        json.dumps(context),
                        sandbox_id,
                        now,
                    ),
                )

            # 2. Credit DB-AI bankrolls (pure transfer — no ledger row).
            # If the row exists: ADD chips. If not: create with chips
            # value AND write an ai_seed ledger row for first-write
            # so drift stays correct.
            for pid, chips in bankroll_credits:
                row = conn.execute(
                    "SELECT chips FROM ai_bankroll_state "
                    "WHERE personality_id = ? AND sandbox_id = ?",
                    (pid, sandbox_id),
                ).fetchone()
                if row is None:
                    # First-time write — emit ai_seed so the seat chips
                    # are accounted for (otherwise drift goes negative
                    # by `chips`).
                    conn.execute(
                        "INSERT INTO ai_bankroll_state "
                        "(personality_id, chips, last_regen_tick, sandbox_id) "
                        "VALUES (?, ?, ?, ?)",
                        (pid, int(chips), now, sandbox_id),
                    )
                    conn.execute(
                        "INSERT INTO chip_ledger_entries "
                        "(source, sink, amount, reason, context_json, "
                        "sandbox_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            CENTRAL_BANK, f"ai:{pid}", int(chips), "ai_seed",
                            json.dumps({"site": "force_respawn_casinos_seed"}),
                            sandbox_id, now,
                        ),
                    )
                else:
                    new_chips = int(row[0]) + int(chips)
                    conn.execute(
                        "UPDATE ai_bankroll_state SET chips = ? "
                        "WHERE personality_id = ? AND sandbox_id = ?",
                        (new_chips, pid, sandbox_id),
                    )

            # 3. Delete the casino table row.
            conn.execute(
                "DELETE FROM cash_tables "
                "WHERE table_id = ? AND sandbox_id = ?",
                (table_id, sandbox_id),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        casinos = list_casinos(conn)
        if not casinos:
            print("No casino tables found — nothing to do.")
            return 0
        render_plan(casinos)
        if args.apply:
            apply_drain(conn, casinos)
            print(f"\n[OK] Drained + deleted {len(casinos)} casinos at {db_path}")
            print("Next lobby refresh will spawn fresh casinos with 4 tourists each.")
        else:
            print("\n(preview only — re-run with --apply to write)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
