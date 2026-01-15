"""Configuration for LLM prompt capture (playground feature).

This module controls whether and when prompts/responses are captured
to the prompt_captures table for debugging and replay.

Environment Variables:
    LLM_PROMPT_CAPTURE: Capture mode
        - "disabled" (default): No automatic capture
        - "all": Capture all LLM calls
        - "all_except_decisions": Capture all except player_decision (unless debug_mode)

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

# Configuration from environment
PROMPT_CAPTURE_MODE = os.environ.get("LLM_PROMPT_CAPTURE", CAPTURE_DISABLED).lower()
PROMPT_RETENTION_DAYS = int(os.environ.get("LLM_PROMPT_RETENTION_DAYS", "0"))


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

    # Never capture if disabled
    if PROMPT_CAPTURE_MODE == CAPTURE_DISABLED:
        return False

    # Capture everything if mode is "all"
    if PROMPT_CAPTURE_MODE == CAPTURE_ALL:
        return True

    # Capture all except player decisions (unless debug mode enabled for that game)
    if PROMPT_CAPTURE_MODE == CAPTURE_ALL_EXCEPT_DECISIONS:
        if call_type == CallType.PLAYER_DECISION:
            # Only capture player decisions if debug mode is on
            return debug_mode
        # Capture all other call types
        return True

    # Unknown mode - log warning and don't capture
    logger.warning(f"Unknown LLM_PROMPT_CAPTURE mode: {PROMPT_CAPTURE_MODE}")
    return False


def get_retention_days() -> int:
    """Get the configured retention period in days.

    Returns:
        Number of days to keep captures (0 = unlimited)
    """
    return PROMPT_RETENTION_DAYS


# Log configuration on module load
if PROMPT_CAPTURE_MODE != CAPTURE_DISABLED:
    retention_msg = f"{PROMPT_RETENTION_DAYS} days" if PROMPT_RETENTION_DAYS > 0 else "unlimited"
    logger.info(f"LLM prompt capture enabled: mode={PROMPT_CAPTURE_MODE}, retention={retention_msg}")
