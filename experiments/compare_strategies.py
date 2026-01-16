#!/usr/bin/env python3
"""
Compare AI decision quality between different strategy configurations.

This script runs A/B tests comparing:
- Baseline (old separate aggression/bluff modifiers)
- New unified strategy matrix

Usage:
    python -m experiments.compare_strategies --quick    # Fast test (2 tournaments, 20 hands each)
    python -m experiments.compare_strategies --full     # Full comparison (10 tournaments each)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.run_ai_tournament import AITournamentRunner, ExperimentConfig, print_summary


def run_comparison(quick: bool = True):
    """Run A/B comparison between strategy variants."""

    if quick:
        num_tournaments = 2
        max_hands = 20
    else:
        num_tournaments = 10
        max_hands = 100

    # Use same personalities for fair comparison
    test_personalities = ["Tyler Durden", "Bob Ross", "Batman", "A Mime"]

    print("=" * 60)
    print("STRATEGY COMPARISON EXPERIMENT")
    print("=" * 60)
    print(f"Mode: {'Quick' if quick else 'Full'}")
    print(f"Tournaments per variant: {num_tournaments}")
    print(f"Max hands per tournament: {max_hands}")
    print(f"Test personalities: {test_personalities}")
    print()

    # The unified strategy matrix is now in place
    # We'll run tournaments and collect decision quality metrics

    config = ExperimentConfig(
        name=f"unified_strategy_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        num_tournaments=num_tournaments,
        max_hands_per_tournament=max_hands,
        num_players=4,
        starting_stack=10000,
        big_blind=100,
        model="gpt-5-nano",
        provider="openai",
        personalities=test_personalities,
        random_seed=42,  # Reproducible
    )

    print("Running with UNIFIED STRATEGY MATRIX...")
    print("-" * 40)

    runner = AITournamentRunner(config)
    results = runner.run_experiment()

    print_summary(results)

    # Save comparison results
    comparison = {
        "experiment_type": "strategy_comparison",
        "timestamp": datetime.now().isoformat(),
        "mode": "quick" if quick else "full",
        "config": {
            "num_tournaments": num_tournaments,
            "max_hands": max_hands,
            "personalities": test_personalities,
        },
        "results": {
            "tournaments": len(results),
            "total_hands": sum(r.hands_played for r in results),
            "winners": {r.winner: 1 for r in results},
            "decision_quality": [r.decision_stats for r in results if r.decision_stats],
        }
    }

    results_dir = project_root / "experiments" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    filename = f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_dir / filename, 'w') as f:
        json.dump(comparison, f, indent=2, default=str)

    print(f"\nResults saved to: experiments/results/{filename}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Compare AI strategy configurations")
    parser.add_argument("--quick", action="store_true", help="Quick test (2 tournaments, 20 hands)")
    parser.add_argument("--full", action="store_true", help="Full test (10 tournaments, 100 hands)")

    args = parser.parse_args()

    quick = not args.full
    run_comparison(quick=quick)

    return 0


if __name__ == "__main__":
    sys.exit(main())
