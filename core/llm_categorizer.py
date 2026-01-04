"""
Generic LLM-based categorization utility.

Provides structured output from LLM calls with schema validation,
timeout handling, and fallback support. Can be used for any task
requiring LLM-generated categorical or dimensional output.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypeVar, Generic
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from core.llm import LLMClient, CallType

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class CategorizationSchema:
    """Defines the expected output structure for categorization."""

    # Schema definition as JSON schema or example
    fields: Dict[str, Dict[str, Any]]  # field_name -> {type, description, range, etc.}

    # Example output for few-shot prompting
    example_output: Optional[Dict[str, Any]] = None

    def to_prompt_description(self) -> str:
        """Generate prompt description of expected output."""
        lines = ["Output a JSON object with these fields:"]
        for field_name, field_spec in self.fields.items():
            field_type = field_spec.get('type', 'any')
            description = field_spec.get('description', '')
            range_info = ""
            if 'min' in field_spec and 'max' in field_spec:
                range_info = f" (range: {field_spec['min']} to {field_spec['max']})"
            elif 'options' in field_spec:
                range_info = f" (options: {', '.join(field_spec['options'])})"
            lines.append(f"  - {field_name} ({field_type}){range_info}: {description}")

        if self.example_output:
            lines.append(f"\nExample output:\n{json.dumps(self.example_output, indent=2)}")

        return "\n".join(lines)

    def validate(self, output: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and clamp output to schema constraints."""
        validated = {}
        for field_name, field_spec in self.fields.items():
            value = output.get(field_name)

            # Handle missing fields
            if value is None:
                validated[field_name] = field_spec.get('default')
                continue

            # Type coercion and range clamping
            field_type = field_spec.get('type', 'any')
            if field_type == 'float':
                try:
                    value = float(value)
                    if 'min' in field_spec:
                        value = max(field_spec['min'], value)
                    if 'max' in field_spec:
                        value = min(field_spec['max'], value)
                except (ValueError, TypeError):
                    value = field_spec.get('default', 0.0)
            elif field_type == 'int':
                try:
                    value = int(value)
                    if 'min' in field_spec:
                        value = max(field_spec['min'], value)
                    if 'max' in field_spec:
                        value = min(field_spec['max'], value)
                except (ValueError, TypeError):
                    value = field_spec.get('default', 0)
            elif field_type == 'string':
                value = str(value) if value else field_spec.get('default', '')
            elif field_type == 'enum' and 'options' in field_spec:
                if value not in field_spec['options']:
                    value = field_spec.get('default', field_spec['options'][0])

            validated[field_name] = value

        return validated


@dataclass
class CategorizationResult(Generic[T]):
    """Result of a categorization call."""
    success: bool
    data: Optional[T] = None
    raw_response: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    used_fallback: bool = False


