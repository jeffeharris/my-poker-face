"""DeepSeek provider implementation.

DeepSeek offers extremely cheap inference with quality comparable to GPT-4.
Uses OpenAI-compatible API.
"""
import os
import logging
from typing import List, Dict, Any, Optional

from openai import OpenAI

from .base import LLMProvider
from ..config import DEFAULT_MAX_TOKENS, DEEPSEEK_DEFAULT_MODEL

logger = logging.getLogger(__name__)


class DeepSeekProvider(LLMProvider):
    """DeepSeek API provider implementation.

    DeepSeek offers some of the cheapest LLM inference available,
    with quality competitive with GPT-4o at a fraction of the cost.
    Uses OpenAI-compatible API.
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # DeepSeek R1 supports reasoning
        api_key: Optional[str] = None,
    ):
        """Initialize DeepSeek provider.

        Args:
            model: Model to use (defaults to DEEPSEEK_DEFAULT_MODEL)
            reasoning_effort: For DeepSeek R1 reasoning model
            api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var)
        """
        self._model = model or DEEPSEEK_DEFAULT_MODEL
        self._reasoning_effort = reasoning_effort
        self._client = OpenAI(
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com/v1"
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

        return self._client.chat.completions.create(**kwargs)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
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
