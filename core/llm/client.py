"""Unified LLM client with built-in tracking."""
import time
import logging
from typing import List, Dict, Optional, Any, Callable

from .config import DEFAULT_MAX_TOKENS, AVAILABLE_PROVIDERS
from .response import LLMResponse, ImageResponse
from .tracking import UsageTracker, CallType, capture_prompt, capture_image_prompt
from .providers.base import LLMProvider
from .providers.openai import OpenAIProvider
from .providers.groq import GroqProvider
from .providers.anthropic import AnthropicProvider
from .providers.deepseek import DeepSeekProvider
from .providers.mistral import MistralProvider
from .providers.google import GoogleProvider
from .providers.xai import XAIProvider
from .providers.pollinations import PollinationsProvider
from .providers.runware import RunwareProvider

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
            provider: Provider name ('openai', 'groq', 'anthropic', 'deepseek', 'mistral', 'google')
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
            "anthropic": lambda: AnthropicProvider(model=model, reasoning_effort=reasoning_effort),
            "deepseek": lambda: DeepSeekProvider(model=model, reasoning_effort=reasoning_effort),
            "mistral": lambda: MistralProvider(model=model, reasoning_effort=reasoning_effort),
            "google": lambda: GoogleProvider(model=model, reasoning_effort=reasoning_effort),
            "xai": lambda: XAIProvider(model=model, reasoning_effort=reasoning_effort),
            "pollinations": lambda: PollinationsProvider(model=model, reasoning_effort=reasoning_effort),
            "runware": lambda: RunwareProvider(model=model, reasoning_effort=reasoning_effort),
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
        # Tool calling support
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], str]] = None,
        max_tool_iterations: int = 5,
        # Tracking context
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
        hand_number: Optional[int] = None,
        prompt_template: Optional[str] = None,
        message_count: Optional[int] = None,
        system_prompt_tokens: Optional[int] = None,
        capture_enricher: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Make a completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'
            json_format: Whether to request JSON output
            max_tokens: Maximum tokens in response
            tools: Optional list of tool definitions for function calling
            tool_choice: Tool choice mode ("auto", "required", "none")
            tool_executor: Callback to execute tools: (name, args) -> result string
            max_tool_iterations: Maximum tool call iterations to prevent infinite loops
            call_type: Type of call for tracking
            game_id: Game ID for tracking
            owner_id: User ID for tracking
            player_name: AI player name for tracking
            hand_number: Hand number for tracking
            prompt_template: Prompt template name for tracking
            message_count: Number of messages in conversation (for Assistant)
            system_prompt_tokens: Token count of system prompt (via tiktoken)
            capture_enricher: Optional callback to add domain-specific fields to capture

        Returns:
            LLMResponse with content and usage data
        """
        import json as json_module

        start_time = time.time()

        # Track total usage across tool iterations
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_reasoning_tokens = 0

        # Make a mutable copy of messages for tool loop
        working_messages = list(messages)
        iteration = 0
        final_tool_calls = None

        try:
            while iteration < max_tool_iterations:
                iteration += 1

                raw_response = self._provider.complete(
                    messages=working_messages,
                    json_format=json_format,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )

                usage = self._provider.extract_usage(raw_response)
                total_input_tokens += usage["input_tokens"]
                total_output_tokens += usage["output_tokens"]
                total_cached_tokens += usage["cached_tokens"]
                total_reasoning_tokens += usage["reasoning_tokens"]

                content = self._provider.extract_content(raw_response)
                finish_reason = self._provider.extract_finish_reason(raw_response)
                request_id = self._provider.extract_request_id(raw_response)
                tool_calls = self._provider.extract_tool_calls(raw_response)

                # If no tool calls or no executor, we're done
                if not tool_calls or not tool_executor:
                    final_tool_calls = tool_calls
                    break

                # Execute each tool call and add results to messages
                # First add the assistant's message with tool calls
                assistant_msg = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                working_messages.append(assistant_msg)

                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    tool_args_str = func.get("arguments", "{}")
                    tool_id = tc.get("id", "")

                    try:
                        tool_args = json_module.loads(tool_args_str)
                    except json_module.JSONDecodeError:
                        tool_args = {}

                    try:
                        tool_result = tool_executor(tool_name, tool_args)
                    except Exception as e:
                        logger.error(f"Tool execution error for {tool_name}: {e}")
                        tool_result = json_module.dumps({"error": str(e)})

                    # Add tool result message
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": tool_result,
                    })

                # Continue loop to get the model's final response after tool execution
                # Reset tool_choice to auto after first iteration to let model decide
                tool_choice = "auto"

            latency_ms = (time.time() - start_time) * 1000

            response = LLMResponse(
                content=content,
                model=self._provider.model,
                provider=self._provider.provider_name,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cached_tokens=total_cached_tokens,
                reasoning_tokens=total_reasoning_tokens,
                reasoning_effort=self._provider.reasoning_effort,
                max_tokens=max_tokens,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                status="ok" if content or final_tool_calls else "error",
                request_id=request_id,
                tool_calls=final_tool_calls,
                raw_response=raw_response,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            error_message = str(e)
            logger.error(f"LLM completion failed: {error_message}")

            response = LLMResponse(
                content="",
                model=self._provider.model,
                provider=self._provider.provider_name,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                cached_tokens=total_cached_tokens,
                reasoning_tokens=total_reasoning_tokens,
                max_tokens=max_tokens,
                latency_ms=latency_ms,
                status="error",
                error_code=type(e).__name__,
                error_message=error_message[:1000] if error_message else None,  # Truncate to 1000 chars
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

        # Capture prompt for playground (if enabled via LLM_PROMPT_CAPTURE env var)
        if response.status == "ok" and call_type:
            capture_prompt(
                messages=messages,
                response=response,
                call_type=call_type,
                game_id=game_id,
                player_name=player_name,
                hand_number=hand_number,
                debug_mode=False,  # Game-level debug mode handled separately
                enricher=capture_enricher,
            )

        return response

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        call_type: CallType = CallType.IMAGE_GENERATION,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        target_personality: Optional[str] = None,
        target_emotion: Optional[str] = None,
        reference_image_id: Optional[str] = None,
        seed_image_url: Optional[str] = None,
        strength: float = 0.75,
        negative_prompt: Optional[str] = None,
        **context: Any,
    ) -> ImageResponse:
        """Generate an image.

        Args:
            prompt: Image generation prompt
            size: Image size (e.g., '1024x1024')
            call_type: Type of call for tracking
            game_id: Game ID for tracking
            owner_id: User ID for tracking
            target_personality: Optional personality name (for avatar generation)
            target_emotion: Optional emotion (for avatar generation)
            reference_image_id: Optional reference image ID (for img2img)
            seed_image_url: Optional URL to base image for img2img generation
            strength: How much to transform the seed image (0.0-1.0).
                      Lower = more like original, higher = more creative.
            negative_prompt: Optional negative prompt for things to avoid
            **context: Additional tracking context

        Returns:
            ImageResponse with URL and metadata
        """
        start_time = time.time()

        try:
            raw_response = self._provider.generate_image(
                prompt=prompt,
                size=size,
                seed_image_url=seed_image_url,
                strength=strength,
                negative_prompt=negative_prompt,
            )
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

        # Capture image prompt for playground (if enabled via LLM_PROMPT_CAPTURE env var)
        if response.status == "ok":
            capture_image_prompt(
                prompt=prompt,
                response=response,
                call_type=call_type,
                target_personality=target_personality or context.get("player_name"),
                target_emotion=target_emotion or context.get("target_emotion"),
                reference_image_id=reference_image_id,
                game_id=game_id,
                owner_id=owner_id,
            )

        return response
