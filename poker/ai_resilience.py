"""
AI Resilience Module - Error handling and fallback behaviors for AI players.

This module provides decorators and utilities to ensure AI players can gracefully
handle API failures, rate limits, and other errors without crashing the game.
"""

import functools
import random
import time
import logging
import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from enum import Enum

from .config import (
    MIN_RAISE,
    DEFAULT_MAX_RAISE_MULTIPLIER,
    FALLBACK_ACTION_WEIGHTS,
    AGGRESSION_RAISE_THRESHOLD,
    AGGRESSION_CALL_THRESHOLD,
)

logger = logging.getLogger(__name__)


class AIError(Exception):
    """Base exception for AI-related errors"""
    pass


class AIResponseError(AIError):
    """Raised when AI response is invalid or unparseable"""
    pass


class AIFallbackStrategy(Enum):
    """Available fallback strategies when AI fails"""
    CONSERVATIVE = "conservative"  # Check/call, never raise
    RANDOM_VALID = "random_valid"  # Random from valid actions
    MIMIC_PERSONALITY = "mimic_personality"  # Based on personality traits


class FallbackActionSelector:
    """
    Centralized fallback action selection logic.
    Used when AI fails to provide a valid response.
    """

    @staticmethod
    def select_action(
        valid_actions: List[str],
        strategy: AIFallbackStrategy = AIFallbackStrategy.CONSERVATIVE,
        personality_traits: Optional[Dict[str, float]] = None,
        call_amount: int = 0,
        min_raise: int = MIN_RAISE,
        max_raise: int = MIN_RAISE * DEFAULT_MAX_RAISE_MULTIPLIER
    ) -> Dict[str, Any]:
        """
        Select a fallback action based on the given strategy.

        Args:
            valid_actions: List of valid actions for current game state
            strategy: The fallback strategy to use
            personality_traits: Optional personality traits for MIMIC_PERSONALITY strategy
            call_amount: Amount required to call
            min_raise: Minimum raise amount
            max_raise: Maximum raise amount

        Returns:
            Dict with 'action' and 'adding_to_pot' keys
        """
        if strategy == AIFallbackStrategy.CONSERVATIVE:
            return FallbackActionSelector._conservative(valid_actions, call_amount)
        elif strategy == AIFallbackStrategy.RANDOM_VALID:
            return FallbackActionSelector._random_valid(valid_actions, call_amount, min_raise, max_raise)
        elif strategy == AIFallbackStrategy.MIMIC_PERSONALITY:
            return FallbackActionSelector._personality_based(
                valid_actions, personality_traits, call_amount, min_raise, max_raise
            )
        else:
            return {"action": "fold", "adding_to_pot": 0}

    @staticmethod
    def _conservative(valid_actions: List[str], call_amount: int) -> Dict[str, Any]:
        """Conservative strategy: check when possible, call when necessary, never raise"""
        if 'check' in valid_actions:
            return {"action": "check", "adding_to_pot": 0}
        elif 'call' in valid_actions:
            return {"action": "call", "adding_to_pot": call_amount}
        else:
            return {"action": "fold", "adding_to_pot": 0}

    @staticmethod
    def _random_valid(
        valid_actions: List[str],
        call_amount: int,
        min_raise: int,
        max_raise: int
    ) -> Dict[str, Any]:
        """Random valid action with weighted selection"""
        # Filter to only valid actions
        available_weights = {
            a: w for a, w in FALLBACK_ACTION_WEIGHTS.items()
            if a in valid_actions
        }

        if not available_weights:
            return {"action": "fold", "adding_to_pot": 0}

        # Normalize weights
        total_weight = sum(available_weights.values())
        normalized_weights = {a: w / total_weight for a, w in available_weights.items()}

        # Random weighted choice
        action = random.choices(
            list(normalized_weights.keys()),
            weights=list(normalized_weights.values())
        )[0]

        adding_to_pot = 0
        if action == 'call':
            adding_to_pot = call_amount
        elif action == 'raise':
            adding_to_pot = random.randint(min_raise, min(max_raise, min_raise * DEFAULT_MAX_RAISE_MULTIPLIER))

        return {"action": action, "adding_to_pot": adding_to_pot}

    @staticmethod
    def _personality_based(
        valid_actions: List[str],
        personality_traits: Optional[Dict[str, float]],
        call_amount: int,
        min_raise: int,
        max_raise: int
    ) -> Dict[str, Any]:
        """Fallback based on personality traits"""
        if not personality_traits:
            return FallbackActionSelector._conservative(valid_actions, call_amount)

        aggression = personality_traits.get('aggression', 0.5)

        # More aggressive players more likely to raise/call
        if 'raise' in valid_actions and aggression > AGGRESSION_RAISE_THRESHOLD:
            if random.random() < aggression:
                adding_to_pot = max(min_raise, int(min_raise + (max_raise - min_raise) * aggression * 0.3))
                return {"action": "raise", "adding_to_pot": adding_to_pot}

        if 'call' in valid_actions and aggression > AGGRESSION_CALL_THRESHOLD:
            if random.random() < (aggression + 0.2):  # Slightly favor calling
                return {"action": "call", "adding_to_pot": call_amount}

        if 'check' in valid_actions:
            return {"action": "check", "adding_to_pot": 0}

        # Low aggression or no good options = fold
        return {"action": "fold", "adding_to_pot": 0}


