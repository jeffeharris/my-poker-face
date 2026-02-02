#!/usr/bin/env python3
"""
CLI tool for listing and resuming stalled experiment variants.

Usage:
    # List stalled variants for an experiment
    python -m experiments.resume_stalled -e 42 --list

    # Resume all stalled variants for an experiment
    python -m experiments.resume_stalled -e 42 --resume-all

    # Resume a specific stalled variant
    python -m experiments.resume_stalled -e 42 -g <game_id>

    # Custom stall threshold (default 5 minutes)
    python -m experiments.resume_stalled -e 42 --list --threshold 10
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from poker.repositories import create_repos

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def list_stalled_variants(
    experiment_repo,
    experiment_id: int,
    threshold_minutes: int = 5
) -> List[Dict]:
    """List stalled variants for an experiment.

    Args:
        experiment_repo: ExperimentRepository instance
        experiment_id: The experiment ID
        threshold_minutes: Minutes of inactivity before considered stalled

    Returns:
        List of stalled variant records
    """
    stalled = experiment_repo.get_stalled_variants(experiment_id, threshold_minutes)

    if not stalled:
        print(f"No stalled variants found for experiment {experiment_id} "
              f"(threshold: {threshold_minutes} minutes)")
        return []

    print(f"\nStalled variants for experiment {experiment_id}:")
    print("-" * 80)

    for variant in stalled:
        print(f"\n  ID: {variant['id']}")
        print(f"  Game ID: {variant['game_id']}")
        print(f"  Variant: {variant['variant']}")
        print(f"  State: {variant['state']}")
        print(f"  Last Heartbeat: {variant['last_heartbeat_at']}")
        print(f"  Last API Call Started: {variant['last_api_call_started_at']}")
        print(f"  Process ID: {variant['process_id']}")
        if variant['resume_lock_acquired_at']:
            print(f"  Resume Lock: {variant['resume_lock_acquired_at']} (locked)")

    print("-" * 80)
    print(f"Total: {len(stalled)} stalled variant(s)")

    return stalled


def resume_variant(
    experiment_repo,
    db_path: str,
    experiment_id: int,
    game_id: str,
    config_dict: Dict
) -> bool:
    """Resume a specific stalled variant.

    Args:
        experiment_repo: ExperimentRepository instance
        db_path: Database path
        experiment_id: The experiment ID
        game_id: The game_id to resume
        config_dict: Experiment configuration

    Returns:
        True if successfully resumed, False otherwise
    """
    from experiments.run_ai_tournament import (
        AITournamentRunner,
        TournamentPausedException, TournamentSupersededException
    )
    from experiments.resume_helpers import resume_variant_impl, build_experiment_config

    logger.info(f"Attempting to resume variant {game_id}")

    # Get experiment game record
    record = experiment_repo.get_experiment_game(game_id, experiment_id)
    if not record:
        logger.error(f"Variant {game_id} not found in experiment {experiment_id}")
        return False

    experiment_game_id = record['id']
    variant = record['variant']
    variant_config = record.get('variant_config')  # Already parsed from JSON by the repo method

    # Acquire resume lock
    lock_acquired = experiment_repo.acquire_resume_lock(experiment_game_id)
    if not lock_acquired:
        logger.error(f"Could not acquire resume lock for {game_id} - may already be resuming")
        return False

    try:
        result = resume_variant_impl(
            db_path=db_path,
            experiment_id=experiment_id,
            game_id=game_id,
            variant=variant,
            variant_config=variant_config,
            config_dict=config_dict,
        )

        if result:
            # Save the result
            exp_config = build_experiment_config(config_dict)
            runner = AITournamentRunner(exp_config, db_path=db_path)
            runner.experiment_id = experiment_id
            runner._save_result(result)
            logger.info(f"Variant {game_id} completed successfully: {result.winner} won in {result.hands_played} hands")
            return True

        return False

    except TournamentSupersededException:
        logger.info(f"Variant {game_id} resume was superseded")
        return False

    except TournamentPausedException as e:
        logger.info(f"Variant {game_id} paused again: {e}")
        return False

    except Exception as e:
        logger.error(f"Error resuming variant {game_id}: {e}", exc_info=True)
        experiment_repo.update_experiment_game_heartbeat(game_id, 'idle')
        return False

    finally:
        experiment_repo.release_resume_lock(game_id)


def main():
    parser = argparse.ArgumentParser(
        description='List and resume stalled experiment variants'
    )
    parser.add_argument(
        '-e', '--experiment-id',
        type=int,
        required=True,
        help='The experiment ID'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List stalled variants'
    )
    parser.add_argument(
        '--resume-all',
        action='store_true',
        help='Resume all stalled variants'
    )
    parser.add_argument(
        '-g', '--game-id',
        type=str,
        help='Resume a specific game/variant by game_id'
    )
    parser.add_argument(
        '--threshold',
        type=int,
        default=5,
        help='Minutes of inactivity before considered stalled (default: 5)'
    )
    parser.add_argument(
        '--db-path',
        type=str,
        help='Path to database (default: auto-detect)'
    )

    args = parser.parse_args()

    # Determine database path
    if args.db_path:
        db_path = args.db_path
    elif os.path.exists('/app/data/poker_games.db'):
        db_path = '/app/data/poker_games.db'
    else:
        db_path = str(project_root / 'poker_games.db')

    repos = create_repos(db_path)
    experiment_repo = repos['experiment_repo']

    # Get experiment config
    experiment = experiment_repo.get_experiment(args.experiment_id)
    if not experiment:
        print(f"Experiment {args.experiment_id} not found")
        sys.exit(1)

    config_dict = experiment.get('config', {})

    if args.list or (not args.resume_all and not args.game_id):
        # Default to list if no action specified
        list_stalled_variants(experiment_repo, args.experiment_id, args.threshold)

    elif args.resume_all:
        stalled = experiment_repo.get_stalled_variants(args.experiment_id, args.threshold)
        if not stalled:
            print(f"No stalled variants to resume")
            sys.exit(0)

        print(f"Resuming {len(stalled)} stalled variant(s)...")
        success_count = 0
        for variant in stalled:
            if resume_variant(experiment_repo, db_path, args.experiment_id, variant['game_id'], config_dict):
                success_count += 1

        print(f"\nResumed {success_count}/{len(stalled)} variants successfully")

    elif args.game_id:
        if resume_variant(experiment_repo, db_path, args.experiment_id, args.game_id, config_dict):
            print(f"Successfully resumed variant {args.game_id}")
        else:
            print(f"Failed to resume variant {args.game_id}")
            sys.exit(1)


if __name__ == '__main__':
    main()
