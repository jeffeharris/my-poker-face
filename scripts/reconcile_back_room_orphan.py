"""One-shot: repair the orphaned "The Back Room" cash row IN PLACE so it
mirrors the still-live game, keeping the session active.

Context
-------
guest_jeff has an ACTIVE cash session (game ``cash-P3lh4jfkwgd4d8ezJgQ8Fg``,
sandbox ``4db9b9f2``, table ``cash-table-2-001`` seat 4). The game lost its
``cash_table_id`` on a cold-load (it's memory-only, not in
``game_state_json``), so the hand-boundary sync stopped re-stamping the human
seat, ``refresh_unseated_tables`` treated the table as empty, and the world
refilled all 6 seats with NEW AIs. The lobby now shows 6 strangers / the seat
"taken"; Resume opens the live game with a different roster. See the
``_restore_cash_table_binding`` fix in ``game_handler.py`` for the code-side
close (prevents recurrence).

This script repairs the EXISTING damage, conservation-safely:

  1. The 6 phantom AIs in the row each had their bankroll debited when the
     greedy fill seated them (``debit_bankroll_for_seat`` — a pure
     bankroll->seat transfer). Removing them is the inverse pure transfer:
     credit each one's CURRENT seat chips back to its stored bankroll. No
     ledger row (the original debit didn't emit one either; both surfaces
     are audit-counted, so seat-X / bankroll+X nets to zero drift).
  2. Rebuild the row to mirror the live game: the human at seat 4 with the
     live stack, the 5 live AIs at the remaining seats with their live
     stacks. This is exactly what the (now-fixed) hand-boundary sync would
     have kept producing.

Run (DRY-RUN by default; STOP THE BACKEND FIRST to avoid racing the ticker):

    docker compose stop backend
    docker compose run --rm --no-deps --entrypoint python3 backend \
        /app/scripts/reconcile_back_room_orphan.py            # dry-run
    docker compose run --rm --no-deps --entrypoint python3 backend \
        /app/scripts/reconcile_back_room_orphan.py --execute  # apply
    docker compose start backend
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime

from cash_mode.tables import CashTableState, ai_slot, human_slot, open_slot
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository

DB_PATH = "/app/data/poker_games.db"

GAME_ID = "cash-P3lh4jfkwgd4d8ezJgQ8Fg"
OWNER_ID = "guest_jeff"
SANDBOX_ID = "4db9b9f2-0724-439a-a4f9-1329c3678611"
TABLE_ID = "cash-table-2-001"
HUMAN_SEAT = 4

# Name -> personality_id, resolved via personality_repo.resolve_name_to_personality_id
# (matches what cold-load rebuilds cash_personality_ids to).
NAME_TO_PID = {
    "Agatha Christie": "agatha_christie",
    "Nikola Tesla": "nikola_tesla",
    "AI 13": "ai_13",
    "Cheshire Cat": "cheshire_cat",
    "Barack Obama": "barack_obama",
}


def backup_db() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = f"/app/data/poker_games.backup_reconcile_backroom_{ts}.db"
    src = sqlite3.connect(DB_PATH)
    try:
        out = sqlite3.connect(dst)
        with out:
            src.backup(out)  # WAL-safe online backup
        out.close()
        # Integrity check on the copy.
        chk = sqlite3.connect(dst)
        res = chk.execute("PRAGMA integrity_check").fetchone()[0]
        chk.close()
        print(f"[backup] {dst} (integrity_check={res})")
        if res != "ok":
            raise SystemExit("backup integrity check failed — aborting")
    finally:
        src.close()
    return dst


def read_live_roster() -> tuple[int, list[tuple[str, int]]]:
    """Return (human_stack, [(pid, stack), ...]) from the persisted game blob."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT game_state_json FROM games WHERE game_id = ?", (GAME_ID,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit(f"game {GAME_ID} not found")
    st = json.loads(row["game_state_json"])
    human_stack = 0
    ai: list[tuple[str, int]] = []
    for p in st.get("players", []):
        if p.get("is_human"):
            human_stack = int(p.get("stack", 0))
            continue
        pid = NAME_TO_PID.get(p.get("name"))
        if pid is None:
            raise SystemExit(f"unmapped live AI name {p.get('name')!r} — refusing to guess")
        ai.append((pid, int(p.get("stack", 0))))
    return human_stack, ai


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply (default: dry-run)")
    args = ap.parse_args()

    tables = CashTableRepository(DB_PATH)
    bankroll = BankrollRepository(DB_PATH)
    now = datetime.utcnow()

    table = tables.load_table(TABLE_ID, sandbox_id=SANDBOX_ID)
    if table is None:
        raise SystemExit(f"table {TABLE_ID} not found in sandbox {SANDBOX_ID}")

    print("=== CURRENT ROW (phantom) ===")
    phantom = []
    for i, s in enumerate(table.seats):
        print(f"  seat{i}: {s.get('kind'):6} pid={s.get('personality_id')} chips={s.get('chips')}")
        if s.get("kind") == "ai":
            phantom.append((i, s["personality_id"], int(s.get("chips", 0) or 0)))

    human_stack, live_ai = read_live_roster()
    print("\n=== LIVE GAME (target mirror) ===")
    print(f"  human {OWNER_ID} -> seat {HUMAN_SEAT}, stack {human_stack}")
    fill_seats = [i for i in range(6) if i != HUMAN_SEAT]
    plan_seats = ["open"] * 6
    plan_seats[HUMAN_SEAT] = f"human({human_stack})"
    for (pid, stack), seat_i in zip(live_ai, fill_seats):
        plan_seats[seat_i] = f"ai {pid}({stack})"
    for i, label in enumerate(plan_seats):
        print(f"  seat{i}: {label}")

    print("\n=== CHIP RETURN (phantom seat -> bankroll, pure transfer) ===")
    for _, pid, chips in phantom:
        cur = bankroll.load_ai_bankroll(pid, sandbox_id=SANDBOX_ID)
        cur_chips = cur.chips if cur is not None else "(no row)"
        print(f"  {pid}: bankroll {cur_chips} += {chips}")

    if not args.execute:
        print("\n[dry-run] no changes written. Re-run with --execute.")
        return

    backup_db()

    # 1. Return phantom seat chips to bankrolls (inverse of the seat debit).
    for _, pid, chips in phantom:
        if chips <= 0:
            continue
        cur = bankroll.load_ai_bankroll(pid, sandbox_id=SANDBOX_ID)
        from cash_mode.bankroll import AIBankrollState

        if cur is None:
            new_state = AIBankrollState(personality_id=pid, chips=chips, last_regen_tick=now)
            print(f"  [warn] {pid} had no bankroll row — creating with {chips}")
        else:
            new_state = AIBankrollState(
                personality_id=pid, chips=cur.chips + chips, last_regen_tick=now
            )
        bankroll.save_ai_bankroll(new_state, sandbox_id=SANDBOX_ID)
        print(f"  credited {pid}: -> {new_state.chips}")

    # 2. Rebuild the row to mirror the live game.
    new_seats = [open_slot() for _ in range(6)]
    new_seats[HUMAN_SEAT] = human_slot(OWNER_ID, human_stack)
    for (pid, stack), seat_i in zip(live_ai, fill_seats):
        new_seats[seat_i] = ai_slot(pid, stack)

    repaired = CashTableState(
        table_id=table.table_id,
        stake_label=table.stake_label,
        seats=new_seats,
        created_at=table.created_at,
        last_activity_at=table.last_activity_at,
        dealer_idx=table.dealer_idx,
        name=table.name,
        table_type=table.table_type,
        closing_hand_countdown=table.closing_hand_countdown,
    )
    tables.save_table(repaired, sandbox_id=SANDBOX_ID, now=now)
    print("\n=== REPAIRED ROW ===")
    final = tables.load_table(TABLE_ID, sandbox_id=SANDBOX_ID)
    for i, s in enumerate(final.seats):
        print(f"  seat{i}: {s.get('kind'):6} pid={s.get('personality_id')} chips={s.get('chips')}")
    print("\n[done] session kept active; lobby + live game now agree.")


if __name__ == "__main__":
    main()