class CircuitBreaker:
    """Circuit breaker pattern to prevent cascading failures"""
    
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.is_open = False
    
    def record_success(self):
        """Reset the circuit breaker on successful call"""
        self.failure_count = 0
        self.is_open = False
    
    def record_failure(self):
        """Record a failure and potentially open the circuit"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.error(f"Circuit breaker opened after {self.failure_count} failures")
    
    def can_attempt(self) -> bool:
        """Check if we can attempt a call"""
        if not self.is_open:
            return True
        
        # Check if recovery timeout has passed
        if time.time() - self.last_failure_time > self.recovery_timeout:
            logger.info("Circuit breaker recovery timeout passed, attempting reset")
            self.is_open = False
            self.failure_count = 0
            return True
        
        return False


# Global circuit breaker for OpenAI API
openai_circuit_breaker = CircuitBreaker()


def parse_json_response(response_text: str) -> Dict[str, Any]:
    """
    Safely parse JSON response from AI, handling common issues.
    
    Args:
        response_text: Raw text response from AI
        
    Returns:
        Parsed JSON as dictionary
        
    Raises:
        AIResponseError: If response cannot be parsed
    """
    if not response_text:
        raise AIResponseError("Empty response from AI")
    
    # Try to extract JSON from response
    # Sometimes AI wraps JSON in markdown code blocks
    if "```json" in response_text:
        try:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        except Exception as e:
            logger.warning(f"Failed to extract JSON from markdown: {e}")
    elif "```" in response_text:
        try:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        except Exception as e:
            logger.warning(f"Failed to extract from code block: {e}")
    
    # Try to parse JSON
    try:
        result = json.loads(response_text)
        if not isinstance(result, dict):
            raise AIResponseError(f"Expected dict, got {type(result)}")
        return result
    except json.JSONDecodeError as e:
        # Try to fix common JSON errors
        logger.warning(f"JSON decode error: {e}. Attempting to fix...")
        
        # Common fixes
        fixed_text = response_text
        
        # Replace single quotes with double quotes
        if "'" in fixed_text:
            try:
                fixed_text = fixed_text.replace("'", '"')
                result = json.loads(fixed_text)
                logger.info("Fixed JSON by replacing single quotes")
                return result
            except:
                pass
        
        # Try to extract just the JSON object if there's extra text
        import re
        json_match = re.search(r'\{[^}]+\}', response_text)
        if json_match:
            try:
                result = json.loads(json_match.group())
                logger.info("Extracted JSON object from text")
                return result
            except:
                pass
        
        raise AIResponseError(f"Could not parse JSON response: {e}") from e


def with_ai_fallback(
    fallback_fn: Optional[Callable] = None,
    max_retries: int = 3,
    fallback_strategy: AIFallbackStrategy = AIFallbackStrategy.CONSERVATIVE
):
    """
    Decorator for AI operations with automatic retry and fallback.
    
    Args:
        fallback_fn: Custom fallback function to use
        max_retries: Maximum number of retry attempts
        fallback_strategy: Strategy to use if no custom fallback provided
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Check circuit breaker first
            if not openai_circuit_breaker.can_attempt():
                logger.warning("Circuit breaker is open, using fallback")
                if fallback_fn:
                    return fallback_fn(*args, **kwargs)
                else:
                    return _get_fallback_response(args, kwargs, fallback_strategy)
            
            last_error = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    
                    # If result is supposed to be JSON, validate it
                    if hasattr(func, '_expects_json') and func._expects_json:
                        if isinstance(result, str):
                            result = parse_json_response(result)
                        validate_ai_response(result, kwargs.get('valid_actions', []))
                    
                    openai_circuit_breaker.record_success()
                    return result
                    
                except (json.JSONDecodeError, AIResponseError) as e:
                    logger.error(f"AI response parsing error: {e}")
                    last_error = e
                    # Don't retry parsing errors, go straight to fallback
                    break
                    
                except Exception as e:
                    last_error = e
                    wait_time = min(2 ** attempt, 16)  # Exponential backoff, max 16 seconds
                    logger.warning(
                        f"AI operation '{func.__name__}' failed "
                        f"(attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    
                    # Check for specific error types
                    error_msg = str(e).lower()
                    if "rate limit" in error_msg:
                        wait_time = 60  # Longer wait for rate limits
                        logger.warning(f"Rate limit hit, waiting {wait_time} seconds")
                    elif "timeout" in error_msg:
                        wait_time = 5  # Shorter wait for timeouts
                    
                    if attempt < max_retries - 1:
                        logger.info(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
            
            # All retries failed
            openai_circuit_breaker.record_failure()
            logger.error(f"AI operation '{func.__name__}' failed after {max_retries} attempts")
            
            if fallback_fn:
                return fallback_fn(*args, **kwargs)
            else:
                return _get_fallback_response(args, kwargs, fallback_strategy)
                
        return wrapper
    return decorator


def expects_json(func):
    """Mark a function as expecting JSON response for validation"""
    func._expects_json = True
    return func


def _get_fallback_response(args: tuple, kwargs: dict, strategy: AIFallbackStrategy) -> Any:
    """
    Generate a fallback response based on the selected strategy.
    Uses the centralized FallbackActionSelector.
    """
    try:
        # Try to extract game context from arguments
        if args and hasattr(args[0], 'personality_traits'):
            personality_traits = args[0].personality_traits
        else:
            personality_traits = None

        # Extract context from kwargs
        valid_actions = kwargs.get('valid_actions', ['fold', 'check', 'call'])
        call_amount = kwargs.get('call_amount', 0)
        min_raise = kwargs.get('min_raise', MIN_RAISE)
        max_raise = kwargs.get('max_raise', MIN_RAISE * DEFAULT_MAX_RAISE_MULTIPLIER)

        return FallbackActionSelector.select_action(
            valid_actions=valid_actions,
            strategy=strategy,
            personality_traits=personality_traits,
            call_amount=call_amount,
            min_raise=min_raise,
            max_raise=max_raise
        )
    except Exception as e:
        logger.error(f"Error in fallback generation: {e}")
        return {"action": "fold", "adding_to_pot": 0}


# Legacy functions - delegate to FallbackActionSelector for backwards compatibility
def _conservative_fallback(args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Conservative strategy: check when possible, call when necessary, never raise"""
    valid_actions = kwargs.get('valid_actions', ['fold', 'check', 'call'])
    call_amount = kwargs.get('call_amount', 0)
    return FallbackActionSelector._conservative(valid_actions, call_amount)


def _random_valid_fallback(args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Random valid action fallback"""
    valid_actions = kwargs.get('valid_actions', ['fold'])
    call_amount = kwargs.get('call_amount', 0)
    min_raise = kwargs.get('min_raise', MIN_RAISE)
    max_raise = kwargs.get('max_raise', MIN_RAISE * DEFAULT_MAX_RAISE_MULTIPLIER)
    return FallbackActionSelector._random_valid(valid_actions, call_amount, min_raise, max_raise)


def _personality_based_fallback(
    args: tuple,
    kwargs: dict,
    personality_traits: Optional[Dict[str, float]]
) -> Dict[str, Any]:
    """Fallback based on personality traits"""
    valid_actions = kwargs.get('valid_actions', ['fold'])
    call_amount = kwargs.get('call_amount', 0)
    min_raise = kwargs.get('min_raise', MIN_RAISE)
    max_raise = kwargs.get('max_raise', MIN_RAISE * DEFAULT_MAX_RAISE_MULTIPLIER)
    return FallbackActionSelector._personality_based(
        valid_actions, personality_traits, call_amount, min_raise, max_raise
    )


def get_fallback_chat_response(personality_name: str, context: str = "") -> str:
    """Generate a fallback chat message when AI chat fails"""
    try:
        fallback_messages = {
            "default": [
                "Interesting hand...",
                "Let me think about this.",
                "Hmm, decisions decisions.",
                "Time to make a move.",
            ],
            "aggressive": [
                "Time to show who's boss.",
                "I'm not backing down.",
                "Let's see what you got.",
                "Bring it on!",
            ],
            "conservative": [
                "Need to play this carefully.",
                "Better safe than sorry.",
                "Patience is key.",
                "Slow and steady...",
            ],
            "chatty": [
                "Oh, this is exciting!",
                "What a game we're having!",
                "Love the action here!",
                "This is why I play poker!",
            ]
        }
        
        # Try to match personality style
        messages = fallback_messages.get(personality_name.lower(), fallback_messages["default"])
        return random.choice(messages)
    except Exception as e:
        logger.error(f"Error generating fallback chat: {e}")
        return "..."


def validate_ai_response(response: Union[Dict[str, Any], str], valid_actions: List[str]) -> Dict[str, Any]:
    """
    Validate and potentially fix an AI response.

    Args:
        response: The AI's response (dict or JSON string)
        valid_actions: List of valid actions for current game state

    Returns:
        A validated response, potentially modified to be valid

    Raises:
        AIResponseError: If response cannot be made valid
    """
    try:
        # Parse JSON if needed
        if isinstance(response, str):
            response = parse_json_response(response)

        if not isinstance(response, dict):
            logger.error(f"Invalid response type: {type(response)}")
            return {"action": "fold", "adding_to_pot": 0}

        action = response.get('action', '').lower()
        # Support both 'adding_to_pot' (preferred) and 'amount' (legacy) keys
        adding_to_pot = response.get('adding_to_pot', response.get('amount', 0))

        # Ensure action is valid
        if action not in valid_actions:
            logger.warning(f"Invalid action '{action}', valid actions: {valid_actions}")
            # Try to find a reasonable alternative
            if 'check' in valid_actions:
                action = 'check'
                adding_to_pot = 0
            elif 'call' in valid_actions:
                action = 'call'
                # Amount should be provided by game context
            else:
                action = 'fold'
                adding_to_pot = 0

        # Ensure adding_to_pot is reasonable
        try:
            adding_to_pot = int(adding_to_pot)
            if adding_to_pot < 0:
                adding_to_pot = 0
        except (ValueError, TypeError):
            adding_to_pot = 0

        return {"action": action, "adding_to_pot": adding_to_pot}
    except Exception as e:
        logger.error(f"Error validating AI response: {e}")
        return {"action": "fold", "adding_to_pot": 0}