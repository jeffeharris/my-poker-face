"""Response dataclasses for LLM operations."""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LLMResponse:
    """Response from a text completion request."""
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0
    finish_reason: str = ""
    status: str = "ok"
    error_code: Optional[str] = None
    raw_response: Any = field(default=None, repr=False)

    @property
    def total_tokens(self) -> int:
        """Total tokens used (input + output)."""
        return self.input_tokens + self.output_tokens

    @property
    def is_error(self) -> bool:
        """Check if response indicates an error."""
        return self.status == "error" or not self.content

    @property
    def was_truncated(self) -> bool:
        """Check if response was truncated due to token limit."""
        return self.finish_reason == "length"


@dataclass
class ImageResponse:
    """Response from an image generation request."""
    url: str
    model: str
    provider: str
    size: str
    image_count: int = 1
    latency_ms: float = 0
    status: str = "ok"
    error_code: Optional[str] = None
    raw_response: Any = field(default=None, repr=False)

    @property
    def is_error(self) -> bool:
        """Check if response indicates an error."""
        return self.status == "error" or not self.url
