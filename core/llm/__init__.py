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

from .response import LLMResponse, ImageResponse
from .tracking import CallType, UsageTracker
from .conversation import ConversationMemory
from .client import LLMClient
from .assistant import Assistant

__all__ = [
    "LLMResponse",
    "ImageResponse",
    "CallType",
    "UsageTracker",
    "ConversationMemory",
    "LLMClient",
    "Assistant",
]
