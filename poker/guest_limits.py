"""Guest user limit validation functions.

All functions return (allowed: bool, error_msg: str | None).
Non-guests always get (True, None). Dev mode bypasses limits.

Constants are defined here (the poker module is the functional core) and
re-exported by flask_app.config for convenience. Do NOT import from
flask_app here — that would create a circular dependency.
"""
import os
from typing import Dict, Any, Optional, Tuple

from poker.config import is_development_mode


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


# Guest limit constants — overridable via environment variables
GUEST_MAX_HANDS = _int_env('GUEST_MAX_HANDS', 50)
GUEST_MAX_ACTIVE_GAMES = _int_env('GUEST_MAX_ACTIVE_GAMES', 1)
GUEST_MAX_OPPONENTS = _int_env('GUEST_MAX_OPPONENTS', 3)
GUEST_MAX_MESSAGES_PER_ACTION = _int_env('GUEST_MAX_MESSAGES_PER_ACTION', 1)

# Disabled in dev mode by default
GUEST_LIMITS_ENABLED = not is_development_mode()


def is_guest(user: Optional[Dict[str, Any]]) -> bool:
    """Check if the user is a guest."""
    if not user:
        return True
    return user.get('is_guest', True)


def _should_enforce(user: Optional[Dict[str, Any]]) -> bool:
    """Return True if guest limits should be enforced for this user."""
    return is_guest(user) and GUEST_LIMITS_ENABLED


def check_guest_hands_limit(user: Optional[Dict[str, Any]], hands_played: int) -> Tuple[bool, Optional[str]]:
    """Check if guest has exceeded their hand limit.

    Returns (True, None) when allowed, or (False, error_msg) when blocked.
    Always allows non-guests and dev mode.
    """
    if not _should_enforce(user):
        return (True, None)
    if hands_played >= GUEST_MAX_HANDS:
        return (False, f'Guest hand limit reached ({GUEST_MAX_HANDS} hands). Sign in with Google to continue playing.')
    return (True, None)


def check_guest_game_limit(user: Optional[Dict[str, Any]], active_game_count: int) -> Tuple[bool, Optional[str]]:
    """Check if guest can create another game.

    Returns (True, None) when allowed, or (False, error_msg) when blocked.
    """
    if not _should_enforce(user):
        return (True, None)
    if active_game_count >= GUEST_MAX_ACTIVE_GAMES:
        return (False, f'Guest users can have up to {GUEST_MAX_ACTIVE_GAMES} active game{"" if GUEST_MAX_ACTIVE_GAMES == 1 else "s"}. Sign in with Google for more.')
    return (True, None)


def validate_guest_opponent_count(user: Optional[Dict[str, Any]], count: int) -> Tuple[bool, Optional[str]]:
    """Check if guest can have this many opponents.

    Returns (True, None) when allowed, or (False, error_msg) when blocked.
    """
    if not _should_enforce(user):
        return (True, None)
    if count > GUEST_MAX_OPPONENTS:
        return (False, f'Guest users can have up to {GUEST_MAX_OPPONENTS} AI opponents. Sign in with Google for more.')
    return (True, None)


def check_guest_message_limit(user: Optional[Dict[str, Any]], messages_this_action: int) -> Tuple[bool, Optional[str]]:
    """Check if guest can send another message this action.

    Returns (True, None) when allowed, or (False, error_msg) when blocked.
    """
    if not _should_enforce(user):
        return (True, None)
    if messages_this_action >= GUEST_MAX_MESSAGES_PER_ACTION:
        return (False, 'Chat available next turn. Sign in with Google for unlimited chat.')
    return (True, None)
