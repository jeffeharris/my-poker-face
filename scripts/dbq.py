#!/usr/bin/env python3
"""
Simple database query utility for quick exploration.

Usage:
    # From command line:
    python scripts/dbq.py "SELECT COUNT(*) FROM prompt_captures"
    python scripts/dbq.py "SELECT * FROM prompt_captures WHERE phase = '{phase}'" --phase PRE_FLOP
    python scripts/dbq.py tables  # List all tables
    python scripts/dbq.py schema prompt_captures  # Show table schema

    # From Python/Claude:
    from scripts.dbq import q, tables, schema
    q("SELECT COUNT(*) FROM prompt_captures")
    q("SELECT * FROM prompt_captures WHERE phase = ?", ("PRE_FLOP",))
    tables()
    schema("prompt_captures")
"""

import sqlite3
import sys
import os
from pathlib import Path
from typing import Any, Optional

# Find the database
DB_PATHS = [
    "data/poker_games.db",
    "../data/poker_games.db",
    "/home/jeffh/projects/my-poker-face-replay-experiments/data/poker_games.db",
]

def get_db_path() -> str:
    """Find the database file."""
    for path in DB_PATHS:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"Database not found. Tried: {DB_PATHS}")


def get_connection() -> sqlite3.Connection:
    """Get a read-only database connection."""
    db_path = get_db_path()
    conn = sqlite3.connect(f'file:{db_path}?immutable=1', uri=True)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    return conn


def q(query: str, params: tuple = (), limit: int = 20, fmt: Optional[dict] = None) -> list[dict]:
    """
    Execute a query and return results as list of dicts.

    Args:
        query: SQL query string (use ? for params or {key} for fmt)
        params: Tuple of parameters for ? placeholders
        limit: Max rows to return (default 20, use 0 for unlimited)
        fmt: Dict for string formatting (e.g., {"phase": "PRE_FLOP"})

    Returns:
        List of dicts with column names as keys

    Examples:
        q("SELECT COUNT(*) FROM prompt_captures")
        q("SELECT * FROM prompt_captures WHERE phase = ?", ("PRE_FLOP",))
        q("SELECT * FROM prompt_captures WHERE phase = '{phase}'", fmt={"phase": "PRE_FLOP"})
    """
    if fmt:
        query = query.format(**fmt)

    if limit and "LIMIT" not in query.upper():
        query = f"{query} LIMIT {limit}"

    conn = get_connection()
    try:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        # Convert to list of dicts
        return [dict(row) for row in rows]
    finally:
        conn.close()


def tables() -> list[str]:
    """List all tables in the database."""
    result = q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", limit=0)
    table_names = [r['name'] for r in result]
    print(f"Tables ({len(table_names)}):")
    for name in table_names:
        print(f"  - {name}")
    return table_names


def schema(table_name: str) -> list[dict]:
    """Show schema for a table."""
    result = q(f"PRAGMA table_info({table_name})", limit=0)
    print(f"Schema for '{table_name}':")
    for col in result:
        nullable = "" if col['notnull'] else " (nullable)"
        pk = " PRIMARY KEY" if col['pk'] else ""
        default = f" DEFAULT {col['dflt_value']}" if col['dflt_value'] else ""
        print(f"  {col['name']}: {col['type']}{pk}{nullable}{default}")
    return result


def count(table_name: str) -> int:
    """Quick count of rows in a table."""
    result = q(f"SELECT COUNT(*) as cnt FROM {table_name}")
    cnt = result[0]['cnt']
    print(f"{table_name}: {cnt} rows")
    return cnt


def sample(table_name: str, n: int = 5) -> list[dict]:
    """Get sample rows from a table."""
    result = q(f"SELECT * FROM {table_name}", limit=n)
    print(f"Sample from '{table_name}' ({len(result)} rows):")
    for row in result:
        print(f"  {dict(row)}")
    return result


def pprint(rows: list[dict], max_width: int = 60) -> None:
    """Pretty print query results."""
    if not rows:
        print("(no results)")
        return

    # Get column names
    cols = list(rows[0].keys())

    # Calculate column widths
    widths = {}
    for col in cols:
        widths[col] = min(max_width, max(len(col), max(len(str(r.get(col, ''))[:max_width]) for r in rows)))

    # Print header
    header = " | ".join(col.ljust(widths[col]) for col in cols)
    print(header)
    print("-" * len(header))

    # Print rows
    for row in rows:
        line = " | ".join(str(row.get(col, ''))[:widths[col]].ljust(widths[col]) for col in cols)
        print(line)


# CLI interface
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "tables":
        tables()
    elif cmd == "schema" and len(sys.argv) > 2:
        schema(sys.argv[2])
    elif cmd == "count" and len(sys.argv) > 2:
        count(sys.argv[2])
    elif cmd == "sample" and len(sys.argv) > 2:
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        sample(sys.argv[2], n)
    else:
        # Treat as SQL query
        query = cmd

        # Parse --key value args for formatting
        fmt = {}
        i = 2
        while i < len(sys.argv):
            if sys.argv[i].startswith("--"):
                key = sys.argv[i][2:]
                value = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
                fmt[key] = value
                i += 2
            else:
                i += 1

        result = q(query, fmt=fmt if fmt else None, limit=20)
        pprint(result)
