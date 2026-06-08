"""Generate poker/repositories/schema_baseline.py from the full migration chain.

Builds the canonical head (baseline DDL + a forced full replay of the v1..vN legacy
chain, which adds both any chain-only DDL and the SEED rows the chain inserts), then
emits:
  * BASELINE_STATEMENTS — every CREATE, made idempotent (IF NOT EXISTS) and
    identifier-unquoted, so replay on an existing DB is a no-op.
  * BASELINE_SEED — the rows the chain seeds (groups/permissions/enabled_models/
    prompt_presets/...), as INSERT-OR-IGNORE data, so a fresh baseline DB is
    functionally identical to a chain-built one (DDL alone would miss this).

Run inside a backend container with the worktree mounted at /app:
    python scripts/_gen_schema_baseline.py
"""
import os
import re
import sqlite3
import tempfile

from poker.repositories.legacy_migrations import LegacyMigrations
from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager

# CREATE [UNIQUE] (TABLE|INDEX|TRIGGER|VIEW) [IF NOT EXISTS] ["]name["]
_HEAD = re.compile(
    r'^\s*CREATE\s+(?P<uniq>UNIQUE\s+)?(?P<kind>TABLE|INDEX|TRIGGER|VIEW)\s+'
    r'(?:IF\s+NOT\s+EXISTS\s+)?(?P<q>["\'`]?)(?P<name>\w+)(?P=q)',
    re.IGNORECASE | re.DOTALL,
)

# Tables whose rows are bookkeeping, NOT seed data to replay on a fresh DB.
_SEED_EXCLUDE = {"schema_version", "applied_migrations"}


def normalize(sql: str) -> str:
    m = _HEAD.match(sql)
    if not m:
        raise ValueError(f"Unrecognized CREATE statement:\n{sql[:200]}")
    uniq = (m.group("uniq") or "").upper()
    kind = m.group("kind").upper()
    head = f"CREATE {uniq}{kind} IF NOT EXISTS {m.group('name')}"
    return head + sql[m.end():]


d = tempfile.mkdtemp()
full = os.path.join(d, "full.db")
sm = SchemaManager(full)
# Bootstrap the head DDL from the CURRENT baseline statements (not _init_db, which
# would import BASELINE_SEED — possibly absent while regenerating), then force a full
# chain replay to add the seed rows (and any chain-only DDL).
from poker.repositories.schema_baseline import BASELINE_STATEMENTS as _BOOTSTRAP  # noqa: E402

with sqlite3.connect(full) as conn:
    for _stmt in _BOOTSTRAP:
        conn.execute(_stmt)
    conn.execute("DELETE FROM schema_version")  # let the chain replay in full
LegacyMigrations().run(sm._get_connection, 0, SCHEMA_VERSION)  # chain → seed rows (+ any chain DDL)

with sqlite3.connect(full) as conn:
    ddl_rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY rowid"
    ).fetchall()
    statements = [normalize(sql) for _t, _n, sql in ddl_rows]

    # Seed rows: every non-empty user table (in creation order, so FK-referenced
    # tables seed first), minus bookkeeping tables.
    table_names = [n for t, n, _ in ddl_rows if t == "table" and n not in _SEED_EXCLUDE]
    seed = []
    for name in table_names:
        cur = conn.execute(f'SELECT * FROM "{name}"')
        rows = cur.fetchall()
        if not rows:
            continue
        columns = [c[0] for c in cur.description]
        seed.append((name, columns, [list(r) for r in rows]))

tables = sum(1 for t, _, _ in ddl_rows if t == "table")
indexes = sum(1 for t, _, _ in ddl_rows if t == "index")

out_path = "poker/repositories/schema_baseline.py"
with open(out_path, "w") as f:
    f.write(
        f'"""Canonical head schema (the v{SCHEMA_VERSION} baseline) — GENERATED, do not hand-edit.\n\n'
    )
    f.write("Regenerate with ``python scripts/_gen_schema_baseline.py`` (force-added in\n")
    f.write("scripts/). ``_init_db`` replays these statements + seed rows instead of the\n")
    f.write("v1..vN ``_migrate_vN`` chain. Statements are idempotent (``IF NOT EXISTS``)\n")
    f.write("and seed rows use INSERT OR IGNORE, so replay on an existing DB is a no-op.\n\n")
    f.write("Proven equivalent to the chain by tests/test_schema_consistency.py.\n")
    f.write('"""\n\n')
    f.write(f"BASELINE_VERSION = {SCHEMA_VERSION}\n\n")
    f.write(f"# {tables} tables, {indexes} indexes — {len(statements)} statements.\n")
    f.write("BASELINE_STATEMENTS = [\n")
    for stmt in statements:
        f.write(f'    """{stmt}""",\n')
    f.write("]\n\n")
    f.write(f"# Seed rows the legacy chain inserts ({len(seed)} tables); without these a\n")
    f.write("# fresh DDL-only DB would lack default groups/permissions/enabled models/etc.\n")
    f.write("BASELINE_SEED = [\n")
    for name, columns, rows in seed:
        f.write(f"    {{\n        \"table\": {name!r},\n        \"columns\": {columns!r},\n")
        f.write('        "rows": [\n')
        for r in rows:
            f.write(f"            {r!r},\n")
        f.write("        ],\n    },\n")
    f.write("]\n")

print(f"SCHEMA_VERSION={SCHEMA_VERSION}")
print(
    f"wrote {out_path}: {len(statements)} statements, {len(seed)} seed tables "
    f"({sum(len(r) for _, _, r in seed)} rows)"
)
