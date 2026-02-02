"""Shared helpers for resuming stalled experiment variants.

This module provides common logic used by both:
- flask_app/routes/experiment_routes.py (resume_single_variant_background)
- experiments/resume_stalled.py (resume_variant)
"""
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Valid fields for ExperimentConfig construction
EXPERIMENT_CONFIG_FIELDS = {
    'name', 'description', 'hypothesis', 'tags', 'capture_prompts',
    'num_tournaments', 'hands_per_tournament', 'num_players',
    'starting_stack', 'big_blind', 'model', 'provider',
    'personalities', 'random_seed', 'control', 'variants',
    'parallel_tournaments', 'stagger_start_delay', 'rate_limit_backoff_seconds',
    'reset_on_elimination'
}


def build_experiment_config(config_dict: Dict[str, Any]):
    """Build ExperimentConfig from a config dictionary.

    Filters the config dict to only include valid ExperimentConfig fields
    and returns a new ExperimentConfig instance.

    Args:
        config_dict: Raw configuration dictionary (may contain extra fields)

    Returns:
        ExperimentConfig instance
    """
    from experiments.run_ai_tournament import ExperimentConfig

    filtered_config = {
        k: v for k, v in config_dict.items()
        if k in EXPERIMENT_CONFIG_FIELDS and v is not None
    }
    return ExperimentConfig(**filtered_config)


def determine_llm_config(
    variant_config: Optional[Dict],
    exp_config
) -> Dict[str, str]:
    """Determine LLM provider and model from variant and experiment config.

    Variant config takes precedence over experiment config.

    Args:
        variant_config: Optional variant-specific configuration
        exp_config: ExperimentConfig instance

    Returns:
        Dict with 'provider' and 'model' keys
    """
    if variant_config:
        return {
            'provider': variant_config.get('provider') or exp_config.provider,
            'model': variant_config.get('model') or exp_config.model,
        }
    return {
        'provider': exp_config.provider,
        'model': exp_config.model,
    }


def create_controllers_for_resume(
    state_machine,
    llm_config: Dict[str, str],
    game_id: str,
    exp_config,
    experiment_repo,
    ai_states: Dict[str, Dict],
    prompt_config=None,
) -> Dict[str, Any]:
    """Create AI player controllers with restored conversation history.

    Args:
        state_machine: The loaded PokerStateMachine instance
        llm_config: Dict with 'provider' and 'model' keys
        game_id: The game ID being resumed
        exp_config: ExperimentConfig instance
        experiment_repo: ExperimentRepository instance
        ai_states: Saved AI player states (from game_repo.load_ai_player_states)
        prompt_config: Optional PromptConfig instance

    Returns:
        Dict mapping player name to AIPlayerController
    """
    from poker.controllers import AIPlayerController

    controllers = {}
    for player in state_machine.game_state.players:
        controller = AIPlayerController(
            player_name=player.name,
            state_machine=state_machine,
            llm_config=llm_config,
            game_id=game_id,
            owner_id=f"experiment_{exp_config.name}",
            experiment_repo=experiment_repo,
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

    return controllers


def resume_variant_impl(
    db_path: str,
    experiment_id: int,
    game_id: str,
    variant: Optional[str],
    variant_config: Optional[Dict],
    config_dict: Dict[str, Any],
):
    """Core implementation of variant resumption.

    This is the shared logic for resuming a stalled variant. It handles:
    - Loading game state and AI states
    - Creating controllers with restored history
    - Continuing the tournament

    Args:
        db_path: Database path
        experiment_id: The experiment ID
        game_id: The game_id to resume
        variant: Variant label (optional)
        variant_config: Variant-specific configuration (optional)
        config_dict: Full experiment configuration

    Returns:
        TournamentResult if successful, None otherwise

    Raises:
        TournamentSupersededException: If another process took over
        TournamentPausedException: If tournament was paused
    """
    from experiments.run_ai_tournament import AITournamentRunner
    from poker.memory.memory_manager import AIMemoryManager
    from poker.prompt_config import PromptConfig
    from poker.repositories import create_repos

    # Build experiment config
    exp_config = build_experiment_config(config_dict)

    # Create repos
    repos = create_repos(db_path)
    game_repo = repos['game_repo']
    experiment_repo = repos['experiment_repo']

    # Load saved game state
    state_machine = game_repo.load_game(game_id)
    if not state_machine:
        logger.warning(f"Could not load game state for {game_id}")
        return None

    # Load AI player states (conversation history)
    ai_states = game_repo.load_ai_player_states(game_id)

    # Determine LLM config
    llm_config = determine_llm_config(variant_config, exp_config)

    # Extract prompt_config from variant
    prompt_config_dict = variant_config.get('prompt_config') if variant_config else None
    prompt_config = PromptConfig.from_dict(prompt_config_dict) if prompt_config_dict else None

    # Create controllers with restored history
    controllers = create_controllers_for_resume(
        state_machine=state_machine,
        llm_config=llm_config,
        game_id=game_id,
        exp_config=exp_config,
        experiment_repo=experiment_repo,
        ai_states=ai_states,
        prompt_config=prompt_config,
    )

    # Create memory manager
    memory_manager = AIMemoryManager(
        game_id=game_id,
        db_path=db_path,
    )

    # Create runner
    runner = AITournamentRunner(
        exp_config,
        db_path=db_path,
    )
    runner.experiment_id = experiment_id

    # Get current hand number from game state
    hand_number = getattr(state_machine.game_state, 'hand_number', 1)

    # Update heartbeat to show we're actively resuming
    experiment_repo.update_experiment_game_heartbeat(game_id, 'processing', process_id=os.getpid())

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

    return result
