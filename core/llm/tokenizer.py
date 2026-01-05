"""Token counting utilities using tiktoken."""
import logging
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid startup cost if not used
_tiktoken = None


def _get_tiktoken():
    """Lazy import tiktoken."""
    global _tiktoken
    if _tiktoken is None:
        try:
            import tiktoken
            _tiktoken = tiktoken
        except ImportError:
            logger.warning("tiktoken not installed, token counting unavailable")
            _tiktoken = False
    return _tiktoken if _tiktoken else None


@lru_cache(maxsize=16)
def _get_encoding(model: str):
    """Get tiktoken encoding for a model, with caching.

    Args:
        model: Model name (e.g., 'gpt-4o-mini', 'gpt-4o')

    Returns:
        tiktoken Encoding object, or None if unavailable
    """
    tiktoken = _get_tiktoken()
    if tiktoken is None:
        return None

    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        # Unknown model, fall back to cl100k_base (GPT-4/3.5) or o200k_base (GPT-4o)
        try:
            # Default to o200k_base for newer models
            if model.startswith(("gpt-4o", "o1", "o3")):
                return tiktoken.get_encoding("o200k_base")
            else:
                return tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning(f"Failed to get fallback encoding: {e}")
            return None
    except Exception as e:
        logger.warning(f"Failed to get encoding for model {model}: {e}")
        return None


def count_tokens(text: str, model: str) -> Optional[int]:
    """Count tokens in text for a given model.

    Args:
        text: The text to tokenize
        model: Model name (e.g., 'gpt-4o-mini')

    Returns:
        Token count, or None if tokenization failed
    """
    if not text:
        return 0

    encoding = _get_encoding(model)
    if encoding is None:
        return None

    try:
        tokens = encoding.encode(text)
        return len(tokens)
    except Exception as e:
        logger.warning(f"Failed to count tokens: {e}")
        return None
