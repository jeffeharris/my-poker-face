#!/usr/bin/env python3
"""
Replay hybrid captures with different prompt configurations to test if
simpler prompts lead to less conservative play.

Usage:
    docker compose exec backend python -m experiments.replay_prompt_configs --limit 20
    docker compose exec backend python -m experiments.replay_prompt_configs --preflop-only
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm import LLMClient, CallType
from poker.prompt_config import PromptConfig
from poker.prompt_manager import PromptManager


# Define prompt config variants to test
PROMPT_CONFIGS = {
    "full": PromptConfig(),  # Current default - everything enabled

    "minimal": PromptConfig(
        include_personality=False,
        use_simple_response_format=True,
        emotional_state=False,
        session_memory=False,
        opponent_intel=False,
        strategic_reflection=False,
        dramatic_sequence=False,
        mind_games=False,
        chattiness=False,
        tilt_effects=False,
        expression_filtering=False,
        zone_benefits=False,
    ),

    "math_focused": PromptConfig(
        gto_equity=True,
        gto_verdict=True,
        emotional_state=False,
        session_memory=False,
        dramatic_sequence=False,
        mind_games=False,
        chattiness=False,
    ),

    "personality_simple": PromptConfig(
        include_personality=True,  # Keep personality
        use_simple_response_format=True,  # But simple response
        emotional_state=False,
        dramatic_sequence=False,
        mind_games=False,
        chattiness=False,
    ),
}


def get_hybrid_captures(db_path: str, limit: int = 50, preflop_only: bool = False) -> List[Dict]:
    """Get hybrid captures for replay testing."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        phase_filter = "AND phase = 'PRE_FLOP'" if preflop_only else ""

        cursor = conn.execute(f'''
            SELECT
                id, game_id, player_name, hand_number, phase,
                player_hand, community_cards, pot_total, cost_to_call,
                action_taken, system_prompt, user_message, ai_response,
                model, provider
            FROM prompt_captures
            WHERE game_id LIKE '%hybrid%'
              AND game_id LIKE '%20260209%'
              AND action_taken IS NOT NULL
              AND system_prompt IS NOT NULL
              {phase_filter}
            ORDER BY RANDOM()
            LIMIT ?
        ''', (limit,))

        return [dict(row) for row in cursor.fetchall()]


def build_minimal_prompt(capture: Dict, config: PromptConfig) -> tuple:
    """Build a simplified prompt based on config."""

    # For minimal config, create a stripped-down system prompt
    if not config.include_personality:
        system_prompt = """You are a poker player. Make the best mathematical decision.

Response format (JSON):
{
  "action": "fold" | "check" | "call" | "raise" | "all_in",
  "raise_to": <number if raising, else omit>
}"""
    else:
        # Use original system prompt but could strip parts
        system_prompt = capture['system_prompt']

    # Strip user message based on config
    user_message = capture['user_message']

    # Remove emotional state section if disabled
    if not config.emotional_state:
        start = user_message.find("[YOUR EMOTIONAL STATE]")
        end = user_message.find("Persona:", start) if start > 0 else -1
        if start > 0 and end > start:
            user_message = user_message[:start] + user_message[end:]

    # Keep the core game state and options
    return system_prompt, user_message


def replay_with_config(capture: Dict, config_name: str, config: PromptConfig) -> Dict:
    """Replay a capture with a specific prompt config."""

    if config_name == "full":
        # Use original prompts
        system_prompt = capture['system_prompt']
        user_message = capture['user_message']
    else:
        system_prompt, user_message = build_minimal_prompt(capture, config)

    # For simple response format, modify the user message
    if config.use_simple_response_format:
        # Replace response format instruction
        if "Respond with JSON:" in user_message:
            idx = user_message.find("Respond with JSON:")
            end_idx = user_message.find("}", idx) + 1
            if end_idx > idx:
                user_message = user_message[:idx] + '''Respond with JSON:
{
  "action": "<your action>",
  "raise_to": <BB amount if raising>
}''' + user_message[end_idx:]

    # Create LLM client
    client = LLMClient(
        provider=capture.get('provider', 'openai'),
        model=capture.get('model', 'gpt-4o-mini')
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    try:
        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.DEBUG_REPLAY
        )

        result = json.loads(response.content)
        return {
            "action": result.get("action") or result.get("choice"),
            "raise_to": result.get("raise_to"),
            "inner_monologue": result.get("inner_monologue", ""),
            "success": True
        }
    except Exception as e:
        return {
            "action": None,
            "error": str(e),
            "success": False
        }


def run_experiment(db_path: str, limit: int, preflop_only: bool, configs_to_test: List[str]):
    """Run the replay experiment."""

    print("=" * 70)
    print("HYBRID PROMPT CONFIG REPLAY EXPERIMENT")
    print("=" * 70)

    # Get captures
    captures = get_hybrid_captures(db_path, limit, preflop_only)
    print(f"\nLoaded {len(captures)} captures for replay")
    print(f"Testing configs: {configs_to_test}")
    print()

    # Track results
    results = {config: {"actions": {}, "changes": 0, "total": 0} for config in configs_to_test}

    for i, capture in enumerate(captures):
        print(f"\n--- Capture {i+1}/{len(captures)}: {capture['player_name']} ---")
        print(f"Hand: {capture['player_hand']}, Phase: {capture['phase']}")
        print(f"Original action: {capture['action_taken']}")

        for config_name in configs_to_test:
            if config_name == "original":
                action = capture['action_taken']
            else:
                config = PROMPT_CONFIGS[config_name]
                result = replay_with_config(capture, config_name, config)
                action = result.get('action')

            # Track results
            results[config_name]["total"] += 1
            results[config_name]["actions"][action] = results[config_name]["actions"].get(action, 0) + 1

            if config_name != "original" and action != capture['action_taken']:
                results[config_name]["changes"] += 1

            print(f"  {config_name}: {action}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for config_name in configs_to_test:
        r = results[config_name]
        print(f"\n{config_name.upper()}:")
        print(f"  Actions: {r['actions']}")
        if config_name != "original":
            print(f"  Changed from original: {r['changes']}/{r['total']} ({100*r['changes']/r['total']:.1f}%)")

        # Calculate VPIP proxy (non-fold actions)
        total = r['total']
        folds = r['actions'].get('fold', 0)
        vpip_proxy = 100 * (total - folds) / total if total > 0 else 0
        print(f"  Non-fold rate (VPIP proxy): {vpip_proxy:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Replay hybrid captures with different prompt configs")
    parser.add_argument("--limit", type=int, default=20, help="Number of captures to replay")
    parser.add_argument("--preflop-only", action="store_true", help="Only test preflop decisions")
    parser.add_argument("--db", default="/app/data/poker_games.db", help="Database path")
    parser.add_argument("--configs", nargs="+",
                        default=["original", "full", "minimal", "math_focused"],
                        choices=["original", "full", "minimal", "math_focused", "personality_simple"],
                        help="Configs to test")

    args = parser.parse_args()

    run_experiment(args.db, args.limit, args.preflop_only, args.configs)


if __name__ == "__main__":
    main()
