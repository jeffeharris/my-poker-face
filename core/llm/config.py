"""LLM configuration defaults.

Single source of truth for model settings.
"""

import os

# PRH-18: short per-call HTTP timeout (seconds) for in-game / ticker LLM calls
# (player decision + narration), distinct from the long LLM_HTTP_TIMEOUT (600s)
# used for batch/experiment work. These calls run synchronously inside a hand,
# often under a per-game or per-sandbox lock, so a stalled provider would
# otherwise hang the hand (and freeze the world ticker for everyone). Bounding
# it makes a stall fail fast into the deterministic fallback. Override with
# LLM_INGAME_TIMEOUT.
INGAME_LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_INGAME_TIMEOUT", "30.0"))

# PRH-21: the world-ticker narration (vice / side-hustle) runs synchronously in
# the single shared ticker greenlet that advances EVERY active sandbox, so a
# stall there pauses the lobby for ALL users — a wider blast radius than a single
# hand. It's pure flavor, so give it a TIGHTER bound than the in-game decision
# timeout. Override with LLM_TICKER_TIMEOUT.
TICKER_LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TICKER_TIMEOUT", "10.0"))

# User-facing FAST-tier calls (chat suggestions, beat cleanup) that a player is
# actively waiting on in a request. Without an explicit per-call timeout these
# build LLMClients that fall through to the 600s shared-httpx default, so a
# provider stall hangs the user's request for minutes. Bound it to a snappy
# ceiling. Override with LLM_FAST_TIMEOUT.
FAST_LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_FAST_TIMEOUT", "15.0"))

# =============================================================================
# OpenAI Configuration
# =============================================================================

# DEFAULT tier — commentary, end-of-hand narration, theme/image-description, and
# game-support tasks that want coherent prose. gpt-5-mini (reasoning_effort
# 'minimal') is the baseline. Override per-tier via env or admin Settings (DB
# app_settings).
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "gpt-5-mini")
DEFAULT_PROVIDER = os.environ.get("DEFAULT_PROVIDER", "openai")

# FAST tier — quick, latency-sensitive flavor (chat suggestions, categorization,
# vice/side-hustle narration, beat cleanup). Code default is groq
# llama-3.1-8b-instant: sub-second and cheap. NOTE: prod overrides this to
# xAI grok-4-fast (via DB app_settings) for more entertaining/coherent lines —
# decoupled from DEFAULT so changing the DEFAULT model doesn't drag FAST with it.
FAST_MODEL = os.environ.get("FAST_MODEL", "llama-3.1-8b-instant")
FAST_PROVIDER = os.environ.get("FAST_PROVIDER", "groq")

# Model for assistants (experiment designer, etc.)
# Default: DeepSeek Chat (supports tools + optional thinking mode)
# Note: deepseek-reasoner does NOT support tool calling
ASSISTANT_MODEL = os.environ.get("ASSISTANT_MODEL", "deepseek-chat")
ASSISTANT_PROVIDER = os.environ.get("ASSISTANT_PROVIDER", "deepseek")

# Default reasoning effort for GPT-5 models
# Options: 'minimal', 'low', 'medium', 'high'
DEFAULT_REASONING_EFFORT = "minimal"

# Available OpenAI models for UI selection
OPENAI_AVAILABLE_MODELS = ["gpt-5-nano", "gpt-5-mini", "gpt-5", "dall-e-2"]

# IMAGE tier — avatar / character image generation. Runware FLUX.2 [dev]
# (runware:400@1) is the default: higher quality than Schnell, far cheaper than
# DALL-E. Override via env or admin Settings.
IMAGE_PROVIDER = os.environ.get("IMAGE_PROVIDER", "runware")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "runware:400@1")

# =============================================================================
# Groq Configuration
# =============================================================================

# Default Groq model - Llama 3.1 8B is fast and comparable to OpenAI nano tier
GROQ_DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

# Available Groq models for UI selection
# See: https://console.groq.com/docs/models
GROQ_AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",  # Best overall, 131k context, 280 tok/s
    "llama-3.1-8b-instant",  # Fast, good for simple tasks, 560 tok/s
    "openai/gpt-oss-20b",  # GPT OSS 20B, 1000 tok/s
    "openai/gpt-oss-120b",  # GPT OSS 120B, 500 tok/s
    "meta-llama/llama-4-scout-17b-16e-instruct",  # Llama 4 Scout, 750 tok/s (lab)
    "qwen/qwen3-32b",  # Qwen3 32B, 400 tok/s (lab)
]

# =============================================================================
# Anthropic Configuration
# =============================================================================

# Default Anthropic model - Claude Sonnet 4.5 is the best balance of speed/quality
ANTHROPIC_DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")

# Available Anthropic models for UI selection
# See: https://docs.anthropic.com/en/docs/about-claude/models
ANTHROPIC_AVAILABLE_MODELS = [
    "claude-sonnet-4-5-20250929",  # Best balance, complex agents/coding ($3/$15 per M tokens)
    "claude-opus-4-5-20251101",  # Most capable, highest cost ($15/$75 per M tokens)
    "claude-haiku-4-5-20251001",  # Fastest, lowest cost ($1/$5 per M tokens)
]

# =============================================================================
# DeepSeek Configuration
# =============================================================================

# Default DeepSeek model - unified model that auto-routes based on reasoning_effort
DEEPSEEK_DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek")

# Available DeepSeek models for UI selection
# See: https://platform.deepseek.com/api-docs/
DEEPSEEK_AVAILABLE_MODELS = [
    "deepseek",  # Unified - routes to chat or reasoner based on reasoning_effort
    "deepseek-chat",  # V3 - Best value, $0.28/$0.42 per M tokens (no reasoning)
    "deepseek-reasoner",  # R1 - Reasoning model, $0.55/$2.19 per M tokens
]

