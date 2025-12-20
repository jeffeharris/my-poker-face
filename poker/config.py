"""
Centralized configuration for poker game constants.
Eliminates magic numbers scattered throughout the codebase.
"""

# Betting configuration
MIN_RAISE = 10
DEFAULT_MAX_RAISE_MULTIPLIER = 3  # Max raise = min_raise * this value

# AI configuration
AI_MESSAGE_CONTEXT_LIMIT = 8  # Number of recent messages to send to AI
AI_MAX_MEMORY_LENGTH = 15  # Maximum number of messages in AI memory

# Chattiness configuration
BIG_POT_THRESHOLD = 500  # Pot size to trigger "big pot" chattiness modifier

# Fallback action weights (used when AI fails)
FALLBACK_ACTION_WEIGHTS = {
    'fold': 0.2,
    'check': 0.3,
    'call': 0.3,
    'raise': 0.2
}

# Personality-based fallback thresholds
AGGRESSION_RAISE_THRESHOLD = 0.6  # Aggression level above which raise is considered
AGGRESSION_CALL_THRESHOLD = 0.3   # Aggression level above which call is considered
