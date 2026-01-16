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
    from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner

    try:
        # Update status to running
        persistence.update_experiment_status(experiment_id, 'running')

        # Build ExperimentConfig from dict
        # Filter to only known fields
        known_fields = {
            'name', 'description', 'hypothesis', 'tags', 'capture_prompts',
            'num_tournaments', 'max_hands_per_tournament', 'num_players',
            'starting_stack', 'big_blind', 'model', 'provider',
            'personalities', 'random_seed', 'control', 'variants'
        }
        filtered_config = {k: v for k, v in config_dict.items() if k in known_fields and v is not None}

        config = ExperimentConfig(**filtered_config)

        # Run the experiment
        runner = AITournamentRunner(config)
        # Override the experiment_id to use our pre-created one
        runner.experiment_id = experiment_id
        results = runner.run_experiment()

        # Complete the experiment (runner already does this, but ensure it's done)
        if results:
            summary = runner._compute_experiment_summary(results)
            persistence.complete_experiment(experiment_id, summary)

        logger.info(f"Experiment {experiment_id} completed successfully")

    except Exception as e:
        logger.error(f"Experiment {experiment_id} failed: {e}")
        persistence.update_experiment_status(experiment_id, 'failed', str(e))
    finally:
        # Clean up thread reference
        if experiment_id in _active_experiments:
            del _active_experiments[experiment_id]


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

        return jsonify({
            'success': True,
            'experiment': experiment,
            'decision_stats': decision_stats,
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
