"""xAI Grok provider implementation.

xAI offers Grok models with strong reasoning capabilities.
Uses OpenAI-compatible API.

Reasoning behavior by model:
- grok-4-fast: Maps to -reasoning or -non-reasoning variant based on effort
  - minimal → grok-4-fast-non-reasoning (no reasoning)
  - low/medium/high → grok-4-fast-reasoning (with reasoning)
- grok-3-mini: Native reasoning_effort parameter support (low/high)
- grok-3: No reasoning capability
- grok-4: Always reasons (cannot disable)
"""
import os
import logging
from typing import List, Dict, Any, Optional

from openai import OpenAI

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MAX_TOKENS, XAI_DEFAULT_MODEL

logger = logging.getLogger(__name__)

# Models that support reasoning_effort parameter (low/high only)
REASONING_EFFORT_MODELS = {"grok-3-mini"}

# Valid reasoning effort values for native xAI reasoning_effort param
VALID_REASONING_EFFORTS = {"low", "high"}

# Models that toggle between reasoning/non-reasoning variants
# Maps base model → (non-reasoning variant, reasoning variant)
TOGGLEABLE_REASONING_MODELS = {
    "grok-4-fast": ("grok-4-fast-non-reasoning", "grok-4-fast-reasoning"),
}


class XAIProvider(LLMProvider):
    """xAI Grok API provider implementation.

    xAI provides Grok models with advanced reasoning, coding, and vision
    capabilities. The API is fully OpenAI-compatible.

    Note: Only grok-3-mini supports configurable reasoning_effort (low/high).
    Other models will error if reasoning_effort is specified.
    Grok does not support image generation.
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,
        api_key: Optional[str] = None,
    ):
        """Initialize xAI provider.

        Args:
            model: Model to use (defaults to XAI_DEFAULT_MODEL from config)
            reasoning_effort: Reasoning effort level
                - For grok-4-fast: "minimal" → no reasoning, others → reasoning
                - For grok-3-mini: "low" or "high" (native parameter)
            api_key: xAI API key (defaults to XAI_API_KEY env var)
        """
        base_model = model or XAI_DEFAULT_MODEL
        self._reasoning_effort = None

        # Handle toggleable models (grok-4-fast → reasoning or non-reasoning variant)
        if base_model in TOGGLEABLE_REASONING_MODELS:
            non_reasoning, with_reasoning = TOGGLEABLE_REASONING_MODELS[base_model]
            if reasoning_effort == "minimal" or reasoning_effort is None:
                self._model = non_reasoning
            else:
                self._model = with_reasoning
        else:
            self._model = base_model
            # Only store reasoning_effort if model supports native parameter
            if self._model in REASONING_EFFORT_MODELS and reasoning_effort in VALID_REASONING_EFFORTS:
                self._reasoning_effort = reasoning_effort

        # xAI uses OpenAI-compatible API with shared HTTP client for connection reuse
        self._client = OpenAI(
            api_key=api_key or os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1",
            http_client=shared_http_client,
        )

    @property
    def provider_name(self) -> str:
        return "xai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        """Return reasoning effort if set and model supports it."""
        return self._reasoning_effort

    @property
    def image_model(self) -> str:
        """xAI doesn't support image generation."""
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

        Note: tools/tool_choice are accepted for interface compatibility but not used.
        """
        kwargs = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 1.0,
        }

        if json_format:
            kwargs["response_format"] = {"type": "json_object"}

        # Only add reasoning_effort for models that support native parameter
        if self._reasoning_effort and self._model in REASONING_EFFORT_MODELS:
            kwargs["reasoning_effort"] = self._reasoning_effort

        return self._client.chat.completions.create(**kwargs)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
    ) -> Any:
        """xAI doesn't support image generation."""
        raise NotImplementedError("xAI does not support image generation")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from xAI response.

        xAI uses OpenAI-compatible response format.
        Reasoning tokens are in completion_tokens_details.reasoning_tokens.
        """
        usage = raw_response.usage
        if not usage:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            }

        # Extract reasoning tokens if available (reasoning models)
        reasoning_tokens = 0
        if hasattr(usage, 'completion_tokens_details'):
            details = usage.completion_tokens_details
            if details:
                reasoning_tokens = getattr(details, 'reasoning_tokens', 0) or 0

        return {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens - reasoning_tokens,
            "cached_tokens": 0,  # xAI doesn't report cached tokens
            "reasoning_tokens": reasoning_tokens,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from xAI response."""
        return raw_response.choices[0].message.content or ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from xAI response."""
        return raw_response.choices[0].finish_reason or ""

    def extract_image_url(self, raw_response: Any) -> str:
        """xAI doesn't support image generation."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from xAI response."""
        request_id = getattr(raw_response, 'id', None)
        if request_id is None or not isinstance(request_id, str):
            return ''
        return request_id
