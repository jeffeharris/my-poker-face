#!/usr/bin/env python3
"""
Run an experiment from a JSON config file.

Usage:
    # Run the prompt ablation study
    python -m experiments.run_from_config experiments/configs/prompt_ablation_study.json

    # Run minimal prompt test
    python -m experiments.run_from_config experiments/configs/minimal_prompt_test.json

    # Override settings via command line
    python -m experiments.run_from_config experiments/configs/prompt_ablation_study.json --hands 50 --tournaments 3
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner, print_summary


def load_config(config_path: str) -> dict:
    """Load experiment config from JSON file."""
    with open(config_path) as f:
        return json.load(f)


def run_experiment_from_config(
    config_path: str,
    hands_override: int = None,
    tournaments_override: int = None,
    model_override: str = None,
    provider_override: str = None,
    db_path: str = None,
):
    """Run an experiment from a config file.

    Args:
        config_path: Path to JSON config file
        hands_override: Override hands_per_tournament
        tournaments_override: Override num_tournaments
        model_override: Override model
        provider_override: Override provider
        db_path: Optional database path
    """
    # Load config
    config_dict = load_config(config_path)

    # Apply overrides
    if hands_override:
        config_dict['hands_per_tournament'] = hands_override
    if tournaments_override:
        config_dict['num_tournaments'] = tournaments_override
    if model_override:
        config_dict['model'] = model_override
    if provider_override:
        config_dict['provider'] = provider_override

    # Add timestamp to name to make it unique
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    original_name = config_dict.get('name', 'experiment')
    config_dict['name'] = f"{original_name}_{timestamp}"

    # Use main database for experiment data
    if db_path is None:
        if (project_root / "data").exists():
            db_path = str(project_root / "data" / "poker_games.db")
        else:
            db_path = str(project_root / "poker_games.db")

    print("=" * 60)
    print(f"RUNNING EXPERIMENT: {config_dict['name']}")
    print("=" * 60)
    print(f"Config file: {config_path}")
    print(f"Description: {config_dict.get('description', 'N/A')}")
    print(f"Hypothesis: {config_dict.get('hypothesis', 'N/A')}")
    print(f"Model: {config_dict.get('provider', 'openai')}/{config_dict.get('model', 'gpt-4o-mini')}")
    print(f"Tournaments: {config_dict.get('num_tournaments', 1)}")
    print(f"Hands per tournament: {config_dict.get('hands_per_tournament', 100)}")
    print(f"Players: {config_dict.get('num_players', 4)}")

    # Show variants if A/B test
    if config_dict.get('control'):
        variants = [config_dict['control'].get('label', 'Control')]
        for v in config_dict.get('variants', []):
            variants.append(v.get('label', 'Variant'))
        print(f"Variants: {variants}")

    print(f"Database: {db_path}")
    print("-" * 60)

    # Create config object
    config = ExperimentConfig(**config_dict)

    # Run experiment
    runner = AITournamentRunner(config, db_path=db_path)
    results = runner.run_experiment()

    print_summary(results)

    # Print experiment ID for reference
    if runner.experiment_id:
        print(f"\nExperiment ID: {runner.experiment_id}")
        print(f"View results: SELECT * FROM experiments WHERE id = {runner.experiment_id}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Run experiment from config file")
    parser.add_argument("config", help="Path to JSON config file")
    parser.add_argument("--hands", "-n", type=int, help="Override hands per tournament")
    parser.add_argument("--tournaments", "-t", type=int, help="Override number of tournaments")
    parser.add_argument("--model", "-m", help="Override LLM model")
    parser.add_argument("--provider", help="Override LLM provider")

    args = parser.parse_args()

    if not Path(args.config).exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)

    run_experiment_from_config(
        config_path=args.config,
        hands_override=args.hands,
        tournaments_override=args.tournaments,
        model_override=args.model,
        provider_override=args.provider,
    )


if __name__ == "__main__":
    main()
