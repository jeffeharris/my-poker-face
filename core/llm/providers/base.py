"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..config import DEFAULT_MAX_TOKENS


class LLMProvider(ABC):
    """Abstract interface for LLM providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name (e.g., 'openai', 'anthropic')."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Return the model name."""
        ...

    @property
    def reasoning_effort(self) -> str | None:
        """Return the reasoning effort (if applicable)."""
        return None

    @property
    def image_model(self) -> str:
        """Return the image generation model name."""
        return "unknown"

    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Make a completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            json_format: Whether to request JSON output
            max_tokens: Maximum tokens in response
            tools: Optional list of tool definitions for function calling
            tool_choice: Optional tool choice mode ("auto", "required", "none")
            timeout: Optional per-call timeout (seconds) overriding the shared
                HTTP client's default (PRH-18). In-game/ticker callers pass a
                short value so a stalled provider fails fast instead of hanging
                a hand under a lock; batch/experiment callers leave it None to
                keep the long default. Providers honor it best-effort.

        Returns:
            Raw provider response object
        """
        ...

    @abstractmethod
    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        seed_image_url: Optional[str] = None,
        strength: float = 0.75,
        negative_prompt: Optional[str] = None,
    ) -> Any:
        """Generate an image.

        Args:
            prompt: Image generation prompt
            size: Image size (e.g., '1024x1024')
            n: Number of images to generate
            seed_image_url: Optional URL to base image for img2img generation
            strength: How much to transform the seed image (0.0-1.0).
                      Lower = more like original, higher = more creative.
                      Only used when seed_image_url is provided.
            negative_prompt: Optional negative prompt for things to avoid

        Returns:
            Raw provider response object
        """
        ...

    @abstractmethod
    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from provider response.

        Returns:
            Dict with keys: input_tokens, output_tokens, cached_tokens, reasoning_tokens
        """
        ...

    @abstractmethod
    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from provider response."""
        ...

    @abstractmethod
    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from provider response."""
        ...

    @abstractmethod
    def extract_image_url(self, raw_response: Any) -> str:
        """Extract image URL from provider response."""
        ...

    @abstractmethod
    def extract_request_id(self, raw_response: Any) -> str:
        """Extract vendor request ID from provider response.

        This ID can be used to correlate with vendor logs/support.
        Each provider has their own format (e.g., OpenAI: 'chatcmpl-xxx').
        """
        ...

    def extract_image_cost(self, raw_response: Any) -> Optional[float]:
        """Extract the provider-reported USD cost for an image generation.

        Returns None when the provider doesn't report a cost (the tracker then
        falls back to SKU-based pricing). Override in image providers that
        return a real per-generation cost (e.g. Runware with includeCost).
        """
        return None

    def is_retryable_error(self, exception: Exception) -> tuple[bool, int]:
        """Check if an exception is retryable and suggest a wait time.

        Returns:
            (is_retryable, suggested_wait_seconds) — providers override
            this to classify their SDK-specific exceptions.
        """
        return False, 0

    def extract_tool_calls(self, raw_response: Any) -> Optional[List[Dict[str, Any]]]:
        """Extract tool calls from response.

        Returns None if no tool calls or not supported by this provider.
        Override in providers that support function calling.
        """
        return None

    def extract_reasoning_content(self, raw_response: Any) -> Optional[str]:
        """Extract reasoning content from response (for models with thinking mode).

        Returns None if no reasoning content or not supported by this provider.
        Override in providers that support thinking/reasoning mode (e.g., DeepSeek).
        """
        return None
