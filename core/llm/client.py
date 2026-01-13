"""Unified LLM client with built-in tracking."""
import time
import logging
from typing import List, Dict, Optional, Any

from .config import DEFAULT_MAX_TOKENS, AVAILABLE_PROVIDERS
from .response import LLMResponse, ImageResponse
from .tracking import UsageTracker, CallType
from .providers.base import LLMProvider
from .providers.openai import OpenAIProvider
from .providers.groq import GroqProvider

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
        # Provider registry - add new providers here
        provider_registry = {
            "openai": lambda: OpenAIProvider(model=model, reasoning_effort=reasoning_effort),
            "groq": lambda: GroqProvider(model=model, reasoning_effort=reasoning_effort),
        }

        if provider not in provider_registry:
            supported = ", ".join(AVAILABLE_PROVIDERS)
            raise ValueError(f"Unknown provider: {provider}. Supported: {supported}")

        return provider_registry[provider]()

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
        max_tokens: int = DEFAULT_MAX_TOKENS,
        # Tracking context
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
        hand_number: Optional[int] = None,
        prompt_template: Optional[str] = None,
        message_count: Optional[int] = None,
        system_prompt_tokens: Optional[int] = None,
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
            message_count: Number of messages in conversation (for Assistant)
            system_prompt_tokens: Token count of system prompt (via tiktoken)

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
            request_id = self._provider.extract_request_id(raw_response)

            response = LLMResponse(
                content=content,
                model=self._provider.model,
                provider=self._provider.provider_name,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_tokens=usage["cached_tokens"],
                reasoning_tokens=usage["reasoning_tokens"],
                reasoning_effort=self._provider.reasoning_effort,
                max_tokens=max_tokens,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                status="ok" if content else "error",
                request_id=request_id,
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
                max_tokens=max_tokens,
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
            message_count=message_count,
            system_prompt_tokens=system_prompt_tokens,
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
            request_id = self._provider.extract_request_id(raw_response)

            response = ImageResponse(
                url=url,
                model=self._provider.image_model,  # Currently hardcoded in provider
                provider=self._provider.provider_name,
                size=size,
                image_count=1,
                latency_ms=latency_ms,
                status="ok" if url else "error",
                request_id=request_id,
                raw_response=raw_response,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            error_message = str(e)
            logger.error(f"Image generation failed: {error_message}")

            # Extract error code - check for content_policy_violation in the error message
            error_code = type(e).__name__
            if "content_policy_violation" in error_message:
                error_code = "content_policy_violation"

            response = ImageResponse(
                url="",
                model=self._provider.image_model,
                provider=self._provider.provider_name,
                size=size,
                latency_ms=latency_ms,
                status="error",
                error_code=error_code,
                error_message=error_message,
            )

        # Track usage
        self._tracker.record(
            response=response,
            call_type=call_type,
            game_id=game_id,
            owner_id=owner_id,
            player_name=context.get("player_name"),
            prompt_template=context.get("prompt_template"),
        )

        return response
