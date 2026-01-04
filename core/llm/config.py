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
DEFAULT_REASONING_EFFORT = "low"

# Available models for UI selection
AVAILABLE_MODELS = ["gpt-5-nano", "gpt-5-mini", "gpt-5"]

# Image generation model
DEFAULT_IMAGE_MODEL = "dall-e-2"
