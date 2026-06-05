#!/usr/bin/env python3
"""Sweep child rows orphaned by deleted games (post-prod-cutover cleanup).

The v70→v151 prod migration runs v142 (`_migrate_v142_drop_tournament_tracker`),
which DELETEs the tournament-linked `games` rows but NOT their descendants. That
leaves child rows (api_usage, prompt_captures, hand_history, ...) pointing at
game_ids that no longer exist. SQLite doesn't enforce foreign keys, so these are
harmless junk — but this script tidies them after the deploy, outside the
migration chain (see docs/plans/PROD_MERGE_PLAN.md, "Post-deploy cleanup").

It finds every table with a `game_id` column and deletes rows whose game_id is
absent from `games`. The `games` table itself is never touched.

DRY-RUN by default — prints what it WOULD delete and changes nothing. Pass
`--apply` to actually delete. Always back up first (the deploy does:
`scripts/backup_db.py <db>`); this script also runs in a single transaction and
rolls back on any error.

Usage:
    # On the prod box, AFTER deploy + a fresh backup:
    python3 scripts/cleanup_orphaned_game_rows.py /opt/poker/data/poker_games.db          # dry-run
    python3 scripts/cleanup_orphaned_game_rows.py /opt/poker/data/poker_games.db --apply  # delete

Exit codes: 0 ok; 1 error / DB missing.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys


def tables_with_game_id(conn: sqlite3.Connection) -> list[str]:
    out = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        if name == "games":
            continue
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
        if "game_id" in cols:
            out.append(name)
    return sorted(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Delete child rows orphaned by deleted games")
    ap.add_argument("db", help="path to the SQLite database")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    args = ap.parse_args(argv)

    try:
        conn = sqlite3.connect(args.db)
    except sqlite3.Error as e:
        print(f"ERROR opening {args.db}: {e}")
        return 1

    try:
        n_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        print(f"games present: {n_games}")
        print(f"mode: {'APPLY (deleting)' if args.apply else 'DRY-RUN (no changes)'}\n")

        orphan_where = "game_id NOT IN (SELECT game_id FROM games)"
        total = 0
        affected = []
        for t in tables_with_game_id(conn):
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {orphan_where}').fetchone()[0]
            if n:
                affected.append((t, n))
                total += n
                print(f"  {t:<32} {n:>7} orphan rows")

        if not total:
            print("  (no orphaned rows — nothing to do)")
            return 0

        print(f"\ntotal orphan rows: {total}")

        if not args.apply:
            print("\nDRY-RUN — nothing deleted. Re-run with --apply to remove them.")
            return 0

        # Single transaction; rollback on any error.
        try:
            for t, _ in affected:
                conn.execute(f'DELETE FROM "{t}" WHERE {orphan_where}')
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            print(f"\nERROR during delete — rolled back, no changes made: {e}")
            return 1

        print(f"\nDeleted {total} orphan rows across {len(affected)} tables. Committed.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
