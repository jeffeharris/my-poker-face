"""Generate poker/repositories/schema_baseline.py from the full migration chain.

Builds a fresh DB via _init_db + the full v1..v154 chain (the canonical head),
reads every user object's CREATE text from sqlite_master in creation order, and
emits a static BASELINE_STATEMENTS list. Each statement is made idempotent
(``IF NOT EXISTS``) and identifier-unquoted so it is safe to replay on existing
DBs and compares equal to the chain's output under the gate test.

Run inside a backend container with the worktree mounted at /app:
    python scripts/_gen_schema_baseline.py
"""

import os
import re
import sqlite3
import tempfile

from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager

# CREATE [UNIQUE] (TABLE|INDEX|TRIGGER|VIEW) [IF NOT EXISTS] ["]name["]
_HEAD = re.compile(
    r'^\s*CREATE\s+(?P<uniq>UNIQUE\s+)?(?P<kind>TABLE|INDEX|TRIGGER|VIEW)\s+'
    r'(?:IF\s+NOT\s+EXISTS\s+)?(?P<q>["\'`]?)(?P<name>\w+)(?P=q)',
    re.IGNORECASE | re.DOTALL,
)


def normalize(sql: str) -> str:
    """Inject IF NOT EXISTS and unquote the object name; leave the body intact."""
    m = _HEAD.match(sql)
    if not m:
        raise ValueError(f"Unrecognized CREATE statement:\n{sql[:200]}")
    uniq = (m.group("uniq") or "").upper()
    kind = m.group("kind").upper()
    name = m.group("name")
    head = f"CREATE {uniq}{kind} IF NOT EXISTS {name}"
    return head + sql[m.end() :]


d = tempfile.mkdtemp()
full = os.path.join(d, "full.db")
sm = SchemaManager(full)
sm._init_db()
sm._run_migrations()

with sqlite3.connect(full) as conn:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
        "ORDER BY rowid"
    ).fetchall()

statements = [normalize(sql) for _typ, _name, sql in rows]
tables = sum(1 for t, _, _ in rows if t == "table")
indexes = sum(1 for t, _, _ in rows if t == "index")
others = len(rows) - tables - indexes

out_path = "poker/repositories/schema_baseline.py"
with open(out_path, "w") as f:
    f.write(
        f'"""Canonical head schema (the v{SCHEMA_VERSION} baseline) — GENERATED, do not hand-edit.\n\n'
    )
    f.write("Regenerate with ``python scripts/_gen_schema_baseline.py`` (force-added in\n")
    f.write("scripts/). This is the squash baseline: ``_init_db`` replays these statements\n")
    f.write("instead of the v1..v154 ``_migrate_vN`` chain. Each statement is idempotent\n")
    f.write("(``IF NOT EXISTS``) so replaying on an existing DB is a no-op.\n\n")
    f.write("Source of truth: a fresh DB built via ``_init_db`` + the full migration chain.\n")
    f.write("The gate test ``tests/test_schema_consistency.py`` proves this equals the chain.\n")
    f.write('"""\n\n')
    f.write(f"BASELINE_VERSION = {SCHEMA_VERSION}\n\n")
    f.write(
        f"# {tables} tables, {indexes} indexes, {others} other — {len(statements)} statements total.\n"
    )
    f.write("BASELINE_STATEMENTS = [\n")
    for stmt in statements:
        # Use a triple-quoted raw-ish literal; statements contain no triple quotes.
        f.write(f'    """{stmt}""",\n')
    f.write("]\n")

print(f"SCHEMA_VERSION={SCHEMA_VERSION}")
print(
    f"wrote {out_path}: {len(statements)} statements ({tables} tables, {indexes} indexes, {others} other)"
)