# =============================================================================
# Mistral Configuration
# =============================================================================

# Default Mistral model - Small is fast and cheap
MISTRAL_DEFAULT_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

# Available Mistral models for UI selection
# See: https://docs.mistral.ai/getting-started/models/
MISTRAL_AVAILABLE_MODELS = [
    "mistral-small-latest",  # Fast, cheap ($0.20/$0.60 per M tokens)
    "mistral-medium-latest",  # Balanced
    "mistral-large-latest",  # Most capable ($2/$6 per M tokens)
    "labs-mistral-small-creative",  # Creative variant ($0.10/$0.30 per M tokens)
    "ministral-3b-latest",  # Tiny, cheapest ($0.10/$0.10 per M tokens)
    "ministral-8b-latest",  # Small ($0.15/$0.15 per M tokens)
]

# =============================================================================
# Google Gemini Configuration
# =============================================================================

# Default Google model - Flash is very cheap with good quality
GOOGLE_DEFAULT_MODEL = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")

# Available Google models for UI selection
# See: https://ai.google.dev/gemini-api/docs/models
GOOGLE_AVAILABLE_MODELS = [
    "gemini-2.0-flash",  # Very cheap ($0.10/$0.40 per M tokens)
    "gemini-2.5-flash",  # Better quality ($0.30/$2.50 per M tokens)
    "gemini-2.5-pro",  # GPT-4o competitor ($1.25/$10 per M tokens)
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
    "grok-4-fast",  # Fast, toggles reasoning via effort ($0.20/$0.50)
    "grok-3-mini",  # Controllable reasoning (low/high, $0.30/$0.50)
    "grok-3",  # No reasoning ($3/$15)
    "grok-4",  # Flagship, always reasons ($3/$15)
]

# =============================================================================
# Runware Configuration (Image-Only Provider)
# =============================================================================

# Default Runware model - FLUX Schnell is fast and good quality
RUNWARE_DEFAULT_MODEL = os.environ.get("RUNWARE_MODEL", "runware:100@1")

# Available Runware models for image generation
# See: https://runware.ai/docs/image-inference/api-reference
RUNWARE_AVAILABLE_MODELS = [
    "runware:100@1",  # FLUX.1 [schnell] - fast ($0.0013/1024x1024)
    "runware:400@1",  # FLUX.2 [dev] - higher quality ($0.0038/1024x1024)
    "runware:400@4",  # FLUX.2 [klein] 4B - fast ($0.0006/1024x1024)
    "runware:z-image@turbo",  # Z-Image Turbo ($0.0032/1024x1024)
]

# =============================================================================
# Pollinations Configuration (Image-Only Provider)
# =============================================================================

# Default Pollinations model - flux is cheap and high quality
POLLINATIONS_DEFAULT_MODEL = os.environ.get("POLLINATIONS_MODEL", "flux")

# Rate limit delay between requests (in seconds)
# Tier delays: anonymous=15, seed=5, flower=3, nectar=0
# Set via POLLINATIONS_RATE_LIMIT_DELAY env var or defaults to 5 (seed tier with API key)
POLLINATIONS_RATE_LIMIT_DELAY = float(os.environ.get("POLLINATIONS_RATE_LIMIT_DELAY", "5"))

# Available Pollinations models for image generation
# See: https://pollinations.ai/pricing
POLLINATIONS_AVAILABLE_MODELS = [
    "flux",  # Flux Schnell - fast, good quality ($0.0002/image)
    "zimage",  # Z-Image Turbo - fast ($0.0002/image)
    "turbo",  # SDXL Turbo ($0.0003/image)
    "klein",  # FLUX.2 Klein 4B - supports img2img ($0.008/image)
    "seedream",  # Seedream 4.0 ($0.03/image)
    "kontext",  # FLUX.1 Kontext ($0.04/image)
    "gptimage",  # GPT Image 1 Mini ($0.008/image approx)
    "nanobanana",  # NanoBanana
]

# =============================================================================
# Provider Registry
# =============================================================================

# All available providers
AVAILABLE_PROVIDERS = [
    "openai",
    "groq",
    "anthropic",
    "deepseek",
    "mistral",
    "google",
    "xai",
    "pollinations",
    "runware",
]

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
    # gpt-5-mini = DEFAULT tier; gpt-5-nano kept enabled (cheap fallback); dall-e-2
    # kept for image fallback though IMAGE now defaults to runware.
    "openai": ["gpt-5-mini", "gpt-5-nano", "dall-e-2"],
    "groq": ["llama-3.1-8b-instant"],  # FAST tier code default — fast + cheap
    "xai": ["grok-4-fast"],  # prod FAST override — toggles reasoning via effort
    # FLUX.2 [dev] = IMAGE tier default; Schnell kept enabled as a fast fallback.
    "runware": ["runware:400@1", "runware:100@1"],
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
    "pollinations": POLLINATIONS_AVAILABLE_MODELS,
    "runware": RUNWARE_AVAILABLE_MODELS,
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
    "pollinations": POLLINATIONS_DEFAULT_MODEL,
    "runware": RUNWARE_DEFAULT_MODEL,
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
    "pollinations": {
        "supports_reasoning": False,
        "supports_json_mode": False,
        "supports_image_generation": True,  # Image-only provider
        "image_only": True,  # Flag for image-only providers
    },
    "runware": {
        "supports_reasoning": False,
        "supports_json_mode": False,
        "supports_image_generation": True,  # Image-only provider
        "image_only": True,  # Flag for image-only providers
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
