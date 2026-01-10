"""LLM configuration defaults.

Single source of truth for model settings.
"""
import os

# Default model for all LLM operations
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano")

# Fast model for quick operations (chat suggestions, theme generation, etc.)
FAST_MODEL = os.environ.get("OPENAI_FAST_MODEL", DEFAULT_MODEL)

# Default reasoning effort for GPT-5 models
# Options: 'minimal', 'low', 'medium', 'high'
DEFAULT_REASONING_EFFORT = "minimal"

# Default max completion tokens (includes both reasoning + output tokens)
# Must be high enough to allow reasoning AND produce output
DEFAULT_MAX_TOKENS = 5000

# Available models for UI selection
AVAILABLE_MODELS = ["gpt-5-nano", "gpt-5-mini", "gpt-5"]

# Image generation model (dall-e-3 follows prompts better but requires API access)
DEFAULT_IMAGE_MODEL = "dall-e-2"
