#!/usr/bin/env python3
"""
Test the minimal prompt system with a short AI tournament.

This runs a quick tournament using only the minimal prompt format:
- BB-normalized values
- Standard poker position names (UTG, CO, BTN, etc.)
- Simple JSON response format: {"action": "...", "raise_to": ...}
- No personality, psychology, or guidance systems

Usage:
    # Run in Docker (recommended)
    docker compose exec backend python -m experiments.run_minimal_prompt_test

    # Or locally with venv activated
    python -m experiments.run_minimal_prompt_test

    # With more hands
    python -m experiments.run_minimal_prompt_test --hands 50

    # Compare minimal vs full prompt
    python -m experiments.run_minimal_prompt_test --compare
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner, print_summary


def run_minimal_prompt_test(
    num_hands: int = 10,
    num_players: int = 3,
    model: str = "gpt-4o-mini",
    provider: str = "openai",
    compare: bool = False,
    db_path: str = None,
):
    """Run a tournament with the minimal prompt system.

    Args:
        num_hands: Number of hands to play
        num_players: Number of AI players
        model: LLM model to use
        provider: LLM provider
        compare: If True, run both minimal and full prompts for A/B comparison
        db_path: Optional database path
    """
    # Use main database for experiment data
    if db_path is None:
        if (project_root / "data").exists():
            db_path = str(project_root / "data" / "poker_games.db")
        else:
            db_path = str(project_root / "poker_games.db")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    print("=" * 60)
    print("MINIMAL PROMPT TEST")
    print("=" * 60)
    print(f"Model: {provider}/{model}")
    print(f"Players: {num_players}")
    print(f"Hands: {num_hands}")
    print(f"Compare mode: {compare}")
    print()

    if compare:
        # A/B test: minimal vs full prompt
        config = ExperimentConfig(
            name=f"minimal_vs_full_{timestamp}",
            description="Compare minimal prompt (pure game state) vs full prompt (with guidance)",
            hypothesis="Minimal prompt produces comparable decision quality with simpler format",
            tags=["minimal-prompt", "comparison", "baseline"],
            num_tournaments=1,
            hands_per_tournament=num_hands,
            num_players=num_players,
            starting_stack=1000,
            big_blind=20,
            model=model,
            provider=provider,
            personalities=[f"Player {i+1}" for i in range(num_players)],
            capture_prompts=True,
            control={
                "label": "Minimal",
                "prompt_config": {
                    "use_minimal_prompt": True,
                }
            },
            variants=[{
                "label": "Full",
                "prompt_config": {
                    "use_minimal_prompt": False,
                    # Keep other features enabled for comparison
                    "pot_odds": True,
                    "hand_strength": True,
                    "situational_guidance": True,
                    # But disable personality/psychology for fair comparison
                    "session_memory": False,
                    "opponent_intel": False,
                    "chattiness": False,
                    "emotional_state": False,
                    "tilt_effects": False,
                    "mind_games": False,
                    "persona_response": False,
                }
            }]
        )
    else:
        # Just run minimal prompt
        config = ExperimentConfig(
            name=f"minimal_prompt_test_{timestamp}",
            description="Test minimal prompt with pure game state, no personality/psychology",
            hypothesis="AI can make reasonable decisions with minimal context",
            tags=["minimal-prompt", "test"],
            num_tournaments=1,
            hands_per_tournament=num_hands,
            num_players=num_players,
            starting_stack=1000,
            big_blind=20,
            model=model,
            provider=provider,
            personalities=[f"Player {i+1}" for i in range(num_players)],
            capture_prompts=True,
            control={
                "label": "Minimal",
                "prompt_config": {
                    "use_minimal_prompt": True,
                }
            }
        )

    print(f"Experiment: {config.name}")
    print(f"Database: {db_path}")
    print("-" * 60)

    runner = AITournamentRunner(config, db_path=db_path)
    results = runner.run_experiment()

    print_summary(results)

    # Print experiment ID for reference
    if runner.experiment_id:
        print(f"\nExperiment ID: {runner.experiment_id}")
        print(f"View results: SELECT * FROM experiments WHERE id = {runner.experiment_id}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Test minimal prompt system")
    parser.add_argument("--hands", "-n", type=int, default=10, help="Hands per tournament")
    parser.add_argument("--players", "-p", type=int, default=3, help="Number of players")
    parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM model")
    parser.add_argument("--provider", default="openai", help="LLM provider")
    parser.add_argument("--compare", "-c", action="store_true", help="Compare minimal vs full prompt")

    args = parser.parse_args()

    run_minimal_prompt_test(
        num_hands=args.hands,
        num_players=args.players,
        model=args.model,
        provider=args.provider,
        compare=args.compare,
    )


if __name__ == "__main__":
    main()
