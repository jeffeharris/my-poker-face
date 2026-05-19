"""In-memory ring buffer of recent lobby movement events.

Surfaces the AI movement that's already happening inside
`refresh_unseated_tables` (and the hand-boundary refresh hook) as
a stream of human-readable events for the lobby UI. The goal is to
make the world feel alive without changing any underlying behavior
— purely a read-side feature.

**Scope, deliberately small:**
- In-memory only. A backend restart wipes the buffer. That's fine —
  events older than a session are stale anyway and a fresh lobby
  pulls the current roster directly.
- Single-process. Multiprocess deployments would need a shared
  store (Redis, DB table). v1 is single-process Flask dev.
- Bounded ring (`maxlen=50`). Old events drop silently. The lobby
  surfaces at most the last 10 by default.

If/when full Path C ships the background AI-only hand simulator,
this same buffer becomes the event surface for hand-level activity
("Napoleon won a $1200 pot vs Bezos at $50"). The shape is meant
to scale without redesign.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Deque, List, Optional


# Event types kept as string constants rather than an enum so the
# serialized shape is JSON-native — frontend reads `type: 'join'`
# without an enum-value translation.
EVENT_JOIN = "join"
"""An AI sat down at a table (either from idle pool or fresh from
the eligible pool via live fill)."""

EVENT_LEAVE = "leave"
"""An AI left a table. `reason` carries the movement decision
(`forced_leave`, `stake_up_queued`, `take_break`, `bored_move`)."""

EVENT_BIG_WIN = "big_win"
"""An AI won a big pot at an unseated table (fake-sim — chips were
mutated by `cash_mode/fake_sim.py:roll_fake_hand`, no real cards).
Honest in the sense that chip counts at the table DO reflect the
move; the AI's bankroll only credits when a player ratifies the
session at that table. `reason` carries the loser's personality_id
so the frontend can render "won $X from <opponent>"."""

EVENT_BIG_LOSS = "big_loss"
"""Symmetric pair to EVENT_BIG_WIN. Same fake-sim origin."""


@dataclass(frozen=True)
class LobbyEvent:
    """One movement event surfaced to the lobby UI.

    Fields are flat strings/ints so the JSON shape is obvious to the
    frontend. `message` is the pre-formatted display string ("Napoleon
    busted out of $50") so the React side doesn't have to know about
    every enum value; the structured fields are still there for any
    future grouping/filtering UI.
    """

    type: str
    table_id: str
    stake_label: str
    personality_id: str
    name: str
    reason: str  # `''` for joins; movement decision for leaves
    message: str
    created_at: str  # ISO-8601, UTC


# Bounded ring; oldest events drop on overflow. 50 is enough to
# tolerate burst movement during a single lobby read (5 tables ×
# multiple seats each) while keeping memory trivial.
_MAX_EVENTS = 50

_events_lock = threading.Lock()
_events: Deque[LobbyEvent] = deque(maxlen=_MAX_EVENTS)


def record_event(event: LobbyEvent) -> None:
    """Append one event to the buffer. Thread-safe.

    Called from `refresh_unseated_tables` / hand-boundary refresh
    after movement decisions are made. Callers don't need to dedupe
    — same-table refresh ticks naturally produce distinct events.
    """
    with _events_lock:
        _events.append(event)


def recent_events(limit: int = 10) -> List[LobbyEvent]:
    """Return up to `limit` most-recent events, newest first.

    Snapshot — callers don't hold the lock; mutations after this
    call don't reflect in the returned list. Safe to serialize.
    """
    with _events_lock:
        snapshot = list(_events)
    snapshot.reverse()
    return snapshot[:limit]


def clear_events() -> None:
    """Drop all buffered events. For tests."""
    with _events_lock:
        _events.clear()


# --- Message formatters -----------------------------------------------------

# Reason → user-facing phrasing for leave events. Frozen here so the
# lobby route stays a thin serializer.
_LEAVE_PHRASES = {
    "forced_leave": "busted out at",
    "stake_up_queued": "won big and is shopping up from",
    "take_break": "is taking a break from",
    "bored_move": "moved on from",
}


def format_leave_message(name: str, stake_label: str, reason: str) -> str:
    """Human-readable phrasing for a leave event."""
    verb = _LEAVE_PHRASES.get(reason, "left")
    return f"{name} {verb} the {stake_label} table"


def format_join_message(name: str, stake_label: str) -> str:
    """Human-readable phrasing for a join event."""
    return f"{name} sat down at the {stake_label} table"


def format_big_win_message(
    winner: str, loser: str, stake_label: str, amount: int,
) -> str:
    """Phrasing for a fake-sim big-win event."""
    return f"{winner} won ${amount:,} off {loser} at {stake_label}"


def format_big_loss_message(
    loser: str, winner: str, stake_label: str, amount: int,
) -> str:
    """Phrasing for a fake-sim big-loss event (the loser's POV).

    Symmetric phrasing to the win event; emitted alongside so the
    ticker can show whichever framing reads best. The lobby keeps
    only one of the pair (win is the more dramatic verb), but both
    are recorded so future filtering / per-personality feeds can
    pick either side."""
    return f"{loser} dropped ${amount:,} to {winner} at {stake_label}"


def serialize_event(event: LobbyEvent) -> dict:
    """JSON-friendly dict for the lobby response. Equivalent to
    dataclasses.asdict but explicit so changes here are visible at
    the wire boundary."""
    return asdict(event)
