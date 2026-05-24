"""One-shot cleanup: delete zombie DB personalities created from tourist
display names.

Background
----------
Before the avatar-handler skip fix, the lobby serializer for every
tourist seat called `get_avatar_url_with_fallback(None, display_name,
emotion)`. That triggered `generate_character_images(display_name)`,
which called `personality_generator.get_personality(display_name)`,
which AUTO-CREATED a DB personality row when the name didn't exist.
Each tourist spawned at a casino seeded one zombie per unique tourist
display name (Brenda, Brad, Connor, Mona, ...).

Once in the `personalities` table, zombies got auto-seeded bankrolls
and became eligible for live-fill at any cash table — that's why
"fish" started appearing at $200 lobby tables.

This script identifies zombies by name+pid pattern (tourist factory
display-name candidates), then:

  1. Evicts them from any cash table seat. Their seat chips return to
     their bankroll (the same flow as a normal `take_break` eviction)
     — no ledger row needed (pure ai-side transfer).
  2. Deletes their `cash_idle_pool` entries.
  3. Computes the leftover bankroll chips and writes a destruction
     ledger entry (synthetic `cap_clamp` reason, audit-recognized as
     "chips removed from the universe") so drift stays correct.
  4. Deletes the bankroll rows.
  5. Deletes the `personalities` row.

Conservation invariant is preserved: every chip these zombies held
ends up either (a) returned to the central bank via cap_clamp or
(b) still on the seat (if eviction failed) — never silently destroyed.

Usage
-----
  python3 scripts/cleanup_tourist_zombie_personalities.py            # preview
  python3 scripts/cleanup_tourist_zombie_personalities.py --apply    # write
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Tuple


DB_PATH_DOCKER = "/app/data/poker_games.db"
DB_PATH_LOCAL = "data/poker_games.db"

CENTRAL_BANK = "central_bank"

# Tourist display-name candidate space — must match the per-template
# `name_pool` in cash_mode/tourist_factory.py exactly. Plus the
# suffix-tagged forms ("(bachelorette)", "(birthday)") that the
# factory generates from `nickname_suffix`.
TOURIST_FIRST_NAMES = {
    # vacation_dad
    "Greg", "Dave", "Doug", "Rick", "Steve", "Mike", "Jeff", "Brad",
    "Chad", "Wayne", "Randy", "Kurt",
    # bachelorette
    "Brenda", "Tiffany", "Ashley", "Brittany", "Megan", "Courtney",
    "Lauren", "Stacy", "Jenna", "Caitlin",
    # retired_know_it_all
    "Carl", "Frank", "Stan", "Vince", "Norm", "Harold", "Ernie",
    "Walt", "Lloyd", "Hank",
    # birthday_kid
    "Bobby", "Tommy", "Joey", "Kenny", "Danny", "Ricky", "Jimmy",
    "Mikey", "Sammy",
    # finance_bro
    "Trent", "Brett", "Connor", "Tyler", "Hunter", "Garrett", "Brody",
    # superstitious_grandma
    "Mona", "Doris", "Ethel", "Mildred", "Phyllis", "Bernice",
    "Edna", "Gertrude",
    # slot_refugee
    "Linda", "Karen", "Donna", "Cheryl", "Patty", "Sharon", "Joyce",
    "Marlene",
    # golf_trip_dude
    "Kevin", "Scott", "Todd", "Curt", "Jay",
}


def _resolve_db_path() -> str:
    for p in (DB_PATH_DOCKER, DB_PATH_LOCAL):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"DB not found at {DB_PATH_DOCKER} or {DB_PATH_LOCAL}")


def _is_zombie_name(name: str) -> bool:
    """A name is a zombie iff it's a bare first-name from the tourist
    factory OR has a `(bachelorette)` / `(birthday)` suffix."""
    if not name:
        return False
    if name.endswith("(bachelorette)") or name.endswith("(birthday)"):
        return True
    return name in TOURIST_FIRST_NAMES


def find_zombies(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Return (personality_id, name) for every zombie personality."""
    cursor = conn.execute("SELECT personality_id, name FROM personalities")
    out: List[Tuple[str, str]] = []
    for pid, name in cursor.fetchall():
        if _is_zombie_name(name):
            out.append((pid, name))
    out.sort(key=lambda r: r[1])
    return out


