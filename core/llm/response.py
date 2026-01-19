"""Response dataclasses for LLM operations."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    reasoning_effort: Optional[str] = None
    max_tokens: Optional[int] = None  # Token limit used for this request
    latency_ms: float = 0
    finish_reason: str = ""
    status: str = "ok"
    error_code: Optional[str] = None
    error_message: Optional[str] = None  # Full error message for debugging
    request_id: Optional[str] = None  # Vendor request ID for correlation
    tool_calls: Optional[List[Dict[str, Any]]] = None  # Tool calls from the model
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
    error_message: Optional[str] = None  # Full error message for debugging
    request_id: Optional[str] = None  # Vendor request ID for correlation
    raw_response: Any = field(default=None, repr=False)

    @property
    def is_error(self) -> bool:
        """Check if response indicates an error."""
        return self.status == "error" or not self.url
