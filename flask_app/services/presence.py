"""In-memory registry of which cash-mode sandboxes are currently active.

The realtime world ticker (`ticker_service.py`) only advances the world
for sandboxes a player is actively using — there is no persistence of
world progression while a user is offline. This module is the source of
truth for "actively using."

A session is **active** when it has at least one live Socket.IO
connection (`mark_active` on `connect`, `mark_inactive` on `disconnect`)
*or* was touched within `ACTIVE_TTL_SECONDS`. The TTL grace serves two
cases:

- The Lobby→Game navigation gap, where the lobby socket disconnects a
  beat before the game socket connects (or vice versa).
- An HTTP-only fallback: `GET /api/cash/lobby` calls `touch(...)`, so a
  client whose websocket failed entirely still keeps its world ticking
  as long as it keeps polling.

**Scope, deliberately small** (mirrors `cash_mode/activity.py`):
in-memory, single-process. A multi-worker deployment would need a shared
store + a single elected ticker owner — out of scope (prod is `-w 1`).
Keyed by `owner_id` (one private sandbox per user in v1).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Set

# Grace window an owner stays "active" after their last socket drops or
# last lobby touch. Long enough to bridge a page navigation and a slow
# poll; short enough that a closed tab stops the world promptly.
ACTIVE_TTL_SECONDS = 60.0


@dataclass
class ActiveSession:
    """One user's live cash presence.

    `sids` is the set of connected Socket.IO session ids for this owner
    (multiple tabs → multiple sids). `last_seen` is refreshed on every
    connect/touch and is what the TTL grace checks once `sids` is empty.
    """

    owner_id: str
    sandbox_id: str
    sids: Set[str] = field(default_factory=set)
    last_seen: float = field(default_factory=time.monotonic)


def lobby_room_name(owner_id: str) -> str:
    """Socket.IO room the ticker pushes a user's world events to.

    Shared by the socket `connect` handler (which joins it) and the
    ticker (which emits to it) so the naming lives in one place.
    """
    return f"lobby:{owner_id}"


_lock = threading.Lock()
# owner_id -> ActiveSession
_sessions: Dict[str, ActiveSession] = {}
# sid -> owner_id, so a bare disconnect (no owner in scope) can be
# resolved back to the session it belonged to.
_sid_owner: Dict[str, str] = {}


def mark_active(owner_id: str, sandbox_id: str, sid: str) -> None:
    """Register a live socket for an owner. Idempotent per sid.

    Also (re)binds the owner's `sandbox_id` — cheap to refresh in case a
    sandbox resolution changed between connections.
    """
    now = time.monotonic()
    with _lock:
        session = _sessions.get(owner_id)
        if session is None:
            session = ActiveSession(owner_id=owner_id, sandbox_id=sandbox_id)
            _sessions[owner_id] = session
        session.sandbox_id = sandbox_id
        session.sids.add(sid)
        session.last_seen = now
        _sid_owner[sid] = owner_id


def mark_inactive(sid: str) -> None:
    """Drop a socket. The owner's session lingers until the TTL lapses.

    We don't delete the session immediately when its last sid drops:
    `active_sessions()` applies the grace window so a navigation gap or a
    transient disconnect doesn't stop the world. Pruning happens lazily
    in `active_sessions()`.
    """
    with _lock:
        owner_id = _sid_owner.pop(sid, None)
        if owner_id is None:
            return
        session = _sessions.get(owner_id)
        if session is None:
            return
        session.sids.discard(sid)
        # Reset the grace clock from the moment the socket dropped.
        session.last_seen = time.monotonic()


def touch(owner_id: str, sandbox_id: str) -> None:
    """Refresh an owner's activity from a non-socket signal (lobby read).

    Creates the session if absent so an HTTP-only client (no working
    websocket) still gets ticked. Does not add a sid.
    """
    now = time.monotonic()
    with _lock:
        session = _sessions.get(owner_id)
        if session is None:
            session = ActiveSession(owner_id=owner_id, sandbox_id=sandbox_id)
            _sessions[owner_id] = session
        session.sandbox_id = sandbox_id
        session.last_seen = now


def active_sessions() -> List[ActiveSession]:
    """Snapshot of currently-active sessions; prunes expired ones.

    Active = has ≥1 live sid OR was seen within `ACTIVE_TTL_SECONDS`.
    Returns copies so the caller can iterate without holding the lock
    (the ticker yields between sandboxes — it must not pin the lock).
    """
    now = time.monotonic()
    with _lock:
        expired = [
            owner_id
            for owner_id, s in _sessions.items()
            if not s.sids and (now - s.last_seen) > ACTIVE_TTL_SECONDS
        ]
        for owner_id in expired:
            _sessions.pop(owner_id, None)
        return [
            ActiveSession(
                owner_id=s.owner_id,
                sandbox_id=s.sandbox_id,
                sids=set(s.sids),
                last_seen=s.last_seen,
            )
            for s in _sessions.values()
        ]


def is_active(owner_id: str) -> bool:
    """Whether an owner currently counts as active (with TTL grace)."""
    now = time.monotonic()
    with _lock:
        session = _sessions.get(owner_id)
        if session is None:
            return False
        return bool(session.sids) or (now - session.last_seen) <= ACTIVE_TTL_SECONDS


def clear() -> None:
    """Drop all presence state. For tests and clean shutdown."""
    with _lock:
        _sessions.clear()
        _sid_owner.clear()
