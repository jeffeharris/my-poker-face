"""Pollinations.ai provider implementation.

Pollinations.ai is an image-only provider with extremely low pricing.
It returns binary image data which we convert to data URLs for compatibility
with the existing image handling infrastructure (urllib.request.urlopen).
"""
import os
import base64
import logging
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import requests

from .base import LLMProvider
from ..config import DEFAULT_MAX_TOKENS, POLLINATIONS_DEFAULT_MODEL

logger = logging.getLogger(__name__)

# HTTP client timeout (60 seconds for image generation)
POLLINATIONS_TIMEOUT = 60


@dataclass
class PollinationsImageResponse:
    """Response object for Pollinations image generation.

    Mimics the structure expected by extract_* methods.
    """
    url: str  # data URL
    id: str  # generated request ID
    model: str
    size: str


class PollinationsProvider(LLMProvider):
    """Pollinations.ai API provider implementation.

    This is an image-only provider - text completion is not supported.
    Pollinations offers extremely cheap image generation (~$0.0002/image).

    API Documentation: https://image.pollinations.ai/docs
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # Unused, but required by interface
        api_key: Optional[str] = None,
    ):
        """Initialize Pollinations provider.

        Args:
            model: Image model to use (defaults to POLLINATIONS_DEFAULT_MODEL)
            reasoning_effort: Unused (image-only provider)
            api_key: Pollinations API key (defaults to POLLINATIONS_API_KEY env var)
        """
        self._model = model or POLLINATIONS_DEFAULT_MODEL
        self._api_key = api_key or os.environ.get("POLLINATIONS_API_KEY")

        # Create a session for connection reuse
        self._session = requests.Session()
        if self._api_key:
            self._session.headers["Authorization"] = f"Bearer {self._api_key}"

    @property
    def provider_name(self) -> str:
        return "pollinations"

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        return None  # Image-only provider

    @property
    def image_model(self) -> str:
        return self._model

    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Any:
        """Text completion is not supported by Pollinations.

        Raises:
            NotImplementedError: Always, as Pollinations is image-only.
        """
        raise NotImplementedError(
            "Pollinations is an image-only provider. Use generate_image() instead."
        )

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
    ) -> PollinationsImageResponse:
        """Generate an image using Pollinations.ai.

        Args:
            prompt: Image generation prompt
            size: Image size (e.g., '512x512', '1024x1024')
            n: Number of images (only 1 supported)

        Returns:
            PollinationsImageResponse with data URL

        Raises:
            Exception: If image generation fails
        """
        if n > 1:
            logger.warning("Pollinations only supports n=1, ignoring n=%d", n)

        # Parse size
        try:
            width, height = map(int, size.split("x"))
        except ValueError:
            logger.warning("Invalid size format '%s', using 1024x1024", size)
            width, height = 1024, 1024

        # Build API URL
        # URL encode the prompt for path inclusion
        encoded_prompt = requests.utils.quote(prompt, safe="")
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"

        params = {
            "model": self._model,
            "width": width,
            "height": height,
            "nologo": "true",  # Remove watermark
        }

        # Add API key as query param if not using header auth
        if self._api_key and "Authorization" not in self._session.headers:
            params["key"] = self._api_key

        logger.debug("Generating image with Pollinations: model=%s, size=%dx%d",
                     self._model, width, height)

        try:
            response = self._session.get(
                url,
                params=params,
                timeout=POLLINATIONS_TIMEOUT,
            )
            response.raise_for_status()

            # Get content type from response
            content_type = response.headers.get("Content-Type", "image/png")
            if ";" in content_type:
                content_type = content_type.split(";")[0].strip()

            # Convert binary to data URL
            image_bytes = response.content
            base64_data = base64.b64encode(image_bytes).decode("utf-8")
            data_url = f"data:{content_type};base64,{base64_data}"

            # Generate a request ID for tracking
            request_id = f"poll-{uuid.uuid4().hex[:12]}"

            logger.debug("Generated image: %d bytes, type=%s", len(image_bytes), content_type)

            return PollinationsImageResponse(
                url=data_url,
                id=request_id,
                model=self._model,
                size=size,
            )

        except requests.exceptions.Timeout:
            raise Exception(f"Pollinations API timeout after {POLLINATIONS_TIMEOUT}s")
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "unknown"
            error_text = e.response.text[:200] if e.response else str(e)
            raise Exception(f"Pollinations API error ({status_code}): {error_text}")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Pollinations API request failed: {e}")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from Pollinations response.

        Pollinations doesn't use tokens - it's per-image pricing.
        """
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from Pollinations response.

        Not applicable for image-only provider.
        """
        return ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from Pollinations response."""
        return "complete"

    def extract_image_url(self, raw_response: Any) -> str:
        """Extract image URL (data URL) from Pollinations response."""
        if isinstance(raw_response, PollinationsImageResponse):
            return raw_response.url
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Pollinations response."""
        if isinstance(raw_response, PollinationsImageResponse):
            return raw_response.id
        return ""
