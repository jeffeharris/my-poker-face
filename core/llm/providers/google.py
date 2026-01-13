"""Google Gemini provider implementation.

Google Gemini offers competitive models with a generous free tier.
Uses the google-generativeai SDK.
"""
import os
import json
import logging
from typing import List, Dict, Any, Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from .base import LLMProvider
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

        # Configure the SDK
        genai.configure(api_key=api_key or os.environ.get("GOOGLE_API_KEY"))
        self._model = genai.GenerativeModel(self._model_name)

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
                contents.append({"role": "model", "parts": [content]})
            else:  # user
                contents.append({"role": "user", "parts": [content]})

        return system_instruction, contents

    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Any:
        """Make a chat completion request."""
        system_instruction, contents = self._convert_messages(messages)

        # Create generation config
        gen_config = GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=1.0,
        )

        if json_format:
            gen_config.response_mime_type = "application/json"

        # Create model with system instruction if provided
        model = self._model
        if system_instruction:
            model = genai.GenerativeModel(
                self._model_name,
                system_instruction=system_instruction
            )

        # Start chat and send message
        chat = model.start_chat(history=contents[:-1] if len(contents) > 1 else [])

        # Get the last user message
        last_message = contents[-1]["parts"][0] if contents else ""

        response = chat.send_message(
            last_message,
            generation_config=gen_config,
        )

        return response

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
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

        return {
            "input_tokens": getattr(usage, 'prompt_token_count', 0) or 0,
            "output_tokens": getattr(usage, 'candidates_token_count', 0) or 0,
            "cached_tokens": getattr(usage, 'cached_content_token_count', 0) or 0,
            "reasoning_tokens": 0,
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
        except Exception:
            pass
        return ""

    def extract_image_url(self, raw_response: Any) -> str:
        """Extract image URL from Gemini response."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Gemini response."""
        # Gemini doesn't provide a request ID in the same way
        return ""
