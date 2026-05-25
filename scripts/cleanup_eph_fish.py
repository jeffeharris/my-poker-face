#!/usr/bin/env python3
"""One-off cleanup: retire pre-migration ephemeral fish-clone personas.

Background
---------
Before the fish-as-personas migration, the casino seated per-session
``<fish>__eph_<hash>`` clone personas via ``ai_slot`` (no
``archetype='fish'`` SEAT stamp). After the migration these clones linger
and cause a split-brain:

  * Their seats are NOT stamped, so the lobby UI / teardown see no fish —
    the casino shows "no tourists".
  * But the clones still carry ``archetype='fish'`` in ``config_json``, so
    the old provisioning fish-count counted their seats as fish, wedging
    the casino: it looked "full" and never refilled or tore down.
  * They also hold pool-funded chips in ``ai_bankroll_state``, violating
    the closed-economy invariant that an un-seated fish's bankroll is 0.

The companion code fix (``_count_seated_fish`` now counts by the seat
stamp, and ``_reclaim_zombie_casino_seats`` reclaims un-stamped fish
seats) stops the wedge going forward and self-heals actively-refreshing
sandboxes. This script handles the *existing* stranded data deterministically:

  1. Return every eph fish's SEAT chips to its sandbox's bank pool and
     open the seat (``casino_seat_return`` — reverses the original seed).
  2. Return every eph fish's BANKROLL chips to the pool and zero the row
     (``_drain_fish_bankroll_to_pool``).
  3. Delete the eph persona rows so refill only ever picks the curated
     base fish.

Every chip move reverses a prior pool draw, so the operation is
conservation-neutral (outstanding chips unchanged). The script asserts
this per sandbox.

Usage
-----
    # Inside the backend container (uses the Flask app DB by default):
    docker compose exec backend python scripts/cleanup_eph_fish.py            # dry-run
    docker compose exec backend python scripts/cleanup_eph_fish.py --execute  # apply

DRY-RUN by default. Back up the DB before ``--execute``.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime

from cash_mode.casino_provisioning import _drain_fish_bankroll_to_pool
from cash_mode.closed_economy import compute_bank_pool_reserves
from cash_mode.tables import open_slot
from core.economy.ledger import record_casino_seat_return
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository

DEFAULT_DB = "/app/data/poker_games.db"


def _eph_fish_pids(db_path: str) -> set[str]:
    """Persona ids that are ephemeral fish clones (``__eph_`` + fish archetype)."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT personality_id FROM personalities "
            "WHERE personality_id IS NOT NULL "
            "AND json_extract(config_json, '$.archetype') = 'fish'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows if "__eph_" in (r[0] or "")}


def _sandboxes(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT sandbox_id FROM cash_tables "
            "UNION SELECT DISTINCT sandbox_id FROM ai_bankroll_state"
        ).fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows if r[0])


