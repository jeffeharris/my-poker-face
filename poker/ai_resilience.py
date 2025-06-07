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
    
    This function attempts to extract context from the arguments and generate
    an appropriate fallback response.
    """
    try:
        # Try to extract game context from arguments
        # This assumes the decorated method is part of a class with game context
        if args and hasattr(args[0], 'personality_traits'):
            personality_traits = args[0].personality_traits
        else:
            personality_traits = None
        
        if strategy == AIFallbackStrategy.CONSERVATIVE:
            return _conservative_fallback(args, kwargs)
        elif strategy == AIFallbackStrategy.RANDOM_VALID:
            return _random_valid_fallback(args, kwargs)
        elif strategy == AIFallbackStrategy.MIMIC_PERSONALITY:
            return _personality_based_fallback(args, kwargs, personality_traits)
        else:
            # Ultimate fallback
            return {"action": "fold", "amount": 0}
    except Exception as e:
        logger.error(f"Error in fallback generation: {e}")
        return {"action": "fold", "amount": 0}


def _conservative_fallback(args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Conservative strategy: check when possible, call when necessary, never raise"""
    try:
        # Extract valid actions if available
        valid_actions = kwargs.get('valid_actions', ['fold', 'check', 'call'])
        
        if 'check' in valid_actions:
            return {"action": "check", "amount": 0}
        elif 'call' in valid_actions:
            # Need to determine call amount from context
            call_amount = kwargs.get('call_amount', 0)
            return {"action": "call", "amount": call_amount}
        else:
            return {"action": "fold", "amount": 0}
    except Exception as e:
        logger.error(f"Error in conservative fallback: {e}")
        return {"action": "fold", "amount": 0}


def _random_valid_fallback(args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Random valid action fallback"""
    try:
        valid_actions = kwargs.get('valid_actions', ['fold'])
        
        # Weight the actions to be somewhat reasonable
        weights = {
            'fold': 0.2,
            'check': 0.3,
            'call': 0.3,
            'raise': 0.2
        }
        
        # Filter to only valid actions
        available_weights = {a: w for a, w in weights.items() if a in valid_actions}
        
        if not available_weights:
            return {"action": "fold", "amount": 0}
        
        # Normalize weights
        total_weight = sum(available_weights.values())
        normalized_weights = {a: w/total_weight for a, w in available_weights.items()}
        
        # Random weighted choice
        action = random.choices(
            list(normalized_weights.keys()),
            weights=list(normalized_weights.values())
        )[0]
        
        amount = 0
        if action == 'call':
            amount = kwargs.get('call_amount', 0)
        elif action == 'raise':
            min_raise = kwargs.get('min_raise', 10)
            max_raise = kwargs.get('max_raise', 100)
            amount = random.randint(min_raise, min(max_raise, min_raise * 3))
        
        return {"action": action, "amount": amount}
    except Exception as e:
        logger.error(f"Error in random fallback: {e}")
        return {"action": "fold", "amount": 0}


def _personality_based_fallback(
    args: tuple,
    kwargs: dict,
    personality_traits: Optional[Dict[str, float]]
) -> Dict[str, Any]:
    """Fallback based on personality traits"""
    try:
        if not personality_traits:
            return _conservative_fallback(args, kwargs)
        
        valid_actions = kwargs.get('valid_actions', ['fold'])
        
        # Use personality traits to weight actions
        aggression = personality_traits.get('aggression', 0.5)
        bluff_tendency = personality_traits.get('bluff_tendency', 0.5)
        
        # More aggressive players more likely to raise/call
        if 'raise' in valid_actions and aggression > 0.6:
            if random.random() < aggression:
                min_raise = kwargs.get('min_raise', 10)
                max_raise = kwargs.get('max_raise', 100)
                amount = int(min_raise + (max_raise - min_raise) * aggression * 0.3)
                return {"action": "raise", "amount": amount}
        
        if 'call' in valid_actions and aggression > 0.3:
            if random.random() < (aggression + 0.2):  # Slightly favor calling
                return {"action": "call", "amount": kwargs.get('call_amount', 0)}
        
        if 'check' in valid_actions:
            return {"action": "check", "amount": 0}
        
        # Low aggression or no good options = fold
        return {"action": "fold", "amount": 0}
    except Exception as e:
        logger.error(f"Error in personality-based fallback: {e}")
        return {"action": "fold", "amount": 0}


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
            return {"action": "fold", "amount": 0}
        
        action = response.get('action', '').lower()
        amount = response.get('amount', 0)
        
        # Ensure action is valid
        if action not in valid_actions:
            logger.warning(f"Invalid action '{action}', valid actions: {valid_actions}")
            # Try to find a reasonable alternative
            if 'check' in valid_actions:
                action = 'check'
                amount = 0
            elif 'call' in valid_actions:
                action = 'call'
                # Amount should be provided by game context
            else:
                action = 'fold'
                amount = 0
        
        # Ensure amount is reasonable
        try:
            amount = int(amount)
            if amount < 0:
                amount = 0
        except (ValueError, TypeError):
            amount = 0
        
        return {"action": action, "amount": amount}
    except Exception as e:
        logger.error(f"Error validating AI response: {e}")
        return {"action": "fold", "amount": 0}