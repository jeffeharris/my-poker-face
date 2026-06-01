"""Unified LLM client with built-in tracking."""

import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .budget import classify_shed, get_spend_gate
from .config import AVAILABLE_PROVIDERS, DEFAULT_MAX_TOKENS
from .providers.anthropic import AnthropicProvider
from .providers.base import LLMProvider
from .providers.deepseek import DeepSeekProvider
from .providers.google import GoogleProvider
from .providers.groq import GroqProvider
from .providers.mistral import MistralProvider
from .providers.openai import OpenAIProvider
from .providers.pollinations import PollinationsProvider
from .providers.runware import RunwareProvider
from .providers.xai import XAIProvider
from .response import ImageResponse, LLMResponse
from .tracking import CallType, UsageTracker, capture_image_prompt, capture_prompt

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
        default_timeout: Optional[float] = None,
    ):
        """Initialize LLM client.

        Args:
            provider: Provider name ('openai', 'groq', 'anthropic', 'deepseek', 'mistral', 'google')
            model: Model to use (provider-specific default if None)
            reasoning_effort: Reasoning effort for models that support it
            tracker: Usage tracker (uses default singleton if None)
            default_timeout: Optional per-call HTTP timeout (seconds) applied to
                every complete() on this client unless overridden per call. Set a
                short value for in-game/ticker clients (PRH-18); leave None for
                batch/experiment clients to keep the long shared-client default.
        """
        self._provider = self._create_provider(provider, model, reasoning_effort)
        self._tracker = tracker or UsageTracker.get_default()
        self._default_timeout = default_timeout

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
            "pollinations": lambda: PollinationsProvider(
                model=model, reasoning_effort=reasoning_effort
            ),
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
        timeout: Optional[float] = None,
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

        # PRH-2 spend gate: short-circuit before any provider dispatch when the
        # daily LLM budget is exceeded. Returns a failed LLMResponse — decision
        # callers fall back to the deterministic engine; cosmetic calls vanish.
        gate = get_spend_gate()
        if gate.enabled:
            reason = gate.over_budget_reason(owner_id, self._tracker)
            if reason:
                logger.warning(
                    "[LLM BUDGET] blocked %s call (%s): %s",
                    call_type.value if call_type else "unknown",
                    classify_shed(call_type),
                    reason,
                )
                return LLMResponse(
                    content="",
                    model=self._provider.model,
                    provider=self._provider.provider_name,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0,
                    status="error",
                    error_code="budget_exceeded",
                    error_message=reason,
                )

        start_time = time.time()

        # Track total usage across tool iterations
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_reasoning_tokens = 0

        # PRH-18: resolve the per-call timeout (explicit arg wins over the
        # client's default). Passed through to the provider only when set, so
        # batch/experiment callers keep the shared client's long default.
        resolved_timeout = timeout if timeout is not None else self._default_timeout
        timeout_kwargs = {"timeout": resolved_timeout} if resolved_timeout is not None else {}

        # Make a mutable copy of messages for tool loop
        working_messages = list(messages)
        iteration = 0
        final_tool_calls = None
        reasoning_content = None

        # Retry config for transient errors (timeouts, 5xx, rate limits)
        max_retries = 2  # up to 3 total attempts

        try:
            while iteration < max_tool_iterations:
                iteration += 1

                raw_response = None
                for attempt in range(max_retries + 1):
                    try:
                        raw_response = self._provider.complete(
                            messages=working_messages,
                            json_format=json_format,
                            max_tokens=max_tokens,
                            tools=tools,
                            tool_choice=tool_choice,
                            **timeout_kwargs,
                        )
                        break  # success
                    except Exception as retry_err:
                        is_retryable, wait = self._provider.is_retryable_error(retry_err)
                        if not is_retryable or attempt >= max_retries:
                            raise  # non-retryable or final attempt — propagate
                        wait = max(wait, min(2**attempt, 16))
                        logger.warning(
                            f"LLM call failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {wait}s: {retry_err}"
                        )
                        time.sleep(wait)

                assert raw_response is not None, "Retry loop completed without response"
                usage = self._provider.extract_usage(raw_response)
                total_input_tokens += usage["input_tokens"]
                total_output_tokens += usage["output_tokens"]
                total_cached_tokens += usage["cached_tokens"]
                total_reasoning_tokens += usage["reasoning_tokens"]

                content = self._provider.extract_content(raw_response)
                finish_reason = self._provider.extract_finish_reason(raw_response)
                request_id = self._provider.extract_request_id(raw_response)
                tool_calls = self._provider.extract_tool_calls(raw_response)
                reasoning_content = self._provider.extract_reasoning_content(raw_response)

                # If no tool calls or no executor, we're done
                if not tool_calls or not tool_executor:
                    final_tool_calls = tool_calls
                    break

                # Execute each tool call and add results to messages
                # First add the assistant's message with tool calls
                # Include reasoning_content for DeepSeek thinking mode (required by API)
                assistant_msg = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                if reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
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
                    working_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": tool_result,
                        }
                    )

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
                reasoning_content=reasoning_content,
                max_tokens=max_tokens,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                status="ok" if content or final_tool_calls else "error",
                request_id=request_id,
                tool_calls=final_tool_calls,
                raw_response=raw_response,
            )

            # Warn if tools were provided but the model output XML-style tool calls
            # This indicates the provider didn't properly pass tools to the API
            if tools and content:
                xml_tool_pattern = r'<(?:tool_call|function_call|[a-z_]+)>\s*(?:<[a-z_]+>|\{)'
                if re.search(xml_tool_pattern, content, re.IGNORECASE):
                    logger.warning(
                        f"Model output appears to contain XML-style tool calls in text. "
                        f"This usually means the provider ({self._provider.provider_name}) "
                        f"did not properly pass tools to the API, or the model "
                        f"({self._provider.model}) does not support function calling. "
                        f"Check provider implementation."
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
                error_message=error_message[:1000]
                if error_message
                else None,  # Truncate to 1000 chars
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
                owner_id=owner_id,
                player_name=player_name,
                hand_number=hand_number,
                debug_mode=False,  # Game-level debug mode handled separately
                enricher=capture_enricher,
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
        # PRH-2 spend gate: image generation is cosmetic and the most expensive
        # per-call spend — block it first when over the daily budget.
        gate = get_spend_gate()
        if gate.enabled:
            reason = gate.over_budget_reason(owner_id, self._tracker)
            if reason:
                logger.warning(
                    "[LLM BUDGET] blocked image generation (%s): %s",
                    classify_shed(call_type),
                    reason,
                )
                return ImageResponse(
                    url="",
                    model=self._provider.image_model,
                    provider=self._provider.provider_name,
                    size=size,
                    status="error",
                    error_code="budget_exceeded",
                    error_message=reason,
                )

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
