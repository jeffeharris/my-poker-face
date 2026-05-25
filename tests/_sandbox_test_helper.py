"""Shared test helper: pin sandbox_id for cash-mode integration tests.

Integration tests that drive Flask routes need the resolver to return
a deterministic sandbox_id for their test owner_id, AND need that
sandbox_id to exist as a row in `sandboxes` (so per-sandbox queries
succeed). Otherwise the route resolves to a fresh UUID and reads from
an empty sandbox.

Use `pin_sandbox_for(owner_id, sandbox_repo)` in the test setUp after
repos are created. Returns the pinned sandbox_id (typically the
constant `TEST_SANDBOX_ID`) so the test can pass it to direct repo
seeds.

The helper is a small primitive — not a magic shim. Tests still pass
`sandbox_id=...` explicitly to every direct repo call; this just
ensures the route resolver agrees on the value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask_app.services import sandbox_resolver
from poker.repositories.sandbox_repository import SandboxState

TEST_SANDBOX_ID = "test-sandbox-1"


def pin_sandbox_for(owner_id: str, sandbox_repo: Any) -> str:
    """Force `resolve_default_sandbox_for(owner_id)` to return TEST_SANDBOX_ID.

    Idempotent: re-calling for the same owner doesn't create a second
    row (the existence check skips if already present). Also clears the
    resolver's per-process cache so subsequent calls don't return a
    stale UUID from a prior test.
    """
    sandbox_resolver.clear_cache()
    existing = sandbox_repo.load(TEST_SANDBOX_ID)
    if existing is None:
        # Bypass `SandboxRepository.create` (which generates its own
        # UUID) by writing the row directly with our fixed test id.
        with sandbox_repo._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sandboxes
                    (sandbox_id, owner_id, name, created_at, archived_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    TEST_SANDBOX_ID,
                    owner_id,
                    "Test Sandbox",
                    datetime.utcnow().isoformat(),
                ),
            )
    # Warm the resolver cache so the route's resolution returns the
    # same id without a DB roundtrip.
    sandbox_resolver._cache[owner_id] = TEST_SANDBOX_ID
    return TEST_SANDBOX_ID
