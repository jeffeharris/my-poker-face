"""Shared HTTP client for OpenAI-compatible providers.

Provides a connection-pooled httpx client with longer keepalive to avoid
cold connection overhead (DNS + TCP + TLS handshake) between API calls.
"""
import atexit

import httpx

# Shared HTTP client for all OpenAI-compatible providers
# Default httpx keepalive is 5s, which causes 5-10s delays on reconnection
shared_http_client = httpx.Client(
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=300.0,  # 5 minutes keepalive (vs 5s default)
    ),
    timeout=httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=600.0),
)


def _cleanup_http_client():
    """Close shared HTTP client on process exit."""
    try:
        shared_http_client.close()
    except Exception:
        # Silently ignore exceptions during cleanup (e.g., already closed,
        # invalid state). At process exit, cleanup failures are non-critical.
        pass


atexit.register(_cleanup_http_client)
