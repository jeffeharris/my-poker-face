"""Google Gemini provider implementation.

Google Gemini offers competitive models with a generous free tier.
Uses the google-genai SDK (the new unified Google GenAI SDK).
"""
import os
import logging
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MAX_TOKENS, GOOGLE_DEFAULT_MODEL

logger = logging.getLogger(__name__)


class GoogleProvider(LLMProvider):
    """Google Gemini API provider implementation.

    Gemini offers competitive pricing with a free tier,
    multimodal capabilities, and thinking mode support.
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # Maps to thinking config
        api_key: Optional[str] = None,
    ):
        """Initialize Google Gemini provider.

        Args:
            model: Model to use (defaults to GOOGLE_DEFAULT_MODEL)
            reasoning_effort: Maps to thinking budget:
                - None/minimal: No thinking
                - low: 4096 tokens
                - medium: 8192 tokens
                - high: 16384 tokens
            api_key: Google API key (defaults to GOOGLE_API_KEY env var)
        """
        self._model_name = model or GOOGLE_DEFAULT_MODEL
        self._reasoning_effort = reasoning_effort

        # Validate API key early for better error messages
        resolved_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Google API key not provided. Set GOOGLE_API_KEY environment variable "
                "or pass api_key parameter."
            )

        # Initialize the client with shared HTTP client for connection reuse
        http_options = types.HttpOptions(httpx_client=shared_http_client)
        self._client = genai.Client(
            api_key=resolved_key,
            http_options=http_options,
        )

        # Map reasoning effort to thinking budget
        self._thinking_budget = self._get_thinking_budget(reasoning_effort)

    def _get_thinking_budget(self, reasoning_effort: str) -> Optional[int]:
        """Convert reasoning effort to thinking budget tokens."""
        if not reasoning_effort or reasoning_effort == "minimal":
            return None

        budget_map = {
            "low": 4096,
            "medium": 8192,
            "high": 16384,
        }
        return budget_map.get(reasoning_effort)

    @property
    def provider_name(self) -> str:
        return "google"

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def image_model(self) -> str:
        """Gemini supports image generation via Imagen."""
        return "imagen-3.0-generate-001"

    def _convert_messages(self, messages: List[Dict[str, str]]) -> tuple:
        """Convert OpenAI-style messages to Gemini format.

        Returns (system_instruction, contents) tuple.
        """
        system_instruction = None
        contents = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
            elif role == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=content)]
                    )
                )
            else:  # user
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=content)]
                    )
                )

        return system_instruction, contents

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
        if not messages:
            raise ValueError("No messages provided to complete()")

        system_instruction, contents = self._convert_messages(messages)

        # Validate that we have user/assistant content (not just system messages)
        if not contents:
            raise ValueError("No user or assistant messages to send (only system prompt found)")

        # Build config kwargs
        config_kwargs = {
            "max_output_tokens": max_tokens,
            "temperature": 1.0,
        }

        # Add system instruction if present
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        # Add thinking config if budget is set
        if self._thinking_budget:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self._thinking_budget
            )

        # Add JSON response format if requested
        if json_format:
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        # Make the API call
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config,
        )

        return response

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        seed_image_url: Optional[str] = None,
        strength: float = 0.75,
        negative_prompt: Optional[str] = None,
    ) -> Any:
        """Generate image using Imagen."""
        # Imagen integration would go here
        raise NotImplementedError("Gemini image generation not yet implemented")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from Gemini response."""
        usage = getattr(raw_response, 'usage_metadata', None)
        if not usage:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            }

        # Extract thinking/reasoning tokens if available
        thinking_tokens = getattr(usage, 'thoughts_token_count', 0) or 0

        return {
            "input_tokens": getattr(usage, 'prompt_token_count', 0) or 0,
            "output_tokens": getattr(usage, 'candidates_token_count', 0) or 0,
            "cached_tokens": getattr(usage, 'cached_content_token_count', 0) or 0,
            "reasoning_tokens": thinking_tokens,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from Gemini response."""
        try:
            return raw_response.text
        except (AttributeError, ValueError):
            # Handle cases where response might be blocked or empty
            if hasattr(raw_response, 'candidates') and raw_response.candidates:
                candidate = raw_response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content.parts:
                    return candidate.content.parts[0].text
            return ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from Gemini response."""
        try:
            if hasattr(raw_response, 'candidates') and raw_response.candidates:
                candidate = raw_response.candidates[0]
                finish_reason = getattr(candidate, 'finish_reason', None)
                if finish_reason:
                    return str(finish_reason.name) if hasattr(finish_reason, 'name') else str(finish_reason)
        except Exception as e:
            logger.debug("Failed to extract finish reason from Gemini response: %s", e)
        return ""

    def extract_image_url(self, raw_response: Any) -> str:
        """Extract image URL from Gemini response."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Gemini response."""
        # Gemini doesn't provide a request ID in the same way
        return ""