def _outstanding(ledger: ChipLedgerRepository, sandbox_id: str) -> int:
    created = sum(ledger.sum_creations_by_reason(sandbox_id=sandbox_id).values())
    destroyed = sum(ledger.sum_destructions_by_reason(sandbox_id=sandbox_id).values())
    return created - destroyed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"DB path (default {DEFAULT_DB})")
    ap.add_argument("--execute", action="store_true", help="Apply changes (default: dry-run)")
    args = ap.parse_args()

    now = datetime.now()
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    eph_pids = _eph_fish_pids(args.db)
    print(f"[{mode}] db={args.db}")
    print(f"[{mode}] {len(eph_pids)} ephemeral fish-clone personas found\n")
    if not eph_pids:
        print("Nothing to do.")
        return 0

    ledger = ChipLedgerRepository(args.db)
    tables = CashTableRepository(args.db)
    bankroll = BankrollRepository(args.db)

    grand_seat = grand_bankroll = 0
    drift_errors: list[str] = []

    for sandbox_id in _sandboxes(args.db):
        pool_before = compute_bank_pool_reserves(ledger, sandbox_id=sandbox_id)
        out_before = _outstanding(ledger, sandbox_id)
        seat_chips = bankroll_chips = 0
        seat_n = bankroll_n = 0

        # --- 1) eph seat chips -> pool, open the seat -------------------
        for table in tables.list_all_tables(sandbox_id=sandbox_id):
            if table.table_type != "casino":
                continue
            new_seats = list(table.seats)
            changed = False
            for idx, slot in enumerate(table.seats):
                if slot.get("kind") != "ai" or slot.get("personality_id") not in eph_pids:
                    continue
                chips = int(slot.get("chips") or 0)
                pid = slot["personality_id"]
                seat_n += 1
                seat_chips += chips
                if args.execute:
                    if chips > 0:
                        row_id = record_casino_seat_return(
                            ledger, personality_id=pid, amount=chips,
                            context={"site": "eph_fish_cleanup", "table_id": table.table_id,
                                     "stake_label": table.stake_label, "reason": "eph_seat"},
                            sandbox_id=sandbox_id,
                        )
                        if row_id is None:
                            drift_errors.append(f"{sandbox_id}: seat-return failed for {pid} ({chips})")
                            continue  # leave seat to retry; don't vanish chips
                    new_seats[idx] = open_slot()
                    changed = True
            if changed and args.execute:
                tables.save_table(table.__class__(
                    table_id=table.table_id, stake_label=table.stake_label, seats=new_seats,
                    created_at=table.created_at, last_activity_at=now, name=table.name,
                    table_type="casino", dealer_idx=table.dealer_idx,
                    closing_hand_countdown=table.closing_hand_countdown,
                ), sandbox_id=sandbox_id, now=now)

        # --- 2) eph bankroll chips -> pool, zero the row ----------------
        for pid in sorted(eph_pids):
            existing = 0
            state = bankroll.load_ai_bankroll(pid, sandbox_id=sandbox_id)
            if state is not None:
                existing = int(state.chips or 0)
            if existing <= 0:
                continue
            bankroll_n += 1
            bankroll_chips += existing
            if args.execute:
                returned, stranded = _drain_fish_bankroll_to_pool(
                    bankroll, ledger, personality_id=pid, sandbox_id=sandbox_id,
                    now=now, reason_detail="eph_fish_cleanup",
                )
                if stranded:
                    drift_errors.append(f"{sandbox_id}: bankroll drain stranded {stranded} for {pid}")

        if seat_n or bankroll_n:
            pool_after = compute_bank_pool_reserves(ledger, sandbox_id=sandbox_id)
            out_after = _outstanding(ledger, sandbox_id)
            print(f"  sb={sandbox_id[:8]}  seats={seat_n} (+{seat_chips} chips)  "
                  f"bankrolls={bankroll_n} (+{bankroll_chips} chips)")
            if args.execute:
                print(f"           pool {pool_before} -> {pool_after} (+{pool_after - pool_before}); "
                      f"outstanding {out_before} -> {out_after}")
                if out_after != out_before:
                    drift_errors.append(
                        f"{sandbox_id}: outstanding changed {out_before} -> {out_after} (NON-NEUTRAL)")
            grand_seat += seat_chips
            grand_bankroll += bankroll_chips

    print(f"\n[{mode}] total seat chips: {grand_seat}  |  total bankroll chips: {grand_bankroll}"
          f"  |  grand total -> pool: {grand_seat + grand_bankroll}")

    # --- 3) delete the eph personas (only after chips are home) ---------
    if args.execute:
        if drift_errors:
            print("\nABORTED persona deletion — chip moves had errors:")
            for e in drift_errors:
                print("  !", e)
            return 1
        conn = sqlite3.connect(args.db)
        try:
            placeholders = ",".join("?" * len(eph_pids))
            cur = conn.execute(
                f"DELETE FROM personalities WHERE personality_id IN ({placeholders})",
                sorted(eph_pids),
            )
            conn.commit()
            print(f"\nDeleted {cur.rowcount} ephemeral fish-clone persona rows.")
        finally:
            conn.close()
    else:
        print(f"\n[DRY-RUN] would delete {len(eph_pids)} persona rows after returning chips.")
        print("[DRY-RUN] re-run with --execute to apply (back up the DB first).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
