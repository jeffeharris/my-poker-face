"""Post-hoc polarization diagnostic over a persisted game database.

Reads `opponent_models` rows from the live game DB and reports each
(observer, opponent) pair's aggression polarization signal — the
delta between mean equity-when-raising and mean equity-when-calling
that Phase A populates at showdown.

Use this after running games (sim or live) to see which opponents
look polarized (CaseBot-style: raise with strong, call with marginal)
vs noisy (LAG-style: raise and call with similar equity distributions).
The signal feeds the Phase B rule gating once threshold values are
finalized; this script is the read-only diagnostic surface.

Usage:
    python3 scripts/polarization_report.py [--game-id GAME_ID]
                                           [--db /path/to/poker_games.db]
                                           [--min-samples N]
                                           [--polarized-only]

Without --game-id, reports across all games. With --min-samples N
(default 5), excludes pairs with fewer than N observations in each
of the betting/raising/calling buckets — small samples produce noisy
polarization numbers.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = "/app/data/poker_games.db"  # Docker path
LOCAL_DB = REPO_ROOT / "poker_games.db"  # local dev fallback


def _resolve_db_path(arg: Optional[str]) -> str:
    if arg:
        return arg
    # Prefer the local poker_games.db if it exists; else Docker path.
    if LOCAL_DB.exists():
        return str(LOCAL_DB)
    return DEFAULT_DB


def _load_pair_rows(
    db_path: str,
    game_id: Optional[str] = None,
) -> List[Dict]:
    """Return one dict per (game_id, observer, opponent) pair with
    equity stats unpacked from the row's tendencies_json blob."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if game_id:
            cursor = conn.execute(
                "SELECT game_id, observer_name, opponent_name, tendencies_json "
                "FROM opponent_models WHERE game_id = ?",
                (game_id,),
            )
        else:
            cursor = conn.execute(
                "SELECT game_id, observer_name, opponent_name, tendencies_json "
                "FROM opponent_models",
            )
        rows = []
        for row in cursor:
            blob = row['tendencies_json']
            if not blob:
                continue
            try:
                tendencies = json.loads(blob)
            except json.JSONDecodeError:
                continue
            rows.append({
                'game_id': row['game_id'],
                'observer': row['observer_name'],
                'opponent': row['opponent_name'],
                'eq_bet_mean': tendencies.get('equity_when_betting_postflop', 0.5),
                'eq_raise_mean': tendencies.get('equity_when_raising_postflop', 0.5),
                'eq_call_mean': tendencies.get('equity_when_calling_postflop', 0.5),
                'n_bet': tendencies.get('_equity_betting_count', 0),
                'n_raise': tendencies.get('_equity_raising_count', 0),
                'n_call': tendencies.get('_equity_calling_count', 0),
                'hands_observed': tendencies.get('hands_observed', 0),
            })
        return rows
    finally:
        conn.close()


def _polarization(row: Dict) -> float:
    return row['eq_raise_mean'] - row['eq_call_mean']


def _label(polarization: float, has_min_sample: bool) -> str:
    """Friendly label for the polarization value. Same thresholds as
    POLARIZATION_DETECTION.md spec."""
    if not has_min_sample:
        return 'insufficient_sample'
    if polarization > 0.25:
        return 'POLARIZED (value-caller)'
    if polarization < -0.05:
        return 'BLUFFER (raises wider)'
    return 'noisy / balanced'


def _format_row(row: Dict, min_sample: int) -> str:
    has_min = (
        row['n_raise'] >= min_sample
        and row['n_call'] >= min_sample
    )
    pol = _polarization(row)
    label = _label(pol, has_min)
    return (
        f"  {row['observer'][:18]:<18} → {row['opponent'][:18]:<18}  "
        f"raise={row['eq_raise_mean']:.2f} (n={row['n_raise']:>3})  "
        f"call={row['eq_call_mean']:.2f} (n={row['n_call']:>3})  "
        f"pol={pol:+.2f}  [{label}]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-id", default=None,
        help="Restrict report to a single game_id. Default: all games.",
    )
    parser.add_argument(
        "--db", default=None,
        help=f"Path to poker_games.db. Defaults to {LOCAL_DB} if present, else {DEFAULT_DB}.",
    )
    parser.add_argument(
        "--min-samples", type=int, default=5,
        help="Minimum observations per (raise OR call) bucket before "
             "labeling. Below this, the row is shown but tagged "
             "insufficient_sample. Default: 5.",
    )
    parser.add_argument(
        "--polarized-only", action="store_true",
        help="Only show pairs flagged as POLARIZED (signal > 0.25 with "
             "adequate sample). Useful for hunting CaseBot-style "
             "opponents in a pool.",
    )
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db)
    rows = _load_pair_rows(db_path, game_id=args.game_id)
    if not rows:
        scope = f"game_id={args.game_id}" if args.game_id else "all games"
        print(
            f"No opponent_models rows with tendencies_json found ({scope}, db={db_path})",
            file=sys.stderr,
        )
        return 1

    # Sort by polarization magnitude, most polarized first
    rows.sort(key=lambda r: abs(_polarization(r)), reverse=True)

    print(f"Polarization report — db={db_path}")
    print(f"Min samples for label: {args.min_samples}")
    if args.game_id:
        print(f"Game ID: {args.game_id}")
    print(f"Total (observer, opponent) pairs: {len(rows)}")
    print()
    print(f"  {'observer':<18}   {'opponent':<18}    "
          f"{'raise (mean, n)':<25}    {'call (mean, n)':<25}    "
          f"{'pol':>5}  label")
    print(f"  {'-' * 18}   {'-' * 18}    {'-' * 25}    {'-' * 25}    "
          f"{'-' * 5}  -----")

    shown = 0
    for row in rows:
        has_min = (
            row['n_raise'] >= args.min_samples
            and row['n_call'] >= args.min_samples
        )
        pol = _polarization(row)
        if args.polarized_only and not (has_min and pol > 0.25):
            continue
        print(_format_row(row, args.min_samples))
        shown += 1

    if args.polarized_only and shown == 0:
        print("  (no pairs above polarization threshold with sufficient samples)")

    print()
    print(f"Shown: {shown} / {len(rows)} pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