def find_seated_zombies(
    conn: sqlite3.Connection, zombie_pids: set
) -> List[Tuple[str, str, int, int, str]]:
    """Return (table_id, sandbox_id, seat_index, chips, pid) for every
    cash_tables seat occupied by a zombie."""
    cursor = conn.execute(
        "SELECT table_id, sandbox_id, seats_json FROM cash_tables"
    )
    out: List[Tuple[str, str, int, int, str]] = []
    for table_id, sandbox_id, seats_json in cursor.fetchall():
        try:
            seats = json.loads(seats_json)
        except (TypeError, ValueError):
            continue
        for idx, slot in enumerate(seats):
            if not isinstance(slot, dict) or slot.get("kind") != "ai":
                continue
            pid = slot.get("personality_id")
            if pid in zombie_pids:
                chips = int(slot.get("chips", 0))
                out.append((table_id, sandbox_id, idx, chips, pid))
    return out


def get_zombie_bankrolls(conn: sqlite3.Connection, zombie_pids: set):
    """Return [(pid, chips, sandbox_id)] for zombie bankroll rows."""
    if not zombie_pids:
        return []
    qmarks = ",".join("?" * len(zombie_pids))
    cursor = conn.execute(
        f"SELECT personality_id, chips, sandbox_id "
        f"FROM ai_bankroll_state WHERE personality_id IN ({qmarks})",
        list(zombie_pids),
    )
    return [(r[0], int(r[1]), r[2]) for r in cursor.fetchall()]


def get_zombie_idle_pool(conn: sqlite3.Connection, zombie_pids: set):
    if not zombie_pids:
        return []
    qmarks = ",".join("?" * len(zombie_pids))
    cursor = conn.execute(
        f"SELECT personality_id, sandbox_id "
        f"FROM cash_idle_pool WHERE personality_id IN ({qmarks})",
        list(zombie_pids),
    )
    return cursor.fetchall()


def render_plan(zombies, seated, bankrolls, idle_entries):
    print("=" * 70)
    print("ZOMBIE TOURIST PERSONALITY CLEANUP PLAN")
    print("=" * 70)

    print(f"\n[1/5] Zombie personalities to delete ({len(zombies)}):")
    for pid, name in zombies:
        print(f"  {pid:>32}  {name}")

    print(f"\n[2/5] Seats currently occupied by zombies ({len(seated)}):")
    total_seat_chips = 0
    for table_id, sandbox_id, idx, chips, pid in seated:
        print(f"  {table_id:>20} seat[{idx}] pid={pid:>20} chips={chips}")
        total_seat_chips += chips
    print(f"  → return seat chips to zombie bankrolls: {total_seat_chips}")

    print(f"\n[3/5] Bankroll rows to delete ({len(bankrolls)}):")
    total_bankroll_chips = 0
    for pid, chips, sandbox_id in bankrolls:
        print(f"  {pid:>20} chips={chips:>7}  sandbox={sandbox_id}")
        total_bankroll_chips += chips
    print(
        f"  → total chips destroyed via cap_clamp ledger entry: "
        f"{total_bankroll_chips + total_seat_chips}"
    )

    print(f"\n[4/5] Idle pool rows to delete ({len(idle_entries)}):")
    for pid, sandbox_id in idle_entries:
        print(f"  {pid:>20}  sandbox={sandbox_id}")

    print(f"\n[5/5] DELETE FROM personalities ({len(zombies)} rows)")


