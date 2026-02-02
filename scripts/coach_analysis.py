#!/usr/bin/env python3
"""
Coach progression threshold analysis.

Queries production data to inform threshold tuning for skill advancement
and regression. Uses the existing player_skill_progress and
player_gate_progress tables.

Usage:
    # From command line:
    python scripts/coach_analysis.py                 # Full report
    python scripts/coach_analysis.py stuck            # Stuck-player analysis
    python scripts/coach_analysis.py thresholds       # Current thresholds vs observed data

    # From Python/Claude:
    from scripts.coach_analysis import report, stuck, thresholds
    report()        # Print full analysis
    stuck()         # Players with many opportunities but low state
    thresholds()    # Compare thresholds to observed advancement rates
"""

import sqlite3
import os
import sys
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

DB_PATHS = [
    "data/poker_games.db",
    "../data/poker_games.db",
]


def _get_db() -> str:
    for path in DB_PATHS:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"Database not found. Tried: {DB_PATHS}")


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(_get_db())


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def _overview(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM player_coach_profile").fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM player_skill_progress "
        "WHERE last_evaluated_at >= datetime('now', '-7 days')"
    ).fetchone()[0]

    levels = conn.execute(
        "SELECT self_reported_level, COUNT(*) "
        "FROM player_coach_profile GROUP BY self_reported_level"
    ).fetchall()

    gates = conn.execute(
        "SELECT gate, COUNT(*) FROM player_gate_progress "
        "WHERE unlocked = 1 GROUP BY gate ORDER BY gate"
    ).fetchall()

    return {
        'total_players': total,
        'active_7d': active,
        'by_level': levels,
        'gate_funnel': gates,
    }


def _skill_states(conn: sqlite3.Connection) -> List[Tuple]:
    return conn.execute(
        "SELECT skill_id, state, COUNT(*) as cnt, "
        "ROUND(AVG(CASE WHEN total_opportunities > 0 "
        "  THEN CAST(total_correct AS REAL) / total_opportunities ELSE 0 END), 3) as avg_acc, "
        "ROUND(AVG(total_opportunities), 1) as avg_opps "
        "FROM player_skill_progress "
        "GROUP BY skill_id, state ORDER BY skill_id, state"
    ).fetchall()


def _stuck_players(conn: sqlite3.Connection, min_opps: int = 20) -> List[Tuple]:
    """Players in introduced/practicing despite many opportunities."""
    return conn.execute(
        "SELECT skill_id, state, user_id, total_opportunities, total_correct, "
        "ROUND(CAST(total_correct AS REAL) / total_opportunities, 3) as accuracy "
        "FROM player_skill_progress "
        "WHERE state IN ('introduced', 'practicing') AND total_opportunities > ? "
        "ORDER BY total_opportunities DESC",
        (min_opps,),
    ).fetchall()


def _advancement_rates(conn: sqlite3.Connection) -> List[Tuple]:
    """For players who reached reliable/automatic, how many opps did it take?"""
    return conn.execute(
        "SELECT skill_id, state, COUNT(*) as cnt, "
        "ROUND(AVG(total_opportunities), 1) as avg_opps, "
        "MIN(total_opportunities) as min_opps, "
        "MAX(total_opportunities) as max_opps "
        "FROM player_skill_progress "
        "WHERE state IN ('reliable', 'automatic') "
        "GROUP BY skill_id, state ORDER BY skill_id, state"
    ).fetchall()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_table(headers: List[str], rows: List[Tuple]) -> None:
    if not rows:
        print("  (no data)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def report() -> None:
    """Print full analysis report."""
    conn = _conn()

    ov = _overview(conn)
    _print_header("Coach Progression Overview")
    print(f"  Total players: {ov['total_players']}")
    print(f"  Active (7d):   {ov['active_7d']}")
    print()
    _print_table(["Level", "Count"], ov['by_level'])
    print()
    _print_table(["Gate", "Unlocked"], ov['gate_funnel'])

    _print_header("Skill State Distribution")
    rows = _skill_states(conn)
    _print_table(["Skill", "State", "Players", "Avg Accuracy", "Avg Opps"], rows)

    _print_header("Advancement Rates (reliable/automatic)")
    rows = _advancement_rates(conn)
    _print_table(["Skill", "State", "Players", "Avg Opps", "Min", "Max"], rows)

    stuck()
    conn.close()


def stuck(min_opps: int = 20) -> None:
    """Show players who may be stuck."""
    conn = _conn()
    rows = _stuck_players(conn, min_opps)
    _print_header(f"Stuck Players (>{min_opps} opps, still introduced/practicing)")
    _print_table(["Skill", "State", "User", "Opps", "Correct", "Accuracy"], rows)
    conn.close()


def thresholds() -> None:
    """Compare current thresholds to observed data."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from flask_app.services.skill_definitions import ALL_SKILLS
    except ImportError:
        print("  Could not import skill definitions. Run from project root.")
        return

    conn = _conn()
    _print_header("Threshold vs Observed Accuracy")
    headers = ["Skill", "Advance @", "Regress @", "Min Opps", "Avg Accuracy", "Avg Opps"]
    rows = []
    for sid, skill in ALL_SKILLS.items():
        er = skill.evidence_rules
        # Get observed stats for players in 'practicing' state
        row = conn.execute(
            "SELECT ROUND(AVG(CASE WHEN total_opportunities > 0 "
            "  THEN CAST(total_correct AS REAL) / total_opportunities ELSE 0 END), 3), "
            "ROUND(AVG(total_opportunities), 1) "
            "FROM player_skill_progress "
            "WHERE skill_id = ? AND state = 'practicing'",
            (sid,),
        ).fetchone()
        avg_acc = row[0] if row and row[0] is not None else '-'
        avg_opps = row[1] if row and row[1] is not None else '-'
        rows.append((sid, er.advancement_threshold, er.regression_threshold,
                      er.min_opportunities, avg_acc, avg_opps))

    _print_table(headers, rows)
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if cmd == 'stuck':
        stuck()
    elif cmd == 'thresholds':
        thresholds()
    else:
        report()
