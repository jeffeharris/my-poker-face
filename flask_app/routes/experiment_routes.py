"""Experiment design and management routes."""

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict
from typing import Dict, Any, Optional, List

from flask import Blueprint, jsonify, request, session

from core.llm import LLMClient, CallType, ASSISTANT_MODEL, ASSISTANT_PROVIDER
from poker.persistence import GamePersistence
from poker.prompt_config import PromptConfig
from ..extensions import persistence, limiter
from .. import config
from experiments.pause_coordinator import pause_coordinator
from datetime import datetime

logger = logging.getLogger(__name__)

experiment_bp = Blueprint('experiments', __name__)

# Store active experiment threads for status checking
_active_experiments: Dict[int, threading.Thread] = {}


def detect_orphaned_experiments():
    """Mark experiments stuck in 'running' as 'interrupted' on startup.

    This is called on module import to handle experiments that were running
    when the server was stopped/restarted. Uses the persistence layer's
    mark_running_experiments_interrupted() method which sets the status and
    adds a helpful message for users.
    """
    try:
        count = persistence.mark_running_experiments_interrupted()
        if count > 0:
            logger.info(f"Marked {count} orphaned experiment(s) as interrupted on startup")
    except Exception as e:
        logger.error(f"Error detecting orphaned experiments: {e}")


# Detect orphaned experiments on module load
detect_orphaned_experiments()


def _complete_experiment_with_summary(experiment_id: int) -> None:
    """Generate summary from DB data and complete experiment.

    This function is used to properly complete experiments that would otherwise
    be marked as completed without a summary (e.g., after resume when all
    tournaments are done, or when run_experiment_background has empty results).

    It queries live stats from the database, builds a summary structure,
    attempts to generate an AI interpretation, and then marks the experiment
    as completed with the full summary.

    Args:
        experiment_id: The experiment ID to complete
    """
    try:
        # Get experiment data
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            logger.error(f"Cannot complete experiment {experiment_id}: not found")
            return

        exp_config = experiment.get('config', {})

        # Get live stats (queries DB directly)
        live_stats = persistence.get_experiment_live_stats(experiment_id)

        # Compute totals from live_stats
        overall = live_stats.get('overall', {})
        by_variant = live_stats.get('by_variant', {})

        # Calculate hands and tournaments from by_variant
        total_hands = 0
        total_api_calls = 0
        tournaments = 0
        winners = {}

        for variant_label, variant_stats in by_variant.items():
            progress = variant_stats.get('progress', {})
            total_hands += progress.get('current_hands', 0)
            tournaments += progress.get('games_count', 0)
            # API calls from latency metrics
            latency = variant_stats.get('latency_metrics', {})
            total_api_calls += latency.get('count', 0)

        # Build summary structure
        summary = {
            'tournaments': tournaments,
            'total_hands': total_hands,
            'total_api_calls': total_api_calls,
            'total_duration_seconds': 0,  # Not available from live_stats
            'avg_hands_per_tournament': round(total_hands / tournaments, 1) if tournaments > 0 else 0,
            'winners': winners,  # Would need to query games table for winners
        }

        # Add decision quality from overall stats
        if overall.get('decision_quality'):
            dq = overall['decision_quality']
            summary['decision_quality'] = {
                'total_decisions': dq.get('total', 0),
                'correct': dq.get('correct', 0),
                'marginal': dq.get('marginal', 0),
                'mistakes': dq.get('mistakes', 0),
                'correct_pct': dq.get('correct_pct', 0),
                'avg_ev_lost': dq.get('avg_ev_lost', 0),
            }

        # Build per-variant summary
        if by_variant:
            variants_summary = {}
            for label, v_stats in by_variant.items():
                v_progress = v_stats.get('progress', {})
                v_dq = v_stats.get('decision_quality', {})
                v_latency = v_stats.get('latency_metrics', {})
                v_cost = v_stats.get('cost_metrics', {})

                variants_summary[label] = {
                    'tournaments': v_progress.get('games_count', 0),
                    'total_hands': v_progress.get('current_hands', 0),
                    'total_api_calls': v_latency.get('count', 0),
                    'winners': {},  # Would need per-variant winner tracking
                    'decision_quality': {
                        'total': v_dq.get('total', 0),
                        'correct': v_dq.get('correct', 0),
                        'correct_pct': v_dq.get('correct_pct', 0),
                        'mistakes': v_dq.get('mistakes', 0),
                        'avg_ev_lost': v_dq.get('avg_ev_lost', 0),
                    } if v_dq else None,
                    'latency': {
                        'avg_ms': v_latency.get('avg_ms', 0),
                        'p50_ms': v_latency.get('p50_ms', 0),
                        'p95_ms': v_latency.get('p95_ms', 0),
                    } if v_latency else None,
                    'cost': {
                        'total_cost': v_cost.get('total_cost', 0),
                        'cost_per_hand': v_cost.get('cost_per_hand', 0),
                    } if v_cost else None,
                }
            summary['variants'] = variants_summary

        # Generate AI interpretation (best-effort)
        summary = _generate_ai_interpretation_standalone(experiment_id, summary)

        # Complete the experiment
        persistence.complete_experiment(experiment_id, summary)
        logger.info(f"Completed experiment {experiment_id} with generated summary")

    except Exception as e:
        logger.error(f"Error completing experiment {experiment_id} with summary: {e}", exc_info=True)
        # Fall back to completing without summary
        persistence.update_experiment_status(experiment_id, 'completed')