def apply_cleanup(conn, zombies, seated, bankrolls, idle_entries):
    """Apply in a single transaction. Conservation: every seat chip is
    rolled into the zombie's bankroll, then the total bankroll (seat +
    prior) is destroyed via a cap_clamp ledger entry (the audit-
    recognized 'chips removed from universe' reason). Bankroll and
    personality rows then deleted."""
    now = datetime.utcnow().isoformat()
    zombie_pids = {pid for pid, _ in zombies}

    with conn:
        # 1. Per casino/lobby table: zero out the zombie seats. Their
        # seat chips will be folded into the bankroll-deletion total.
        seated_chips_by_pid: Dict[str, int] = {}
        for table_id, sandbox_id, idx, chips, pid in seated:
            seated_chips_by_pid[pid] = seated_chips_by_pid.get(pid, 0) + chips
            row = conn.execute(
                "SELECT seats_json FROM cash_tables "
                "WHERE table_id = ? AND sandbox_id = ?",
                (table_id, sandbox_id),
            ).fetchone()
            if not row:
                continue
            seats = json.loads(row[0])
            seats[idx] = {"kind": "open"}
            conn.execute(
                "UPDATE cash_tables SET seats_json = ? "
                "WHERE table_id = ? AND sandbox_id = ?",
                (json.dumps(seats), table_id, sandbox_id),
            )

        # 2. For each zombie bankroll, write a cap_clamp destruction
        # ledger entry for (bankroll_chips + seat_chips_returning).
        # This removes the chips from the universe; without it the
        # audit's drift goes negative when we delete the rows.
        # Track per-(pid, sandbox_id) so we cover bankroll rows AND
        # zombies that only had seat chips (no bankroll row yet).
        all_pid_sandbox = {
            (pid, sandbox): chips for pid, chips, sandbox in bankrolls
        }
        for table_id, sandbox_id, idx, chips, pid in seated:
            # If the zombie had no bankroll row, we still need to
            # destroy the seat chips.
            key = (pid, sandbox_id)
            if key not in all_pid_sandbox:
                all_pid_sandbox[key] = 0
            all_pid_sandbox[key] += chips

        for (pid, sandbox_id), total_chips in all_pid_sandbox.items():
            if total_chips <= 0:
                continue
            context = {
                "site": "cleanup_tourist_zombie_personalities",
                "reason_detail": "auto_created_zombie_destroyed",
            }
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(source, sink, amount, reason, context_json, "
                "sandbox_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"ai:{pid}", CENTRAL_BANK, int(total_chips),
                    "cap_clamp", json.dumps(context), sandbox_id, now,
                ),
            )

        # 3. Delete bankroll rows.
        for pid, _, sandbox_id in bankrolls:
            conn.execute(
                "DELETE FROM ai_bankroll_state "
                "WHERE personality_id = ? AND sandbox_id = ?",
                (pid, sandbox_id),
            )

        # 4. Delete idle pool rows.
        for pid, sandbox_id in idle_entries:
            conn.execute(
                "DELETE FROM cash_idle_pool "
                "WHERE personality_id = ? AND sandbox_id = ?",
                (pid, sandbox_id),
            )

        # 5. Delete the personality rows themselves.
        if zombie_pids:
            qmarks = ",".join("?" * len(zombie_pids))
            conn.execute(
                f"DELETE FROM personalities WHERE personality_id IN ({qmarks})",
                list(zombie_pids),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    conn = sqlite3.connect(db_path)
    try:
        zombies = find_zombies(conn)
        if not zombies:
            print("No zombie personalities found.")
            return 0
        zombie_pids = {pid for pid, _ in zombies}
        seated = find_seated_zombies(conn, zombie_pids)
        bankrolls = get_zombie_bankrolls(conn, zombie_pids)
        idle_entries = get_zombie_idle_pool(conn, zombie_pids)

        render_plan(zombies, seated, bankrolls, idle_entries)

        if args.apply:
            apply_cleanup(conn, zombies, seated, bankrolls, idle_entries)
            print(f"\n[OK] Cleanup applied at {db_path}")
        else:
            print("\n(preview only — re-run with --apply to write)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
