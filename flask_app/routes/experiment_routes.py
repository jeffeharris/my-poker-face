"""Experiment design and management routes."""

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict
from typing import Dict, Any, Optional, List

from flask import Blueprint, jsonify, request

from core.llm import LLMClient, CallType
from poker.persistence import GamePersistence
from poker.utils import get_celebrities
from poker.prompt_config import PromptConfig
from ..extensions import persistence, limiter
from .. import config
from experiments.pause_coordinator import pause_coordinator

logger = logging.getLogger(__name__)

experiment_bp = Blueprint('experiments', __name__)

# Store active experiment threads for status checking
_active_experiments: Dict[int, threading.Thread] = {}

# Store chat sessions for experiment design
_chat_sessions: Dict[str, List[Dict[str, str]]] = {}

# Default experiment config values
DEFAULT_EXPERIMENT_CONFIG = {
    'name': '',
    'description': '',
    'hypothesis': '',
    'tags': [],
    'capture_prompts': True,
    'num_tournaments': 1,
    'max_hands_per_tournament': 100,
    'num_players': 4,
    'starting_stack': 10000,
    'big_blind': 100,
    'model': 'gpt-5-nano',
    'provider': 'openai',
    'personalities': None,
    'random_seed': None,
    'prompt_config': None,
    'player_configs': None,
    'control': None,
    'variants': None,
    # Parallel execution settings
    'parallel_tournaments': 1,
    'stagger_start_delay': 0.0,
    'rate_limit_backoff_seconds': 30.0,
}

# System prompt for the experiment design assistant
EXPERIMENT_DESIGN_SYSTEM_PROMPT = """You are an experiment design assistant for AI poker tournament testing. Your job is to help users design experiments that test AI player behavior, decision quality, and model performance.

You help configure experiments with these parameters:
- name: Unique identifier for the experiment (required, snake_case)
- description: What the experiment is testing
- hypothesis: The expected outcome or question being answered
- tags: Categories for filtering (e.g., ["model_comparison", "prompt_testing"])
- num_tournaments: How many tournaments to run PER VARIANT (1-20)
- max_hands_per_tournament: Maximum hands per tournament (20-500)
- num_players: Players per tournament (2-8)
- starting_stack: Chips per player (1000-100000)
- big_blind: Big blind amount (10-1000)
- model: Default LLM model to use (e.g., "gpt-5-nano", "claude-sonnet-4-20250514")
- provider: Default LLM provider ("openai", "anthropic", "groq")
- personalities: List of AI personalities to use (or null for random selection)
- prompt_config: Default prompt settings for all players (toggles for different prompt components)
- player_configs: Per-player overrides for prompt settings

## A/B Testing with Control + Variants

For comparing models, prompts, or other configurations, use the control/variants structure:

- control: The baseline configuration (required for A/B tests)
  - label: Name shown in results (e.g., "GPT-4o Baseline")
  - model: Model to use (optional, defaults to experiment's model)
  - provider: Provider to use (optional, defaults to experiment's provider)
  - prompt_config: Prompt settings for control (optional)

- variants: List of variations to compare against control
  - Each variant inherits from control and only needs to specify what's different
  - Same structure as control: label, model, provider, prompt_config

Example A/B test structure for model comparison:
{
  "name": "gpt_vs_claude_comparison",
  "num_tournaments": 3,
  "control": {
    "label": "GPT-4o Baseline",
    "model": "gpt-4o",
    "provider": "openai"
  },
  "variants": [
    {
      "label": "Claude Sonnet",
      "model": "claude-sonnet-4-20250514",
      "provider": "anthropic"
    }
  ]
}

This runs 3 tournaments with GPT-4o AND 3 tournaments with Claude (6 total).

Example A/B test for prompt ablation:
{
  "name": "pot_odds_ablation",
  "num_tournaments": 5,
  "control": {
    "label": "Full Prompts",
    "prompt_config": {"pot_odds": true, "hand_strength": true}
  },
  "variants": [
    {
      "label": "No Pot Odds",
      "prompt_config": {"pot_odds": false, "hand_strength": true}
    }
  ]
}

Available prompt_config options (all boolean, default true):
- pot_odds: Include pot odds and equity calculations
- hand_strength: Include hand strength evaluation
- session_memory: Include session stats (win rate, streaks)
- opponent_intel: Include opponent tendencies
- strategic_reflection: Include past strategic reflections
- chattiness: Include chattiness guidance
- emotional_state: Include emotional state narrative
- tilt_effects: Include tilt-based modifications
- mind_games: Include mind games instruction
- persona_response: Include persona response instruction
- memory_keep_exchanges: Number of conversation exchanges to retain (integer, default 0)

When the user describes what they want to test, suggest appropriate configuration values. Ask clarifying questions if needed.

IMPORTANT: When you have configuration suggestions, include them in your response wrapped in <config_updates> tags like this:
<config_updates>{"name": "example_name", "num_tournaments": 5}</config_updates>

Only include fields that should be updated based on the conversation. The frontend will merge your updates with the existing config.

Common experiment scenarios:
1. Model comparison: Use control + variants with different models/providers
2. Personality testing: See which AI personalities perform best
3. Prompt ablation: Use control + variants with different prompt_config settings
4. Minimal vs full prompts: Compare stripped-down prompts to full prompts
5. Baseline measurement: Simple default config to establish baseline metrics

When users ask to "compare", "A/B test", or run experiments "against each other", use the control/variants structure.

Keep responses concise and focused on experiment design. Be helpful and proactive in suggesting configurations."""