def _generate_ai_interpretation_standalone(experiment_id: int, summary: Dict) -> Dict:
    """Generate AI interpretation of experiment results (standalone version).

    This is a standalone version of AITournamentRunner._generate_ai_interpretation
    that can be called without an active runner instance.

    Args:
        experiment_id: The experiment ID
        summary: The computed experiment summary

    Returns:
        Updated summary dict with 'ai_interpretation' field added
    """
    # Skip if no tournaments completed
    if summary.get('tournaments', 0) == 0:
        logger.info("Skipping AI interpretation: no completed tournaments")
        return summary

    try:
        # Get experiment data
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            logger.warning("Could not retrieve experiment data for AI interpretation")
            return summary

        exp_config = experiment.get('config', {})

        # Build system prompt for analysis
        system_prompt = """You are the experiment design assistant for AI poker tournament testing. You helped design this experiment, and now you're analyzing the results.

Given the experiment configuration and results, provide a concise analysis:

## Summary
1-2 sentences: What was tested and what was the outcome?

## Verdict
One sentence: Did the hypothesis hold? Which variant won (if A/B test)? Include the key numbers.

## Surprises
Only list genuinely unexpected or anomalous findings. If results were as expected, return an empty array.

## Next Steps
Suggest 2-3 follow-up experiments that can be configured with these options:
- num_tournaments: Number of tournaments to run (more = less variance)
- hands_per_tournament: Hands per game (more = longer games)
- model/provider: LLM model (gpt-4o-mini, claude-sonnet, gemini-flash, etc.)
- personalities: Which AI personalities play
- A/B testing: control vs variant configs with different models/providers
- prompt_config: Toggle features like pot_odds, hand_strength, session_memory, strategic_reflection

Return as objects with:
- hypothesis: Testable hypothesis that can be verified by changing the above configs
- description: What config changes to make and why

Be extremely concise. Don't repeat information across sections.
Respond in JSON format with keys: summary, verdict, surprises (array, can be empty), next_steps (array of {hypothesis, description})"""

        # Build results context
        results_context = {
            'experiment': {
                'name': exp_config.get('name'),
                'description': exp_config.get('description'),
                'hypothesis': exp_config.get('hypothesis'),
                'tags': exp_config.get('tags'),
            },
            'config': {
                'num_tournaments': exp_config.get('num_tournaments'),
                'hands_per_tournament': exp_config.get('hands_per_tournament'),
                'num_players': exp_config.get('num_players'),
                'model': exp_config.get('model'),
                'provider': exp_config.get('provider'),
            },
            'results': {
                'tournaments_completed': summary.get('tournaments', 0),
                'total_hands': summary.get('total_hands', 0),
                'total_duration_seconds': summary.get('total_duration_seconds', 0),
                'winners_distribution': summary.get('winners', {}),
            },
        }

        # Add decision quality if available
        if summary.get('decision_quality'):
            results_context['results']['decision_quality'] = summary['decision_quality']

        # Add A/B test info if present
        if exp_config.get('control'):
            results_context['ab_test'] = {
                'control_label': exp_config['control'].get('label'),
                'variant_labels': [v.get('label') for v in exp_config.get('variants', [])],
            }

        # Add per-variant stats if available
        if summary.get('variants'):
            results_context['results']['per_variant_stats'] = summary['variants']

        # Build messages array
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"The experiment has completed. Here are the results:\n\n{json.dumps(results_context, indent=2)}\n\nPlease analyze these results."}
        ]

        # Make LLM call
        client = LLMClient(model=ASSISTANT_MODEL, provider=ASSISTANT_PROVIDER)
        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.EXPERIMENT_ANALYSIS,
            game_id=f"experiment_{experiment_id}",
        )

        # Parse response
        interpretation = json.loads(response.content)
        interpretation['generated_at'] = datetime.now().isoformat()
        interpretation['model_used'] = client.model

        summary['ai_interpretation'] = interpretation
        logger.info(f"Generated AI interpretation for experiment {experiment_id}")

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse AI interpretation response: {e}")
        summary['ai_interpretation'] = {
            'error': f'Failed to parse AI response: {str(e)}',
            'generated_at': datetime.now().isoformat(),
        }
    except Exception as e:
        logger.warning(f"AI interpretation failed for experiment {experiment_id}: {e}")
        summary['ai_interpretation'] = {
            'error': str(e),
            'generated_at': datetime.now().isoformat(),
        }

    return summary


# Store chat sessions for experiment design
# Each session stores:
#   'history': List[Dict] - conversation messages
#   'last_config': Dict - last known config state for diff computation
#   'config_versions': List[Dict] - list of {timestamp, config, message_index}
#   'failure_context': Optional[Dict] - context from failed experiment being fixed
_chat_sessions: Dict[str, Dict[str, Any]] = {}


def _compute_config_diff(old_config: Dict[str, Any], new_config: Dict[str, Any]) -> Optional[str]:
    """Compute a human-readable diff between two configs.

    Returns None if configs are identical, otherwise a formatted diff string.
    """
    if old_config == new_config:
        return None

    changes = []

    # Find all keys in either config
    all_keys = set(old_config.keys()) | set(new_config.keys())

    for key in sorted(all_keys):
        old_val = old_config.get(key)
        new_val = new_config.get(key)

        if old_val != new_val:
            # Format values for display
            def format_val(v):
                if v is None:
                    return "null"
                if isinstance(v, (list, dict)):
                    return json.dumps(v, separators=(',', ':'))
                return repr(v)

            if old_val is None or key not in old_config:
                changes.append(f"  + {key}: {format_val(new_val)}")
            elif new_val is None or key not in new_config:
                changes.append(f"  - {key}: {format_val(old_val)}")
            else:
                changes.append(f"  ~ {key}: {format_val(old_val)} → {format_val(new_val)}")

    if changes:
        return "Config changes:\n" + "\n".join(changes)
    return None


# Tool definition for getting available personalities
PERSONALITY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_available_personalities",
        "description": "Get list of available AI personalities with their play styles and traits. Call this when the user asks about personalities or wants to select specific ones for their experiment.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "filter_play_style": {
                    "type": ["string", "null"],
                    "description": "Optional keyword filter for play style (partial match). Common keywords: 'aggressive', 'calculated', 'strategic', 'unpredictable', 'charismatic', 'calm', 'bold', 'tight'",
                }
            },
            "additionalProperties": False,
            "required": ["filter_play_style"],
        }
    }
}


