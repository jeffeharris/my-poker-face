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
    "llama-3.3-70b-versatile",                   # Best overall, 131k context, 280 tok/s
    "llama-3.1-8b-instant",                      # Fast, good for simple tasks, 560 tok/s
    "openai/gpt-oss-20b",                        # GPT OSS 20B, 1000 tok/s
    "openai/gpt-oss-120b",                       # GPT OSS 120B, 500 tok/s
    "meta-llama/llama-4-scout-17b-16e-instruct", # Llama 4 Scout, 750 tok/s (lab)
    "qwen/qwen3-32b",                            # Qwen3 32B, 400 tok/s (lab)
]

# =============================================================================
# Anthropic Configuration
# =============================================================================

# Default Anthropic model - Claude Sonnet 4.5 is the best balance of speed/quality
ANTHROPIC_DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

# Available Anthropic models for UI selection
# See: https://docs.anthropic.com/en/docs/about-claude/models
ANTHROPIC_AVAILABLE_MODELS = [
    "claude-sonnet-4-5-20250929",   # Best balance, complex agents/coding ($3/$15 per M tokens)
    "claude-opus-4-5-20251101",     # Most capable, highest cost ($15/$75 per M tokens)
    "claude-haiku-4-5-20251001",    # Fastest, lowest cost ($1/$5 per M tokens)
]

# =============================================================================
# DeepSeek Configuration
# =============================================================================

# Default DeepSeek model - unified model that auto-routes based on reasoning_effort
DEEPSEEK_DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek")

# Available DeepSeek models for UI selection
# See: https://platform.deepseek.com/api-docs/
DEEPSEEK_AVAILABLE_MODELS = [
    "deepseek",             # Unified - routes to chat or reasoner based on reasoning_effort
    "deepseek-chat",        # V3 - Best value, $0.28/$0.42 per M tokens (no reasoning)
    "deepseek-reasoner",    # R1 - Reasoning model, $0.55/$2.19 per M tokens
]

# =============================================================================
# Mistral Configuration
# =============================================================================

# Default Mistral model - Small is fast and cheap
MISTRAL_DEFAULT_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

# Available Mistral models for UI selection
# See: https://docs.mistral.ai/getting-started/models/
MISTRAL_AVAILABLE_MODELS = [
    "mistral-small-latest",     # Fast, cheap ($0.20/$0.60 per M tokens)
    "mistral-medium-latest",    # Balanced
    "mistral-large-latest",     # Most capable ($2/$6 per M tokens)
]

# =============================================================================
# Google Gemini Configuration
# =============================================================================

# Default Google model - Flash is very cheap with good quality
GOOGLE_DEFAULT_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")

# Available Google models for UI selection
# See: https://ai.google.dev/gemini-api/docs/models
GOOGLE_AVAILABLE_MODELS = [
    "gemini-2.0-flash",         # Very cheap ($0.10/$0.40 per M tokens)
    "gemini-2.5-flash",         # Better quality ($0.30/$2.50 per M tokens)
    "gemini-2.5-pro",           # GPT-4o competitor ($1.25/$10 per M tokens)
]

# =============================================================================
# xAI Configuration
# =============================================================================

# Default xAI model - grok-4-fast is fast and cheap with optional reasoning
XAI_DEFAULT_MODEL = os.environ.get("XAI_MODEL", "grok-4-fast")

# Available xAI models for UI selection
# See: https://docs.x.ai/docs/models
#
# Reasoning behavior (controlled by reasoning_effort setting):
# - grok-4-fast: Maps to -reasoning or -non-reasoning variant based on effort
# - grok-3-mini: Native reasoning_effort support (low/high)
# - grok-3: No reasoning capability
# - grok-4: Always reasons (flagship model)
XAI_AVAILABLE_MODELS = [
    "grok-4-fast",                  # Fast, toggles reasoning via effort ($0.20/$0.50)
    "grok-3-mini",                  # Controllable reasoning (low/high, $0.30/$0.50)
    "grok-3",                       # No reasoning ($3/$15)
    "grok-4",                       # Flagship, always reasons ($3/$15)
]

# =============================================================================
# Provider Registry
# =============================================================================

# All available providers
AVAILABLE_PROVIDERS = ["openai", "groq", "anthropic", "deepseek", "mistral", "google", "xai"]

# =============================================================================
# Default Enabled Models (for new deployments)
# =============================================================================
# Models listed here will be enabled by default when the database is first created.
# All other models will be seeded but disabled. Admins can enable/disable models
# via the admin dashboard after deployment.
#
# To enable all models by default, set this to None or an empty dict.
# To restrict to specific models, list them by provider.
DEFAULT_ENABLED_MODELS = {
    "openai": ["gpt-5-nano"],           # Cheapest OpenAI model
    "groq": ["llama-3.1-8b-instant"],   # Fast and free-tier friendly
}

# Models by provider for UI selection
PROVIDER_MODELS = {
    "openai": OPENAI_AVAILABLE_MODELS,
    "groq": GROQ_AVAILABLE_MODELS,
    "anthropic": ANTHROPIC_AVAILABLE_MODELS,
    "deepseek": DEEPSEEK_AVAILABLE_MODELS,
    "mistral": MISTRAL_AVAILABLE_MODELS,
    "google": GOOGLE_AVAILABLE_MODELS,
    "xai": XAI_AVAILABLE_MODELS,
}

# Default model per provider
PROVIDER_DEFAULT_MODELS = {
    "openai": DEFAULT_MODEL,
    "groq": GROQ_DEFAULT_MODEL,
    "anthropic": ANTHROPIC_DEFAULT_MODEL,
    "deepseek": DEEPSEEK_DEFAULT_MODEL,
    "mistral": MISTRAL_DEFAULT_MODEL,
    "google": GOOGLE_DEFAULT_MODEL,
    "xai": XAI_DEFAULT_MODEL,
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
    "deepseek": {
        "supports_reasoning": True,  # R1 model
        "supports_json_mode": True,
        "supports_image_generation": False,
    },
    "mistral": {
        "supports_reasoning": False,
        "supports_json_mode": True,
        "supports_image_generation": False,
    },
    "google": {
        "supports_reasoning": True,  # Thinking mode
        "supports_json_mode": True,
        "supports_image_generation": True,  # Imagen
    },
    "xai": {
        "supports_reasoning": True,  # grok-3-mini only (low/high)
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
