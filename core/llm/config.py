"""LLM configuration defaults.

Single source of truth for model settings.
"""
import os

# =============================================================================
# OpenAI Configuration
# =============================================================================

# Default model for all LLM operations
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano")

# Fast model for quick operations (chat suggestions, theme generation, etc.)
FAST_MODEL = os.environ.get("OPENAI_FAST_MODEL", DEFAULT_MODEL)

# Default reasoning effort for GPT-5 models
# Options: 'minimal', 'low', 'medium', 'high'
DEFAULT_REASONING_EFFORT = "minimal"

# Available OpenAI models for UI selection
OPENAI_AVAILABLE_MODELS = ["gpt-5-nano", "gpt-5-mini", "gpt-5"]

# Image generation model (dall-e-3 follows prompts better but requires API access)
DEFAULT_IMAGE_MODEL = "dall-e-2"

# =============================================================================
# Groq Configuration
# =============================================================================

# Default Groq model - Llama 3.1 8B is fast and comparable to OpenAI nano tier
GROQ_DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

# Available Groq models for UI selection
# See: https://console.groq.com/docs/models
GROQ_AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",                       # Best overall, 128k context, 280 tok/s
    "llama-3.1-8b-instant",                          # Fast, good for simple tasks, 560 tok/s
    "meta-llama/llama-4-scout-17b-16e-instruct",    # Llama 4 Scout, 750 tok/s (preview)
    "qwen/qwen3-32b",                                # Qwen 3 32B, 400 tok/s (preview)
]

# =============================================================================
# Anthropic Configuration
# =============================================================================

# Default Anthropic model - Claude Sonnet 4 is the best balance of speed/quality
ANTHROPIC_DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

# Available Anthropic models for UI selection
# See: https://docs.anthropic.com/en/docs/about-claude/models
ANTHROPIC_AVAILABLE_MODELS = [
    "claude-sonnet-4-20250514",     # Best balance of speed/quality/cost
    "claude-opus-4-20250514",       # Most capable, highest cost
    "claude-haiku-3-5-20241022",    # Fastest, lowest cost
]

# =============================================================================
# Provider Registry
# =============================================================================

# All available providers
AVAILABLE_PROVIDERS = ["openai", "groq", "anthropic"]

# Models by provider for UI selection
PROVIDER_MODELS = {
    "openai": OPENAI_AVAILABLE_MODELS,
    "groq": GROQ_AVAILABLE_MODELS,
    "anthropic": ANTHROPIC_AVAILABLE_MODELS,
}

# Default model per provider
PROVIDER_DEFAULT_MODELS = {
    "openai": DEFAULT_MODEL,
    "groq": GROQ_DEFAULT_MODEL,
    "anthropic": ANTHROPIC_DEFAULT_MODEL,
}

# Provider capabilities
PROVIDER_CAPABILITIES = {
    "openai": {
        "supports_reasoning": True,
        "supports_json_mode": True,
        "supports_image_generation": True,
    },
    "groq": {
        "supports_reasoning": False,
        "supports_json_mode": True,
        "supports_image_generation": False,
    },
    "anthropic": {
        "supports_reasoning": True,  # Extended thinking
        "supports_json_mode": True,
        "supports_image_generation": False,
    },
}

# =============================================================================
# Common Settings
# =============================================================================

# Default max completion tokens (includes both reasoning + output tokens)
# Must be high enough to allow reasoning AND produce output
DEFAULT_MAX_TOKENS = 5000

# Legacy alias for backwards compatibility
AVAILABLE_MODELS = OPENAI_AVAILABLE_MODELS
