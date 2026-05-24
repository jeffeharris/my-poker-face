"""One-shot backfill: merge legacy display-name observer_ids into their
canonical owner_id / personality_id slugs in cash_pair_stats.

Background
----------
Before the cold-load wiring fix in `flask_app/routes/game_routes.py`,
`set_relationship_repo` was called BEFORE `load_opponent_models`. The
OPM swap that followed dropped the relationship_repo on the new OPM
AND left the detector's `_name_to_id` reference pointing at the
orphaned dict — so the resolver fell back to the seat display name.
Result: rows like `("Jeff", "Oscar Wilde", -27397)` instead of
`("guest_jeff", "oscar_wilde", -27397)`.

This script identifies every (display_name, slug) pair we can map
confidently and merges their PnL + hands into the canonical row.
Collisions are SUMMED, not overwritten. Bilateral symmetry is
preserved: every (A, B) merge is paired with its (B, A) mirror.

Usage
-----
  python3 scripts/backfill_cash_pair_stats_identity.py            # preview
  python3 scripts/backfill_cash_pair_stats_identity.py --apply    # write

Preview mode prints the planned UPSERT and DELETE without touching
the DB. Apply mode wraps every write in a single transaction so the
merge is all-or-nothing.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Dict, List, Tuple


DB_PATH_DOCKER = "/app/data/poker_games.db"
DB_PATH_LOCAL = "poker_games.db"


def _resolve_db_path() -> str:
    """Pick the right DB path for the environment. Docker mounts the
    canonical DB at /app/data/poker_games.db; outside Docker the project
    root has poker_games.db.
    """
    if os.path.exists(DB_PATH_DOCKER):
        return DB_PATH_DOCKER
    return DB_PATH_LOCAL


def _load_personality_name_to_slug(conn: sqlite3.Connection) -> Dict[str, str]:
    """Build `{display_name: personality_id}` from personalities.

    Used to map the AI seat display names (e.g. "Oscar Wilde") back to
    their stable personality_id slugs (e.g. "oscar_wilde"). Falls back
    to the bare slug if no row exists.
    """
    rows = conn.execute(
        "SELECT personality_id, name FROM personalities WHERE name IS NOT NULL"
    ).fetchall()
    return {row[1]: row[0] for row in rows}


def _load_human_name_to_owner(conn: sqlite3.Connection) -> Dict[str, str]:
    """Build `{owner_name: owner_id}` from the games table.

    Human seats write `cash_pair_stats.observer_id` as the seat display
    name (e.g. "Jeff"); the canonical key is the owner_id (e.g.
    "guest_jeff"). For each owner_id, take the most-recent non-empty
    `owner_name` as the legacy display label.
    """
    rows = conn.execute(
        """
        SELECT owner_id, owner_name
        FROM games
        WHERE owner_id IS NOT NULL
          AND owner_name IS NOT NULL
          AND owner_name != ''
          AND (owner_id, created_at) IN (
              SELECT owner_id, MAX(created_at)
              FROM games
              WHERE owner_id IS NOT NULL
                AND owner_name IS NOT NULL
                AND owner_name != ''
              GROUP BY owner_id
          )
        """
    ).fetchall()
    return {row[1]: row[0] for row in rows}


def _build_id_mapping(conn: sqlite3.Connection) -> Dict[str, str]:
    """Combine personality + human display-name mappings.

    Returns `{legacy_id: canonical_id}` for every legacy row we can
    confidently remap. Personality slugs win over human owner_ids if a
    name collides (no current collisions in production, but defensive).
    """
    mapping: Dict[str, str] = {}
    mapping.update(_load_human_name_to_owner(conn))
    mapping.update(_load_personality_name_to_slug(conn))
    return mapping


def _collect_merges(
    conn: sqlite3.Connection, id_map: Dict[str, str],
) -> List[Tuple[str, str, str, str, str, int, int]]:
    """Return the merge plan as a list of tuples.

    Each tuple: (old_observer, old_opponent, new_observer, new_opponent,
                 sandbox_id, cumulative_pnl, hands_played_cash).
    Only rows where at least one side maps to a canonical id are
    included. Rows where neither side needs remapping are left alone.
    """
    rows = conn.execute(
        """
        SELECT observer_id, opponent_id, sandbox_id,
               cumulative_pnl, hands_played_cash
        FROM cash_pair_stats
        """
    ).fetchall()
    plan = []
    for obs, opp, sandbox, pnl, hands in rows:
        new_obs = id_map.get(obs, obs)
        new_opp = id_map.get(opp, opp)
        if new_obs == obs and new_opp == opp:
            continue
        plan.append((obs, opp, new_obs, new_opp, sandbox, pnl, hands))
    return plan


def _project_post_merge_totals(
    conn: sqlite3.Connection,
    plan: List[Tuple[str, str, str, str, str, int, int]],
) -> Dict[Tuple[str, str, str], Tuple[int, int]]:
    """Compute what each canonical row will look like after merge.

    Maps `(sandbox, observer, opponent) → (final_pnl, final_hands)`.
    For each target row, starts from the existing row (if any) and
    layers every legacy row that maps into it.
    """
    projected: Dict[Tuple[str, str, str], Tuple[int, int]] = {}
    target_keys = {
        (sandbox, new_obs, new_opp)
        for _, _, new_obs, new_opp, sandbox, _, _ in plan
    }
    for sandbox, new_obs, new_opp in target_keys:
        existing = conn.execute(
            """
            SELECT cumulative_pnl, hands_played_cash
            FROM cash_pair_stats
            WHERE sandbox_id = ? AND observer_id = ? AND opponent_id = ?
            """,
            (sandbox, new_obs, new_opp),
        ).fetchone()
        if existing:
            projected[(sandbox, new_obs, new_opp)] = (
                int(existing[0]), int(existing[1]),
            )
        else:
            projected[(sandbox, new_obs, new_opp)] = (0, 0)
    for _, _, new_obs, new_opp, sandbox, pnl, hands in plan:
        key = (sandbox, new_obs, new_opp)
        cur_pnl, cur_hands = projected[key]
        projected[key] = (cur_pnl + pnl, cur_hands + hands)
    return projected


def _print_plan(
    plan: List[Tuple[str, str, str, str, str, int, int]],
    projected: Dict[Tuple[str, str, str], Tuple[int, int]],
) -> None:
    """Pretty-print the merge plan for human review."""
    if not plan:
        print("No legacy rows to merge — nothing to do.")
        return
    print(f"Legacy rows to merge: {len(plan)}\n")
    print(f"{'old_observer':<15} {'old_opponent':<15} → "
          f"{'new_observer':<15} {'new_opponent':<15} "
          f"{'pnl':>10} {'hands':>6}")
    print("-" * 100)
    for obs, opp, new_obs, new_opp, _sandbox, pnl, hands in plan:
        print(f"{obs:<15} {opp:<15} → {new_obs:<15} {new_opp:<15} "
              f"{pnl:>10} {hands:>6}")
    print()
    print(f"Canonical rows after merge: {len(projected)}\n")
    print(f"{'sandbox':<38} {'observer':<15} {'opponent':<15} "
          f"{'pnl':>10} {'hands':>6}")
    print("-" * 100)
    for (sandbox, obs, opp), (pnl, hands) in sorted(projected.items()):
        sandbox_disp = (sandbox or '<none>')[:36]
        print(f"{sandbox_disp:<38} {obs:<15} {opp:<15} "
              f"{pnl:>10} {hands:>6}")


def _apply_merge(
    conn: sqlite3.Connection,
    plan: List[Tuple[str, str, str, str, str, int, int]],
    projected: Dict[Tuple[str, str, str], Tuple[int, int]],
) -> None:
    """Execute the merge in a single transaction.

    Order: upsert every target row to its projected total, then delete
    every legacy source row. Doing upserts first means a partial failure
    leaves no orphaned legacy rows lying around (we'd still see the
    "merged" totals on the canonical rows even if cleanup didn't run).
    """
    with conn:
        for (sandbox, obs, opp), (pnl, hands) in projected.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO cash_pair_stats
                    (sandbox_id, observer_id, opponent_id,
                     cumulative_pnl, hands_played_cash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sandbox, obs, opp, pnl, hands),
            )
        for obs, opp, _new_obs, _new_opp, sandbox, _pnl, _hands in plan:
            conn.execute(
                """
                DELETE FROM cash_pair_stats
                WHERE sandbox_id = ? AND observer_id = ? AND opponent_id = ?
                """,
                (sandbox, obs, opp),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write the merged rows. Without this flag, "
             "the script prints the plan and exits.",
    )
    parser.add_argument(
        "--db", default=None,
        help="Override the SQLite path. Defaults to the Docker mount "
             "if present, else ./poker_games.db.",
    )
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    print(f"DB: {db_path}")
    if not os.path.exists(db_path):
        print(f"  ✗ Not found", file=sys.stderr)
        return 2

    with sqlite3.connect(db_path) as conn:
        id_map = _build_id_mapping(conn)
        print(f"Display-name → canonical-id mappings loaded: {len(id_map)}\n")
        plan = _collect_merges(conn, id_map)
        projected = _project_post_merge_totals(conn, plan)
        _print_plan(plan, projected)

        if not plan:
            return 0
        if not args.apply:
            print("\n(preview only — pass --apply to write)")
            return 0
        print("\nApplying...")
        _apply_merge(conn, plan, projected)
        print(f"✓ Merged {len(plan)} legacy rows into "
              f"{len(projected)} canonical rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
