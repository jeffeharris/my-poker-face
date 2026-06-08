"""Dump the _init_db-vs-full-chain schema diff (the Phase 1 reconcile worklist).

Run inside a backend container with the worktree mounted at /app:
    python scripts/_schema_diff_tmp.py
"""

import os
import re
import sqlite3
import tempfile

from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager


def objects(db_path):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
    return {f"{t}:{n}": re.sub(r"\s+", " ", s).strip() for t, n, s in rows}


d = tempfile.mkdtemp()
init_only = os.path.join(d, "init.db")
full = os.path.join(d, "full.db")

SchemaManager(init_only)._init_db()

sm = SchemaManager(full)
sm._init_db()
sm._run_migrations()

a = objects(init_only)  # _init_db alone
b = objects(full)  # _init_db + chain (head)

print(f"SCHEMA_VERSION = {SCHEMA_VERSION}")
print(f"init_only objects: {len(a)}   full objects: {len(b)}\n")

missing = sorted(set(b) - set(a))  # chain creates, init_db lacks
extra = sorted(set(a) - set(b))  # init_db creates, chain lacks (unexpected)
shape = sorted(k for k in set(a) & set(b) if a[k] != b[k])  # both have, different SQL

print(f"== MISSING from _init_db ({len(missing)}) ==")
for k in missing:
    print(f"  {k}")

print(f"\n== EXTRA in _init_db ({len(extra)}) ==")
for k in extra:
    print(f"  {k}")

print(f"\n== SHAPE differs ({len(shape)}) ==")
for k in shape:
    print(f"  {k}")
    print(f"    init: {a[k]}")
    print(f"    full: {b[k]}")
