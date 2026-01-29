"""Unified LLM abstraction with built-in tracking.

This module provides a clean abstraction over LLM providers with:
- Unified interface for completions and image generation
- Built-in usage tracking for cost analysis
- Conversation memory management
- Provider abstraction (currently OpenAI, extensible)

Quick Start:
    # Stateful conversation (like old OpenAILLMAssistant)
    from core.llm import Assistant, CallType

    assistant = Assistant(
        system_prompt="You are a poker player...",
        call_type=CallType.PLAYER_DECISION,
        game_id="game_123"
    )
    response = assistant.chat("What's your move?", json_format=True)

    # Stateless one-off call
    from core.llm import LLMClient, CallType

    client = LLMClient()
    response = client.complete(
        messages=[{"role": "user", "content": "Hello"}],
        call_type=CallType.CHAT_SUGGESTION
    )
"""

from .config import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    FAST_MODEL,
    FAST_PROVIDER,
    ASSISTANT_MODEL,
    ASSISTANT_PROVIDER,
    DEFAULT_REASONING_EFFORT,
    AVAILABLE_MODELS,
    IMAGE_PROVIDER,
    IMAGE_MODEL,
    # Provider config
    AVAILABLE_PROVIDERS,
    PROVIDER_MODELS,
    PROVIDER_DEFAULT_MODELS,
    PROVIDER_CAPABILITIES,
    GROQ_DEFAULT_MODEL,
    GROQ_AVAILABLE_MODELS,
    ANTHROPIC_DEFAULT_MODEL,
    ANTHROPIC_AVAILABLE_MODELS,
)
from .response import LLMResponse, ImageResponse
from .tracking import CallType, UsageTracker
from .conversation import ConversationMemory
from .client import LLMClient
from .assistant import Assistant
from .tokenizer import count_tokens

__all__ = [
    # Config
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "FAST_MODEL",
    "FAST_PROVIDER",
    "ASSISTANT_MODEL",
    "ASSISTANT_PROVIDER",
    "DEFAULT_REASONING_EFFORT",
    "AVAILABLE_MODELS",
    "IMAGE_PROVIDER",
    "IMAGE_MODEL",
    # Provider config
    "AVAILABLE_PROVIDERS",
    "PROVIDER_MODELS",
    "PROVIDER_DEFAULT_MODELS",
    "PROVIDER_CAPABILITIES",
    "GROQ_DEFAULT_MODEL",
    "GROQ_AVAILABLE_MODELS",
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_AVAILABLE_MODELS",
    # Classes
    "LLMResponse",
    "ImageResponse",
    "CallType",
    "UsageTracker",
    "ConversationMemory",
    "LLMClient",
    "Assistant",
    # Utilities
    "count_tokens",
]
