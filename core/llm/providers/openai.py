"""OpenAI provider implementation."""
import os
import logging
from typing import List, Dict, Any, Optional

from openai import OpenAI

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MODEL, DEFAULT_IMAGE_MODEL, DEFAULT_MAX_TOKENS, DEFAULT_REASONING_EFFORT

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI API provider implementation."""

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,
        api_key: Optional[str] = None,
    ):
        """Initialize OpenAI provider.

        Args:
            model: Model to use (defaults to DEFAULT_MODEL from config)
            reasoning_effort: Reasoning effort for GPT-5 models ('minimal', 'low', 'medium', 'high')
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
        """
        self._model = model or DEFAULT_MODEL
        self._reasoning_effort = reasoning_effort or DEFAULT_REASONING_EFFORT
        self._client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            http_client=shared_http_client,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        """Return the reasoning effort for GPT-5 models."""
        if self._model.startswith("gpt-5"):
            return self._reasoning_effort
        return None

    @property
    def image_model(self) -> str:
        """Return the image generation model name."""
        return DEFAULT_IMAGE_MODEL

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
            "max_completion_tokens": max_tokens,
        }

        if json_format:
            kwargs["response_format"] = {"type": "json_object"}

        # GPT-5 models use reasoning_effort instead of temperature
        if self._model.startswith("gpt-5"):
            kwargs["reasoning_effort"] = self._reasoning_effort
        else:
            # Legacy models (shouldn't be used, but handle gracefully)
            kwargs["temperature"] = 1.0

        return self._client.chat.completions.create(**kwargs)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
    ) -> Any:
        """Generate an image using DALL-E."""
        return self._client.images.generate(
            model=DEFAULT_IMAGE_MODEL,
            prompt=prompt,
            n=n,
            size=size,
        )

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from OpenAI response."""
        usage = raw_response.usage
        if not usage:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            }

        # Extract reasoning tokens if available (GPT-5 models)
        reasoning_tokens = 0
        if hasattr(usage, 'completion_tokens_details') and usage.completion_tokens_details:
            reasoning_tokens = getattr(usage.completion_tokens_details, 'reasoning_tokens', 0) or 0

        output_tokens = usage.completion_tokens - reasoning_tokens

        # Extract cached tokens if available
        cached_tokens = 0
        if hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
            cached_tokens = getattr(usage.prompt_tokens_details, 'cached_tokens', 0) or 0

        return {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from OpenAI response."""
        return raw_response.choices[0].message.content or ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from OpenAI response."""
        return raw_response.choices[0].finish_reason or ""

    def extract_image_url(self, raw_response: Any) -> str:
        """Extract image URL from DALL-E response."""
        return raw_response.data[0].url or ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from OpenAI response.

        OpenAI returns IDs like 'chatcmpl-abc123' for completions
        and similar formats for image generation.
        """
        request_id = getattr(raw_response, 'id', None)
        # Ensure we return a string (handles Mock objects in tests)
        if request_id is None or not isinstance(request_id, str):
            return ''
        return request_id
