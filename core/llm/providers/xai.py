"""xAI Grok provider implementation.

xAI offers Grok models with strong reasoning capabilities.
Uses OpenAI-compatible API.

Note on reasoning_effort:
- Only grok-3-mini supports the reasoning_effort parameter
- Valid values are "low" or "high" only
- Other models (grok-3, grok-4, etc.) do NOT support reasoning_effort
"""
import os
import logging
from typing import List, Dict, Any, Optional

from openai import OpenAI

from .base import LLMProvider
from ..config import DEFAULT_MAX_TOKENS, XAI_DEFAULT_MODEL

logger = logging.getLogger(__name__)

# Models that support reasoning_effort parameter (low/high only)
REASONING_MODELS = {"grok-3-mini"}

# Valid reasoning effort values for xAI (different from OpenAI)
VALID_REASONING_EFFORTS = {"low", "high"}


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
            reasoning_effort: Reasoning effort ("low" or "high", grok-3-mini only)
            api_key: xAI API key (defaults to XAI_API_KEY env var)
        """
        self._model = model or XAI_DEFAULT_MODEL

        # Only store reasoning_effort if model supports it and value is valid
        if self._model in REASONING_MODELS and reasoning_effort in VALID_REASONING_EFFORTS:
            self._reasoning_effort = reasoning_effort
        else:
            self._reasoning_effort = None

        # xAI uses OpenAI-compatible API
        self._client = OpenAI(
            api_key=api_key or os.environ.get("XAI_API_KEY"),
            base_url="https://api.x.ai/v1"
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
    ) -> Any:
        """Make a chat completion request."""
        kwargs = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 1.0,
        }

        if json_format:
            kwargs["response_format"] = {"type": "json_object"}

        # Only add reasoning_effort for models that support it
        if self._reasoning_effort and self._model in REASONING_MODELS:
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
            "output_tokens": usage.completion_tokens,
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
