"""Unified LLM client with built-in tracking."""
import time
import logging
from typing import List, Dict, Optional, Any

from .response import LLMResponse, ImageResponse
from .tracking import UsageTracker, CallType
from .providers.base import LLMProvider
from .providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)


class LLMClient:
    """Low-level, stateless LLM client with usage tracking.

    Use this for one-off completions where you don't need conversation memory.
    For stateful conversations, use the Assistant class instead.
    """

    def __init__(
        self,
        provider: str = "openai",
        model: Optional[str] = None,
        reasoning_effort: str = "low",
        tracker: Optional[UsageTracker] = None,
    ):
        """Initialize LLM client.

        Args:
            provider: Provider name ('openai', future: 'anthropic', 'groq')
            model: Model to use (provider-specific default if None)
            reasoning_effort: Reasoning effort for models that support it
            tracker: Usage tracker (uses default singleton if None)
        """
        self._provider = self._create_provider(provider, model, reasoning_effort)
        self._tracker = tracker or UsageTracker.get_default()

    def _create_provider(
        self,
        provider: str,
        model: Optional[str],
        reasoning_effort: str,
    ) -> LLMProvider:
        """Create the appropriate provider instance."""
        if provider == "openai":
            return OpenAIProvider(model=model, reasoning_effort=reasoning_effort)
        else:
            raise ValueError(f"Unknown provider: {provider}. Supported: openai")

    @property
    def model(self) -> str:
        """Get the model name."""
        return self._provider.model

    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return self._provider.provider_name

    def complete(
        self,
        messages: List[Dict[str, str]],
        json_format: bool = False,
        max_tokens: int = 2800,
        # Tracking context
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
        hand_number: Optional[int] = None,
        prompt_template: Optional[str] = None,
    ) -> LLMResponse:
        """Make a completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            json_format: Whether to request JSON output
            max_tokens: Maximum tokens in response
            call_type: Type of call for tracking
            game_id: Game ID for tracking
            owner_id: User ID for tracking
            player_name: AI player name for tracking
            hand_number: Hand number for tracking
            prompt_template: Prompt template name for tracking

        Returns:
            LLMResponse with content and usage data
        """
        start_time = time.time()

        try:
            raw_response = self._provider.complete(
                messages=messages,
                json_format=json_format,
                max_tokens=max_tokens,
            )
            latency_ms = (time.time() - start_time) * 1000

            usage = self._provider.extract_usage(raw_response)
            content = self._provider.extract_content(raw_response)
            finish_reason = self._provider.extract_finish_reason(raw_response)

            response = LLMResponse(
                content=content,
                model=self._provider.model,
                provider=self._provider.provider_name,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_tokens=usage["cached_tokens"],
                reasoning_tokens=usage["reasoning_tokens"],
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                status="ok" if content else "error",
                raw_response=raw_response,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"LLM completion failed: {e}")

            response = LLMResponse(
                content="",
                model=self._provider.model,
                provider=self._provider.provider_name,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                status="error",
                error_code=type(e).__name__,
            )

        # Track usage
        self._tracker.record(
            response=response,
            call_type=call_type,
            game_id=game_id,
            owner_id=owner_id,
            player_name=player_name,
            hand_number=hand_number,
            prompt_template=prompt_template,
        )

        return response

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        call_type: CallType = CallType.IMAGE_GENERATION,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        **context: Any,
    ) -> ImageResponse:
        """Generate an image.

        Args:
            prompt: Image generation prompt
            size: Image size (e.g., '1024x1024')
            call_type: Type of call for tracking
            game_id: Game ID for tracking
            owner_id: User ID for tracking
            **context: Additional tracking context

        Returns:
            ImageResponse with URL and metadata
        """
        start_time = time.time()

        try:
            raw_response = self._provider.generate_image(prompt=prompt, size=size)
            latency_ms = (time.time() - start_time) * 1000

            url = self._provider.extract_image_url(raw_response)

            response = ImageResponse(
                url=url,
                model="dall-e-2",  # Currently hardcoded in provider
                provider=self._provider.provider_name,
                size=size,
                image_count=1,
                latency_ms=latency_ms,
                status="ok" if url else "error",
                raw_response=raw_response,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"Image generation failed: {e}")

            response = ImageResponse(
                url="",
                model="dall-e-2",
                provider=self._provider.provider_name,
                size=size,
                latency_ms=latency_ms,
                status="error",
                error_code=type(e).__name__,
            )

        # Track usage
        self._tracker.record(
            response=response,
            call_type=call_type,
            game_id=game_id,
            owner_id=owner_id,
            player_name=context.get("player_name"),
        )

        return response
