"""Game state management service.

This module provides the central source of truth for all game state.
All modules that need access to game state import from here.
"""

import threading
import logging
from typing import Dict, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Global state - single source of truth
games: Dict[str, dict] = {}
game_locks: Dict[str, threading.Lock] = {}
_game_locks_lock = threading.Lock()

# TTL-based eviction for stale games
game_last_access: Dict[str, datetime] = {}
GAME_TTL_HOURS = 2


def _cleanup_stale_games():
    """Remove games not accessed within GAME_TTL_HOURS."""
    cutoff = datetime.now() - timedelta(hours=GAME_TTL_HOURS)
    stale_keys = [k for k, t in game_last_access.items() if t < cutoff]
    for key in stale_keys:
        games.pop(key, None)
        game_locks.pop(key, None)
        game_last_access.pop(key, None)
    if stale_keys:
        logger.info(f"[TTL] Evicted {len(stale_keys)} stale game(s): {stale_keys}")


def get_game(game_id: str) -> Optional[dict]:
    """Get game data by ID.

    Args:
        game_id: The game identifier

    Returns:
        The game data dictionary, or None if not found
    """
    _cleanup_stale_games()
    game_data = games.get(game_id)
    if game_data is not None:
        game_last_access[game_id] = datetime.now()
    return game_data


def set_game(game_id: str, game_data: dict) -> None:
    """Store or update game data.

    Args:
        game_id: The game identifier
        game_data: The game data dictionary
    """
    _cleanup_stale_games()
    games[game_id] = game_data
    game_last_access[game_id] = datetime.now()


def delete_game(game_id: str) -> Optional[dict]:
    """Remove a game from memory.

    Args:
        game_id: The game identifier

    Returns:
        The removed game data, or None if not found
    """
    game_last_access.pop(game_id, None)
    game_locks.pop(game_id, None)
    return games.pop(game_id, None)


def list_game_ids() -> list:
    """Get a list of all active game IDs.

    Returns:
        List of game IDs currently in memory
    """
    return list(games.keys())


def get_game_lock(game_id: str) -> threading.Lock:
    """Get or create a lock for a specific game.

    Used to serialize progress_game calls and prevent race conditions.

    Args:
        game_id: The game identifier

    Returns:
        A threading.Lock for the game
    """
    with _game_locks_lock:
        if game_id not in game_locks:
            game_locks[game_id] = threading.Lock()
        return game_locks[game_id]


def get_game_owner_info(game_id: str) -> tuple:
    """Get owner_id and owner_name for a game.

    Args:
        game_id: The game identifier

    Returns:
        Tuple of (owner_id, owner_name)
    """
    game_data = games.get(game_id, {})
    return game_data.get('owner_id'), game_data.get('owner_name')


def get_state_machine(game_id: str):
    """Get the state machine for a game.

    Args:
        game_id: The game identifier

    Returns:
        The state machine, or None if game not found
    """
    game_data = games.get(game_id)
    if game_data:
        return game_data.get('state_machine')
    return None


def get_ai_controllers(game_id: str) -> dict:
    """Get AI controllers for a game.

    Args:
        game_id: The game identifier

    Returns:
        Dictionary of player name -> AIPlayerController
    """
    game_data = games.get(game_id)
    if game_data:
        return game_data.get('ai_controllers', {})
    return {}


def get_messages(game_id: str) -> list:
    """Get messages for a game.

    Args:
        game_id: The game identifier

    Returns:
        List of message dictionaries
    """
    game_data = games.get(game_id)
    if game_data:
        return game_data.get('messages', [])
    return []


def add_message(game_id: str, message: dict) -> None:
    """Add a message to a game's message list.

    Args:
        game_id: The game identifier
        message: The message dictionary to add
    """
    game_data = games.get(game_id)
    if game_data:
        if 'messages' not in game_data:
            game_data['messages'] = []
        game_data['messages'].append(message)
