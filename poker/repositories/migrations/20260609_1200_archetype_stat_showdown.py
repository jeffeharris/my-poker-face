"""Add showdown + per-street aggression columns to `archetype_stat_counts`.

Backlog #11 (ARCHETYPE_SHAPING_HANDOFF): the Archetype Review tool gains AFq
(folds in the postflop-aggression denominator), WTSD/W$SD (showdown reach + win
rate), and per-street AF (flop/turn/river aggression texture). These need new
sim-side counters; the live path sources the same family from
`player_decision_analysis` + `hand_history` and is retroactive on existing rows.

Twelve additive columns (postflop aggregate fold = sum of the three street folds,
so it is NOT stored separately):
  - saw_flop / showdowns / showdowns_won : WTSD/W$SD numerators+denominators
  - {flop,turn,river}_{agg,call,fold}    : per-street AF + AFq components

SQLite has no ``ADD COLUMN IF NOT EXISTS``, so each ALTER is wrapped in a
try/except for OperationalError (re-runs / fresh-schema bases are no-ops).
Forward-only, additive, idempotent.
"""

import sqlite3

DESCRIPTION = "Add showdown + per-street aggression columns to archetype_stat_counts"

_NEW_COLUMNS = (
    "saw_flop",
    "showdowns",
    "showdowns_won",
    "flop_agg",
    "flop_call",
    "flop_fold",
    "turn_agg",
    "turn_call",
    "turn_fold",
    "river_agg",
    "river_call",
    "river_fold",
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
