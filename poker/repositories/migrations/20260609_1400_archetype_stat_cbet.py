"""Add c-bet + fold-to-c-bet columns to `archetype_stat_counts`.

Backlog #6 (ARCHETYPE_SHAPING_HANDOFF): the Archetype Review tool gains C-bet %
(flop continuation bets by the preflop aggressor) and Fold-to-C-bet % (folds when
facing a flop c-bet). These need new sim-side counters; the live path reconstructs
the same family best-effort from `player_decision_analysis` rows.

Four additive columns:
  - cbet_opportunity : times the preflop aggressor saw the flop first-in (un-bet)
  - cbet_made        : of those, a flop bet/raise was made (the c-bet taken)
  - cbet_faced       : times a player faced a flop c-bet
  - fold_to_cbet     : of those, the player folded

SQLite has no ``ADD COLUMN IF NOT EXISTS``, so each ALTER is wrapped in a
try/except for OperationalError (re-runs / fresh-schema bases are no-ops).
Forward-only, additive, idempotent.
"""

import sqlite3

DESCRIPTION = "Add c-bet + fold-to-c-bet columns to archetype_stat_counts"

_NEW_COLUMNS = (
    "cbet_opportunity",
    "cbet_made",
    "cbet_faced",
    "fold_to_cbet",
)


def upgrade(conn: sqlite3.Connection) -> None:
    for col in _NEW_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE archetype_stat_counts ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            # Column already exists (re-run or baseline already carries it).
            pass
