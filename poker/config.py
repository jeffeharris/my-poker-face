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

# Memory and learning configuration
SESSION_MEMORY_HANDS = 10         # Number of hands to remember in session
MEMORY_CONTEXT_TOKENS = 150       # Max tokens for session context in prompts
OPPONENT_SUMMARY_TOKENS = 200     # Max tokens for opponent summaries in prompts
COMMENTARY_ENABLED = True         # Enable end-of-hand AI commentary
MEMORABLE_HAND_THRESHOLD = 0.7    # Impact score threshold for memorable hands (0-1)
MEMORY_TRIM_KEEP_EXCHANGES = 0    # Clear conversation memory each turn (was 4) - table chatter preserved via game_messages

# Opponent modeling thresholds
MIN_HANDS_FOR_STYLE_LABEL = 5     # Minimum hands observed before labeling play style
MIN_HANDS_FOR_SUMMARY = 3         # Minimum hands observed before generating summary
VPIP_TIGHT_THRESHOLD = 0.3        # VPIP below this = tight player
VPIP_LOOSE_THRESHOLD = 0.5        # VPIP above this = loose player (plays many hands)
VPIP_VERY_SELECTIVE = 0.2         # VPIP below this = very selective
AGGRESSION_FACTOR_HIGH = 1.5      # AF above this = aggressive player
AGGRESSION_FACTOR_VERY_HIGH = 2.0 # AF above this = very aggressive
AGGRESSION_FACTOR_LOW = 0.5       # AF below this = passive player
