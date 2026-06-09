"""Composite index on player_decision_analysis(game_id, hand_number).

The Archetype Review live aggregator (`archetype_review_routes._aggregate` /
`_fetch_showdown_map`) links per-decision PDA rows to their parent hand on the
NATURAL key (game_id, hand_number) — the c-bet/WTSD reconstruction replays a
hand's decisions in order, and WTSD/W$SD join the hand-level showdown outcome.
PDA was indexed only on `game_id`, so that per-hand lookup scanned every
decision in the game. This composite index makes the parent-hand ->
child-decisions lookup an index seek.

`hand_history` already carries UNIQUE(game_id, hand_number) (the parent key);
this is the matching child-side index. The relationship stays a natural-key
link — deliberately NO surrogate FK: PDA rows are written per-decision *during*
the hand while `hand_history` is written at hand *end* (and some hands
legitimately have one without the other), so a hard FK would be fragile.
Completeness is instead an audited invariant (see experiments/pda_completeness_monitor.py).

Forward-only, idempotent.
"""

import sqlite3

DESCRIPTION = "Index player_decision_analysis(game_id, hand_number) for the hand<->decisions link"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_analysis_game_hand "
        "ON player_decision_analysis(game_id, hand_number)"
    )
