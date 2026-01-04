"""Abstract base class for LLM providers."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


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

    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = 2800,
    ) -> Any:
        """Make a completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            json_format: Whether to request JSON output
            max_tokens: Maximum tokens in response

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
    ) -> Any:
        """Generate an image.

        Args:
            prompt: Image generation prompt
            size: Image size (e.g., '1024x1024')
            n: Number of images to generate

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
