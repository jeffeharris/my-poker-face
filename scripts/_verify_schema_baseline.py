"""Verify the generated baseline reproduces the full-chain head schema.

Proves the squash procedure without touching _init_db:
  * chain_db   = _init_db() + full v1..vN chain   (today's head)
  * base_db    = replay BASELINE_STATEMENTS         (the squash baseline)
  * base+chain = replay BASELINE_STATEMENTS, then run the chain (must no-op)

All three must have identical normalized schema. The third catches any migration
whose guard fails to no-op against the head schema (the only real squash risk).

Run inside a backend container with the worktree mounted at /app:
    python scripts/_verify_schema_baseline.py
"""

import os
import re
import sqlite3
import sys
import tempfile

from poker.repositories.schema_baseline import BASELINE_STATEMENTS, BASELINE_VERSION
from poker.repositories.schema_manager import SchemaManager


def objects(db_path):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
    return {f"{t}:{n}": re.sub(r"\s+", " ", s).strip() for t, n, s in rows}


def diff(a_name, a, b_name, b):
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    shape = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    ok = not (only_a or only_b or shape)
    print(f"\n[{a_name} vs {b_name}] {'OK' if ok else 'MISMATCH'}")
    for k in only_a:
        print(f"  only in {a_name}: {k}")
    for k in only_b:
        print(f"  only in {b_name}: {k}")
    for k in shape:
        print(f"  shape differs: {k}\n    {a_name}: {a[k]}\n    {b_name}: {b[k]}")
    return ok


d = tempfile.mkdtemp()

chain = os.path.join(d, "chain.db")
sm = SchemaManager(chain)
sm._init_db()
sm._run_migrations()

base = os.path.join(d, "base.db")
with sqlite3.connect(base) as conn:
    for stmt in BASELINE_STATEMENTS:
        conn.execute(stmt)

base_idem = os.path.join(d, "base_idem.db")
with sqlite3.connect(base_idem) as conn:
    for stmt in BASELINE_STATEMENTS:
        conn.execute(stmt)
    for stmt in BASELINE_STATEMENTS:  # replay twice: IF NOT EXISTS must no-op
        conn.execute(stmt)

base_plus_chain = os.path.join(d, "base_plus_chain.db")
with sqlite3.connect(base_plus_chain) as conn:
    for stmt in BASELINE_STATEMENTS:
        conn.execute(stmt)
sm2 = SchemaManager(base_plus_chain)
sm2._run_migrations()  # version 0 -> chain runs; every step must no-op on head

print(f"BASELINE_VERSION = {BASELINE_VERSION}, statements = {len(BASELINE_STATEMENTS)}")
results = [
    diff("chain", objects(chain), "base", objects(base)),
    diff("base", objects(base), "base_idem(2x)", objects(base_idem)),
    diff("chain", objects(chain), "base+chain", objects(base_plus_chain)),
]
print("\n" + ("ALL GREEN — baseline reproduces chain head" if all(results) else "FAILURES ABOVE"))
sys.exit(0 if all(results) else 1)