# Quick prompts for common scenarios
QUICK_PROMPTS = {
    'compare_models': 'I want to compare GPT vs Claude decision quality in poker',
    'test_personalities': 'Help me test which AI personalities perform best',
    'test_prompts': 'I want to A/B test enabling/disabling specific prompt components',
    'minimal_prompts': 'Compare minimal prompts (all disabled) vs full prompts',
    'baseline': 'Set up a baseline measurement with default settings',
    'quick_test': 'Create a minimal 1-tournament quick test for sanity checking',
}


def extract_config_updates(response_text: str) -> Optional[Dict[str, Any]]:
    """Extract config updates from AI response if present."""
    pattern = r'<config_updates>(.*?)</config_updates>'
    match = re.search(pattern, response_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse config updates: {e}")
    return None


def clean_response_text(response_text: str) -> str:
    """Remove config_updates tags from response for display."""
    pattern = r'<config_updates>.*?</config_updates>'
    return re.sub(pattern, '', response_text, flags=re.DOTALL).strip()


def is_config_complete(config: Dict[str, Any]) -> bool:
    """Check if experiment config has minimum required fields."""
    return bool(config.get('name'))


@experiment_bp.route('/api/experiments/chat', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def chat_experiment_design():
    """Chat with AI to design experiment configuration."""
    try:
        data = request.get_json()
        message = data.get('message', '')
        session_id = data.get('session_id')
        current_config = data.get('current_config', {})

        if not message:
            return jsonify({'error': 'Message is required'}), 400

        # Create or retrieve session
        if not session_id:
            session_id = str(uuid.uuid4())
            _chat_sessions[session_id] = []

        # Get conversation history
        history = _chat_sessions.get(session_id, [])

        # Build context about current config
        config_context = f"\nCurrent experiment config:\n{json.dumps(current_config, indent=2)}"

        # Build messages for LLM
        messages = [
            {"role": "system", "content": EXPERIMENT_DESIGN_SYSTEM_PROMPT + config_context}
        ]

        # Add conversation history
        for entry in history[-10:]:  # Keep last 10 exchanges
            messages.append(entry)

        # Add current user message
        messages.append({"role": "user", "content": message})

        # Call LLM
        client = LLMClient(model=config.FAST_AI_MODEL)
        response = client.complete(
            messages=messages,
            call_type=CallType.EXPERIMENT_DESIGN,
            prompt_template='experiment_design_chat',
        )

        # Extract config updates from response
        config_updates = extract_config_updates(response.content)
        display_text = clean_response_text(response.content)

        # Update session history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": response.content})
        _chat_sessions[session_id] = history

        # Merge updates into current config
        merged_config = {**DEFAULT_EXPERIMENT_CONFIG, **current_config}
        if config_updates:
            merged_config.update(config_updates)

        return jsonify({
            'success': True,
            'response': display_text,
            'session_id': session_id,
            'config_updates': config_updates,
            'merged_config': merged_config,
            'config_complete': is_config_complete(merged_config),
        })

    except Exception as e:
        logger.error(f"Error in experiment chat: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/personalities', methods=['GET'])
def get_personalities():
    """Get available AI personalities for experiments."""
    try:
        personalities = get_celebrities()
        return jsonify({
            'success': True,
            'personalities': personalities,
        })
    except Exception as e:
        logger.error(f"Error getting personalities: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/prompt-options', methods=['GET'])
def get_prompt_options():
    """Get available PromptConfig options for experiments."""
    try:
        # Get field info from PromptConfig dataclass
        from dataclasses import fields
        config_fields = []
        for field in fields(PromptConfig):
            config_fields.append({
                'name': field.name,
                'type': 'boolean' if field.type == bool else 'integer',
                'default': field.default if hasattr(field, 'default') else None,
            })

        return jsonify({
            'success': True,
            'fields': config_fields,
            'descriptions': {
                'pot_odds': 'Include pot odds and equity calculations',
                'hand_strength': 'Include hand strength evaluation',
                'session_memory': 'Include session stats (win rate, streaks)',
                'opponent_intel': 'Include opponent tendencies and playing style',
                'strategic_reflection': 'Include past strategic reflections',
                'chattiness': 'Include chattiness guidance',
                'emotional_state': 'Include emotional state narrative',
                'tilt_effects': 'Include tilt-based modifications',
                'mind_games': 'Include mind games instruction',
                'persona_response': 'Include persona response instruction',
                'memory_keep_exchanges': 'Number of conversation exchanges to retain',
            },
        })
    except Exception as e:
        logger.error(f"Error getting prompt options: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/validate', methods=['POST'])
def validate_experiment_config():
    """Validate experiment configuration before launch."""
    try:
        data = request.get_json()
        config_data = data.get('config', {})

        errors = []
        warnings = []

        # Required fields
        if not config_data.get('name'):
            errors.append('Experiment name is required')
        elif not re.match(r'^[a-z][a-z0-9_]*$', config_data.get('name', '')):
            errors.append('Name must be snake_case (lowercase letters, numbers, underscores, starting with letter)')

        # Check for duplicate name
        if config_data.get('name'):
            existing = persistence.get_experiment_by_name(config_data['name'])
            if existing:
                errors.append(f"Experiment with name '{config_data['name']}' already exists")

        # Validate numeric ranges
        num_tournaments = config_data.get('num_tournaments', 1)
        if not isinstance(num_tournaments, int) or num_tournaments < 1 or num_tournaments > 20:
            errors.append('num_tournaments must be between 1 and 20')

        max_hands = config_data.get('max_hands_per_tournament', 100)
        if not isinstance(max_hands, int) or max_hands < 20 or max_hands > 500:
            errors.append('max_hands_per_tournament must be between 20 and 500')

        num_players = config_data.get('num_players', 4)
        if not isinstance(num_players, int) or num_players < 2 or num_players > 8:
            errors.append('num_players must be between 2 and 8')

        # Validate personalities if specified
        personalities = config_data.get('personalities')
        if personalities:
            available = get_celebrities()
            for p in personalities:
                if p not in available:
                    warnings.append(f"Personality '{p}' not found in available personalities")

        # Validate provider
        valid_providers = {'openai', 'anthropic', 'groq'}
        provider = config_data.get('provider', 'openai')
        if provider not in valid_providers:
            errors.append(f"Invalid provider: {provider}. Must be one of {valid_providers}")

        # Validate control/variants structure if present
        control = config_data.get('control')
        variants = config_data.get('variants')

        if control is not None:
            if not isinstance(control, dict):
                errors.append('control must be an object')
            elif not control.get('label'):
                errors.append('control.label is required')

        if variants is not None:
            if not isinstance(variants, list):
                errors.append('variants must be an array')
            else:
                variant_labels = set()
                for i, v in enumerate(variants):
                    if not isinstance(v, dict):
                        errors.append(f'variants[{i}] must be an object')
                    elif not v.get('label'):
                        errors.append(f'variants[{i}].label is required')
                    else:
                        label = v.get('label')
                        if label in variant_labels:
                            errors.append(f"Duplicate variant label: '{label}'")
                        variant_labels.add(label)
                        # Check for collision with control label
                        if control and label == control.get('label'):
                            errors.append(f"Variant label '{label}' cannot match control label")

        # Calculate total tournaments for A/B tests
        if control is not None:
            num_variants = 1 + len(variants or [])  # control + variants
            total_tournaments = num_tournaments * num_variants
            if total_tournaments > 20:
                warnings.append(f'Total tournaments ({total_tournaments}) exceeds 20 - this may take a long time')
        elif num_tournaments > 10:
            warnings.append('Running more than 10 tournaments may take a long time')

        return jsonify({
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
        })

    except Exception as e:
        logger.error(f"Error validating config: {e}")
        return jsonify({'error': str(e)}), 500


def run_experiment_background(experiment_id: int, config_dict: Dict[str, Any]):
    """Run experiment in background thread."""
    from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner, TournamentPausedException

    try:
        # Update status to running
        persistence.update_experiment_status(experiment_id, 'running')

        # Build ExperimentConfig from dict
        # Filter to only known fields
        known_fields = {
            'name', 'description', 'hypothesis', 'tags', 'capture_prompts',
            'num_tournaments', 'max_hands_per_tournament', 'num_players',
            'starting_stack', 'big_blind', 'model', 'provider',
            'personalities', 'random_seed', 'control', 'variants',
            'parallel_tournaments', 'stagger_start_delay', 'rate_limit_backoff_seconds'
        }
        filtered_config = {k: v for k, v in config_dict.items() if k in known_fields and v is not None}

        exp_config = ExperimentConfig(**filtered_config)

        # Run the experiment with pause coordinator
        runner = AITournamentRunner(exp_config, pause_coordinator=pause_coordinator)
        # Override the experiment_id to use our pre-created one
        runner.experiment_id = experiment_id
        results = runner.run_experiment()

        # Check if paused (via pause coordinator flag still set)
        if pause_coordinator.should_pause(experiment_id):
            logger.info(f"Experiment {experiment_id} paused")
            persistence.update_experiment_status(experiment_id, 'paused')
        elif results:
            # Complete the experiment (runner already does this, but ensure it's done)
            summary = runner._compute_experiment_summary(results)
            persistence.complete_experiment(experiment_id, summary)
            logger.info(f"Experiment {experiment_id} completed successfully")
        else:
            logger.info(f"Experiment {experiment_id} completed with no results")
            persistence.update_experiment_status(experiment_id, 'completed')

    except Exception as e:
        logger.error(f"Experiment {experiment_id} failed: {e}")
        # Check if this was due to pause
        if pause_coordinator.should_pause(experiment_id):
            persistence.update_experiment_status(experiment_id, 'paused')
        else:
            persistence.update_experiment_status(experiment_id, 'failed', str(e))
    finally:
        # Clean up thread reference (use pop to avoid race condition)
        _active_experiments.pop(experiment_id, None)


@experiment_bp.route('/api/experiments', methods=['POST'])
def create_experiment():
    """Create and launch a new experiment."""
    try:
        data = request.get_json()
        config_data = data.get('config', {})

        # Validate first
        if not config_data.get('name'):
            return jsonify({'error': 'Experiment name is required'}), 400

        # Check for duplicate
        existing = persistence.get_experiment_by_name(config_data['name'])
        if existing:
            return jsonify({'error': f"Experiment '{config_data['name']}' already exists"}), 400

        # Create experiment record
        experiment_id = persistence.create_experiment(config_data)

        # Launch in background
        thread = threading.Thread(
            target=run_experiment_background,
            args=(experiment_id, config_data),
            daemon=True
        )
        _active_experiments[experiment_id] = thread
        thread.start()

        return jsonify({
            'success': True,
            'experiment_id': experiment_id,
            'name': config_data['name'],
            'status': 'running',
        })

    except Exception as e:
        logger.error(f"Error creating experiment: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments', methods=['GET'])
def list_experiments():
    """List all experiments with optional status filter."""
    try:
        status = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))

        experiments = persistence.list_experiments(
            status=status,
            limit=limit,
            offset=offset
        )

        return jsonify({
            'success': True,
            'experiments': experiments,
        })

    except Exception as e:
        logger.error(f"Error listing experiments: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>', methods=['GET'])
def get_experiment(experiment_id: int):
    """Get experiment details by ID."""
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        # Get decision stats
        decision_stats = persistence.get_experiment_decision_stats(experiment_id)

        # Get real-time unified stats per variant
        live_stats = persistence.get_experiment_live_stats(experiment_id)

        return jsonify({
            'success': True,
            'experiment': experiment,
            'decision_stats': decision_stats,
            'live_stats': live_stats,
        })

    except Exception as e:
        logger.error(f"Error getting experiment: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/games', methods=['GET'])
def get_experiment_games(experiment_id: int):
    """Get games linked to an experiment."""
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        games = persistence.get_experiment_games(experiment_id)

        return jsonify({
            'success': True,
            'games': games,
        })

    except Exception as e:
        logger.error(f"Error getting experiment games: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/quick-prompts', methods=['GET'])
def get_quick_prompts():
    """Get quick prompt suggestions for common experiment scenarios."""
    return jsonify({
        'success': True,
        'prompts': [
            {'id': 'compare_models', 'label': 'Compare Models', 'prompt': QUICK_PROMPTS['compare_models']},
            {'id': 'test_personalities', 'label': 'Test Personalities', 'prompt': QUICK_PROMPTS['test_personalities']},
            {'id': 'test_prompts', 'label': 'Test Prompt Components', 'prompt': QUICK_PROMPTS['test_prompts']},
            {'id': 'minimal_prompts', 'label': 'Minimal vs Full Prompts', 'prompt': QUICK_PROMPTS['minimal_prompts']},
            {'id': 'baseline', 'label': 'Baseline Measurement', 'prompt': QUICK_PROMPTS['baseline']},
            {'id': 'quick_test', 'label': 'Quick Test', 'prompt': QUICK_PROMPTS['quick_test']},
        ],
    })


@experiment_bp.route('/api/experiments/<int:experiment_id>/cost-trends', methods=['GET'])
def get_experiment_cost_trends(experiment_id: int):
    """Get cost trends over time for an experiment.

    Returns time-bucketed cost data for charting cost accumulation over time.

    Query params:
        bucket: Bucket size in minutes (default 5)
    """
    import sqlite3

    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        bucket_minutes = request.args.get('bucket', 5, type=int)

        with sqlite3.connect(persistence.db_path) as conn:
            cursor = conn.execute("""
                SELECT
                    strftime('%Y-%m-%d %H:%M', au.created_at, 'start of minute',
                        printf('-%d minutes', CAST(strftime('%M', au.created_at) AS INTEGER) % ?)) as bucket,
                    eg.variant,
                    SUM(au.estimated_cost) as cost,
                    COUNT(*) as calls
                FROM api_usage au
                JOIN experiment_games eg ON au.game_id = eg.game_id
                WHERE eg.experiment_id = ? AND au.estimated_cost IS NOT NULL
                GROUP BY bucket, eg.variant
                ORDER BY bucket
            """, (bucket_minutes, experiment_id))

            trends = [{'time': r[0], 'variant': r[1], 'cost': r[2], 'calls': r[3]}
                      for r in cursor.fetchall()]

        return jsonify({
            'success': True,
            'trends': trends,
            'bucket_minutes': bucket_minutes,
        })

    except Exception as e:
        logger.error(f"Error getting cost trends for experiment {experiment_id}: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/live-games', methods=['GET'])
def get_live_games(experiment_id: int):
    """Get live game snapshots for monitoring running experiments.

    Returns all current game states with player info, cards, pot, and psychology data.
    Designed to be polled every 5 seconds for live monitoring view.
    """
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        games = persistence.get_experiment_game_snapshots(experiment_id)

        return jsonify({
            'success': True,
            'games': games,
            'experiment_status': experiment.get('status'),
        })

    except Exception as e:
        logger.error(f"Error getting live games for experiment {experiment_id}: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/games/<game_id>/player/<player_name>', methods=['GET'])
def get_player_detail(experiment_id: int, game_id: str, player_name: str):
    """Get detailed player info for the drill-down panel.

    Returns comprehensive player data including psychology, LLM stats,
    play style analysis, and recent decisions.
    """
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        player_detail = persistence.get_experiment_player_detail(
            experiment_id, game_id, player_name
        )

        if not player_detail:
            return jsonify({'error': 'Player not found in game'}), 404

        return jsonify({
            'success': True,
            **player_detail,
        })

    except Exception as e:
        logger.error(f"Error getting player detail for {player_name} in game {game_id}: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/pause', methods=['POST'])
def pause_experiment(experiment_id: int):
    """Request pause for a running experiment.

    Sets a pause flag that workers will check after each action.
    The experiment will stop after the current action completes.
    """
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        if experiment.get('status') != 'running':
            return jsonify({
                'error': f"Cannot pause experiment with status '{experiment.get('status')}'. Only running experiments can be paused."
            }), 400

        # Set the pause flag - workers will check this after each action
        pause_coordinator.request_pause(experiment_id)

        logger.info(f"Pause requested for experiment {experiment_id}")

        return jsonify({
            'success': True,
            'message': 'Pause requested. Experiment will stop after current action completes.',
            'experiment_id': experiment_id,
        })

    except Exception as e:
        logger.error(f"Error pausing experiment {experiment_id}: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/resume', methods=['POST'])
def resume_experiment(experiment_id: int):
    """Resume a paused experiment.

    Finds incomplete tournaments and continues them from their saved state.
    """
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        if experiment.get('status') != 'paused':
            return jsonify({
                'error': f"Cannot resume experiment with status '{experiment.get('status')}'. Only paused experiments can be resumed."
            }), 400

        # Get incomplete tournaments
        incomplete = persistence.get_incomplete_tournaments(experiment_id)

        if not incomplete:
            # No incomplete tournaments - mark as completed
            logger.info(f"No incomplete tournaments for experiment {experiment_id}, marking as completed")
            persistence.update_experiment_status(experiment_id, 'completed')
            return jsonify({
                'success': True,
                'message': 'No incomplete tournaments found. Experiment marked as completed.',
                'experiment_id': experiment_id,
            })

        # Clear the pause flag
        pause_coordinator.clear_pause(experiment_id)

        # Update status to running
        persistence.update_experiment_status(experiment_id, 'running')

        # Get experiment config
        config_dict = experiment.get('config', {})

        # Launch background resume thread
        thread = threading.Thread(
            target=resume_experiment_background,
            args=(experiment_id, incomplete, config_dict),
            daemon=True
        )
        _active_experiments[experiment_id] = thread
        thread.start()

        logger.info(f"Resuming experiment {experiment_id} with {len(incomplete)} incomplete tournaments")

        return jsonify({
            'success': True,
            'message': f'Resuming experiment with {len(incomplete)} incomplete tournaments.',
            'experiment_id': experiment_id,
            'incomplete_tournaments': len(incomplete),
        })

    except Exception as e:
        logger.error(f"Error resuming experiment {experiment_id}: {e}")
        return jsonify({'error': str(e)}), 500


def resume_experiment_background(experiment_id: int, incomplete_tournaments: List[Dict], config_dict: Dict[str, Any]):
    """Resume incomplete tournaments in background thread."""
    from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner, TournamentPausedException
    from poker.poker_state_machine import PokerStateMachine
    from poker.controllers import AIPlayerController
    from poker.memory.memory_manager import AIMemoryManager
    from poker.prompt_config import PromptConfig

    try:
        # Build ExperimentConfig
        known_fields = {
            'name', 'description', 'hypothesis', 'tags', 'capture_prompts',
            'num_tournaments', 'max_hands_per_tournament', 'num_players',
            'starting_stack', 'big_blind', 'model', 'provider',
            'personalities', 'random_seed', 'control', 'variants',
            'parallel_tournaments', 'stagger_start_delay', 'rate_limit_backoff_seconds'
        }
        filtered_config = {k: v for k, v in config_dict.items() if k in known_fields and v is not None}
        exp_config = ExperimentConfig(**filtered_config)

        results = []
        paused_again = False

        for tournament_info in incomplete_tournaments:
            game_id = tournament_info['game_id']
            variant = tournament_info.get('variant')
            variant_config = tournament_info.get('variant_config')

            logger.info(f"Resuming tournament {game_id}")

            try:
                # Load saved game state
                state_machine = persistence.load_game(game_id)
                if not state_machine:
                    logger.warning(f"Could not load game state for {game_id}, skipping")
                    continue

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
                    owner_id=f"experiment_{exp_config.name}"
                )
                memory_manager.set_persistence(persistence)

                # Initialize memory manager for players
                for player in state_machine.game_state.players:
                    memory_manager.initialize_for_player(player.name)

                # Create a runner instance for the tournament logic
                runner = AITournamentRunner(exp_config, pause_coordinator=pause_coordinator)
                runner.experiment_id = experiment_id
                runner.persistence = persistence

                # Determine current hand number from game state
                # We'll continue from hand 1 and let the game state guide us
                hand_number = 0
                max_hands = exp_config.max_hands_per_tournament

                # Continue the tournament from saved state
                while hand_number < max_hands:
                    hand_number += 1

                    should_continue = runner.run_hand(
                        state_machine, controllers, memory_manager, hand_number,
                        tournament_id=game_id
                    )

                    # Save game state for live monitoring
                    persistence.save_game(game_id, state_machine, f"experiment_{exp_config.name}")

                    if not should_continue:
                        # Check if paused
                        if pause_coordinator.should_pause(experiment_id):
                            paused_again = True
                            logger.info(f"Tournament {game_id} paused again")
                        break

                if paused_again:
                    break

                logger.info(f"Tournament {game_id} resumed and completed")

            except TournamentPausedException as e:
                paused_again = True
                logger.info(f"Tournament {game_id} paused: {e}")
                break

            except Exception as e:
                logger.error(f"Error resuming tournament {game_id}: {e}", exc_info=True)

        # Update final status
        if paused_again or pause_coordinator.should_pause(experiment_id):
            persistence.update_experiment_status(experiment_id, 'paused')
            logger.info(f"Experiment {experiment_id} paused again")
        else:
            persistence.update_experiment_status(experiment_id, 'completed')
            logger.info(f"Experiment {experiment_id} resume completed")

    except Exception as e:
        logger.error(f"Error in resume_experiment_background for {experiment_id}: {e}", exc_info=True)
        if pause_coordinator.should_pause(experiment_id):
            persistence.update_experiment_status(experiment_id, 'paused')
        else:
            persistence.update_experiment_status(experiment_id, 'failed', str(e))
    finally:
        _active_experiments.pop(experiment_id, None)
