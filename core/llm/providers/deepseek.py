"""DeepSeek provider implementation.

DeepSeek offers extremely cheap inference with quality comparable to GPT-4.
Uses OpenAI-compatible API.

Reasoning behavior:
- deepseek: Maps to deepseek-chat or deepseek-reasoner based on effort
  - minimal/None → deepseek-chat (no reasoning)
  - low/medium/high → deepseek-reasoner (with reasoning)
- deepseek-chat: Direct access to chat model, supports tools and optional thinking mode
- deepseek-reasoner: Direct access to reasoning model (always reasons, NO tool support)

Tool calling notes:
- deepseek-reasoner does NOT support function/tool calling
- deepseek-chat supports tools, and can enable thinking mode via extra_body parameter
- When tools are provided with reasoning_effort, we use deepseek-chat + thinking:enabled
"""
import os
import logging
from typing import List, Dict, Any, Optional

from openai import OpenAI

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MAX_TOKENS, DEEPSEEK_DEFAULT_MODEL

logger = logging.getLogger(__name__)

# Models that toggle between reasoning/non-reasoning variants
# Maps base model → (non-reasoning variant, reasoning variant)
TOGGLEABLE_REASONING_MODELS = {
    "deepseek": ("deepseek-chat", "deepseek-reasoner"),
}


class DeepSeekProvider(LLMProvider):
    """DeepSeek API provider implementation.

    DeepSeek offers some of the cheapest LLM inference available,
    with quality competitive with GPT-4o at a fraction of the cost.
    Uses OpenAI-compatible API.

    The unified "deepseek" model automatically routes to deepseek-chat
    or deepseek-reasoner based on reasoning_effort setting.
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,
        api_key: Optional[str] = None,
    ):
        """Initialize DeepSeek provider.

        Args:
            model: Model to use (defaults to DEEPSEEK_DEFAULT_MODEL)
                - "deepseek": Auto-routes based on reasoning_effort
                - "deepseek-chat": Direct chat model (no reasoning)
                - "deepseek-reasoner": Direct reasoning model
            reasoning_effort: Controls model routing for "deepseek"
                - "minimal" or None → deepseek-chat
                - "low"/"medium"/"high" → deepseek-reasoner
            api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var)
        """
        base_model = model or DEEPSEEK_DEFAULT_MODEL
        self._reasoning_effort = reasoning_effort

        # Handle toggleable models (deepseek → chat or reasoner variant)
        if base_model in TOGGLEABLE_REASONING_MODELS:
            non_reasoning, with_reasoning = TOGGLEABLE_REASONING_MODELS[base_model]
            if reasoning_effort == "minimal" or reasoning_effort is None:
                self._model = non_reasoning
            else:
                self._model = with_reasoning
        else:
            self._model = base_model

        self._client = OpenAI(
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1",
            http_client=shared_http_client,
        )

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def image_model(self) -> str:
        """DeepSeek doesn't support image generation."""
        return "unsupported"

    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Any:
        """Make a chat completion request.

        Tool calling is supported for deepseek-chat. If tools are provided when
        using deepseek-reasoner, we automatically switch to deepseek-chat with
        thinking mode enabled to get both reasoning and tool support.
        """
        # Determine actual model to use
        # If tools requested but using reasoner (which doesn't support tools),
        # switch to deepseek-chat with thinking mode
        actual_model = self._model
        use_thinking = False

        if tools and self._model == "deepseek-reasoner":
            logger.info(
                "Tools requested with deepseek-reasoner; switching to "
                "deepseek-chat with thinking mode enabled"
            )
            actual_model = "deepseek-chat"
            use_thinking = True
        elif tools and self._reasoning_effort and self._reasoning_effort != "minimal":
            # User wants reasoning + tools, enable thinking mode on chat model
            use_thinking = True

        kwargs = {
            "model": actual_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        # Only set temperature for non-thinking mode (has no effect in thinking mode)
        if not use_thinking:
            kwargs["temperature"] = 1.0

        if json_format:
            kwargs["response_format"] = {"type": "json_object"}

        # Add tools if provided
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        # Enable thinking mode if needed (for reasoning + tools)
        if use_thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        return self._client.chat.completions.create(**kwargs)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        seed_image_url: Optional[str] = None,
        strength: float = 0.75,
        negative_prompt: Optional[str] = None,
    ) -> Any:
        """DeepSeek doesn't support image generation."""
        raise NotImplementedError("DeepSeek does not support image generation")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from DeepSeek response."""
        usage = raw_response.usage
        if not usage:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            }

        # Extract reasoning tokens for DeepSeek R1 model
        # DeepSeek R1 returns reasoning_tokens in completion_tokens_details
        reasoning_tokens = 0
        completion_details = getattr(usage, 'completion_tokens_details', None)
        if completion_details:
            reasoning_tokens = getattr(completion_details, 'reasoning_tokens', 0) or 0

        return {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens - reasoning_tokens,
            "cached_tokens": getattr(usage, 'prompt_cache_hit_tokens', 0) or 0,
            "reasoning_tokens": reasoning_tokens,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from DeepSeek response."""
        return raw_response.choices[0].message.content or ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from DeepSeek response."""
        return raw_response.choices[0].finish_reason or ""

    def extract_image_url(self, raw_response: Any) -> str:
        """DeepSeek doesn't support image generation."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from DeepSeek response."""
        request_id = getattr(raw_response, 'id', None)
        if request_id is None or not isinstance(request_id, str):
            return ''
        return request_id

    def extract_tool_calls(self, raw_response: Any) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from DeepSeek response.

        DeepSeek uses OpenAI-compatible format for tool calls.
        """
        message = raw_response.choices[0].message

        tool_calls = getattr(message, 'tool_calls', None)
        if tool_calls is None or not isinstance(tool_calls, (list, tuple)):
            return None

        if len(tool_calls) == 0:
            return None

        return [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
            }
            for tc in tool_calls
        ]
