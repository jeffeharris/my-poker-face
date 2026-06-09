"""Create `archetype_stat_counts` — per-archetype behavioral tallies for the
background AI-vs-AI cash sim.

The lobby sim (`cash_mode/full_sim.py`) plays full hands with TieredBotControllers
but is LEAN by construction — it never wires the decision-analysis repo, so its
(perpetual) decision stream is discarded. This table is the lightweight,
*bounded* alternative: the `ArchetypeStatRecorder` accumulates counters in memory
and flushes them here as deltas, so the Archetype Review tool can read AI-only
sim behavior without bloating `player_decision_analysis` with unbounded
per-decision rows.

Sandbox-scoped, one row per (sandbox_id, archetype). Counts:
  - hands / pf_decisions: denominators
  - vpip / pfr / allin_hands: per-hand-instance booleans (rolled up at hand end)
  - vs_open(+agg) / vs_3bet(+agg/+fold): node-keyed opportunity counts
  - postflop_agg / postflop_call: aggression factor
Forward-only, additive, idempotent.
"""

import sqlite3

DESCRIPTION = "Create archetype_stat_counts (background-sim per-archetype behavioral tallies)"


def upgrade(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archetype_stat_counts (
            sandbox_id     TEXT NOT NULL,
            archetype      TEXT NOT NULL,
            hands          INTEGER NOT NULL DEFAULT 0,
            pf_decisions   INTEGER NOT NULL DEFAULT 0,
            vpip           INTEGER NOT NULL DEFAULT 0,
            pfr            INTEGER NOT NULL DEFAULT 0,
            vs_open        INTEGER NOT NULL DEFAULT 0,
            vs_open_agg    INTEGER NOT NULL DEFAULT 0,
            vs_3bet        INTEGER NOT NULL DEFAULT 0,
            vs_3bet_agg    INTEGER NOT NULL DEFAULT 0,
            vs_3bet_fold   INTEGER NOT NULL DEFAULT 0,
            postflop_agg   INTEGER NOT NULL DEFAULT 0,
            postflop_call  INTEGER NOT NULL DEFAULT 0,
            allin_hands    INTEGER NOT NULL DEFAULT 0,
            updated_at     TIMESTAMP,
            PRIMARY KEY (sandbox_id, archetype)
        )
        """
    )
