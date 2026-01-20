"""Anthropic Claude provider implementation."""
import os
import logging
from typing import List, Dict, Any, Optional

import anthropic

from .base import LLMProvider
from .http_client import shared_http_client
from ..config import DEFAULT_MAX_TOKENS, ANTHROPIC_DEFAULT_MODEL

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider implementation.

    Supports Claude 4.5 models (Opus, Sonnet, Haiku) with extended thinking.
    Handles Anthropic's different message format (system prompt separate from messages).
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # Maps to extended thinking budget
        api_key: Optional[str] = None,
    ):
        """Initialize Anthropic provider.

        Args:
            model: Model to use (defaults to ANTHROPIC_DEFAULT_MODEL)
            reasoning_effort: Maps to thinking budget tokens:
                - None/minimal: No extended thinking
                - low: 4000 tokens
                - medium: 8000 tokens
                - high: 16000 tokens
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
        """
        self._model = model or ANTHROPIC_DEFAULT_MODEL
        self._reasoning_effort = reasoning_effort

        # Validate API key early for better error messages
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Anthropic API key not provided. Set ANTHROPIC_API_KEY environment variable "
                "or pass api_key parameter."
            )

        self._client = anthropic.Anthropic(
            api_key=resolved_key,
            http_client=shared_http_client,
        )

        # Map reasoning effort to thinking budget tokens
        self._thinking_budget = self._get_thinking_budget(reasoning_effort)

    def _get_thinking_budget(self, reasoning_effort: str) -> Optional[int]:
        """Convert reasoning effort to thinking budget tokens."""
        if not reasoning_effort or reasoning_effort == "minimal":
            return None

        budget_map = {
            "low": 4000,
            "medium": 8000,
            "high": 16000,
        }
        return budget_map.get(reasoning_effort)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    @property
    def image_model(self) -> str:
        """Anthropic doesn't have image generation."""
        return "unsupported"

    # Explicit JSON instruction to append when json_format=True
    _JSON_INSTRUCTION = (
        "\n\nIMPORTANT: You MUST respond with valid JSON only. "
        "Do not include any text, explanation, or markdown formatting outside the JSON object. "
        "Your entire response must be parseable as JSON."
    )

    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Any:
        """Make a chat completion request.

        Anthropic has a different message format:
        - System prompt is a separate parameter
        - Messages array only contains user/assistant messages

        When json_format=True, explicit JSON instructions are injected into the
        system prompt since Anthropic doesn't have native JSON mode support.

        Note: tools/tool_choice are accepted for interface compatibility but not used.
        """
        # Extract system prompt from messages if present
        system_prompt = None
        filtered_messages = []

        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                filtered_messages.append(msg)

        # JSON output handling:
        # Anthropic does not have a native JSON mode like OpenAI's response_format.
        # We enforce JSON output by injecting explicit instructions into the system prompt.
        if json_format:
            if system_prompt:
                system_prompt = system_prompt + self._JSON_INSTRUCTION
            else:
                # If no system prompt exists, add one with JSON instruction
                system_prompt = self._JSON_INSTRUCTION.strip()
            logger.debug(
                "json_format=True for Anthropic model %s; injected JSON instruction into system prompt",
                self._model,
            )

        kwargs = {
            "model": self._model,
            "messages": filtered_messages,
            "max_tokens": max_tokens,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        # Add extended thinking if configured
        if self._thinking_budget:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }

        return self._client.messages.create(**kwargs)

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        seed_image_url: Optional[str] = None,
        strength: float = 0.75,
        negative_prompt: Optional[str] = None,
    ) -> Any:
        """Anthropic doesn't support image generation."""
        raise NotImplementedError("Anthropic does not support image generation")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from Anthropic response."""
        usage = raw_response.usage
        if not usage:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
            }

        # Extract thinking tokens from content blocks if present
        # Anthropic returns thinking as content blocks with type="thinking"
        thinking_tokens = 0
        content_blocks = getattr(raw_response, 'content', [])
        for block in content_blocks:
            if getattr(block, 'type', None) == 'thinking':
                # Each thinking block has its own token count
                thinking_tokens += len(getattr(block, 'thinking', '')) // 4  # Approximate

        # Also check if usage has explicit thinking token count (newer API versions)
        if hasattr(usage, 'thinking_tokens'):
            thinking_tokens = usage.thinking_tokens or 0

        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": getattr(usage, 'cache_read_input_tokens', 0) or 0,
            "reasoning_tokens": thinking_tokens,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from Anthropic response.

        Anthropic returns content as a list of content blocks.
        We need to extract the text from text blocks, ignoring thinking blocks.
        """
        content_blocks = raw_response.content
        if not content_blocks:
            return ""

        # Extract text from text blocks only (not thinking blocks)
        text_parts = []
        for block in content_blocks:
            if block.type == "text":
                text_parts.append(block.text)

        return "".join(text_parts)

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from Anthropic response.

        Anthropic uses 'stop_reason' instead of 'finish_reason'.
        """
        return raw_response.stop_reason or ""

    def extract_image_url(self, raw_response: Any) -> str:
        """Anthropic doesn't support image generation."""
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Anthropic response."""
        return getattr(raw_response, 'id', '') or ''
