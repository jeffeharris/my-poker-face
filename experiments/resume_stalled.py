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
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from poker.persistence import GamePersistence

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def list_stalled_variants(
    persistence: GamePersistence,
    experiment_id: int,
    threshold_minutes: int = 5
) -> List[Dict]:
    """List stalled variants for an experiment.

    Args:
        persistence: Database persistence layer
        experiment_id: The experiment ID
        threshold_minutes: Minutes of inactivity before considered stalled

    Returns:
        List of stalled variant records
    """
    stalled = persistence.get_stalled_variants(experiment_id, threshold_minutes)

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
    persistence: GamePersistence,
    experiment_id: int,
    game_id: str,
    config_dict: Dict
) -> bool:
    """Resume a specific stalled variant.

    Args:
        persistence: Database persistence layer
        experiment_id: The experiment ID
        game_id: The game_id to resume
        config_dict: Experiment configuration

    Returns:
        True if successfully resumed, False otherwise
    """
    from experiments.run_ai_tournament import (
        ExperimentConfig, AITournamentRunner,
        TournamentPausedException, TournamentSupersededException
    )
    from poker.controllers import AIPlayerController
    from poker.memory.memory_manager import AIMemoryManager
    from poker.prompt_config import PromptConfig

    logger.info(f"Attempting to resume variant {game_id}")

    # Get experiment game record
    import sqlite3
    with sqlite3.connect(persistence.db_path) as conn:
        cursor = conn.execute("""
            SELECT id, variant, variant_config_json, tournament_number
            FROM experiment_games WHERE game_id = ? AND experiment_id = ?
        """, (game_id, experiment_id))
        row = cursor.fetchone()
        if not row:
            logger.error(f"Variant {game_id} not found in experiment {experiment_id}")
            return False

        experiment_game_id, variant, variant_config_json, tournament_number = row
        variant_config = json.loads(variant_config_json) if variant_config_json else None

    # Acquire resume lock
    lock_acquired = persistence.acquire_resume_lock(experiment_game_id)
    if not lock_acquired:
        logger.error(f"Could not acquire resume lock for {game_id} - may already be resuming")
        return False

    try:
        # Build ExperimentConfig
        known_fields = {
            'name', 'description', 'hypothesis', 'tags', 'capture_prompts',
            'num_tournaments', 'hands_per_tournament', 'num_players',
            'starting_stack', 'big_blind', 'model', 'provider',
            'personalities', 'random_seed', 'control', 'variants',
            'parallel_tournaments', 'stagger_start_delay', 'rate_limit_backoff_seconds',
            'reset_on_elimination'
        }
        filtered_config = {k: v for k, v in config_dict.items() if k in known_fields and v is not None}
        exp_config = ExperimentConfig(**filtered_config)

        # Load saved game state
        state_machine = persistence.load_game(game_id)
        if not state_machine:
            logger.error(f"Could not load game state for {game_id}")
            return False

        # Load AI player states (conversation history)
        ai_states = persistence.load_ai_player_states(game_id)

        # Determine LLM config
        if variant_config:
            llm_config = {
                'provider': variant_config.get('provider') or exp_config.provider,
                'model': variant_config.get('model') or exp_config.model,
            }
        else:
            llm_config = {
                'provider': exp_config.provider,
                'model': exp_config.model,
            }

        # Extract prompt_config from variant
        prompt_config_dict = variant_config.get('prompt_config') if variant_config else None
        prompt_config = PromptConfig.from_dict(prompt_config_dict) if prompt_config_dict else None

        # Recreate controllers for all players
        controllers = {}
        for player in state_machine.game_state.players:
            controller = AIPlayerController(
                player_name=player.name,
                state_machine=state_machine,
                llm_config=llm_config,
                game_id=game_id,
                owner_id=f"experiment_{exp_config.name}",
                persistence=persistence,
                debug_capture=exp_config.capture_prompts,
                prompt_config=prompt_config,
            )

            # Restore conversation history if available
            if player.name in ai_states:
                saved_messages = ai_states[player.name].get('messages', [])
                if saved_messages and hasattr(controller, 'assistant') and controller.assistant:
                    controller.assistant.memory.set_history(saved_messages)
                    logger.debug(f"Restored {len(saved_messages)} messages for {player.name}")

            controllers[player.name] = controller

        # Create memory manager
        memory_manager = AIMemoryManager(
            game_id=game_id,
            db_path=persistence.db_path,
        )

        # Create runner
        runner = AITournamentRunner(
            exp_config,
            db_path=persistence.db_path,
        )
        runner.experiment_id = experiment_id

        # Get current hand number from game state
        hand_number = getattr(state_machine.game_state, 'hand_number', 1)

        # Update heartbeat to show we're actively resuming
        persistence.update_experiment_game_heartbeat(game_id, 'processing', process_id=os.getpid())

        logger.info(f"Resuming variant {game_id} from hand {hand_number}")

        # Continue the tournament
        result = runner._continue_tournament(
            game_id,
            state_machine,
            controllers,
            memory_manager,
            variant_label=variant,
            variant_config=variant_config,
            starting_hand=hand_number,
        )

        if result:
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
        persistence.update_experiment_game_heartbeat(game_id, 'idle')
        return False

    finally:
        persistence.release_resume_lock(game_id)


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

    persistence = GamePersistence(db_path)

    # Get experiment config
    experiment = persistence.get_experiment(args.experiment_id)
    if not experiment:
        print(f"Experiment {args.experiment_id} not found")
        sys.exit(1)

    config_dict = experiment.get('config', {})

    if args.list or (not args.resume_all and not args.game_id):
        # Default to list if no action specified
        list_stalled_variants(persistence, args.experiment_id, args.threshold)

    elif args.resume_all:
        stalled = persistence.get_stalled_variants(args.experiment_id, args.threshold)
        if not stalled:
            print(f"No stalled variants to resume")
            sys.exit(0)

        print(f"Resuming {len(stalled)} stalled variant(s)...")
        success_count = 0
        for variant in stalled:
            if resume_variant(persistence, args.experiment_id, variant['game_id'], config_dict):
                success_count += 1

        print(f"\nResumed {success_count}/{len(stalled)} variants successfully")

    elif args.game_id:
        if resume_variant(persistence, args.experiment_id, args.game_id, config_dict):
            print(f"Successfully resumed variant {args.game_id}")
        else:
            print(f"Failed to resume variant {args.game_id}")
            sys.exit(1)


if __name__ == '__main__':
    main()