class StructuredLLMCategorizer:
    """
    Generic utility for getting structured categorical output from LLMs.

    Features:
    - Schema-based output validation
    - Timeout handling with fallback
    - Cheap/fast model support
    - Reusable across different categorization tasks

    Example usage:
        schema = CategorizationSchema(
            fields={
                'sentiment': {'type': 'float', 'min': -1.0, 'max': 1.0, 'description': 'Sentiment score'},
                'category': {'type': 'enum', 'options': ['positive', 'negative', 'neutral']},
            }
        )
        categorizer = StructuredLLMCategorizer(schema)
        result = categorizer.categorize(
            context="User said: I love this product!",
            system_prompt="Analyze the sentiment of the user message."
        )
    """

    # Default fast/cheap model for categorization
    DEFAULT_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        schema: CategorizationSchema,
        model: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        fallback_generator: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    ):
        """
        Initialize the categorizer.

        Args:
            schema: Schema defining expected output structure
            model: LLM model to use (defaults to FAST_MODEL from config)
            timeout_seconds: Timeout for LLM calls
            fallback_generator: Function to generate fallback output from context
        """
        from core.llm import FAST_MODEL
        self.schema = schema
        self.model = model or FAST_MODEL
        self.timeout_seconds = timeout_seconds
        self.fallback_generator = fallback_generator

        # Initialize LLM client for tracked API calls
        self._llm_client = LLMClient(model=self.model)

        # Thread pool for timeout handling
        self._executor = ThreadPoolExecutor(max_workers=2)

    def categorize(
        self,
        context: str,
        system_prompt: str,
        additional_context: Optional[Dict[str, Any]] = None
    ) -> CategorizationResult[Dict[str, Any]]:
        """
        Perform categorization using LLM.

        Args:
            context: The main context/content to categorize
            system_prompt: System prompt describing the categorization task
            additional_context: Optional additional context to include

        Returns:
            CategorizationResult with validated output or fallback
        """
        start_time = time.time()

        # Build the full prompt
        user_prompt = self._build_user_prompt(context, additional_context)
        full_system_prompt = f"{system_prompt}\n\n{self.schema.to_prompt_description()}"

        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            # Run with timeout
            future = self._executor.submit(self._call_llm, messages)
            raw_response = future.result(timeout=self.timeout_seconds)

            latency_ms = (time.time() - start_time) * 1000

            # Parse and validate response
            try:
                parsed = json.loads(raw_response)
                validated = self.schema.validate(parsed)

                logger.info(
                    f"[LLM_CATEGORIZER] model={self.model} | "
                    f"latency={latency_ms:.0f}ms | status=ok"
                )

                return CategorizationResult(
                    success=True,
                    data=validated,
                    raw_response=raw_response,
                    latency_ms=latency_ms,
                    used_fallback=False
                )
            except json.JSONDecodeError as e:
                logger.warning(f"[LLM_CATEGORIZER] JSON parse error: {e}")
                return self._generate_fallback(context, additional_context, start_time, str(e))

        except FuturesTimeoutError:
            logger.warning(f"[LLM_CATEGORIZER] Timeout after {self.timeout_seconds}s")
            return self._generate_fallback(
                context, additional_context, start_time,
                f"Timeout after {self.timeout_seconds}s"
            )
        except Exception as e:
            logger.error(f"[LLM_CATEGORIZER] Error: {e}")
            return self._generate_fallback(context, additional_context, start_time, str(e))

    def _build_user_prompt(
        self,
        context: str,
        additional_context: Optional[Dict[str, Any]]
    ) -> str:
        """Build the user prompt from context."""
        prompt_parts = [context]

        if additional_context:
            prompt_parts.append("\nAdditional context:")
            for key, value in additional_context.items():
                if isinstance(value, dict):
                    prompt_parts.append(f"  {key}: {json.dumps(value)}")
                else:
                    prompt_parts.append(f"  {key}: {value}")

        prompt_parts.append("\nRespond with valid JSON only.")
        return "\n".join(prompt_parts)

    def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        """Make the LLM API call with tracking."""
        response = self._llm_client.complete(
            messages=messages,
            json_format=True,
            max_tokens=500,
            call_type=CallType.CATEGORIZATION
        )
        return response.content or ""

    def _generate_fallback(
        self,
        context: str,
        additional_context: Optional[Dict[str, Any]],
        start_time: float,
        error: str
    ) -> CategorizationResult[Dict[str, Any]]:
        """Generate fallback output when LLM fails."""
        latency_ms = (time.time() - start_time) * 1000

        if self.fallback_generator:
            try:
                fallback_input = {'context': context}
                if additional_context:
                    fallback_input.update(additional_context)
                fallback_data = self.fallback_generator(fallback_input)
                validated = self.schema.validate(fallback_data)

                logger.info(
                    f"[LLM_CATEGORIZER] model={self.model} | "
                    f"latency={latency_ms:.0f}ms | status=fallback | error={error}"
                )

                return CategorizationResult(
                    success=True,
                    data=validated,
                    error=error,
                    latency_ms=latency_ms,
                    used_fallback=True
                )
            except Exception as fallback_error:
                logger.error(f"[LLM_CATEGORIZER] Fallback generator failed: {fallback_error}")

        # Generate default output from schema
        default_data = {
            field_name: field_spec.get('default')
            for field_name, field_spec in self.schema.fields.items()
        }

        return CategorizationResult(
            success=False,
            data=default_data,
            error=error,
            latency_ms=latency_ms,
            used_fallback=True
        )

    def categorize_batch(
        self,
        items: List[Dict[str, Any]],
        system_prompt: str,
        context_key: str = 'context',
        max_parallel: int = 4
    ) -> List[CategorizationResult[Dict[str, Any]]]:
        """
        Categorize multiple items in parallel.

        Args:
            items: List of items to categorize, each with context
            system_prompt: System prompt for categorization
            context_key: Key in each item dict containing the context
            max_parallel: Maximum parallel requests

        Returns:
            List of CategorizationResults in same order as input
        """
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = []
            for item in items:
                context = item.get(context_key, '')
                additional = {k: v for k, v in item.items() if k != context_key}
                future = executor.submit(
                    self.categorize, context, system_prompt, additional
                )
                futures.append(future)

            return [f.result() for f in futures]


# Pre-built schemas for common use cases

SENTIMENT_SCHEMA = CategorizationSchema(
    fields={
        'sentiment': {
            'type': 'float',
            'min': -1.0,
            'max': 1.0,
            'default': 0.0,
            'description': 'Sentiment score from very negative (-1) to very positive (1)'
        },
        'confidence': {
            'type': 'float',
            'min': 0.0,
            'max': 1.0,
            'default': 0.5,
            'description': 'Confidence in the sentiment assessment'
        },
        'category': {
            'type': 'enum',
            'options': ['positive', 'negative', 'neutral', 'mixed'],
            'default': 'neutral',
            'description': 'Overall sentiment category'
        }
    },
    example_output={
        'sentiment': 0.7,
        'confidence': 0.85,
        'category': 'positive'
    }
)