def _execute_experiment_tool(name: str, args: Dict[str, Any]) -> str:
    """Execute experiment design tools.

    Args:
        name: The tool name to execute
        args: Arguments for the tool

    Returns:
        JSON string with tool results
    """
    if name == "get_available_personalities":
        import sqlite3

        result = []
        filter_style = args.get("filter_play_style")

        # Query personalities directly from database to get configs
        with sqlite3.connect(persistence.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT name, config_json
                FROM personalities
                ORDER BY times_used DESC, name
                LIMIT 100
            """)

            for row in cursor:
                personality_name = row['name']
                try:
                    config = json.loads(row['config_json']) if row['config_json'] else {}
                except json.JSONDecodeError:
                    config = {}

                play_style = config.get("play_style", "unknown")

                # Apply filter if provided
                if filter_style and filter_style.lower() not in play_style.lower():
                    continue

                result.append({
                    "name": personality_name,
                    "play_style": play_style,
                    "traits": config.get("personality_traits", {}),
                })

        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})


# Default experiment config values
DEFAULT_EXPERIMENT_CONFIG = {
    'name': '',
    'description': '',
    'hypothesis': '',
    'tags': [],
    'capture_prompts': True,
    'num_tournaments': 1,
    'hands_per_tournament': 10,
    'num_players': 4,
    'starting_stack': 2000,
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
    # Tournament reset behavior options
    'reset_on_elimination': False,
}

# System prompt for the experiment design assistant
EXPERIMENT_DESIGN_SYSTEM_PROMPT = """You are the Lab Assistant, the AI experiment design helper for AI poker tournament testing. Your job is to help users design experiments that test AI player behavior, decision quality, and model performance.

You help configure experiments with these parameters:
- name: Unique identifier for the experiment (required, snake_case)
- description: What the experiment is testing
- hypothesis: The expected outcome or question being answered
- tags: Categories for filtering (e.g., ["model_comparison", "prompt_testing"])
- num_tournaments: How many tournaments to run PER VARIANT (1-20)
- hands_per_tournament: Hands per tournament (5-500)
- reset_on_elimination: If true, reset all stacks when one player is eliminated (default false)
- num_players: Players per tournament (2-8)
- starting_stack: Chips per player (1000-100000)
- big_blind: Big blind amount (10-1000)
- model: Default LLM model to use (e.g., "gpt-5-nano", "claude-sonnet-4-20250514")
- provider: Default LLM provider ("openai", "anthropic", "groq")
- personalities: List of AI personalities to use (or null for random selection)
- prompt_config: Default prompt settings for all players (toggles for different prompt components)
- player_configs: Per-player overrides for prompt settings
- random_seed: Integer for reproducible experiments. Controls both personality selection AND deck shuffling. Same hand number across variants receives identical cards (dealt to seat positions, not player names). **Enabled by default in the UI.** IMPORTANT: For A/B testing, always recommend keeping this enabled to ensure fair comparisons between variants.

## How hands_per_tournament and reset_on_elimination Compose

| Config | Behavior |
|--------|----------|
| `reset_on_elimination: false` | Tournament ends when one player wins OR hits hand limit (variable hands) |
| `reset_on_elimination: true` | Stacks reset on elimination, always plays EXACTLY hands_per_tournament |

Key insight: `reset_on_elimination` determines if hand count is a maximum or exact target.

## A/B Testing with Control + Variants

For comparing models, prompts, or other configurations, use the control/variants structure.

**IMPORTANT**: For A/B tests, always ensure `random_seed` is set (enabled by default in UI). This guarantees all variants receive identical card distributions for the same hand number, eliminating card-luck as a confounding variable.

- control: The baseline configuration (required for A/B tests)
  - label: Name shown in results (e.g., "GPT-4o Baseline")
  - prompt_config: Prompt settings for control (optional)
  - enable_psychology: Enable tilt/emotional state generation (default false, ~4 LLM calls/hand)
  - enable_commentary: Enable commentary generation (default false, ~4 LLM calls/hand)
  - NOTE: Control always uses the experiment-level model/provider settings

- variants: List of variations to compare against control
  - Each variant can override model/provider to test different LLMs
  - Variants inherit psychology/commentary settings from control if not specified
  - Fields: label (required), model, provider, prompt_config, enable_psychology, enable_commentary

## Real Examples from Successful Experiments

### Example 1: Psychology Impact Test (6 variants, multi-model) ✓ COMPLETED
Tests whether psychology/commentary improves decision quality across different models.
Uses reset_on_elimination to ensure exactly 15 hands per variant for fair comparison.
NOTE: Control uses the top-level model/provider (gpt-5-nano/openai). Variants can override.
{
  "name": "psychology_impact_test_v2",
  "description": "Test impact of psychology and commentary on decision quality",
  "hypothesis": "Psychology/commentary may help smaller models make better decisions by adding emotional context",
  "tags": ["psychology", "commentary", "ablation"],
  "num_tournaments": 1,
  "hands_per_tournament": 15,
  "reset_on_elimination": true,
  "num_players": 6,
  "starting_stack": 10000,
  "big_blind": 250,
  "model": "gpt-5-nano",
  "provider": "openai",
  "parallel_tournaments": 6,
  "stagger_start_delay": 2,
  "personalities": ["Batman", "Gordon Ramsay", "Buddha", "Deadpool", "James Bond", "Daniel Negreanu"],
  "control": {
    "label": "GPT-5 Nano (no psych)",
    "enable_psychology": false,
    "enable_commentary": false
  },
  "variants": [
    {
      "label": "GPT-5 Nano (with psych)",
      "enable_psychology": true,
      "enable_commentary": true
    },
    {
      "label": "Gemini Flash (no psych)",
      "model": "gemini-2.0-flash",
      "provider": "google",
      "enable_psychology": false,
      "enable_commentary": false
    },
    {
      "label": "Gemini Flash (with psych)",
      "model": "gemini-2.0-flash",
      "provider": "google",
      "enable_psychology": true,
      "enable_commentary": true
    },
    {
      "label": "Llama 8B (no psych)",
      "model": "llama-3.1-8b-instant",
      "provider": "groq",
      "enable_psychology": false,
      "enable_commentary": false
    },
    {
      "label": "Llama 8B (with psych)",
      "model": "llama-3.1-8b-instant",
      "provider": "groq",
      "enable_psychology": true,
      "enable_commentary": true
    }
  ]
}

### Example 2: Simple Poker Pros Personality Test ✓ COMPLETED
Simple test with specific poker pro personalities using a single model:
{
  "name": "poker_pros_comparison",
  "description": "Compare Phil Ivey and Daniel Negreanu against other personalities",
  "hypothesis": "Professional poker player personalities will show tighter, more aggressive play",
  "num_tournaments": 2,
  "hands_per_tournament": 50,
  "num_players": 4,
  "starting_stack": 10000,
  "big_blind": 100,
  "model": "gpt-4o-mini",
  "provider": "openai",
  "personalities": ["Phil Ivey", "Daniel Negreanu", "The Rock", "Gordon Ramsay"],
  "control": {
    "label": "Poker Pros Test",
    "enable_psychology": true
  }
}

### Example 3: Fast Model Comparison (5 providers) ✓ COMPLETED
Compare decision quality across 5 fast/budget models with parallel execution.
NOTE: Control uses top-level model/provider. Each variant overrides to test different models.
{
  "name": "preflop_discipline_test_v1",
  "description": "Test pre-flop raising discipline prompt - compare fast models",
  "hypothesis": "New prompt guidance should reduce EV lost and prevent runaway all-ins",
  "tags": ["prompt_test", "preflop_discipline", "fast_models"],
  "num_tournaments": 1,
  "hands_per_tournament": 100,
  "num_players": 5,
  "starting_stack": 10000,
  "big_blind": 250,
  "model": "mistral-small-latest",
  "provider": "mistral",
  "capture_prompts": true,
  "parallel_tournaments": 5,
  "stagger_start_delay": 2.0,
  "personalities": ["Batman", "Gordon Ramsay", "Buddha", "Deadpool", "James Bond"],
  "control": {
    "label": "Mistral Small"
  },
  "variants": [
    {
      "label": "Gemini 2.0 Flash",
      "provider": "google",
      "model": "gemini-2.0-flash"
    },
    {
      "label": "GPT-5 Nano",
      "provider": "openai",
      "model": "gpt-5-nano"
    },
    {
      "label": "Groq Llama 3.1 8B",
      "provider": "groq",
      "model": "llama-3.1-8b-instant"
    },
    {
      "label": "Groq Llama 3.3 70B",
      "provider": "groq",
      "model": "llama-3.3-70b-versatile"
    }
  ]
}

### Example 4: Groq Model Size Comparison ✓ COMPLETED
Compare Groq 8B vs 70B model decision quality (exactly 20 hands per variant).
NOTE: Control uses top-level model/provider. Variant overrides to test larger model.
{
  "name": "reset_fix_v2",
  "num_tournaments": 1,
  "hands_per_tournament": 20,
  "reset_on_elimination": true,
  "num_players": 5,
  "starting_stack": 10000,
  "big_blind": 250,
  "model": "llama-3.1-8b-instant",
  "provider": "groq",
  "parallel_tournaments": 2,
  "stagger_start_delay": 1.0,
  "personalities": ["Batman", "Gordon Ramsay", "Buddha", "Deadpool", "James Bond"],
  "control": {
    "label": "Groq 8B"
  },
  "variants": [
    {
      "label": "Groq 70B",
      "provider": "groq",
      "model": "llama-3.3-70b-versatile"
    }
  ]
}

### Example 5: Quick Sanity Test
Minimal config for quick testing (up to 10 hands, may end early if someone wins):
{
  "name": "quick_sanity_check",
  "description": "Quick test to verify experiment system works",
  "num_tournaments": 1,
  "hands_per_tournament": 10,
  "num_players": 4,
  "starting_stack": 2000,
  "big_blind": 100
}

### Example 6: Prompt Ablation Study
Test specific prompt component impact:
{
  "name": "pot_odds_ablation",
  "description": "Test if pot odds info improves decision quality",
  "hypothesis": "Players with pot odds info will make better call/fold decisions",
  "num_tournaments": 5,
  "control": {
    "label": "Full Prompts",
    "prompt_config": {
      "pot_odds": true,
      "hand_strength": true,
      "session_memory": true,
      "opponent_intel": true
    }
  },
  "variants": [
    {
      "label": "No Pot Odds",
      "prompt_config": {
        "pot_odds": false,
        "hand_strength": true,
        "session_memory": true,
        "opponent_intel": true
      }
    }
  ]
}

## Available prompt_config Options

All boolean options (default true unless specified):
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

## Guidelines

When the user describes what they want to test, suggest appropriate configuration values. Ask clarifying questions if needed.

## Parallel Execution Best Practices

PREFER PARALLEL EXECUTION by default:
- Set parallel_tournaments equal to the number of variants when using different providers
- Different providers (OpenAI, Anthropic, Groq, Google, Mistral) have SEPARATE rate limits
- Variants using different providers can safely run concurrently
- Only reduce parallel_tournaments if the user reports rate limit errors from the SAME provider
- Use stagger_start_delay (1-3 seconds) to spread out initial requests slightly

## How to Propose Configuration Changes

When you have configuration suggestions:
1. First, describe the changes you want to make in plain text
2. Ask the user if they want to apply these changes (e.g., "Should I apply these changes?")
3. ONLY include <config_updates> tags AFTER the user confirms

Example flow:
- User: "I want to compare GPT vs Claude"
- Assistant: "I'd suggest: control with GPT-4o, variant with Claude Sonnet, 5 tournaments each. Want me to apply this?"
- User: "Yes" / "Looks good" / "Apply it"
- Assistant: "Done!" <config_updates>{"control": {...}, "variants": [...], "num_tournaments": 5}</config_updates>

EXCEPTION: For fixing failed experiments, you may apply changes immediately since the user explicitly asked for a fix.

Common experiment scenarios:
1. Model comparison: Use control + variants with different models/providers
2. Personality testing: See which AI personalities perform best
3. Prompt ablation: Use control + variants with different prompt_config settings
4. Minimal vs full prompts: Compare stripped-down prompts to full prompts
5. Baseline measurement: Simple default config to establish baseline metrics
6. Psychology impact: Test if enable_psychology improves decision quality (tilt + emotional state)
7. Commentary impact: Test if enable_commentary affects player behavior
8. Fixed hand count experiments: Use reset_on_elimination: true for equal hand counts across variants (fair A/B comparisons)
9. Natural tournaments: Use reset_on_elimination: false (default) for tournaments that end when one player wins all chips

When users ask to "compare", "A/B test", or run experiments "against each other", use the control/variants structure.

## Tools Available

You have access to the `get_available_personalities` tool that queries the personalities database to get the real, current list of AI personalities with their play styles and traits.

**IMPORTANT**: Always use this tool to get personality names - do NOT guess or make up personality names. The tool returns actual personalities from the system.

Use this tool when:
- The user asks about available personalities or wants to know who they can use
- The user wants to select specific personalities for their experiment
- You need to suggest personality names for the experiment config
- You want to find personalities matching a certain play style

The tool accepts an optional `filter_play_style` parameter for keyword filtering (e.g., "aggressive", "calculated", "calm").

## Response Style

- Be CONCISE. Users want configs, not essays.
- Lead with config updates when you have suggestions - put <config_updates> near the top of your response.
- Avoid long bullet lists of possibilities. Pick the most likely answer.
- Don't repeat information the user already knows.
- One short paragraph of explanation is usually enough."""

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
    cleaned = re.sub(pattern, '', response_text, flags=re.DOTALL).strip()
    # Collapse multiple consecutive newlines to at most two (one blank line)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned


def _describe_config_updates(updates: Dict[str, Any]) -> str:
    """Generate a human-readable description of config updates."""
    if not updates:
        return ""

    descriptions = []
    for key, value in updates.items():
        # Format the value for display
        if isinstance(value, list):
            if len(value) <= 3:
                formatted = ", ".join(str(v) for v in value)
            else:
                formatted = f"{len(value)} items"
        elif isinstance(value, dict):
            formatted = f"{len(value)} settings"
        elif value is None:
            formatted = "default"
        else:
            formatted = str(value)

        # Convert key to readable form
        readable_key = key.replace('_', ' ')
        descriptions.append(f"**{readable_key}**: {formatted}")

    return "Updated config:\n" + "\n".join(f"- {d}" for d in descriptions)


def is_config_complete(config: Dict[str, Any]) -> bool:
    """Check if experiment config has minimum required fields."""
    return bool(config.get('name'))


def _format_failed_tournaments(failed_tournaments: List[Dict]) -> str:
    """Format failed tournaments for display in the system prompt."""
    if not failed_tournaments:
        return "No failed tournament details available."

    lines = []
    for ft in failed_tournaments[:5]:  # Limit to first 5 for brevity
        lines.append(f"- Tournament #{ft.get('tournament_number', '?')}: {ft.get('error_type', 'Unknown')} - {ft.get('error', 'No message')[:100]}")
    if len(failed_tournaments) > 5:
        lines.append(f"... and {len(failed_tournaments) - 5} more failures")
    return "\n".join(lines)


@experiment_bp.route('/api/experiments/chat', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_CHAT_SUGGESTIONS)
def chat_experiment_design():
    """Chat with AI to design experiment configuration."""
    from datetime import datetime

    try:
        data = request.get_json()
        message = data.get('message', '')
        session_id = data.get('session_id')
        current_config = data.get('current_config', {})
        failure_context = data.get('failure_context')

        if not message:
            return jsonify({'error': 'Message is required'}), 400

        # Create or retrieve session
        if not session_id:
            session_id = str(uuid.uuid4())
            _chat_sessions[session_id] = {
                'history': [],
                'last_config': {},
                'config_versions': [],
                'failure_context': failure_context,
            }
        elif session_id not in _chat_sessions:
            # Session exists but not in memory - try to restore from database
            db_session = persistence.get_chat_session(session_id)
            if db_session:
                # Convert UI messages back to history format
                # Preserve reasoning_content for DeepSeek API compatibility
                history_from_db = []
                for msg in db_session.get('messages', []):
                    entry = {'role': msg['role'], 'content': msg['content']}
                    if msg.get('reasoning_content'):
                        entry['reasoning_content'] = msg['reasoning_content']
                    history_from_db.append(entry)
                _chat_sessions[session_id] = {
                    'history': history_from_db,
                    'last_config': db_session.get('config', {}),
                    'config_versions': db_session.get('config_versions') or [],
                    'failure_context': failure_context,
                }
                logger.info(f"Restored chat session {session_id} from database")
            else:
                # Session ID provided but not found anywhere - create new
                _chat_sessions[session_id] = {
                    'history': [],
                    'last_config': {},
                    'config_versions': [],
                    'failure_context': failure_context,
                }

        # Get session data (handle legacy format)
        session_data = _chat_sessions.get(session_id, {'history': [], 'last_config': {}, 'config_versions': []})
        if isinstance(session_data, list):
            # Migrate from old format
            session_data = {'history': session_data, 'last_config': {}, 'config_versions': []}

        history = session_data.get('history', [])
        last_config = session_data.get('last_config', {})
        config_versions = session_data.get('config_versions', [])

        # Store failure context if provided (only on first message)
        if failure_context and not session_data.get('failure_context'):
            session_data['failure_context'] = failure_context

        # Compute diff between last known config and current config
        config_diff = _compute_config_diff(last_config, current_config)

        # Build context about current config
        config_context = f"\nCurrent experiment config:\n{json.dumps(current_config, indent=2)}"

        # Build failure context if fixing a failed experiment or building from suggestion
        failure_context_prompt = ""
        stored_failure_context = session_data.get('failure_context') or failure_context
        if stored_failure_context:
            context_type = stored_failure_context.get('type', 'failure')

            if context_type == 'suggestion':
                # Building a follow-up experiment from a suggestion
                suggestion = stored_failure_context.get('suggestion', {})
                failure_context_prompt = f"""

## Building Follow-up Experiment

**Parent experiment:** {stored_failure_context.get('experimentName', 'Unknown')}
**Hypothesis to test:** {suggestion.get('hypothesis', 'Unknown')}
**Description:** {suggestion.get('description', 'N/A')}

RULES:
1. Generate a complete config that tests this hypothesis
2. Set parent_experiment_id to {stored_failure_context.get('experimentId')} in the config
3. Auto-generate an appropriate experiment name (e.g., "{stored_failure_context.get('experimentName', 'experiment')}_followup")
4. Use the parent config as a starting point, modify as needed for the hypothesis
5. Be CONCISE - config updates and a brief explanation of what you're testing

Response format (NO numbered list - the config_updates tag gets hidden from user):
- One sentence explaining the hypothesis being tested
- <config_updates>{{...}}</config_updates> (this will be hidden, config panel updates automatically)
- One sentence explaining what changed from the parent config
"""
            else:
                # Fixing a failed experiment (existing behavior)
                failure_context_prompt = f"""

## Fixing Failed Experiment

**Failed experiment:** {stored_failure_context.get('experimentName', 'Unknown')}
**Error:** {stored_failure_context.get('errorMessage', 'Unknown error')}

**Failures:**
{_format_failed_tournaments(stored_failure_context.get('failedTournaments', []))}

RULES:
1. Be CONCISE - one sentence diagnosis, config updates, one sentence explaining the fix.
2. ONLY mention things you can fix via config changes. Do NOT speculate about network issues, server problems, transient errors, or anything outside the experiment config.
3. Focus on these actionable fixes:
   - RateLimitError from SAME provider → increase stagger_start_delay for that provider's variants
   - RateLimitError across DIFFERENT providers → usually fine to run in parallel (each provider has separate limits)
   - Model errors → change model or provider
   - Timeout → reduce hands_per_tournament
   - Invalid config → fix the specific field

IMPORTANT: Different providers (OpenAI, Anthropic, Groq, Google) have SEPARATE rate limits. Variants using different providers CAN run in parallel safely. Only reduce parallelism when you see rate limit errors from the SAME provider running concurrently.

Response format (NO numbered list - the config_updates tag gets hidden from user):
- One sentence identifying the error type
- <config_updates>{{...}}</config_updates> (this will be hidden, config panel updates automatically)
- One sentence explaining what changed and why it should help
"""

        # Build messages for LLM
        messages = [
            {"role": "system", "content": EXPERIMENT_DESIGN_SYSTEM_PROMPT + config_context + failure_context_prompt}
        ]

        # Add conversation history
        for entry in history[-10:]:  # Keep last 10 exchanges
            messages.append(entry)

        # Build user message, including config diff if present
        user_message_content = message
        if config_diff:
            user_message_content = f"[User edited the config form]\n{config_diff}\n\n{message}"

        # Add current user message
        messages.append({"role": "user", "content": user_message_content})

        # Call LLM with tool support - use reasoning model for better analysis
        client = LLMClient(model=config.ASSISTANT_MODEL, provider=config.ASSISTANT_PROVIDER)
        response = client.complete(
            messages=messages,
            tools=[PERSONALITY_TOOL],
            tool_choice="auto",
            tool_executor=_execute_experiment_tool,
            call_type=CallType.EXPERIMENT_DESIGN,
            prompt_template='experiment_design_chat',
        )

        # Extract config updates from response
        config_updates = extract_config_updates(response.content)
        display_text = clean_response_text(response.content)

        # If display_text is empty but we have config updates, describe what changed
        if not display_text and config_updates:
            display_text = _describe_config_updates(config_updates)

        # Merge updates into current config
        merged_config = {**DEFAULT_EXPERIMENT_CONFIG, **current_config}
        config_diff_for_response = None
        if config_updates:
            merged_config.update(config_updates)
            # Compute diff between current config and merged config (what the AI changed)
            config_diff_for_response = _compute_config_diff(current_config, merged_config)

        # Update session history and last_config
        # Include reasoning_content for DeepSeek thinking mode (required by API)
        history.append({"role": "user", "content": user_message_content})
        assistant_entry = {"role": "assistant", "content": response.content}
        if response.reasoning_content:
            assistant_entry["reasoning_content"] = response.reasoning_content
        history.append(assistant_entry)

        # Track config version if there were updates
        if config_updates:
            # On first update, save original config as v0 if it was meaningful (had a name)
            # This allows reverting when editing a previous experiment
            if not config_versions and current_config.get('name'):
                config_versions.append({
                    'timestamp': datetime.utcnow().isoformat(),
                    'config': current_config.copy(),
                    'message_index': 0,
                    'label': 'Original',
                })
            # Save the updated config
            config_versions.append({
                'timestamp': datetime.utcnow().isoformat(),
                'config': merged_config.copy(),
                'message_index': len(history),
            })

        _chat_sessions[session_id] = {
            'history': history,
            'last_config': merged_config,  # Store the merged config for next diff
            'config_versions': config_versions,
            'failure_context': session_data.get('failure_context'),
        }

        # Persist session to database for resume functionality
        owner_id = session.get('owner_id', 'anonymous')
        # Convert history to frontend-compatible format (with configDiff)
        # Preserve reasoning_content for DeepSeek thinking mode compatibility
        ui_messages = []
        for msg in history:
            ui_msg = {'role': msg['role'], 'content': msg['content']}
            # For assistant messages, extract and clean the response
            if msg['role'] == 'assistant':
                # Check if this message had config updates by looking at surrounding context
                # For now, just store content - configDiff is added via response
                ui_msg['content'] = clean_response_text(msg['content'])
                # Preserve reasoning_content for DeepSeek API compatibility
                if msg.get('reasoning_content'):
                    ui_msg['reasoning_content'] = msg['reasoning_content']
            ui_messages.append(ui_msg)

        persistence.save_chat_session(
            session_id=session_id,
            owner_id=owner_id,
            messages=ui_messages,
            config_snapshot=merged_config,
            config_versions=config_versions,
        )

        return jsonify({
            'success': True,
            'response': display_text,
            'session_id': session_id,
            'config_updates': config_updates,
            'config_diff': config_diff_for_response,  # Human-readable diff of AI changes
            'merged_config': merged_config,
            'config_complete': is_config_complete(merged_config),
            'config_versions': config_versions,
            'current_version_index': len(config_versions) - 1 if config_versions else 0,
        })

    except Exception as e:
        logger.error(f"Error in experiment chat: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/chat/latest', methods=['GET'])
def get_latest_chat_session():
    """Get the most recent unfinished chat session for the current user.

    Returns the session data if one exists, allowing users to resume their work.
    """
    try:
        owner_id = session.get('owner_id', 'anonymous')
        session_data = persistence.get_latest_chat_session(owner_id)

        if session_data:
            return jsonify({
                'success': True,
                'session': {
                    'session_id': session_data['session_id'],
                    'messages': session_data['messages'],
                    'config': session_data['config'],
                    'config_versions': session_data['config_versions'],
                    'updated_at': session_data['updated_at'],
                },
            })

        return jsonify({
            'success': True,
            'session': None,
        })

    except Exception as e:
        logger.error(f"Error getting latest chat session: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/chat/archive', methods=['POST'])
def archive_chat_session():
    """Archive a chat session so it won't be returned as the latest session.

    Called when the user chooses to start fresh instead of resuming.
    """
    try:
        data = request.get_json()
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'error': 'session_id is required'}), 400

        persistence.archive_chat_session(session_id)

        return jsonify({
            'success': True,
            'archived': True,
        })

    except Exception as e:
        logger.error(f"Error archiving chat session: {e}")
        return jsonify({'error': str(e)}), 500


def _build_experiment_assistant_context(experiment: dict) -> str:
    """Build context for the experiment-scoped assistant.

    Includes design history, experiment config, and results if available.
    """
    context_parts = []

    # 1. Design conversation (how it was conceived)
    design_chat = persistence.get_experiment_design_chat(experiment['id'])
    if design_chat:
        context_parts.append("## Original Design Conversation")
        for msg in design_chat:
            role = "User" if msg.get('role') == 'user' else "Assistant"
            context_parts.append(f"{role}: {msg.get('content', '')[:500]}")  # Truncate long messages
        context_parts.append("")

    # 2. Experiment details (config, status)
    context_parts.append("## Experiment Details")
    context_parts.append(f"Name: {experiment.get('name')}")
    context_parts.append(f"Description: {experiment.get('description', 'Not provided')}")
    context_parts.append(f"Hypothesis: {experiment.get('hypothesis', 'Not provided')}")
    context_parts.append(f"Status: {experiment.get('status')}")
    context_parts.append(f"Tags: {', '.join(experiment.get('tags', []))}")
    context_parts.append("")

    # 3. Config summary (not full JSON, just key parts)
    exp_config = experiment.get('config', {})
    context_parts.append("## Configuration")
    context_parts.append(f"Tournaments: {exp_config.get('num_tournaments', 1)}")
    context_parts.append(f"Hands per tournament: {exp_config.get('hands_per_tournament', 100)}")
    context_parts.append(f"Players: {exp_config.get('num_players', 4)}")
    context_parts.append(f"Model: {exp_config.get('model', 'default')} ({exp_config.get('provider', 'openai')})")
    if exp_config.get('control'):
        context_parts.append(f"A/B Testing: Yes (control + {len(exp_config.get('variants', []))} variants)")
    context_parts.append("")

    # 4. Results summary if completed
    summary = experiment.get('summary')
    if summary:
        context_parts.append("## Results Summary")
        context_parts.append(f"Total tournaments: {summary.get('tournaments', 0)}")
        context_parts.append(f"Total hands: {summary.get('total_hands', 0)}")
        context_parts.append(f"Total API calls: {summary.get('total_api_calls', 0)}")
        context_parts.append(f"Duration: {summary.get('total_duration_seconds', 0):.1f} seconds")

        winners = summary.get('winners', {})
        if winners:
            context_parts.append("Winners:")
            for name, count in sorted(winners.items(), key=lambda x: -x[1]):
                context_parts.append(f"  - {name}: {count} wins")

        decision_quality = summary.get('decision_quality')
        if decision_quality:
            context_parts.append(f"Decision quality: {decision_quality.get('correct_pct', 0):.1f}% correct")
            context_parts.append(f"Mistakes: {decision_quality.get('mistakes', 0)} ({decision_quality.get('mistake_pct', 0):.1f}%)")

        # Per-variant results for A/B testing
        variants = summary.get('variants')
        if variants:
            context_parts.append("")
            context_parts.append("### Results by Variant")
            for variant_name, variant_stats in variants.items():
                context_parts.append(f"\n**{variant_name}**")
                context_parts.append(f"  Tournaments: {variant_stats.get('tournaments', 0)}")
                context_parts.append(f"  Hands: {variant_stats.get('total_hands', 0)}")
                vq = variant_stats.get('decision_quality', {})
                if vq:
                    context_parts.append(f"  Decision quality: {vq.get('correct_pct', 0):.1f}% correct")

        # Failed tournaments if any
        failed = summary.get('failed_tournaments', [])
        if failed:
            context_parts.append(f"\nFailed tournaments: {len(failed)}")
            for ft in failed[:3]:  # Show first 3
                context_parts.append(f"  - Tournament {ft.get('tournament_number')}: {ft.get('error_type')} - {ft.get('error', '')[:100]}")

        # AI interpretation if available
        ai_interpretation = summary.get('ai_interpretation')
        if ai_interpretation and not ai_interpretation.get('error'):
            context_parts.append("")
            context_parts.append("### AI Analysis")
            context_parts.append(f"Summary: {ai_interpretation.get('summary', 'N/A')}")
            context_parts.append(f"Verdict: {ai_interpretation.get('verdict', 'N/A')}")
            if ai_interpretation.get('next_steps'):
                context_parts.append("Suggested next steps:")
                for step in ai_interpretation['next_steps']:
                    if isinstance(step, dict):
                        context_parts.append(f"  - {step.get('hypothesis')}: {step.get('description')}")
                    else:
                        context_parts.append(f"  - {step}")
        elif ai_interpretation and ai_interpretation.get('error'):
            context_parts.append(f"\nAI interpretation failed: {ai_interpretation.get('error')}")

    return "\n".join(context_parts)


# In-memory storage for experiment assistant sessions
_experiment_assistant_sessions: Dict[str, dict] = {}


@experiment_bp.route('/api/experiments/<int:experiment_id>/chat', methods=['POST'])
def experiment_assistant_chat(experiment_id: int):
    """Chat with an experiment-scoped assistant that has context about the experiment.

    The assistant knows the design history, configuration, and results.
    """
    try:
        data = request.get_json()
        message = data.get('message', '').strip()

        if not message:
            return jsonify({'error': 'Message is required'}), 400

        # Get experiment details
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        # Get or create session
        session_key = f"exp_assistant_{experiment_id}"
        if session_key not in _experiment_assistant_sessions:
            _experiment_assistant_sessions[session_key] = {
                'history': [],
            }

        session_data = _experiment_assistant_sessions[session_key]
        history = session_data['history']

        # Build context for the assistant
        experiment_context = _build_experiment_assistant_context(experiment)

        # Build system prompt
        system_prompt = f"""You are an AI assistant helping analyze a poker AI experiment.

{experiment_context}

IMPORTANT - Response style:
- Be brief and conversational. Match the length of your response to the complexity of the question.
- For yes/no questions, start with yes or no, then add 1-2 sentences of context if needed.
- Don't dump all possible information at once. Answer the specific question asked.
- If a question is ambiguous, ask a short clarifying question rather than covering all possibilities.
- Use bullet points sparingly - prefer short prose for simple answers.
- If suggesting follow-up experiments, offer 2-3 brief options and ask which interests them."""

        # Build messages for LLM
        llm_messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            llm_messages.append({"role": msg["role"], "content": msg["content"]})
        llm_messages.append({"role": "user", "content": message})

        # Call LLM
        client = LLMClient()
        response = client.complete(
            messages=llm_messages,
            call_type=CallType.CHAT_SUGGESTION,
            game_id=None,
        )

        response_text = response.content

        # Update history
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": response_text})
        _experiment_assistant_sessions[session_key]['history'] = history

        # Persist to database
        persistence.save_experiment_assistant_chat(experiment_id, history)

        return jsonify({
            'success': True,
            'response': response_text,
        })

    except Exception as e:
        logger.error(f"Error in experiment assistant chat: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/chat/history', methods=['GET'])
def get_experiment_chat_history(experiment_id: int):
    """Get the chat history for an experiment assistant session."""
    try:
        # Try to get from database
        history = persistence.get_experiment_assistant_chat(experiment_id)

        return jsonify({
            'success': True,
            'history': history or [],
        })

    except Exception as e:
        logger.error(f"Error getting experiment chat history: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/chat/clear', methods=['POST'])
def clear_experiment_chat_history(experiment_id: int):
    """Clear the chat history for an experiment assistant session."""
    try:
        # Clear from memory
        session_key = f"exp_assistant_{experiment_id}"
        if session_key in _experiment_assistant_sessions:
            del _experiment_assistant_sessions[session_key]

        # Clear from database
        persistence.save_experiment_assistant_chat(experiment_id, [])

        return jsonify({
            'success': True,
            'cleared': True,
        })

    except Exception as e:
        logger.error(f"Error clearing experiment chat history: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/personalities', methods=['GET'])
def get_personalities():
    """Get available AI personalities for experiments."""
    try:
        # Get personalities from database
        personality_list = persistence.list_personalities(limit=200)
        personalities = [p['name'] for p in personality_list]
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

        hands_per_tournament = config_data.get('hands_per_tournament', 100)
        if not isinstance(hands_per_tournament, int) or hands_per_tournament < 5 or hands_per_tournament > 500:
            errors.append('hands_per_tournament must be between 5 and 500')

        num_players = config_data.get('num_players', 4)
        if not isinstance(num_players, int) or num_players < 2 or num_players > 8:
            errors.append('num_players must be between 2 and 8')

        # Validate personalities if specified (check against database)
        personalities = config_data.get('personalities')
        if personalities:
            available_list = persistence.list_personalities(limit=200)
            available_names = {p['name'] for p in available_list}
            for p in personalities:
                if p not in available_names:
                    warnings.append(f"Personality '{p}' not found in database")

        # Validate provider against models in database
        provider = config_data.get('provider')
        if provider:
            valid_providers = persistence.get_available_providers()
            if valid_providers and provider not in valid_providers:
                warnings.append(f"Provider '{provider}' not found in system models")

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
            'num_tournaments', 'hands_per_tournament', 'num_players',
            'starting_stack', 'big_blind', 'model', 'provider',
            'personalities', 'random_seed', 'control', 'variants',
            'parallel_tournaments', 'stagger_start_delay', 'rate_limit_backoff_seconds',
            'reset_on_elimination'
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
            # Runner already completes the experiment with summary + AI interpretation
            # in run_experiment() - just verify it completed successfully
            exp = persistence.get_experiment(experiment_id)
            if exp and exp.get('status') == 'completed' and exp.get('summary'):
                logger.info(f"Experiment {experiment_id} completed successfully by runner")
            else:
                # Runner didn't complete it (maybe error in completion code) - do it here
                logger.info(f"Experiment {experiment_id} needs completion, generating summary")
                _complete_experiment_with_summary(experiment_id)
        else:
            # No results from runner (e.g., all tournaments already complete)
            # Generate summary from DB data before completing
            logger.info(f"Experiment {experiment_id} completed with no results, generating summary from DB")
            _complete_experiment_with_summary(experiment_id)

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
        design_session_id = data.get('session_id')  # Chat session ID for design history

        # Validate first
        if not config_data.get('name'):
            return jsonify({'error': 'Experiment name is required'}), 400

        # Check for duplicate
        existing = persistence.get_experiment_by_name(config_data['name'])
        if existing:
            return jsonify({'error': f"Experiment '{config_data['name']}' already exists"}), 400

        # Extract parent_experiment_id from config if present
        parent_experiment_id = config_data.pop('parent_experiment_id', None)

        # Create experiment record with optional lineage
        experiment_id = persistence.create_experiment(config_data, parent_experiment_id=parent_experiment_id)

        # Save design chat history if session_id provided
        if design_session_id and design_session_id in _chat_sessions:
            session_data = _chat_sessions[design_session_id]
            # Convert internal history format to storage format
            design_chat = []
            for msg in session_data.get('history', []):
                design_chat.append({
                    'role': msg.get('role'),
                    'content': clean_response_text(msg.get('content', '')),
                })
            persistence.save_experiment_design_chat(experiment_id, design_chat)
            # Archive the design session so it won't be returned as latest
            persistence.archive_chat_session(design_session_id)

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
        include_archived = request.args.get('include_archived', 'false').lower() == 'true'
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))

        experiments = persistence.list_experiments(
            status=status,
            include_archived=include_archived,
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

        # Add pause_requested flag for "Pausing..." UI state
        pause_requested = pause_coordinator.should_pause(experiment_id)

        return jsonify({
            'success': True,
            'experiment': experiment,
            'decision_stats': decision_stats,
            'live_stats': live_stats,
            'pause_requested': pause_requested,
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

        if experiment.get('status') not in ('paused', 'interrupted'):
            return jsonify({
                'error': f"Cannot resume experiment with status '{experiment.get('status')}'. Only paused or interrupted experiments can be resumed."
            }), 400

        # Get incomplete tournaments
        incomplete = persistence.get_incomplete_tournaments(experiment_id)

        if not incomplete:
            # No incomplete tournaments - generate summary and complete
            logger.info(f"No incomplete tournaments for experiment {experiment_id}, generating summary and completing")
            _complete_experiment_with_summary(experiment_id)
            return jsonify({
                'success': True,
                'message': 'No incomplete tournaments found. Experiment completed with summary.',
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


@experiment_bp.route('/api/experiments/<int:experiment_id>/archive', methods=['POST'])
def archive_experiment(experiment_id: int):
    """Archive an experiment by adding _archived tag."""
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        tags = experiment.get('tags', []) or []
        if '_archived' not in tags:
            tags.append('_archived')
            persistence.update_experiment_tags(experiment_id, tags)
            logger.info(f"Archived experiment {experiment_id}")

        return jsonify({
            'success': True,
            'experiment_id': experiment_id,
            'archived': True,
        })

    except Exception as e:
        logger.error(f"Error archiving experiment {experiment_id}: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/unarchive', methods=['POST'])
def unarchive_experiment(experiment_id: int):
    """Unarchive an experiment by removing _archived tag."""
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        tags = experiment.get('tags', []) or []
        if '_archived' in tags:
            tags.remove('_archived')
            persistence.update_experiment_tags(experiment_id, tags)
            logger.info(f"Unarchived experiment {experiment_id}")

        return jsonify({
            'success': True,
            'experiment_id': experiment_id,
            'archived': False,
        })

    except Exception as e:
        logger.error(f"Error unarchiving experiment {experiment_id}: {e}")
        return jsonify({'error': str(e)}), 500


@experiment_bp.route('/api/experiments/<int:experiment_id>/regenerate-summary', methods=['POST'])
def regenerate_summary(experiment_id: int):
    """Regenerate summary for a completed experiment.

    Useful for experiments that were completed without a summary (e.g., due to
    interruption/resume cycles) or to regenerate the AI interpretation with a
    newer model.
    """
    try:
        experiment = persistence.get_experiment(experiment_id)
        if not experiment:
            return jsonify({'error': 'Experiment not found'}), 404

        if experiment.get('status') != 'completed':
            return jsonify({
                'error': f"Only completed experiments can regenerate summary. Current status: {experiment.get('status')}"
            }), 400

        logger.info(f"Regenerating summary for experiment {experiment_id}")
        _complete_experiment_with_summary(experiment_id)

        # Fetch the updated experiment
        updated = persistence.get_experiment(experiment_id)

        return jsonify({
            'success': True,
            'experiment_id': experiment_id,
            'summary': updated.get('summary') if updated else None,
        })

    except Exception as e:
        logger.error(f"Error regenerating summary for experiment {experiment_id}: {e}")
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
            'num_tournaments', 'hands_per_tournament', 'num_players',
            'starting_stack', 'big_blind', 'model', 'provider',
            'personalities', 'random_seed', 'control', 'variants',
            'parallel_tournaments', 'stagger_start_delay', 'rate_limit_backoff_seconds',
            'reset_on_elimination'
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

                # Get original player names from experiment config for reset scenarios
                original_player_names = exp_config.personalities or []
                if not original_player_names:
                    # Fall back to current players if personalities not configured
                    original_player_names = [p.name for p in state_machine.game_state.players]

                # Determine reset behavior
                should_reset = exp_config.reset_on_elimination
                max_hands = exp_config.hands_per_tournament

                # Continue the tournament from saved state
                hand_number = 0
                while hand_number < max_hands:
                    hand_number += 1

                    hand_result = runner.run_hand(
                        state_machine, controllers, memory_manager, hand_number,
                        tournament_id=game_id,
                        variant_config=variant_config
                    )

                    # Save game state for live monitoring
                    persistence.save_game(game_id, state_machine, f"experiment_{exp_config.name}")

                    # Handle reset_needed - restore all original players
                    if hand_result == "reset_needed":
                        if should_reset:
                            from poker.poker_game import Player
                            logger.info(f"Resetting all players for {game_id}")

                            # Recreate all original players with full stacks
                            reset_players = tuple(
                                Player(name=name, stack=exp_config.starting_stack, is_human=False)
                                for name in original_player_names
                            )
                            game_state = state_machine.game_state.update(players=reset_players)
                            state_machine.game_state = game_state

                            # Recreate controllers for any players that were eliminated
                            for name in original_player_names:
                                if name not in controllers:
                                    controllers[name] = AIPlayerController(
                                        player_name=name,
                                        state_machine=state_machine,
                                        llm_config=llm_config,
                                        game_id=game_id,
                                        owner_id=f"experiment_{exp_config.name}",
                                        persistence=persistence,
                                        debug_capture=exp_config.capture_prompts,
                                        prompt_config=prompt_config,
                                    )
                                    memory_manager.initialize_for_player(name)
                            continue
                        else:
                            # No reset - tournament ends
                            break
                    elif not hand_result:
                        # False means paused
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
            # Generate summary from DB data before completing
            logger.info(f"Experiment {experiment_id} resume completed, generating summary")
            _complete_experiment_with_summary(experiment_id)

    except Exception as e:
        logger.error(f"Error in resume_experiment_background for {experiment_id}: {e}", exc_info=True)
        if pause_coordinator.should_pause(experiment_id):
            persistence.update_experiment_status(experiment_id, 'paused')
        else:
            persistence.update_experiment_status(experiment_id, 'failed', str(e))
    finally:
        _active_experiments.pop(experiment_id, None)
