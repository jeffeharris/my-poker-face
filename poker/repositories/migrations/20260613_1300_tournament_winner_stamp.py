"""Denormalize the champion + field size onto `tournaments` rows.

The circuit crowns a Main Event winner every cycle — including the autonomous
events the player declined/expired (the "world runs without you" theme). The
Champions Roll renders that history; stamping `winner_pid` + `field_size` lets
the roll query read the result without deserializing each session's
`session_json` blob per row.

Only the durable session FACTS are stored: the winner's display NAME is resolved
on read via the canonical resolver (the repo layer has no personality access),
so the roll always shows the persona's current name and we avoid coupling the
repo to name resolution.

Additive, idempotent, forward-only. Backfilled lazily — existing completed rows
keep NULLs until re-saved; the roll resolves a NULL winner as unknown and omits a
NULL field size. Main Events are short-lived, so live history fills in within a
cycle.
"""

import sqlite3

DESCRIPTION = "Add tournaments.winner_pid + field_size (Champions Roll denormalization)"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tournaments)")}
    if "winner_pid" not in cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN winner_pid TEXT")
    if "field_size" not in cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN field_size INTEGER")
