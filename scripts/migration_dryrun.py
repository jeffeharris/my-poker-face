#!/usr/bin/env python3
"""
migration_dryrun.py — exercise the full schema-migration chain against a COPY
of a real (e.g. production) database, and report exactly what it does.

Run this BEFORE deploying a branch that bumps SCHEMA_VERSION. Prod auto-runs
migrations on boot; this lets you see the result on real data first — does it
land at the target version, stay integrity-clean, and which tables (if any)
lose rows to the destructive DROP/DELETE migrations in the chain.

It NEVER touches the file you point it at: it copies to a scratch path and
migrates the copy. It also refuses to run against the live dev/prod DB names.

Usage (inside the backend container):
    docker compose exec -T backend python scripts/migration_dryrun.py /app/data/poker_games.prodbackup_<ts>.db

Exit code 0 = PASS (reached target version, integrity ok). Non-zero = FAIL.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time

# Belt-and-suspenders: never let the test-only template fast-path fire here.
# (It only seeds EMPTY DBs, and our copy is non-empty — but be explicit.)
os.environ.pop("POKER_TEST_SCHEMA_TEMPLATE", None)

from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager  # noqa: E402

PROTECTED = {"poker_games.db"}  # never operate on the live DB itself


def table_counts(db_path: str) -> dict[str, int]:
    """Row count per user table (sorted), so we can diff before/after."""
    conn = sqlite3.connect(db_path)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        out = {}
        for n in names:
            try:
                out[n] = conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            except sqlite3.Error as e:
                out[n] = f"ERR: {e}"
        return out
    finally:
        conn.close()


def scalar(db_path: str, sql: str):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def fk_violations(db_path: str) -> set[tuple]:
    """Set of foreign-key-check rows (table, rowid, referred_table, fk_id).

    SQLite enforces FKs only when PRAGMA foreign_keys=ON, which it is not by
    default — so real DBs commonly carry benign legacy orphans. We compare this
    set before vs after migration and only flag violations the migration *adds*.
    """
    conn = sqlite3.connect(db_path)
    try:
        return {tuple(r) for r in conn.execute("PRAGMA foreign_key_check")}
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    if not os.path.isfile(src):
        print(f"FAIL: source DB not found: {src}")
        return 2
    if os.path.basename(src) in PROTECTED:
        print(f"FAIL: refusing to migrate the live DB ({src}). Point me at a backup copy.")
        return 2

    scratch = f"/tmp/migration_dryrun_{int(time.time())}.db"
    # Copy ONLY the main file. We deliberately drop any -wal/-shm sidecars so the
    # copy is a clean, checkpointed standalone DB (the backup API already gives
    # us that; this guards against being handed a raw file+WAL pair).
    shutil.copyfile(src, scratch)
    for sidecar in (src + "-wal", src + "-shm"):
        if os.path.exists(sidecar):
            print(f"NOTE: ignoring sidecar {os.path.basename(sidecar)} (using checkpointed main file only)")

    print("=" * 70)
    print(" SCHEMA MIGRATION DRY-RUN")
    print(f"   source (untouched): {src}")
    print(f"   scratch copy:       {scratch}")
    print(f"   target version:     {SCHEMA_VERSION}")
    print("=" * 70)

    # ---- PRE ----
    start_ver = scalar(scratch, "SELECT MAX(version) FROM schema_version")
    pre_integrity = scalar(scratch, "PRAGMA integrity_check")
    pre_fk = fk_violations(scratch)
    pre = table_counts(scratch)
    print(f"\n[PRE]  schema_version = {start_ver}")
    print(f"[PRE]  integrity_check = {pre_integrity}")
    print(f"[PRE]  pre-existing FK orphans = {len(pre_fk)} (legacy data, not a migration concern)")
    print(f"[PRE]  tables = {len(pre)}, total rows = {sum(v for v in pre.values() if isinstance(v, int)):,}")
    if pre_integrity != "ok":
        print("\nFAIL: source copy is already corrupt before migrating. Stop.")
        return 1

    # ---- MIGRATE ----
    print(f"\n[RUN]  SchemaManager.ensure_schema()  ({start_ver} → {SCHEMA_VERSION}) …")
    t0 = time.time()
    try:
        SchemaManager(scratch).ensure_schema()
    except Exception as e:
        print(f"\nFAIL: migration raised {type(e).__name__}: {e}")
        print(f"      (scratch copy left at {scratch} for inspection)")
        return 1
    dt = time.time() - t0
    print(f"[RUN]  completed in {dt:.1f}s")

    # ---- POST ----
    end_ver = scalar(scratch, "SELECT MAX(version) FROM schema_version")
    post_integrity = scalar(scratch, "PRAGMA integrity_check")
    post_fk = fk_violations(scratch)
    new_fk = post_fk - pre_fk  # only violations the migration itself introduced
    post = table_counts(scratch)
    print(f"\n[POST] schema_version = {end_ver}")
    print(f"[POST] integrity_check = {post_integrity}")
    print(f"[POST] FK orphans = {len(post_fk)} total, {len(new_fk)} NEW (introduced by migration)")
    if new_fk:
        for row in sorted(new_fk)[:10]:
            print(f"         NEW FK violation: {row}")
    print(f"[POST] tables = {len(post)}, total rows = {sum(v for v in post.values() if isinstance(v, int)):,}")

    # ---- DIFF ----
    dropped = sorted(set(pre) - set(post))
    added = sorted(set(post) - set(pre))
    emptied = [
        t for t in sorted(set(pre) & set(post))
        if isinstance(pre[t], int) and isinstance(post[t], int) and pre[t] > 0 and post[t] == 0
    ]
    shrunk = [
        t for t in sorted(set(pre) & set(post))
        if isinstance(pre[t], int) and isinstance(post[t], int) and post[t] < pre[t] and post[t] > 0
    ]

    print("\n" + "-" * 70)
    print(" DATA IMPACT (review these against your expectations)")
    print("-" * 70)
    if dropped:
        print(f" DROPPED tables ({len(dropped)}): " + ", ".join(dropped))
    if emptied:
        print(" EMPTIED tables (had rows → now 0):")
        for t in emptied:
            print(f"   - {t}: {pre[t]:,} → 0")
    if shrunk:
        print(" SHRUNK tables (lost some rows):")
        for t in shrunk:
            print(f"   - {t}: {pre[t]:,} → {post[t]:,}")
    if added:
        print(f" NEW tables ({len(added)}): " + ", ".join(added))
    if not (dropped or emptied or shrunk):
        print(" No tables dropped, emptied, or shrunk. (New tables, if any, listed above.)")
    print("-" * 70)
    print(" NOTE: emptied/dropped tables are EXPECTED for some migrations in this")
    print("       chain (api_usage, prompt_captures, opponent_models, etc. are")
    print("       drop-and-rebuild). Confirm none of them hold data you care about.")

    # ---- VERDICT ----
    # Pre-existing FK orphans are ignored (legacy data); only NEW ones fail.
    ok = (end_ver == SCHEMA_VERSION) and (post_integrity == "ok") and (not new_fk)
    print("\n" + "=" * 70)
    if ok:
        extra = f" ({len(pre_fk)} pre-existing FK orphans left untouched)" if pre_fk else ""
        print(f" PASS — reached v{end_ver}, integrity ok, no NEW FK violations.{extra}")
    else:
        print(" FAIL —"
              + ("" if end_ver == SCHEMA_VERSION else f" version {end_ver}!={SCHEMA_VERSION};")
              + ("" if post_integrity == "ok" else f" integrity={post_integrity};")
              + ("" if not new_fk else f" {len(new_fk)} NEW foreign-key violation(s) introduced;"))
    print(f" scratch copy: {scratch}  (delete when done)")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
