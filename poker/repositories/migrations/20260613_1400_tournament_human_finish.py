"""Denormalize the human's finishing position onto `tournaments` rows.

Sibling to the winner/field-size stamp: the Champions Roll shows your own finish
on the events you played ("you finished 4th"). `human_finish` is the human seat's
finishing position (1 = you won), stamped at the same `save()` chokepoint off the
collapsed field. NULL on events you didn't play (every autonomous/declined one —
the field ran without you) and on pre-stamp rows.

Additive, idempotent, forward-only. Backfilled lazily; a NULL finish simply omits
the "you finished" line.
"""

import sqlite3

DESCRIPTION = "Add tournaments.human_finish (Champions Roll — your own finish)"


def upgrade(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tournaments)")}
    if "human_finish" not in cols:
        conn.execute("ALTER TABLE tournaments ADD COLUMN human_finish INTEGER")
