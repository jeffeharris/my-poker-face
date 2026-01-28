"""Tests for T2-17: atexit cleanup of shared HTTP client."""
import atexit
import importlib
from unittest.mock import patch


def test_atexit_cleanup_is_registered():
    """Verify that _cleanup_http_client is registered with atexit."""
    with patch.object(atexit, 'register', wraps=atexit.register) as mock_register:
        # Re-import to trigger module-level atexit.register call
        import core.llm.providers.http_client as http_mod
        importlib.reload(http_mod)

        # Find the call that registered our cleanup function
        registered_names = [
            call[0][0].__name__
            for call in mock_register.call_args_list
            if callable(call[0][0]) and hasattr(call[0][0], '__name__')
        ]
        assert '_cleanup_http_client' in registered_names


def test_cleanup_calls_close():
    """Verify the cleanup function calls shared_http_client.close()."""
    from core.llm.providers.http_client import _cleanup_http_client, shared_http_client

    with patch.object(shared_http_client, 'close') as mock_close:
        _cleanup_http_client()
        mock_close.assert_called_once()


def test_cleanup_handles_exception_gracefully():
    """Verify cleanup does not raise if close() fails (e.g., double-close)."""
    from core.llm.providers.http_client import _cleanup_http_client, shared_http_client

    with patch.object(shared_http_client, 'close', side_effect=RuntimeError("already closed")):
        # Should not raise
        _cleanup_http_client()
