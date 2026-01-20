"""Mistral provider implementation.

Mistral offers high-quality European AI models with competitive pricing.
Uses OpenAI-compatible API.
"""
import os
import logging
from typing import List, Dict, Any, Optional

from openai import OpenAI

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MAX_TOKENS, MISTRAL_DEFAULT_MODEL

logger = logging.getLogger(__name__)


class MistralProvider(LLMProvider):
    """Mistral API provider implementation.

    Mistral is a French AI company offering high-quality open and
    proprietary models. Uses OpenAI-compatible API.
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # Mistral doesn't have reasoning modes
        api_key: Optional[str] = None,
    ):
        """Initialize Mistral provider.

        Args:
            model: Model to use (defaults to MISTRAL_DEFAULT_MODEL)
            reasoning_effort: Ignored - Mistral doesn't have reasoning modes
            api_key: Mistral API key (defaults to MISTRAL_API_KEY env var)
        """
        self._model = model or MISTRAL_DEFAULT_MODEL
        self._client = OpenAI(
            api_key=api_key or os.environ.get("MISTRAL_API_KEY"),
            base_url="https://api.mistral.ai/v1",
            http_client=shared_http_client,
        )

    @property
    def provider_name(self) -> str:
        return "mistral"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        """Mistral doesn't support reasoning modes."""
        return None

    @property
    def image_model(self) -> str:
        """Mistral doesn't support image generation."""
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

        return self._client.chat.completions.create(**kwargs)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        seed_image_url: Optional[str] = None,
    ) -> Any:
        """Mistral doesn't support image generation."""
        raise NotImplementedError("Mistral does not support image generation")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from Mistral response."""
        usage = raw_response.usage
        if not usage:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            }

        return {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from Mistral response."""
        return raw_response.choices[0].message.content or ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from Mistral response."""
        return raw_response.choices[0].finish_reason or ""

    def extract_image_url(self, raw_response: Any) -> str:
        """Mistral doesn't support image generation."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Mistral response."""
        request_id = getattr(raw_response, 'id', None)
        if request_id is None or not isinstance(request_id, str):
            return ''
        return request_id
