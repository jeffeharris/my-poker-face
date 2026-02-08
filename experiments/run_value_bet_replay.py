#!/usr/bin/env python3
"""Replay 80%+ equity checks with gpt-5 and value-bet guidance.

Queries exp 86 (true-lean-with-coaching) for postflop decisions where the
model checked with 80%+ equity, then replays 20 of them across a 2x2 matrix:
  model (nano vs gpt-5) × guidance (baseline vs value-bet coaching)

80 total replays. Run inside Docker:
    docker compose exec backend python -m experiments.run_value_bet_replay
"""

import logging
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from poker.repositories import create_repos
from experiments.run_replay_experiment import ReplayExperimentRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SOURCE_EXPERIMENT_ID = 86
SOURCE_VARIANT = "true-lean-with-coaching"
CAPTURE_LIMIT = 20
DB_PATH = "data/poker_games.db"

VALUE_BET_GUIDANCE = (
    "VALUE BETTING: You have a strong hand. Checking lets opponents see free "
    "cards and costs you money.\n"
    "With 60%+ equity, you should BET or RAISE to extract value. Bet 50-75% "
    "of the pot.\n"
    "Strong hands that check the river are wasted opportunities."
)

VARIANTS = [
    {
        "label": "nano-baseline",
        "model": "gpt-5-nano",
        "provider": "openai",
    },
    {
        "label": "nano-value-bet",
        "model": "gpt-5-nano",
        "provider": "openai",
        "guidance_injection": VALUE_BET_GUIDANCE,
    },
    {
        "label": "gpt5-baseline",
        "model": "gpt-5",
        "provider": "openai",
    },
    {
        "label": "gpt5-value-bet",
        "model": "gpt-5",
        "provider": "openai",
        "guidance_injection": VALUE_BET_GUIDANCE,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def query_capture_ids(db_path: str) -> list[int]:
    """Find 80%+ equity postflop checks from the source experiment."""
    sql = """
        SELECT pc.id
        FROM prompt_captures pc
        JOIN player_decision_analysis pda ON pda.capture_id = pc.id
        JOIN experiment_games eg ON eg.game_id = pc.game_id
        WHERE eg.experiment_id = ?
          AND eg.variant = ?
          AND pda.phase != 'PRE_FLOP'
          AND pda.equity >= 0.80
          AND pda.action_taken = 'check'
        ORDER BY pda.equity DESC
        LIMIT ?
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql, (SOURCE_EXPERIMENT_ID, SOURCE_VARIANT, CAPTURE_LIMIT)).fetchall()
    return [row[0] for row in rows]


def print_summary(repo, experiment_id: int) -> None:
    """Query and print a summary table of results."""
    summary = repo.get_replay_results_summary(experiment_id)

    print("\n" + "=" * 72)
    print("REPLAY EXPERIMENT RESULTS")
    print("=" * 72)

    # Action distribution per variant
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT variant, new_action, COUNT(*) as cnt
            FROM replay_results
            WHERE experiment_id = ?
            GROUP BY variant, new_action
            ORDER BY variant, cnt DESC
        """, (experiment_id,)).fetchall()

    # Build table: variant -> {action: count}
    action_dist: dict[str, dict[str, int]] = {}
    for row in rows:
        v = row["variant"]
        action_dist.setdefault(v, {})[row["new_action"]] = row["cnt"]

    # Collect all actions
    all_actions = sorted({a for d in action_dist.values() for a in d})

    # Print header
    col_w = 14
    header = f"{'Variant':<20}" + "".join(f"{a:>{col_w}}" for a in all_actions) + f"{'Total':>{col_w}}"
    print(header)
    print("-" * len(header))

    for variant in sorted(action_dist):
        total = sum(action_dist[variant].values())
        cols = "".join(
            f"{action_dist[variant].get(a, 0):>{col_w}}" for a in all_actions
        )
        print(f"{variant:<20}{cols}{total:>{col_w}}")

    # Per-variant stats from summary
    print("\n" + "-" * 72)
    print(f"{'Variant':<20}{'Changed':>10}{'Improved':>10}{'Degraded':>10}{'Avg EV Δ':>12}{'Errors':>8}")
    print("-" * 72)
    for variant, stats in sorted(summary.get("by_variant", {}).items()):
        ev_delta = f"{stats['avg_ev_delta']:.3f}" if stats.get("avg_ev_delta") is not None else "N/A"
        print(
            f"{variant:<20}"
            f"{stats['actions_changed']:>10}"
            f"{stats['improved']:>10}"
            f"{stats['degraded']:>10}"
            f"{ev_delta:>12}"
            f"{stats['errors']:>8}"
        )

    overall = summary.get("overall", {})
    print(f"\nTotal results: {overall.get('total_results', 0)}")
    print(f"Actions changed: {overall.get('actions_changed', 0)}")
    print(f"Errors: {overall.get('errors', 0)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # 1. Query capture IDs
    logger.info(
        "Querying exp %d (%s) for 80%%+ equity checks...",
        SOURCE_EXPERIMENT_ID, SOURCE_VARIANT,
    )
    capture_ids = query_capture_ids(DB_PATH)
    if not capture_ids:
        print("ERROR: No matching captures found. Check experiment ID and variant.")
        sys.exit(1)
    logger.info("Found %d captures: %s", len(capture_ids), capture_ids)

    # 2. Create replay experiment
    repos = create_repos(DB_PATH)
    replay_repo = repos["replay_experiment_repo"]

    experiment_id = replay_repo.create_replay_experiment(
        name=f"value-bet-replay-exp{SOURCE_EXPERIMENT_ID}",
        capture_ids=capture_ids,
        variants=VARIANTS,
        description=(
            f"Replay {len(capture_ids)} high-equity checks from exp {SOURCE_EXPERIMENT_ID} "
            f"({SOURCE_VARIANT}) across nano/gpt-5 × baseline/value-bet guidance."
        ),
        hypothesis=(
            "gpt-5 and/or explicit value-bet guidance will convert passive checks "
            "into bets/raises when the player has 80%+ equity."
        ),
        parent_experiment_id=SOURCE_EXPERIMENT_ID,
    )
    logger.info("Created replay experiment %d", experiment_id)

    # 3. Run
    def progress(completed, total, message):
        print(f"  [{completed}/{total}] {message}")

    runner = ReplayExperimentRunner(
        replay_experiment_repo=replay_repo,
        db_path=DB_PATH,
        max_workers=3,
        progress_callback=progress,
    )
    logger.info("Running %d captures × %d variants = %d replays...",
                len(capture_ids), len(VARIANTS), len(capture_ids) * len(VARIANTS))
    runner.run_experiment(experiment_id)

    # 4. Print summary
    print_summary(replay_repo, experiment_id)


if __name__ == "__main__":
    main()
