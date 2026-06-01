"""Game state management service.

This module provides the central source of truth for all game state.
All modules that need access to game state import from here.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Global state - single source of truth
games: Dict[str, dict] = {}
game_locks: Dict[str, threading.Lock] = {}
_game_locks_lock = threading.Lock()

# Per-sandbox locks serialize cash-mode seat mutations. The human sit /
# sponsor-sit routes and the world ticker's `refresh_unseated_tables` both
# read-modify-write the same `cash_tables` seats JSON blob; without a shared
# lock a human claim can clobber a just-placed live-fill AI (stranding its
# already-debited buy-in). Per-sandbox granularity matches the ticker, which
# advances one sandbox at a time.
sandbox_locks: Dict[str, threading.Lock] = {}
_sandbox_locks_lock = threading.Lock()

# TTL-based eviction for stale games
game_last_access: Dict[str, datetime] = {}
GAME_TTL_HOURS = 2
CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes

# Background cleanup timer
_cleanup_timer: Optional[threading.Timer] = None
_cleanup_timer_lock = threading.RLock()


def _cleanup_stale_games():
    """Remove games not accessed within GAME_TTL_HOURS.

    PRH-23: mutate `game_locks` under `_game_locks_lock` (the lock it's created
    under in `get_game_lock`) and **never evict a game whose lock is currently
    held** — popping a held lock would let the next `get_game_lock` mint a fresh
    one, so two requests could progress the same game concurrently. Staleness is
    re-checked inside the lock so a game touched between the snapshot and the
    pop isn't evicted. A held-or-freshly-touched stale game is simply skipped
    this cycle and collected next time (games are persisted, so an over-eager
    eviction would only force a cold-load anyway).
    """
    cutoff = datetime.now() - timedelta(hours=GAME_TTL_HOURS)
    stale_keys = [k for k, t in game_last_access.items() if t < cutoff]
    if not stale_keys:
        return
    evicted = []
    with _game_locks_lock:
        for key in stale_keys:
            last = game_last_access.get(key)
            if last is None or last >= cutoff:
                continue  # re-accessed since the snapshot — not stale anymore
            lock = game_locks.get(key)
            if lock is not None and lock.locked():
                continue  # in-flight request holds it — don't evict under the lock
            games.pop(key, None)
            game_locks.pop(key, None)
            game_last_access.pop(key, None)
            evicted.append(key)
    if evicted:
        logger.info(f"[TTL] Evicted {len(evicted)} stale game(s): {evicted}")


def _schedule_cleanup():
    """Schedule periodic cleanup of stale games."""
    global _cleanup_timer
    with _cleanup_timer_lock:
        _cleanup_stale_games()
        _cleanup_timer = threading.Timer(CLEANUP_INTERVAL_SECONDS, _schedule_cleanup)
        _cleanup_timer.daemon = True
        _cleanup_timer.start()


def start_cleanup_timer():
    """Start the background cleanup timer.

    Called automatically on module import, but can be called
    explicitly after stop_cleanup_timer() to restart.
    """
    global _cleanup_timer
    with _cleanup_timer_lock:
        if _cleanup_timer is None:
            _schedule_cleanup()


def stop_cleanup_timer():
    """Stop the background cleanup timer.

    Useful for testing or graceful shutdown.
    """
    global _cleanup_timer
    with _cleanup_timer_lock:
        if _cleanup_timer is not None:
            _cleanup_timer.cancel()
            _cleanup_timer = None


def get_game(game_id: str) -> Optional[dict]:
    """Get game data by ID.

    Args:
        game_id: The game identifier

    Returns:
        The game data dictionary, or None if not found
    """
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
    # Stamp the game's own id into its data (PRH-9). Several consumers need
    # it from the dict alone — e.g. build_cash_mode_payload's active_loan
    # lookup keys on game_data['game_id'], which no builder set, so staked
    # players never saw their leave-breakdown panel. Doing it here is the
    # single point every warm/cold/tournament builder funnels through.
    game_data['game_id'] = game_id
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


def get_sandbox_lock(sandbox_id: str) -> threading.Lock:
    """Get or create the per-sandbox seat-mutation lock.

    Held around any read-modify-write of a sandbox's `cash_tables` seats
    — the human sit / sponsor-sit seat claims and the world ticker's
    `refresh_unseated_tables` — so they serialize instead of clobbering
    each other's last-write-wins seat blob. See `sandbox_locks`.

    `sandbox_id` may be None for legacy/unscoped callers; they share a
    single lock keyed on "" (still correct, just coarser).
    """
    key = sandbox_id or ""
    with _sandbox_locks_lock:
        if key not in sandbox_locks:
            sandbox_locks[key] = threading.Lock()
        return sandbox_locks[key]


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
