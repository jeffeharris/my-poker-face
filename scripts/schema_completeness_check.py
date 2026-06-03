#!/usr/bin/env python3
"""Schema-completeness gate — does a real DB match the canonical schema?

A DB stamped `schema_version = N` does NOT prove its schema is complete: a
migration renumbered to a version BELOW a DB's current max never runs on that DB
(the migration walk only applies versions > current). That's exactly how the dev
DB reached v148 while missing the v139 `prestige_snapshots.entity_kind` column +
the v139/v140 indexes — the renown AI fan-out then failed silently.

This tool builds a FRESH canonical DB via `SchemaManager.ensure_schema()` (which
runs the full 1→SCHEMA_VERSION walk and is complete by construction) and diffs a
target DB against it: missing/extra tables, missing/extra columns per table, and
missing/extra indexes. MISSING items (present in canonical, absent in target) are
the gate — they fail the check. EXTRA items (legacy tables/columns the canonical
build doesn't have — expected on an old prod DB) are reported but don't fail.

Use it as the post-migration assertion in the prod dry-run (see
docs/plans/PROD_MERGE_PLAN.md): migrate the prod copy, then run this against it;
require zero MISSING before cutover. Exit 0 = complete, 1 = missing items, 2 = error.

    docker compose exec backend python scripts/schema_completeness_check.py --db /app/data/poker_games.db
    docker compose exec backend python scripts/schema_completeness_check.py --db <prod-copy> --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _schema(db_path: str):
    """{table: set(columns)}, set(index_names) for a DB. Excludes sqlite internals."""
    conn = sqlite3.connect(db_path)
    try:
        tables = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ):
            tables[name] = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        return tables, idx
    finally:
        conn.close()


def _canonical_schema():
    """Build a fresh, fully-migrated DB and return its schema (the source of truth)."""
    from poker.repositories.schema_manager import SchemaManager

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        SchemaManager(path).ensure_schema()
        return _schema(path)
    finally:
        os.unlink(path)


def diff(target_db: str) -> dict:
    """Diff a target DB against the canonical fresh build. Returns a report dict;
    `missing_*` keys are the gate (non-empty → incomplete)."""
    ctab, cidx = _canonical_schema()
    ttab, tidx = _schema(target_db)

    missing_tables = sorted(set(ctab) - set(ttab))
    extra_tables = sorted(set(ttab) - set(ctab))
    missing_cols, extra_cols = {}, {}
    for t in sorted(set(ctab) & set(ttab)):
        miss = sorted(ctab[t] - ttab[t])
        extra = sorted(ttab[t] - ctab[t])
        if miss:
            missing_cols[t] = miss
        if extra:
            extra_cols[t] = extra
    return {
        "missing_tables": missing_tables,
        "missing_columns": missing_cols,
        "missing_indexes": sorted(cidx - tidx),
        "extra_tables": extra_tables,
        "extra_columns": extra_cols,
        "extra_indexes": sorted(tidx - cidx),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="DB to check against the canonical schema")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}")
        return 2

    report = diff(args.db)
    missing_count = (
        len(report["missing_tables"])
        + sum(len(v) for v in report["missing_columns"].values())
        + len(report["missing_indexes"])
    )

    if args.json:
        print(json.dumps({**report, "missing_count": missing_count}, indent=2))
    else:
        print(f"Schema-completeness check: {args.db}")
        if report["missing_tables"]:
            print(f"  MISSING tables: {report['missing_tables']}")
        for t, cols in report["missing_columns"].items():
            print(f"  MISSING columns  {t}: {cols}")
        if report["missing_indexes"]:
            print(f"  MISSING indexes: {report['missing_indexes']}")
        if missing_count == 0:
            print("  ✓ complete — no missing tables/columns/indexes vs canonical")
        # Extras are informational (legacy prod tables the canonical build lacks).
        if report["extra_tables"]:
            print(f"  (extra/legacy tables, not a failure: {report['extra_tables']})")
        for t, cols in report["extra_columns"].items():
            print(f"  (extra/legacy columns {t}: {cols})")
        if report["extra_indexes"]:
            print(f"  (extra/legacy indexes: {report['extra_indexes']})")

    return 1 if missing_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
