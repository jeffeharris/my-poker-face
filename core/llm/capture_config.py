"""Configuration for LLM prompt capture (playground feature).

This module controls whether and when prompts/responses are captured
to the prompt_captures table for debugging and replay.

All captures go through the unified LLMClient with enricher callbacks that add
full game state (hand, board, pot, stack, valid actions) when available.

Configuration Sources (in order of priority):
    1. Database app_settings table (updated via admin dashboard)
    2. Environment variables (fallback defaults)

Environment Variables:
    LLM_PROMPT_CAPTURE: Capture mode
        - "disabled" (default): No automatic capture
        - "all": Capture all LLM calls with full context
        - "all_except_decisions": Capture all except player_decision (reduces volume)

    LLM_PROMPT_RETENTION_DAYS: Days to keep captured prompts
        - 0 (default): Keep forever (no automatic cleanup)
        - N: Delete captures older than N days
"""
import os
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import CallType

logger = logging.getLogger(__name__)

# Capture mode constants
CAPTURE_DISABLED = "disabled"
CAPTURE_ALL = "all"
CAPTURE_ALL_EXCEPT_DECISIONS = "all_except_decisions"

# Environment variable defaults (used when no DB value exists)
_ENV_CAPTURE_MODE = os.environ.get("LLM_PROMPT_CAPTURE", CAPTURE_DISABLED).lower()
_ENV_RETENTION_DAYS = int(os.environ.get("LLM_PROMPT_RETENTION_DAYS", "0"))


def _get_config_repo():
    """Get the config repository, handling import lazily to avoid circular imports."""
    try:
        from flask_app.extensions import get_repository_factory
        return get_repository_factory().config
    except ImportError:
        # Not running in Flask context
        return None
    except Exception:
        return None


def get_capture_mode() -> str:
    """Get the current capture mode from DB, falling back to env var.

    Returns:
        One of: 'disabled', 'all', 'all_except_decisions'
    """
    config_repo = _get_config_repo()
    if config_repo:
        db_value = config_repo.get_setting('LLM_PROMPT_CAPTURE', None)
        if db_value is not None:
            return db_value.lower()
    return _ENV_CAPTURE_MODE


def get_retention_days() -> int:
    """Get the configured retention period in days from DB, falling back to env var.

    Returns:
        Number of days to keep captures (0 = unlimited)
    """
    config_repo = _get_config_repo()
    if config_repo:
        db_value = config_repo.get_setting('LLM_PROMPT_RETENTION_DAYS', None)
        if db_value is not None:
            try:
                return int(db_value)
            except ValueError:
                logger.warning(f"Invalid LLM_PROMPT_RETENTION_DAYS value in DB: {db_value}")
    return _ENV_RETENTION_DAYS


def get_env_defaults() -> dict:
    """Get the environment variable defaults (for UI display).

    Returns:
        Dict with env_capture_mode and env_retention_days
    """
    return {
        'capture_mode': _ENV_CAPTURE_MODE,
        'retention_days': _ENV_RETENTION_DAYS,
    }


def should_capture_prompt(call_type: "CallType", debug_mode: bool = False) -> bool:
    """Determine if a prompt should be captured based on configuration.

    Args:
        call_type: The type of LLM call (player_decision, commentary, etc.)
        debug_mode: True if game has debug capture explicitly enabled

    Returns:
        True if the prompt should be captured to prompt_captures table
    """
    # Import here to avoid circular imports
    from .tracking import CallType

    # Get current capture mode (queries DB each time for instant updates)
    capture_mode = get_capture_mode()

    # Never capture if disabled
    if capture_mode == CAPTURE_DISABLED:
        return False

    # Capture everything if mode is "all"
    if capture_mode == CAPTURE_ALL:
        return True

    # Capture all except player decisions (unless debug mode enabled for that game)
    if capture_mode == CAPTURE_ALL_EXCEPT_DECISIONS:
        if call_type == CallType.PLAYER_DECISION:
            # Only capture player decisions if debug mode is on
            return debug_mode
        # Capture all other call types
        return True

    # Unknown mode - log warning and don't capture
    logger.warning(f"Unknown LLM_PROMPT_CAPTURE mode: {capture_mode}")
    return False


# Log initial configuration on module load
_initial_mode = get_capture_mode()
if _initial_mode != CAPTURE_DISABLED:
    _initial_retention = get_retention_days()
    retention_msg = f"{_initial_retention} days" if _initial_retention > 0 else "unlimited"
    logger.info(f"LLM prompt capture enabled: mode={_initial_mode}, retention={retention_msg}")
