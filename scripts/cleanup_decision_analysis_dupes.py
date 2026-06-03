#!/usr/bin/env python3
"""Conservation-safe cleanup of pre-fix double-written player_decision_analysis rows.

Background
----------
Before commit 378e3175 ("stop double-writing player_decision_analysis rows")
every AI decision was recorded TWICE:

  * a controller-side row (Path A, written inside ``decide_action``), and
  * a handler-side row (Path B, written after ``play_turn``).

The two writes land on immediately-consecutive ids (the loop is single-threaded
under the per-game lock), describe the *same* game moment, and are near-identical
full copies — the only meaningful difference is ``capture_id`` (linked on the
controller row when an LLM capture exists, NULL on the handler row). Humans get a
single row. The fix stopped new double-writes; this script removes the stale
twins so the analyzer stops showing inflated/odd decision counts for old hands.

Deletion predicate (a row D is deleted iff a keeper K exists where):
  * K.id == D.id - 1                                 (immediately consecutive)
  * D.capture_id IS NULL                             (D is the handler copy)
  * same game_id, hand_number, phase, player_name, action_taken
  * identical game moment: pot_total, cost_to_call, player_stack,
    community_cards, player_hand

The game-moment match is the key safety gate: a *legitimate* repeat action
(e.g. limp pre-flop, then call a 3-bet) is never immediately consecutive AND
always differs in pot/stack/board, so it can never be matched. We always keep
the lower id (the controller row, which carries capture_id when present).

Usage
-----
    # dry run (default) — reports what WOULD be deleted, changes nothing
    docker compose exec backend python3 scripts/cleanup_decision_analysis_dupes.py

    # actually delete (backs up the DB first, integrity-checks the backup)
    docker compose exec backend python3 scripts/cleanup_decision_analysis_dupes.py --apply

Options:
    --db PATH     database path (default: /app/data/poker_games.db)
    --apply       perform the deletion (otherwise dry-run only)
    --limit N     show at most N sample pairs in the report (default: 20)
"""
from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys

DEFAULT_DB = "/app/data/poker_games.db"

# Keeper K sits at D.id - 1; D is the redundant handler copy (capture_id NULL).
# IS is null-safe equality so NULL columns match NULL columns.
CANDIDATE_SQL = """
    SELECT d.id            AS del_id,
           k.id            AS keep_id,
           d.game_id       AS game_id,
           d.hand_number   AS hand_number,
           d.phase         AS phase,
           d.player_name   AS player_name,
           d.action_taken  AS action_taken,
           k.capture_id    AS keep_cap,
           d.capture_id    AS del_cap,
           k.created_at    AS keep_ts,
           d.created_at    AS del_ts
    FROM player_decision_analysis d
    JOIN player_decision_analysis k ON k.id = d.id - 1
    WHERE d.capture_id IS NULL
      AND k.game_id        IS d.game_id
      AND k.hand_number    IS d.hand_number
      AND k.phase          IS d.phase
      AND k.player_name    IS d.player_name
      AND k.action_taken   IS d.action_taken
      AND k.pot_total      IS d.pot_total
      AND k.cost_to_call   IS d.cost_to_call
      AND k.player_stack   IS d.player_stack
      AND k.community_cards IS d.community_cards
      AND k.player_hand    IS d.player_hand
    ORDER BY d.game_id, d.id
"""


def find_candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(CANDIDATE_SQL).fetchall()


def backup_db(db_path: str) -> str:
    """WAL-safe backup via the SQLite backup API, then integrity-check it."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_path = f"{db_path}.bak_dedup_{ts}"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dst_path)
    try:
        with dst:
            src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        src.close()
        dst.close()
    if result != "ok":
        raise RuntimeError(f"Backup integrity_check failed: {result!r}")
    size = os.path.getsize(dst_path)
    print(f"  backup written: {dst_path} ({size:,} bytes, integrity_check=ok)")
    return dst_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="database path")
    parser.add_argument("--apply", action="store_true", help="perform the deletion")
    parser.add_argument("--limit", type=int, default=20, help="sample pairs to show")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    total_rows = conn.execute("SELECT COUNT(*) FROM player_decision_analysis").fetchone()[0]
    candidates = find_candidates(conn)
    del_ids = [r["del_id"] for r in candidates]

    print(f"Database: {args.db}")
    print(f"Total player_decision_analysis rows: {total_rows:,}")
    print(f"Duplicate handler-copy rows to delete: {len(del_ids):,}")
    print(f"Rows remaining after cleanup:          {total_rows - len(del_ids):,}")

    # Safety report: any candidate whose deleted row carries a capture_id, or
    # whose keeper lacks one while the deleted row has one. The predicate
    # already requires del_cap IS NULL, so this should always be empty — print
    # it as an explicit assertion for the operator.
    anomalies = [r for r in candidates if r["del_cap"] is not None]
    print(f"Anomalies (deleted row has a capture_id): {len(anomalies)} (expected 0)")
    if anomalies:
        print("  REFUSING to proceed — predicate guarantee violated. Investigate:")
        for r in anomalies[:10]:
            print(f"    del_id={r['del_id']} del_cap={r['del_cap']} keep_id={r['keep_id']}")
        return 2

    # Per-game breakdown
    by_game: dict[str, int] = {}
    for r in candidates:
        by_game[r["game_id"]] = by_game.get(r["game_id"], 0) + 1
    if by_game:
        print("\nPer-game deletions:")
        for gid, n in sorted(by_game.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>5}  {gid}")

    # Sample pairs
    if candidates:
        print(f"\nSample twin pairs (keep -> delete), up to {args.limit}:")
        for r in candidates[: args.limit]:
            kc = "None" if r["keep_cap"] is None else r["keep_cap"]
            print(
                f"  keep id={r['keep_id']:>5} (cap={kc:>4}, {r['keep_ts']})  "
                f"delete id={r['del_id']:>5} (cap=None, {r['del_ts']})  "
                f"{r['game_id']} h{r['hand_number']} {r['phase']} "
                f"{r['player_name']} {r['action_taken']}"
            )

    if not del_ids:
        print("\nNothing to delete. Done.")
        conn.close()
        return 0

    if not args.apply:
        print("\n[DRY RUN] No changes made. Re-run with --apply to delete the above rows.")
        conn.close()
        return 0

    # --- apply ---
    print("\n[APPLY] Backing up database before deletion...")
    backup_db(args.db)

    print("[APPLY] Deleting duplicate rows in a transaction...")
    cur = conn.cursor()
    deleted = 0
    try:
        cur.execute("BEGIN")
        # Delete in chunks to keep the IN-list bounded.
        CHUNK = 500
        for i in range(0, len(del_ids), CHUNK):
            chunk = del_ids[i : i + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            cur.execute(
                f"DELETE FROM player_decision_analysis WHERE id IN ({placeholders})",
                chunk,
            )
            deleted += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        print("  ERROR during delete — rolled back, no rows changed.", file=sys.stderr)
        raise

    remaining = conn.execute("SELECT COUNT(*) FROM player_decision_analysis").fetchone()[0]
    print(f"  deleted {deleted:,} rows; {remaining:,} rows remain.")
    conn.close()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
