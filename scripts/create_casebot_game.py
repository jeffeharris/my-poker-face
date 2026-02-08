#!/usr/bin/env python3
"""Create a game with CaseBot and AI players for a human to play against.

Usage:
    docker compose exec backend python scripts/create_casebot_game.py --player-name "Jeff"

This creates a game in the database that can be loaded from the web UI.
"""

import argparse
import json
import logging
import secrets
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from poker.poker_game import initialize_game_state
from poker.poker_state_machine import PokerStateMachine
from poker.rule_based_controller import RuleBasedController, RuleConfig, CHAOS_BOTS
from poker.controllers import AIPlayerController
from poker.repositories.game_repository import GameRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_casebot_game(
    player_name: str = "Jeff",
    owner_id: str = "guest_jeff",
    ai_personalities: list = None,
    bot_types: dict = None,
    starting_stack: int = 5000,
    big_blind: int = 100,
    db_path: str = "/app/data/poker_games.db",
):
    """Create a game with CaseBot and AI players.

    Args:
        player_name: Human player name
        owner_id: User ID for game ownership
        ai_personalities: List of AI personality names (LLM-powered)
        bot_types: Dict of name -> bot strategy (rule-based bots)
        starting_stack: Starting chip stack
        big_blind: Big blind amount
        db_path: Path to database

    Returns:
        game_id: The created game ID
    """
    if ai_personalities is None:
        ai_personalities = ["Batman", "Gordon Ramsay"]

    if bot_types is None:
        bot_types = {"CaseBot": "case_based"}

    # Combine all opponent names
    all_opponents = list(ai_personalities) + list(bot_types.keys())

    # Initialize game state with all players
    game_state = initialize_game_state(
        player_names=all_opponents,
        human_name=player_name,
        starting_stack=starting_stack,
        big_blind=big_blind,
    )

    # Create state machine
    blind_config = {
        'growth': 1.5,
        'hands_per_level': 6,
        'max_blind': 1000,
    }
    state_machine = PokerStateMachine(game_state=game_state, blind_config=blind_config)

    # Generate game ID
    game_id = secrets.token_urlsafe(16)

    # Advance to deal cards and post blinds
    state_machine.run_until_player_action()

    # Save bot types configuration for the game handler to restore
    # This is stored in llm_configs with a special marker
    llm_configs = {
        'default_llm_config': {'provider': 'openai', 'model': 'gpt-5-nano'},
        'player_llm_configs': {},
        'bot_types': bot_types,  # Special field for rule-based bots
    }

    # Save to database
    game_repo = GameRepository(db_path)
    game_repo.save_game(game_id, state_machine, owner_id, player_name, llm_configs=llm_configs)

    logger.info(f"Created game {game_id}")
    logger.info(f"  Human: {player_name}")
    logger.info(f"  AI Players: {ai_personalities}")
    logger.info(f"  Rule Bots: {list(bot_types.keys())}")
    logger.info(f"  Owner: {owner_id}")

    return game_id


def main():
    parser = argparse.ArgumentParser(description="Create a game with CaseBot")
    parser.add_argument("--player-name", default="Jeff", help="Human player name")
    parser.add_argument("--owner-id", default="guest_jeff", help="Owner user ID")
    parser.add_argument("--ai", nargs="*", default=["Batman", "Gordon Ramsay"],
                        help="AI personality names")
    parser.add_argument("--bots", nargs="*", default=["CaseBot:case_based"],
                        help="Rule bots as name:strategy pairs")
    parser.add_argument("--stack", type=int, default=5000, help="Starting stack")
    parser.add_argument("--blind", type=int, default=100, help="Big blind")
    parser.add_argument("--db", default="/app/data/poker_games.db", help="Database path")

    args = parser.parse_args()

    # Parse bot types
    bot_types = {}
    for bot_spec in args.bots:
        if ":" in bot_spec:
            name, strategy = bot_spec.split(":", 1)
        else:
            name = bot_spec
            strategy = "case_based"
        bot_types[name] = strategy

    game_id = create_casebot_game(
        player_name=args.player_name,
        owner_id=args.owner_id,
        ai_personalities=args.ai,
        bot_types=bot_types,
        starting_stack=args.stack,
        big_blind=args.blind,
        db_path=args.db,
    )

    print(f"\n{'='*60}")
    print(f"Game created: {game_id}")
    print(f"{'='*60}")
    print(f"\nOpen in browser:")
    print(f"  http://localhost:5173/game/{game_id}")
    print(f"\nOr on production:")
    print(f"  https://mypokerfacegame.com/game/{game_id}")
    print()


if __name__ == "__main__":
    main()
