#!/usr/bin/env python3
"""
A/B Test Demo - Demonstrates experiment tracking with variant comparison.

Runs a short experiment comparing two configurations (variants A and B),
then queries the results to show how variant comparison works.

Usage:
    python -m experiments.ab_test_demo
"""

import sys
from pathlib import Path
from datetime import datetime
from dataclasses import asdict

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner, TournamentResult


def run_ab_test(db_path: str = None):
    """Run a short A/B test experiment.

    Args:
        db_path: Optional database path. If None, uses the main app database.
    """
    # Use the main database (same as Flask app) for experiment data
    # This enables JOINs with game data for analysis
    if db_path is None:
        if (project_root / "data").exists():
            db_path = str(project_root / "data" / "poker_games.db")
        else:
            db_path = str(project_root / "poker_games.db")

    experiment_name = f"ab_test_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print("=" * 60)
    print("A/B TEST DEMO")
    print("=" * 60)
    print(f"Experiment: {experiment_name}")
    print(f"Database: {db_path}")
    print()

    # Configuration for both variants
    base_config = {
        'num_tournaments': 1,
        'hands_per_tournament': 10,  # Short for demo
        'num_players': 3,
        'starting_stack': 1000,
        'big_blind': 50,
        'capture_prompts': True,
    }

    # Variant A: baseline model
    config_a = ExperimentConfig(
        name=f"{experiment_name}_variant_a",
        description="Variant A - baseline configuration",
        hypothesis="Baseline performance measurement",
        tags=["ab-test", "variant-a", "demo"],
        model="gpt-5-nano",
        provider="openai",
        **base_config
    )

    # Variant B: same model (in real test, would differ)
    config_b = ExperimentConfig(
        name=f"{experiment_name}_variant_b",
        description="Variant B - treatment configuration",
        hypothesis="Compare against baseline",
        tags=["ab-test", "variant-b", "demo"],
        model="gpt-5-nano",  # In real A/B test, this might be different
        provider="openai",
        **base_config
    )

    results_a = []
    results_b = []

    # Run Variant A
    print("Running Variant A...")
    print("-" * 40)
    runner_a = AITournamentRunner(config_a, db_path=db_path)
    results_a = runner_a.run_experiment()
    experiment_id_a = runner_a.experiment_id
    print(f"Variant A complete. Experiment ID: {experiment_id_a}")
    print()

    # Run Variant B
    print("Running Variant B...")
    print("-" * 40)
    runner_b = AITournamentRunner(config_b, db_path=db_path)
    results_b = runner_b.run_experiment()
    experiment_id_b = runner_b.experiment_id
    print(f"Variant B complete. Experiment ID: {experiment_id_b}")
    print()

    # Query and compare results
    print("=" * 60)
    print("RESULTS COMPARISON")
    print("=" * 60)

    from poker.repositories import create_repos
    repos = create_repos(db_path)
    experiment_repo = repos['experiment_repo']

    # Get experiment details
    exp_a = experiment_repo.get_experiment(experiment_id_a) if experiment_id_a else None
    exp_b = experiment_repo.get_experiment(experiment_id_b) if experiment_id_b else None

    print("\nVariant A:")
    if exp_a:
        print(f"  Name: {exp_a['name']}")
        print(f"  Status: {exp_a['status']}")
        summary_a = exp_a.get('summary', {})
        print(f"  Tournaments: {summary_a.get('tournaments', 0)}")
        print(f"  Total hands: {summary_a.get('total_hands', 0)}")
        print(f"  Winners: {summary_a.get('winners', {})}")
        if 'decision_quality' in summary_a:
            dq = summary_a['decision_quality']
            print(f"  Decision quality: {dq.get('correct_pct', 0)}% correct")

    print("\nVariant B:")
    if exp_b:
        print(f"  Name: {exp_b['name']}")
        print(f"  Status: {exp_b['status']}")
        summary_b = exp_b.get('summary', {})
        print(f"  Tournaments: {summary_b.get('tournaments', 0)}")
        print(f"  Total hands: {summary_b.get('total_hands', 0)}")
        print(f"  Winners: {summary_b.get('winners', {})}")
        if 'decision_quality' in summary_b:
            dq = summary_b['decision_quality']
            print(f"  Decision quality: {dq.get('correct_pct', 0)}% correct")

    # Show linked games
    print("\n" + "-" * 40)
    print("Linked Games:")

    if experiment_id_a:
        games_a = experiment_repo.get_experiment_games(experiment_id_a)
        print(f"\nVariant A games ({len(games_a)}):")
        for g in games_a:
            print(f"  - {g['game_id']} (tournament #{g['tournament_number']})")

    if experiment_id_b:
        games_b = experiment_repo.get_experiment_games(experiment_id_b)
        print(f"\nVariant B games ({len(games_b)}):")
        for g in games_b:
            print(f"  - {g['game_id']} (tournament #{g['tournament_number']})")

    # Show decision stats query capability
    print("\n" + "-" * 40)
    print("Decision Stats Query (via get_experiment_decision_stats):")

    if experiment_id_a:
        stats_a = experiment_repo.get_experiment_decision_stats(experiment_id_a)
        print(f"\nVariant A: {stats_a.get('total', 0)} decisions, {stats_a.get('correct_pct', 0)}% correct")

    if experiment_id_b:
        stats_b = experiment_repo.get_experiment_decision_stats(experiment_id_b)
        print(f"Variant B: {stats_b.get('total', 0)} decisions, {stats_b.get('correct_pct', 0)}% correct")

    print("\n" + "=" * 60)
    print("A/B Test Demo Complete!")
    print("=" * 60)

    # Show SQL query for manual analysis
    print("\nSQL query for manual variant comparison:")
    print("""
    SELECT
        e.name as experiment,
        eg.variant,
        COUNT(pda.id) as decisions,
        ROUND(AVG(CASE WHEN pda.decision_quality = 'correct' THEN 100.0 ELSE 0 END), 1) as correct_pct,
        ROUND(AVG(pda.ev_lost), 2) as avg_ev_lost
    FROM experiments e
    JOIN experiment_games eg ON e.id = eg.experiment_id
    LEFT JOIN player_decision_analysis pda ON eg.game_id = pda.game_id
    WHERE e.name LIKE 'ab_test_demo_%'
    GROUP BY e.name
    ORDER BY e.created_at;
    """)

    return 0


if __name__ == "__main__":
    sys.exit(run_ab_test())
