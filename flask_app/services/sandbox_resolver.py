"""Sandbox resolution for cash-mode routes.

Every cash-mode route resolves `sandbox_id` from `owner_id` at
request-handler entry. v1 ships 1:1 ownership: each owner has exactly
one default sandbox, auto-created on first access. Future multi-
sandbox UI would let callers specify a sandbox_id explicitly; this
resolver returns the default.

The mapping is cached per-process in a `Dict[owner_id, sandbox_id]`
so hot-path resolution is one dict lookup after warmup. Cache misses
hit `SandboxRepository.list_for_owner` (indexed query) and, if no
live sandbox exists, `SandboxRepository.create` (one INSERT).

Spec: docs/plans/CASH_MODE_PER_PLAYER_SANDBOX_HANDOFF.md
Phase 2.5 Commit 1.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)


# Per-process cache. Keyed on owner_id, values are stable for the
# lifetime of the process (sandbox_ids are opaque UUIDs and don't
# get renamed; archive flows go through a separate code path that
# can `invalidate_cache_for_owner`).
_cache: Dict[str, str] = {}
_cache_lock = threading.Lock()


def resolve_default_sandbox_for(
    owner_id: str,
    *,
    sandbox_repo,
    default_name: str = "My Casino",
) -> str:
    """Return the owner's default sandbox_id, creating one on first access.

    Hot path: warm cache → one dict lookup, ~O(1).
    Cold path: query `list_for_owner` (single-row partial-index hit);
               if empty, call `create` (single INSERT).

    Idempotent under concurrent callers: a lost race during create
    (rare — two requests for the same brand-new user simultaneously)
    is resolved by the second caller's `list_for_owner` finding the
    row the first inserted. The cache write under the lock means
    only one winner ends up persisted.

    `default_name` is the name for the auto-created sandbox. v1
    always uses "My Casino"; future multi-sandbox UI passes the
    user-supplied name when explicitly creating a new sandbox via
    a different route, not this resolver.
    """
    cached = _cache.get(owner_id)
    if cached is not None:
        return cached

    with _cache_lock:
        # Double-check after taking the lock — another thread may
        # have raced through the cache miss while we waited.
        cached = _cache.get(owner_id)
        if cached is not None:
            return cached

        existing = sandbox_repo.list_for_owner(owner_id, include_archived=False)
        if existing:
            sandbox_id = existing[0].sandbox_id
            logger.debug(
                "[SANDBOX] resolver hit existing sandbox %r for owner %r",
                sandbox_id,
                owner_id,
            )
        else:
            state = sandbox_repo.create(owner_id, name=default_name)
            sandbox_id = state.sandbox_id
            logger.info(
                "[SANDBOX] resolver created default sandbox %r for owner %r",
                sandbox_id,
                owner_id,
            )

        _cache[owner_id] = sandbox_id
        return sandbox_id


def invalidate_cache_for_owner(owner_id: str) -> None:
    """Drop the cached sandbox_id for `owner_id`.

    Call after archiving or replacing an owner's default sandbox so
    the next resolver hit returns the new live row instead of the
    stale cached id. The hot path (cache-hit) stays O(1) because
    invalidation is rare.
    """
    with _cache_lock:
        _cache.pop(owner_id, None)


def clear_cache() -> None:
    """Drop the entire resolver cache. Test-only helper."""
    with _cache_lock:
        _cache.clear()
