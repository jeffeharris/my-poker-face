"""Groq provider implementation.

Groq provides extremely fast inference for open-source models like Llama.
Uses OpenAI-compatible API, so we leverage the OpenAI SDK.
"""
import os
import logging
from typing import List, Dict, Any, Optional

import openai
from openai import OpenAI

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MAX_TOKENS, GROQ_DEFAULT_MODEL

logger = logging.getLogger(__name__)


class GroqProvider(LLMProvider):
    """Groq API provider implementation.

    Groq offers extremely fast inference (~10x faster than OpenAI) for
    open-source models like Llama 3.3, Mixtral, and Gemma.

    Note: Groq does not support reasoning modes or image generation.

    Service Tiers:
        - "on_demand" (default): Standard tier with occasional queue latency during peak times
        - "flex": Higher throughput, best effort, may return over-capacity errors
        - "auto": Automatically selects best available tier
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # Ignored - Groq doesn't support this
        api_key: Optional[str] = None,
        service_tier: Optional[str] = None,
    ):
        """Initialize Groq provider.

        Args:
            model: Model to use (defaults to GROQ_DEFAULT_MODEL from config)
            reasoning_effort: Ignored - Groq doesn't have reasoning models
            api_key: Groq API key (defaults to GROQ_API_KEY env var)
            service_tier: Groq service tier - "on_demand", "flex", or "auto"
                         Defaults to GROQ_SERVICE_TIER env var or "auto"
        """
        self._model = model or GROQ_DEFAULT_MODEL
        self._service_tier = service_tier or os.environ.get("GROQ_SERVICE_TIER", "auto")

        # Validate API key early for better error messages
        resolved_key = api_key or os.environ.get("GROQ_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Groq API key not provided. Set GROQ_API_KEY environment variable "
                "or pass api_key parameter."
            )

        # Groq uses OpenAI-compatible API with shared HTTP client for connection reuse
        self._client = OpenAI(
            api_key=resolved_key,
            base_url="https://api.groq.com/openai/v1",
            http_client=shared_http_client,
        )
        logger.info(f"Groq provider initialized with service_tier={self._service_tier}")

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        """Groq doesn't support reasoning modes."""
        return None

    @property
    def image_model(self) -> str:
        """Groq doesn't support image generation."""
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
            "max_tokens": max_tokens,  # Groq uses max_tokens, not max_completion_tokens
            "temperature": 1.0,
        }

        # Add service tier for queue priority
        if self._service_tier:
            kwargs["extra_body"] = {"service_tier": self._service_tier}

        if json_format:
            kwargs["response_format"] = {"type": "json_object"}

        # Add tools if provided (Groq uses OpenAI-compatible format)
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
        """Groq doesn't support image generation."""
        raise NotImplementedError("Groq does not support image generation")

    def is_retryable_error(self, exception: Exception) -> tuple[bool, int]:
        if isinstance(exception, openai.RateLimitError):
            return True, 30
        if isinstance(exception, (openai.APITimeoutError, openai.APIConnectionError)):
            return True, 2
        if isinstance(exception, openai.InternalServerError):
            return True, 2
        return False, 0

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from Groq response.

        Groq uses OpenAI-compatible response format.
        """
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
            "cached_tokens": 0,  # Groq doesn't report cached tokens
            "reasoning_tokens": 0,  # Groq doesn't have reasoning tokens
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from Groq response."""
        return raw_response.choices[0].message.content or ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from Groq response."""
        return raw_response.choices[0].finish_reason or ""

    def extract_image_url(self, raw_response: Any) -> str:
        """Groq doesn't support image generation."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Groq response."""
        request_id = getattr(raw_response, 'id', None)
        if request_id is None or not isinstance(request_id, str):
            return ''
        return request_id

    def extract_tool_calls(self, raw_response: Any) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from Groq response.

        Groq uses OpenAI-compatible format for tool calls.
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
