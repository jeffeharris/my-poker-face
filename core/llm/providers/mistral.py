"""Mistral provider implementation.

Mistral offers high-quality European AI models with competitive pricing.
Uses OpenAI-compatible API.
"""
import os
import logging
from typing import List, Dict, Any, Optional

import openai
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

        # Validate API key early for better error messages
        resolved_key = api_key or os.environ.get("MISTRAL_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Mistral API key not provided. Set MISTRAL_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self._client = OpenAI(
            api_key=resolved_key,
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
        """Make a chat completion request."""
        kwargs = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 1.0,
        }

        if json_format:
            kwargs["response_format"] = {"type": "json_object"}

        # Add tools if provided (Mistral uses OpenAI-compatible format)
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

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
        """Mistral doesn't support image generation."""
        raise NotImplementedError("Mistral does not support image generation")

    def is_retryable_error(self, exception: Exception) -> tuple[bool, int]:
        if isinstance(exception, openai.RateLimitError):
            return True, 30
        if isinstance(exception, (openai.APITimeoutError, openai.APIConnectionError)):
            return True, 2
        if isinstance(exception, openai.InternalServerError):
            return True, 2
        return False, 0

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

    def extract_tool_calls(self, raw_response: Any) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from Mistral response.

        Mistral uses OpenAI-compatible format for tool calls.
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
