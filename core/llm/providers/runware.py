"""Runware.ai provider implementation.

Runware.ai is an image-only provider with fast generation times.
API Documentation: https://runware.ai/docs/image-inference/api-reference
"""
import os
import logging
import time
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import requests

from .base import LLMProvider
from ..config import DEFAULT_MAX_TOKENS, RUNWARE_DEFAULT_MODEL

logger = logging.getLogger(__name__)

# HTTP client timeout (60 seconds for image generation)
RUNWARE_TIMEOUT = 60

# Retry configuration
MAX_RETRIES = 2
INITIAL_RETRY_DELAY = 2  # seconds
MAX_RETRY_DELAY = 30  # seconds


def round_to_multiple_of_64(value: int) -> int:
    """Round value to nearest multiple of 64 (Runware requirement)."""
    return round(value / 64) * 64


@dataclass
class RunwareImageResponse:
    """Response object for Runware image generation.

    Mimics the structure expected by extract_* methods.
    """
    url: str  # Image URL from Runware CDN
    id: str  # Task UUID
    model: str
    size: str


class RunwareProvider(LLMProvider):
    """Runware.ai API provider implementation.

    This is an image-only provider - text completion is not supported.
    Runware offers fast, high-quality image generation with FLUX models.

    API Documentation: https://runware.ai/docs/image-inference/api-reference
    """

    def __init__(
        self,
        model: str = None,
        reasoning_effort: str = None,  # Unused, but required by interface
        api_key: Optional[str] = None,
    ):
        """Initialize Runware provider.

        Args:
            model: Image model to use (defaults to RUNWARE_DEFAULT_MODEL)
            reasoning_effort: Unused (image-only provider)
            api_key: Runware API key (defaults to RUNWARE_API_KEY env var)
        """
        self._model = model or RUNWARE_DEFAULT_MODEL
        self._api_key = api_key or os.environ.get("RUNWARE_API_KEY")

        if not self._api_key:
            logger.warning("RUNWARE_API_KEY not set - Runware requests will fail")

        # Create a session for connection reuse
        self._session = requests.Session()
        if self._api_key:
            self._session.headers["Authorization"] = f"Bearer {self._api_key}"
        self._session.headers["Content-Type"] = "application/json"

    @property
    def provider_name(self) -> str:
        return "runware"

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
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Any:
        """Text completion is not supported by Runware.

        Raises:
            NotImplementedError: Always, as Runware is image-only.
        """
        raise NotImplementedError(
            "Runware is an image-only provider. Use generate_image() instead."
        )

    def generate_image(
        self,
        prompt: str,
        size: str = "512x512",
        n: int = 1,
    ) -> RunwareImageResponse:
        """Generate an image using Runware.ai.

        Args:
            prompt: Image generation prompt
            size: Image size (e.g., '512x512', '1024x1024')
            n: Number of images (only 1 supported)

        Returns:
            RunwareImageResponse with image URL

        Raises:
            Exception: If image generation fails
        """
        if n > 1:
            logger.warning("Runware only supports n=1, ignoring n=%d", n)

        # Parse size
        try:
            width, height = map(int, size.split("x"))
        except ValueError:
            logger.warning("Invalid size format '%s', using 512x512", size)
            width, height = 512, 512

        # Runware requires dimensions as multiples of 64 (128-2048)
        width = max(128, min(round_to_multiple_of_64(width), 2048))
        height = max(128, min(round_to_multiple_of_64(height), 2048))

        # Build request payload
        task_uuid = str(uuid.uuid4())
        payload = [
            {
                "taskType": "imageInference",
                "taskUUID": task_uuid,
                "positivePrompt": prompt,
                "width": width,
                "height": height,
                "model": self._model,
                "numberResults": 1,
            }
        ]

        logger.debug("Generating image with Runware: model=%s, size=%dx%d",
                     self._model, width, height)

        last_exception = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.post(
                    "https://api.runware.ai/v1",
                    json=payload,
                    timeout=RUNWARE_TIMEOUT,
                )
                response.raise_for_status()

                data = response.json()

                # Check for errors in response
                if data.get("errors"):
                    error_msg = data["errors"][0].get("message", "Unknown error")
                    raise Exception(f"Runware API error: {error_msg}")

                # Extract image URL from response
                if not data.get("data") or len(data["data"]) == 0:
                    raise Exception("Runware API returned empty data")

                image_data = data["data"][0]
                image_url = image_data.get("imageURL")

                if not image_url:
                    raise Exception("Runware API response missing imageURL")

                logger.debug("Generated image: url=%s", image_url[:80] + "...")

                return RunwareImageResponse(
                    url=image_url,
                    id=task_uuid,
                    model=self._model,
                    size=f"{width}x{height}",
                )

            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < MAX_RETRIES:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    logger.warning(
                        "Runware timeout, retry %d/%d in %ds",
                        attempt + 1, MAX_RETRIES, delay
                    )
                    time.sleep(delay)
                else:
                    raise Exception(
                        f"Runware API timeout after {MAX_RETRIES + 1} attempts"
                    )
            except requests.exceptions.HTTPError as e:
                # Don't retry client errors (4xx)
                status_code = e.response.status_code if e.response else 0
                error_text = e.response.text[:200] if e.response else str(e)
                if 400 <= status_code < 500:
                    raise Exception(f"Runware API error ({status_code}): {error_text}")
                # Retry server errors (5xx)
                last_exception = e
                if attempt < MAX_RETRIES:
                    delay = min(INITIAL_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                    logger.warning(
                        "Runware server error (%s), retry %d/%d in %ds",
                        status_code, attempt + 1, MAX_RETRIES, delay
                    )
                    time.sleep(delay)
                else:
                    raise Exception(f"Runware API error ({status_code}): {error_text}")
            except requests.exceptions.RequestException as e:
                raise Exception(f"Runware API request failed: {e}")

        # Should never reach here, but just in case
        raise Exception(f"Runware API failed: {last_exception}")

    def extract_usage(self, raw_response: Any) -> Dict[str, int]:
        """Extract token usage from Runware response.

        Runware doesn't use tokens - it's per-image pricing.
        """
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }

    def extract_content(self, raw_response: Any) -> str:
        """Extract text content from Runware response.

        Not applicable for image-only provider.
        """
        return ""

    def extract_finish_reason(self, raw_response: Any) -> str:
        """Extract finish reason from Runware response."""
        return "complete"

    def extract_image_url(self, raw_response: Any) -> str:
        """Extract image URL from Runware response."""
        if isinstance(raw_response, RunwareImageResponse):
            return raw_response.url
        return ""

    def extract_request_id(self, raw_response: Any) -> str:
        """Extract request ID from Runware response."""
        if isinstance(raw_response, RunwareImageResponse):
            return raw_response.id
        return ""
