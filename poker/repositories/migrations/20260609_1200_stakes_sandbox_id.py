"""Add `sandbox_id` to `stakes` — pin a stake to its origination sandbox.

Root cause of the 2026-06-09 cross-sandbox chip mint: AI personas exist
per-sandbox, but the `stakes` table was global and `load_active_for_borrower`
filtered only on `(borrower_id, borrower_kind, status)`. Once a second human
joined (a second sandbox), a stake funded in sandbox A — crediting
`seat:ai:<A>:<borrower>` — could be loaded and settled while a world-tick
processed sandbox B, draining `seat:ai:<B>:<borrower>`, a seat the stake never
funded → the seat goes negative → chips mint. (The funding/settle ledger rows
were already sandbox-tagged; only the stake ROW being selected was unscoped.)

The fix stores the origination sandbox on the row so the active-stake lookup
can be scoped to it: settlement in sandbox B can no longer find a stake
originated in sandbox A. Legacy rows keep `sandbox_id` NULL and remain findable
from any sandbox (the scoped query matches `= ? OR IS NULL`), so pre-existing
active stakes still settle under the old behavior and drain out.

Additive, idempotent, forward-only. The `idx_stakes_active_borrower` partial
index backs the new scoped active-borrower read.
"""

import sqlite3

DESCRIPTION = "Add stakes.sandbox_id (pin stake settlement to origination sandbox)"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(stakes)")}
    if "sandbox_id" not in cols:
        conn.execute("ALTER TABLE stakes ADD COLUMN sandbox_id TEXT")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_stakes_active_borrower
            ON stakes(borrower_id, borrower_kind, sandbox_id)
            WHERE status = 'active'
        """
    )
